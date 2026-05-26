"""Synthesis Agent — On-Demand Mode (spec §4).

The PM types a question or request into Agent Chat. Synthesis loads the
workspace's KG context (workspace + active hypotheses + recent decisions +
recent outcomes via `GraphFacade.load_session_context`) and reasons over
that snapshot to decide one of three outcomes:

  1. KG has full context  → generate the requested artifact immediately.
  2. KG is missing critical context → ask ONE targeted clarifying question.
  3. KG has partial context → generate a best-effort artifact, flagging
                              the assumptions inline.

Hard rules:
  - Exactly one clarifying question per turn. Anything more is rejected
    (we truncate to the first question — silently dropping any extras —
    so the chat surface never has to render a list of asks).
  - Never ask what the KG can already answer. That's enforced by the
    prompt; we don't have a deterministic check here because the
    "answerability" predicate is fuzzy.
  - Allowed artifact_type values are clamped to the literal whitelist; an
    out-of-band value from the LLM is rejected (502).
  - If the user message starts with the "I want to build X" pattern, we
    bias the artifact_type to "prd". The LLM still picks the type, but
    we override on artifact responses where it disagrees with that
    explicit signal.

Engineering decision (Apurva, 2026-05-26): the conversation_id passed
back in the response is either the one the caller sent us (multi-turn
continuation) or a freshly-generated UUID4 if this was the first turn.
We don't persist conversations server-side yet — the frontend round-trips
prior_turns. A persistence layer lands when chat history surfaces in
the UI; until then this keeps the route stateless.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from app.graph import GraphFacade
from app.llm import call_json

logger = logging.getLogger(__name__)


# ─────────────────────────── constants ───────────────────────────

ALLOWED_ARTIFACT_TYPES = (
    "prd",
    "leadership_comm",
    "strategic_analysis",
    "sprint_plan",
    "documentation",
)

ALLOWED_CONFIDENCE_TIERS = ("low", "medium", "high", "very_high")

# Pattern that biases artifact_type to "prd". Matches things like
# "I want to build X", "I'd like to build a new onboarding flow", etc.
# Case-insensitive, tolerant of leading whitespace. The branches are
# split because "I'd" has no space between "I" and "'d", while "I want"
# does — a single \s+ between i and the verb-stem can't span both.
_BUILD_X_PATTERN = re.compile(
    r"^\s*i(?:\s+(?:want|would\s+like)|'(?:d\s+like|ll\s+want))\s+to\s+build\b",
    re.IGNORECASE,
)


# ─────────────────────────── pydantic models ───────────────────────────


class PmChatTurn(BaseModel):
    """One turn of the PM ↔ Synthesis chat. Multi-turn continuations
    include `conversation_id` + `prior_turns` so the LLM can ground on
    earlier exchanges without a server-side store.
    """

    workspace_id: str = Field(..., min_length=1)
    user_message: str = Field(..., min_length=1, max_length=2000)
    conversation_id: Optional[str] = None
    prior_turns: list[dict] = Field(default_factory=list)


class ClarifyingQuestion(BaseModel):
    """The single targeted question Synthesis asks when the KG is missing
    critical context. `kg_gaps` enumerates what's missing so the frontend
    can render a hint and (eventually) wire it to the right connector
    onboarding flow.
    """

    question: str = Field(..., min_length=1)
    kg_gaps: list[str] = Field(default_factory=list)


class ArtifactResponse(BaseModel):
    """The artifact Synthesis produced. `assumptions` is non-empty when
    the KG had partial context — those assumptions are also flagged
    inline in the markdown body. `confidence` corresponds to the KG
    `ConfidenceTier` so downstream consumers can match thresholds.
    """

    artifact_type: Literal[
        "prd", "leadership_comm", "strategic_analysis", "sprint_plan", "documentation"
    ]
    title: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)  # markdown body
    assumptions: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high", "very_high"]


class SynthesisOnDemandResponse(BaseModel):
    """Envelope returned from `respond_to_pm`. Exactly one of
    `clarification` / `artifact` is populated, matching `mode`."""

    mode: Literal["clarify", "artifact"]
    clarification: Optional[ClarifyingQuestion] = None
    artifact: Optional[ArtifactResponse] = None
    conversation_id: str

    @field_validator("conversation_id")
    @classmethod
    def _conversation_id_nonempty(cls, v: str) -> str:
        if not v:
            raise ValueError("conversation_id must be non-empty")
        return v


# ─────────────────────────── prompt assembly ───────────────────────────


SYSTEM_PROMPT = """\
You are Sprntly's Synthesis Agent operating in ON-DEMAND mode.

