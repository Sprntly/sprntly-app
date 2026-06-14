"""Unified Q&A agent — the single front door behind every "ask" surface.

Pipeline (deterministic control flow; model only where judgement is needed):

  1. ROUTE   — decide skill-or-direct:
       slash fast-path  (`/prioritize …`)            → that skill, conf 1.0
       regex fast-path  (skill_router.detect_intent) → that skill, if routable
       else LLM router  (haiku over the routable manifest) → {skill_id|none}
  2. ANSWER  — skill → gateway.llm_call(skill=…) on sonnet, escalating heavy
               skills to opus; direct → compose_ask_answer (corpus + KG).
  3. (history) prior conversation turns are folded in for both the router and
     the answer so follow-ups ("now prioritise them") resolve.

Everything routes through the existing LLM gateway, so tenant isolation,
prompt-cache, cost/usage, and the decision-log audit spine keep working. The
generic path keeps using compose_ask_answer unchanged.

Models (decision 2026-06-13): router = haiku, answer = sonnet, heavy → opus.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from app.ask_runner import _ASK_RESPONSE_SCHEMA, compose_ask_answer
from app.graph.gateway import llm_call
from app.llm import run_tool_loop
from app.prompts import ASK_SYSTEM
from app.skill_router import detect_intent
from app.skills.catalog import COST_GATED, NON_ROUTABLE, routable_manifest
from app.skills.loader import get_skill, list_skills
from app.skills.scripts import SCRIPT_TOOLS

logger = logging.getLogger(__name__)

ROUTER_MODEL = "claude-haiku-4-5"
ANSWER_MODEL = "claude-sonnet-4-6"
HEAVY_MODEL = "claude-opus-4-8"

# Skills heavy enough (deep analysis / long output) to answer on opus rather
# than sonnet. Tunable — keep small; most skills do fine on sonnet.
HEAVY_SKILLS: frozenset[str] = frozenset(
    {"competitive-intelligence-review", "prd-author"}
)

# Optional fact-check verify pass over high-stakes answers (claims/numbers).
# OFF by default — flip via set_verify(True) / config. fact-check is otherwise
# non-routable; this is the internal verification use it was kept for.
VERIFY_ENABLED = False
HIGH_STAKES_SKILLS: frozenset[str] = frozenset(
    {"prd-author", "competitive-intelligence-review", "saas-metrics-diagnosis",
     "experiment-readout", "market-structure"}
)


def set_verify(enabled: bool) -> None:
    """Toggle the fact-check verify pass (config hook)."""
    global VERIFY_ENABLED
    VERIFY_ENABLED = enabled

# LLM router accepts a skill only at/above this confidence; below → direct.
_LLM_ROUTE_THRESHOLD = 0.6
# Regex fast-path threshold (matches the historical /v1/ask gate).
_REGEX_ROUTE_THRESHOLD = 0.75
# How many prior turns to feed the router / answer for follow-up context.
_HISTORY_TURNS = 6

_ROUTE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "skill_id": {
            "type": "string",
            "description": "Exact id of the single best-fit skill, or 'none' if the question is general and no skill clearly applies.",
        },
        "confidence": {"type": "number", "description": "0..1"},
        "reason": {"type": "string", "description": "One short clause."},
    },
    "required": ["skill_id", "confidence", "reason"],
}

_ROUTER_SYSTEM = (
    "You are a router for a product-management assistant. Given the user's "
    "question (and recent conversation), pick the SINGLE best-fit PM skill from "
    "the menu, or 'none' if the question is general/conversational and no skill "
    "clearly applies. Prefer 'none' over a weak match. Return the skill's exact "
    "id."
)


@dataclass
class RouteDecision:
    skill_id: Optional[str]      # None → answer directly (no skill)
    confidence: float
    source: str                  # "slash" | "regex" | "llm" | "none"
    action: str = ""             # human label for the UI


@lru_cache(maxsize=1)
def _router_menu() -> str:
    """`- <id>: <description>` for every routable skill — the router's menu.
    Stable across calls, so it rides the gateway's cacheable prefix."""
    lines = [f"- {s['id']}: {s['description']}" for s in routable_manifest()]
    return "Available skills:\n" + "\n".join(lines)


def _render_history(history: Optional[list[dict]]) -> str:
    """Render the last few turns as plain text for prompt context."""
    if not history:
        return ""
    recent = history[-_HISTORY_TURNS:]
    rows = [f"{t.get('role', 'user').capitalize()}: {t.get('content', '')}" for t in recent]
    return "Conversation so far:\n" + "\n".join(rows) + "\n\n"


def _routable(skill_id: str) -> bool:
    return skill_id in set(list_skills()) and skill_id not in NON_ROUTABLE


def route(
    question: str,
    *,
    enterprise_id: str,
    history: Optional[list[dict]] = None,
) -> RouteDecision:
    """Decide whether a skill applies, and which. Slash + regex fast-paths skip
    the LLM router; otherwise classify with haiku over the routable menu."""
    q = question.strip()

    # 1) Explicit slash command: "/prioritize rank these"
    if q.startswith("/"):
        token = q[1:].split(None, 1)[0].lower()
        if _routable(token):
            return RouteDecision(token, 1.0, "slash", token)

    # 2) Regex fast-path (cheap, no LLM) — only for routable skills.
    intent = detect_intent(question)
    if intent and intent.confidence >= _REGEX_ROUTE_THRESHOLD and _routable(intent.skill_id):
        return RouteDecision(intent.skill_id, intent.confidence, "regex", intent.action)

    # 3) LLM router over the full routable menu.
    try:
        result = llm_call(
            enterprise_id=enterprise_id,
            agent="qa-router",
            purpose="route",
            model=ROUTER_MODEL,
            system=_ROUTER_SYSTEM,
            input=_render_history(history) + f"Question: {question}",
            prompt_version="qa-router-v1",
            json_schema=_ROUTE_SCHEMA,
            user_cacheable_prefix=_router_menu(),
            max_tokens=300,
        )
        out = result.output if isinstance(result.output, dict) else {}
        sid = (out.get("skill_id") or "none").strip()
        conf = float(out.get("confidence") or 0.0)
        if sid != "none" and _routable(sid) and conf >= _LLM_ROUTE_THRESHOLD:
            return RouteDecision(sid, conf, "llm", sid)
    except Exception:  # noqa: BLE001 — routing must never break the answer
        logger.exception("LLM router failed; answering directly")

    return RouteDecision(None, 0.0, "none")


# Confirm gate (decision 2026-06-13): cost-gated skills return this instead of
# running, so the UI can ask the PM how deep to go. v1 trips on every fresh
# route of a cost-gated skill (not when pinned via the follow-up); scope-aware
# auto-run of a tiny one-competitor teardown is a documented follow-up.
def _confirm_payload(skill_id: str, question: str) -> dict:
    return {
        "type": "needs_confirmation",
        "skill": skill_id,
        "scope": {"depth": "full"},
        "estimate": {"tier": "deep", "duration_s": 210},
        "options": [
            {"id": "quick", "label": "Quick teardown", "scope": {"depth": "quick"}},
            {"id": "full", "label": "Full review", "scope": {"depth": "full"}},
        ],
        # Keep the standard Ask shape so the route's _strip_citations + UI don't break.
        "answer": "",
        "key_points": [],
        "citations": [],
        "confidence": 0.0,
        "unanswered": "",
        "_skill": skill_id,
    }


def _tag(payload: dict, decision: RouteDecision) -> dict:
    payload["_skill"] = decision.skill_id
    payload["_skill_action"] = decision.action
    payload["_skill_confidence"] = decision.confidence
    payload["_skill_source"] = decision.source
    return payload


def _answer_single_shot(decision: RouteDecision, enterprise_id, question, history) -> dict:
    """Skill answer via one gateway call (SKILL.md injected by the gateway)."""
    model = HEAVY_MODEL if decision.skill_id in HEAVY_SKILLS else ANSWER_MODEL
    result = llm_call(
        enterprise_id=enterprise_id,
        agent="qa",
        purpose="skill_answer",
        model=model,
        system=(
            ASK_SYSTEM
            + f"\n\nThe user's question maps to the '{decision.skill_id}' skill. "
            "Follow that skill's method to produce a structured, actionable answer."
        ),
        input=_render_history(history) + f"Question: {question}",
        prompt_version="qa-skill-v1",
        json_schema=_ASK_RESPONSE_SCHEMA,
        skill=decision.skill_id,
        max_tokens=12000,
    )
    payload = (
        result.output
        if isinstance(result.output, dict)
        else {"answer": str(result.output), "key_points": [], "citations": [],
              "confidence": decision.confidence, "unanswered": ""}
    )
    return _tag(payload, decision)