A Product Manager has typed a message into Agent Chat. You have a snapshot \
of their workspace's Knowledge Graph: the company profile, the KPI tree, \
current strategy (OKRs + dead-ends), up to 10 active hypotheses, the 5 most \
recent decisions, and the 3 most recently measured outcomes.

Your job is to decide whether the KG holds enough context to fulfill the PM's \
request right now.

Decision rules:
  1. If the KG has full context → produce the requested artifact and return \
     mode="artifact" with confidence="high" or "very_high" and assumptions=[].
  2. If the KG is missing CRITICAL context that no reasonable assumption can \
     bridge → return mode="clarify" with EXACTLY ONE targeted question.
  3. If the KG has partial context → produce a best-effort artifact, set \
     mode="artifact", set confidence="low" or "medium", and list every \
     assumption you made in the `assumptions` array. Also flag each \
     assumption inline in the markdown body using > **Assumption:** quotes.

HARD RULES (violations are bugs):
  - NEVER ask more than one clarifying question in a single turn. If you \
    have multiple, pick the one that unblocks the most of the request and \
    drop the rest.
  - NEVER ask what the KG already answers. If the KPI tree, hypotheses, \
    decisions, or outcomes contain the information, USE IT.
  - artifact_type MUST be exactly one of: \
    "prd", "leadership_comm", "strategic_analysis", "sprint_plan", "documentation".
  - For strategic_analysis artifacts, frame findings using the 5-field \
    hypothesis structure (claim, evidence, predicted impact, reversal \
    condition, assumptions) from the KG schema.
  - If the PM's message matches the pattern "I want to build X" / "I'd like \
    to build X", default artifact_type to "prd".

Output JSON matching this exact shape (extra keys are stripped):