def _answer_with_script(decision: RouteDecision, enterprise_id, question, history) -> dict:
    """Skill answer via a tool-use loop so the skill's deterministic script runs
    ON OUR INFRA (app.skills.scripts) instead of the model estimating the math."""
    skill_id = decision.skill_id
    tool = SCRIPT_TOOLS[skill_id]
    spec = get_skill(skill_id)
    system = (
        ASK_SYSTEM
        + f"\n\n## METHOD (skill: {skill_id})\n{spec.method}\n\n"
        f"You have a tool, `{tool.name}`, that runs the skill's deterministic "
        "script. Call it for the math instead of computing it yourself, then "
        "present the result clearly."
    )

    def dispatch(name: str, inp: dict) -> str:
        return tool.run(inp) if name == tool.name else f"(unknown tool {name})"

    meta: dict = {}
    text = run_tool_loop(
        system=system,
        user=_render_history(history) + f"Question: {question}",
        tools=[tool.as_tool()],
        dispatch=dispatch,
        model=HEAVY_MODEL if skill_id in HEAVY_SKILLS else ANSWER_MODEL,
        max_tokens=8000,
        meta_out=meta,
    )
    _log_qa(enterprise_id, skill_id, "skill_answer_script", meta)
    payload = {
        "answer": text, "key_points": [], "citations": [],
        "confidence": decision.confidence, "unanswered": "",
    }
    return _tag(payload, decision)


def _maybe_verify(payload: dict, enterprise_id: str) -> dict:
    """When enabled, run a fact-check pass over a high-stakes answer and attach
    `_verification`. Best-effort and OFF by default, so the normal flow and
    every existing test are unaffected."""
    if not VERIFY_ENABLED:
        return payload
    skill = payload.get("_skill")
    answer_text = payload.get("answer") or ""
    if skill not in HIGH_STAKES_SKILLS or not answer_text:
        return payload
    try:
        result = llm_call(
            enterprise_id=enterprise_id,
            agent="qa-verify",
            purpose="fact_check",
            model=ANSWER_MODEL,
            system="Verify the claims in the answer are grounded; flag anything unsupported.",
            input=answer_text,
            prompt_version="qa-verify-v1",
            skill="fact-check",
            max_tokens=4000,
        )
        payload["_verification"] = result.output
    except Exception:  # noqa: BLE001 — verification must never break the answer
        logger.exception("qa verify pass failed")
    return payload


def _log_qa(enterprise_id: str, skill_id: str, purpose: str, meta: dict) -> None:
    """Best-effort decision-log row for the tool-loop path (the single-shot path
    is logged by the gateway itself)."""
    try:
        from app.graph.decision_log import log_agent_decision

        log_agent_decision(
            enterprise_id=enterprise_id,
            agent="qa",
            decision_type=purpose,
            factors={"skill": skill_id, **{k: meta.get(k) for k in
                     ("input_tokens", "output_tokens") if k in meta}},
            model=meta.get("model"),
            prompt_version=f"qa-skill-script-v1+{skill_id}",
        )
    except Exception:  # noqa: BLE001
        logger.exception("qa script decision-log write failed")


def answer(
    *,
    enterprise_id: str,
    question: str,
    dataset: str,
    history: Optional[list[dict]] = None,
    pinned_skill: Optional[str] = None,
) -> dict:
    """Answer a question via the best skill, or directly. `pinned_skill` skips
    routing (used when a confirm-gate follow-up has already chosen the skill)."""
    if pinned_skill and _routable(pinned_skill):
        decision = RouteDecision(pinned_skill, 1.0, "pinned", pinned_skill)
    else:
        decision = route(question, enterprise_id=enterprise_id, history=history)

    if not decision.skill_id:
        # Direct path — corpus + KG, unchanged. Fold history into the question.
        q = _render_history(history) + question if history else question
        return compose_ask_answer(dataset, q, enterprise_id=enterprise_id)

    # Cost-gated skill freshly routed → ask before spending (CIR). A pinned
    # follow-up has already confirmed, so it runs.
    if decision.skill_id in COST_GATED and decision.source != "pinned":
        return _confirm_payload(decision.skill_id, question)

    if decision.skill_id in SCRIPT_TOOLS:
        payload = _answer_with_script(decision, enterprise_id, question, history)
    else:
        payload = _answer_single_shot(decision, enterprise_id, question, history)
    return _maybe_verify(payload, enterprise_id)