{
  "mode": "clarify" | "artifact",
  "clarification": {"question": "...", "kg_gaps": ["..."]} | null,
  "artifact": {
     "artifact_type": "prd" | "leadership_comm" | "strategic_analysis" | "sprint_plan" | "documentation",
     "title": "...",
     "content": "...(markdown)...",
     "assumptions": ["..."],
     "confidence": "low" | "medium" | "high" | "very_high"
  } | null,
  "conversation_id": "..."
}
"""


def _summarize_workspace(ws: Any) -> dict[str, Any]:
    """Pull the strategic context out of the Workspace entity. We send a
    compact dict (not the full pydantic dump) to keep the LLM prompt
    tight — load_session_context can return ~10KB of JSON otherwise.
    """
    if ws is None:
        return {}
    strategy = getattr(ws, "strategy", None)
    kpi_tree = getattr(ws, "kpi_tree", []) or []
    return {
        "company_name": getattr(ws, "company_name", None),
        "industry": getattr(ws, "industry", None),
        "stage": getattr(getattr(ws, "stage", None), "value", None),
        "business_model": getattr(ws, "business_model", None),
        "kpi_tree": [
            {
                "name": getattr(n, "name", None),
                "role": getattr(n, "role", None),
                "target_value": getattr(n, "target_value", None),
                "current_value": getattr(n, "current_value", None),
                "parent": getattr(n, "parent", None),
            }
            for n in kpi_tree
        ],
        "strategy": {
            "okrs": getattr(strategy, "okrs", []) if strategy else [],
            "dead_ends": getattr(strategy, "dead_ends", []) if strategy else [],
            "current_priorities": getattr(strategy, "current_priorities", [])
            if strategy
            else [],
            "biggest_risk": getattr(strategy, "biggest_risk", None) if strategy else None,
        },
        "competitors": getattr(ws, "competitors", []),
    }


def _summarize_hypotheses(hypotheses: list[Any]) -> list[dict[str, Any]]:
    out = []
    for h in hypotheses or []:
        out.append(
            {
                "hypothesis_id": getattr(h, "hypothesis_id", None),
                "claim": getattr(h, "claim", None),
                "status": getattr(getattr(h, "status", None), "value", None),
                "predicted_metric": getattr(h, "predicted_metric", None),
                "predicted_impact_low": getattr(h, "predicted_impact_low", None),
                "predicted_impact_high": getattr(h, "predicted_impact_high", None),
                "confidence_tier": getattr(
                    getattr(h, "confidence_tier", None), "value", None
                ),
                "reversal_condition": getattr(h, "reversal_condition", None),
            }
        )
    return out


def _summarize_decisions(decisions: list[Any]) -> list[dict[str, Any]]:
    out = []
    for d in decisions or []:
        out.append(
            {
                "decision_id": getattr(d, "decision_id", None),
                "claim": getattr(d, "claim", None),
                "reasoning": getattr(d, "reasoning", None),
                "approved_at": getattr(d, "approved_at", None).isoformat()
                if getattr(d, "approved_at", None)
                else None,
            }
        )
    return out


def _summarize_outcomes(outcomes: list[Any]) -> list[dict[str, Any]]:
    out = []
    for o in outcomes or []:
        out.append(
            {
                "outcome_id": getattr(o, "outcome_id", None),
                "feature_name": getattr(o, "feature_name", None),
                "metric_measured": getattr(o, "metric_measured", None),
                "predicted_impact_low": getattr(o, "predicted_impact_low", None),
                "predicted_impact_high": getattr(o, "predicted_impact_high", None),
                "actual_impact": getattr(o, "actual_impact", None),
                "prediction_hit": getattr(o, "prediction_hit", None),
            }
        )
    return out


def _build_user_prompt(turn: PmChatTurn, context: dict[str, Any]) -> str:
    """Compose the user-turn payload sent to the LLM. JSON-serialized
    so the model can reliably parse the snapshot without us pretending
    to render it as markdown."""
    snapshot = {
        "workspace": _summarize_workspace(context.get("workspace")),
        "active_hypotheses": _summarize_hypotheses(context.get("active_hypotheses", [])),
        "recent_decisions": _summarize_decisions(context.get("recent_decisions", [])),
        "recent_outcomes": _summarize_outcomes(context.get("recent_outcomes", [])),
    }
    parts = [
        "WORKSPACE KG SNAPSHOT (JSON):",
        json.dumps(snapshot, default=str, indent=2),
    ]
    if turn.prior_turns:
        parts += [
            "",
            "PRIOR CHAT TURNS (oldest first):",
            json.dumps(turn.prior_turns, default=str, indent=2),
        ]
    parts += ["", "PM MESSAGE:", turn.user_message]
    return "\n".join(parts)


# ─────────────────────────── response normalization ───────────────────────────


def _normalize_clarification(raw: Any) -> ClarifyingQuestion:
    """Coerce the LLM's clarification block into the model. If the LLM
    returned multiple questions (in `questions` or a list under `question`),
    we silently keep only the first — spec rule §4."""
    if not isinstance(raw, dict):
        raise HTTPException(502, "clarification must be an object")

    question = raw.get("question")
    if isinstance(question, list):
        # The LLM returned multiple questions. Spec: one per turn — keep the
        # first, drop the rest. Don't fail; the chat surface still has
        # something to render.
        if not question:
            raise HTTPException(502, "clarification.question list is empty")
        logger.warning(
            "synthesis on-demand: LLM returned %d clarifying questions; truncating to 1",
            len(question),
        )
        question = question[0]
    if isinstance(question, str):
        # Reject multi-question strings concatenated with '?' — split on '?'
        # and keep only the first non-empty fragment. Same reasoning.
        chunks = [c.strip() for c in question.split("?") if c.strip()]
        if len(chunks) > 1:
            logger.warning(
                "synthesis on-demand: clarification contained %d '?'-delimited "
                "questions; truncating to 1",
                len(chunks),
            )
            question = chunks[0] + "?"
    if not isinstance(question, str) or not question.strip():
        raise HTTPException(502, "clarification.question must be a non-empty string")

    gaps = raw.get("kg_gaps") or []
    if not isinstance(gaps, list):
        raise HTTPException(502, "clarification.kg_gaps must be a list")
    return ClarifyingQuestion(question=question, kg_gaps=[str(g) for g in gaps])


def _normalize_artifact(raw: Any, user_message: str) -> ArtifactResponse:
    if not isinstance(raw, dict):
        raise HTTPException(502, "artifact must be an object")
    artifact_type = raw.get("artifact_type")
    if artifact_type not in ALLOWED_ARTIFACT_TYPES:
        raise HTTPException(
            502,
            f"invalid artifact_type={artifact_type!r}; "
            f"must be one of {ALLOWED_ARTIFACT_TYPES}",
        )
    # "I want to build X" pattern: prefer PRD even if the LLM picked
    # something else.
    if _BUILD_X_PATTERN.search(user_message or ""):
        if artifact_type != "prd":
            logger.info(
                'synthesis on-demand: rewriting artifact_type from %r to "prd" '
                "due to 'I want to build X' pattern",
                artifact_type,
            )
        artifact_type = "prd"

    confidence = raw.get("confidence")
    if confidence not in ALLOWED_CONFIDENCE_TIERS:
        raise HTTPException(
            502,
            f"invalid confidence={confidence!r}; "
            f"must be one of {ALLOWED_CONFIDENCE_TIERS}",
        )
    title = raw.get("title")
    content = raw.get("content")
    if not isinstance(title, str) or not title.strip():
        raise HTTPException(502, "artifact.title must be a non-empty string")
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(502, "artifact.content must be a non-empty string")

    assumptions = raw.get("assumptions") or []
    if not isinstance(assumptions, list):
        raise HTTPException(502, "artifact.assumptions must be a list")
    return ArtifactResponse(
        artifact_type=artifact_type,
        title=title,
        content=content,
        assumptions=[str(a) for a in assumptions],
        confidence=confidence,
    )


# ─────────────────────────── entry point ───────────────────────────


def respond_to_pm(turn: PmChatTurn, graph: GraphFacade) -> SynthesisOnDemandResponse:
    """Decide between clarify / artifact for a single PM chat turn.

    Args:
        turn: the PM's chat turn. Multi-turn continuations include
              `conversation_id` so the same id round-trips back.
        graph: the GraphFacade — tenant-isolated KG access.

    Returns:
        SynthesisOnDemandResponse with either `clarification` or
        `artifact` populated.
    """
    context = graph.load_session_context(turn.workspace_id)
    user_prompt = _build_user_prompt(turn, context)
    conversation_id = turn.conversation_id or f"conv-{uuid.uuid4().hex[:12]}"

    raw = call_json(system=SYSTEM_PROMPT, user=user_prompt)

    mode = raw.get("mode")
    if mode == "clarify":
        clarification = _normalize_clarification(raw.get("clarification"))
        return SynthesisOnDemandResponse(
            mode="clarify",
            clarification=clarification,
            artifact=None,
            conversation_id=raw.get("conversation_id") or conversation_id,
        )
    if mode == "artifact":
        artifact = _normalize_artifact(raw.get("artifact"), turn.user_message)
        return SynthesisOnDemandResponse(
            mode="artifact",
            clarification=None,
            artifact=artifact,
            conversation_id=raw.get("conversation_id") or conversation_id,
        )
    raise HTTPException(
        502,
        f"invalid mode={mode!r}; must be 'clarify' or 'artifact'",
    )
