"""Generate tracker-ready tickets from a PRD (or a free-form insight).

Binds the vendored `user-stories` skill (backend/skills/user-stories/ — the
"ticket" spec-consuming rewrite) through the LLM gateway so every batch is
produced by the same method the founder mapped to this capability, and is
recorded in the decision log under agent="user_stories".

The skill is spec-aware: when the PRD carries a machine-readable Part B
(`prds.llm_part`), it INHERITS acceptance criteria from the spec's tests rather
than re-deriving them; with prose only (`payload_md`) it generates INVEST
stories with Given/When/Then criteria from scratch. We pass both parts to the
model and let the skill decide.

The output contract mirrors the skill's *canonical ticket*: a five-section
structured description (What / Why now / User story / Scope / Out of scope),
the trace spine (`Part A §5 R# → Part B EARS → tests`), inherited acceptance
criteria carrying inline `[failure]`/`[edge]` tags, child issues (subtasks),
blocked-by/blocks dependencies, the stakes-gate route, and ticket-type
(build / decision / spike) so decision tickets and spikes render distinctly.
Every field is additive over the legacy `{title, body, acceptance_criteria,
priority, route}` shape, so persisted sets and the push step keep working.

This module ONLY generates. Pushing tickets into a tracker is a separate,
explicit step (see app.stories.push) so the user reviews before anything is
written to their tracker.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.db.prds import get_prd_rendered
from app.graph.gateway import llm_call

logger = logging.getLogger(__name__)

PROMPT_VERSION = "user-stories-v2"

# Output contract for the gateway — the skill's canonical ticket. `title`,
# `body`, and `acceptance_criteria` stay required for backward compatibility
# (legacy readers and the ClickUp push path still use them); every structured
# field below is additive and optional, filled by the ticket skill when the
# input supports it and left empty when it doesn't (never fabricated).
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "part_b_detected": {
            "type": "boolean",
            "description": (
                "True when a machine-readable Part B (Implementation Spec) was "
                "provided and acceptance criteria were inherited from it."
            ),
        },
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticket_type": {
                        "type": "string",
                        "enum": ["build", "decision", "spike"],
                        "description": (
                            "build = a deliverable; decision = a [ESCALATE] "
                            "choice with an owner; spike = a timeboxed "
                            "[ASSUMPTION → T0] validation. Default build."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "Short ticket title (no key prefix).",
                    },
                    "body": {
                        "type": "string",
                        "description": (
                            "User story: 'As a <role>, I want <goal>, so that "
                            "<benefit>.' plus 1-2 lines of context. Kept for the "
                            "tracker description; mirror it in `user_story`."
                        ),
                    },
                    # ── Five-section structured description ──
                    "what": {
                        "type": "string",
                        "description": "One plain sentence naming the deliverable.",
                    },
                    "why_now": {
                        "type": "string",
                        "description": (
                            "1-2 sentences of background: the driving facts "
                            "(cite the signal) and the window/urgency."
                        ),
                    },
                    "user_story": {
                        "type": "string",
                        "description": "One 'As a… I want… so that…' sentence.",
                    },
                    "scope": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "3-5 short 'must cover' scope bullets.",
                    },
                    "out_of_scope": {
                        "type": "string",
                        "description": (
                            "One line; name the ticket that owns excluded work "
                            "where relevant."
                        ),
                    },
                    # ── Provenance / trace spine ──
                    "prd_section": {
                        "type": "string",
                        "description": (
                            "Provenance anchor, e.g. 'Part A §5 R3'. Empty when "
                            "generated from prose without a §5 table."
                        ),
                    },
                    "ears_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Part B EARS ids this ticket traces to (e.g. ['E1']).",
                    },
                    "signals": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Signal/Source citations from Part A §5.",
                    },
                    # ── Acceptance ──
                    "acceptance_criteria": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Given/When/Then scenarios. Inherited VERBATIM from "
                            "Part B tests when present; prefix failure branches "
                            "with '[failure]' and edge cases with '[edge]'. With "
                            "prose only, generate them and include >=1 edge/"
                            "negative case."
                        ),
                    },
                    "ac_inherited": {
                        "type": "boolean",
                        "description": (
                            "True when acceptance_criteria were inherited from "
                            "Part B (read-only downstream); false when generated."
                        ),
                    },
                    # ── Delivery structure ──
                    "subtasks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Child issues from Part B tasks. Prefix parallel-safe "
                            "ones with '[P]'."
                        ),
                    },
                    "blocked_by": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Titles of tickets that must land first.",
                    },
                    "blocks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Titles of tickets this one unblocks.",
                    },
                    "story_points": {
                        "type": ["integer", "null"],
                        "description": "Advisory estimate; null if unknown.",
                    },
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Short labels/tags for the ticket.",
                    },
                    "data_gaps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "[NEED] markers surfaced verbatim — never filled with "
                            "invented numbers."
                        ),
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["urgent", "high", "normal", "low"],
                        "description": "Priority from the Part A §5 Priority column.",
                    },
                    "route": {
                        "type": "string",
                        "enum": ["agent-ready", "needs-human"],
                        "description": (
                            "Stakes gate: agent-ready (reversible, fully "
                            "specified → Claude Code) vs needs-human."
                        ),
                    },
                    # ── Decision-ticket fields (ticket_type == 'decision') ──
                    "decision": {
                        "type": ["string", "null"],
                        "description": "The decision to make (decision tickets).",
                    },
                    "owner": {
                        "type": ["string", "null"],
                        "description": "Who decides (decision tickets).",
                    },
                    "decide_by": {
                        "type": ["string", "null"],
                        "description": "Decide-by date/marker (decision tickets).",
                    },
                    # ── Spike fields (ticket_type == 'spike') ──
                    "timebox": {
                        "type": ["string", "null"],
                        "description": "Timebox for a spike (e.g. '2 days').",
                    },
                    "exit_condition": {
                        "type": ["string", "null"],
                        "description": (
                            "What validates the [ASSUMPTION → T0] contract "
                            "(spike tickets)."
                        ),
                    },
                },
                "required": ["title", "body", "acceptance_criteria"],
            },
        },
    },
    "required": ["stories"],
}

_SYSTEM = (
    "You are the Ticket agent. Apply the bound skill (the METHOD above) to turn "
    "the given PRD (or insight) into the skill's CANONICAL tickets — one or more "
    "per Part A §5 requirement row.\n"
    "For each BUILD ticket, populate the five-section description (what, "
    "why_now, user_story, scope, out_of_scope), the trace spine (prd_section "
    "like 'Part A §5 R3', ears_ids, signals), child issues (subtasks; prefix "
    "parallel-safe ones with '[P]'), dependencies (blocked_by / blocks by "
    "title), priority (from the §5 Priority column), and the stakes-gate route.\n"
    "ACCEPTANCE CRITERIA: when a machine-readable Part B is provided, INHERIT "
    "them verbatim from its spec-first tests and set ac_inherited=true; render "
    "failure branches prefixed '[failure]' and edge cases '[edge]'. With prose "
    "only, GENERATE Given/When/Then criteria (>=1 edge/negative case) and set "
    "ac_inherited=false.\n"
    "Turn each Part B [ESCALATE] into a DECISION ticket (ticket_type=decision, "
    "with decision/owner/decide_by and the build tickets it blocks) and each "
    "[ASSUMPTION → T0] into a SPIKE (ticket_type=spike, with timebox and "
    "exit_condition). Preserve [NEED] markers verbatim in data_gaps — never "
    "invent numbers, owners, or criteria. Also mirror the user story into "
    "`body`. Return only the structured tickets."
)

# ClickUp priority is an int 1-4 (1=urgent ... 4=low). Map the skill's
# human-readable label to that scale for the push step.
_PRIORITY_TO_CLICKUP = {"urgent": 1, "high": 2, "normal": 3, "low": 4}


def _clean_str_list(value: Any) -> list[str]:
    """Coerce a schema array into a list of trimmed non-empty strings."""
    if not isinstance(value, list):
        return []
    return [s for s in (str(x).strip() for x in value) if s]


@dataclass
class Story:
    """One generated ticket — the skill's canonical ticket, tracker-agnostic
    until pushed. `title`/`body`/`acceptance_criteria` are the legacy core;
    everything else is the additive structured contract (empty when the input
    doesn't support it — never fabricated)."""

    title: str
    body: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    priority: Optional[str] = None
    route: Optional[str] = None
    # Ticket type + five-section description
    ticket_type: str = "build"
    what: str = ""
    why_now: str = ""
    user_story: str = ""
    scope: list[str] = field(default_factory=list)
    out_of_scope: str = ""
    # Provenance / trace spine
    prd_section: str = ""
    ears_ids: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    ac_inherited: bool = False
    # Delivery structure
    subtasks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    story_points: Optional[int] = None
    labels: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    # Decision-ticket fields
    decision: Optional[str] = None
    owner: Optional[str] = None
    decide_by: Optional[str] = None
    # Spike fields
    timebox: Optional[str] = None
    exit_condition: Optional[str] = None

    def stable_id(self) -> str:
        """A content-derived id (hash of title + body). Stable across list
        reordering and identical regenerations; a genuinely different story
        (changed title/body) hashes differently, so per-ticket edit overrides
        keyed off this id never misattach to the wrong ticket."""
        seed = f"{self.title}\x1f{self.body}".encode("utf-8")
        return hashlib.sha256(seed).hexdigest()[:12]

    def clickup_priority(self) -> Optional[int]:
        """ClickUp's 1-4 priority for this story, or None if unset/unknown."""
        if not self.priority:
            return None
        return _PRIORITY_TO_CLICKUP.get(self.priority.lower())

    def to_description(self) -> str:
        """Render the ticket as a tracker task description (markdown). Uses the
        five-section body when present, falling back to the legacy story body.
        Used by the push step."""
        parts: list[str] = []
        if self.what:
            parts += ["**What**", self.what, ""]
        if self.why_now:
            parts += ["**Why now**", self.why_now, ""]
        story_line = self.user_story or self.body
        if story_line:
            parts += ["**User story**", story_line.strip(), ""]
        if self.scope:
            parts += ["**Scope**"]
            parts += [f"- {s}" for s in self.scope]
            parts += [""]
        if self.out_of_scope:
            parts += ["**Out of scope**", self.out_of_scope, ""]
        if not parts:  # nothing structured — fall back to the raw body
            parts = [self.body.strip(), ""]
        if self.acceptance_criteria:
            parts += ["**Acceptance criteria**"]
            parts += [f"- {ac}" for ac in self.acceptance_criteria]
            parts += [""]
        if self.subtasks:
            parts += ["**Child issues**"]
            parts += [f"- {t}" for t in self.subtasks]
            parts += [""]
        if self.prd_section:
            parts += [f"_Provenance: {self.prd_section}_"]
        if self.route:
            parts += [f"_Route: {self.route}_"]
        return "\n".join(parts).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.stable_id(),
            "ticket_type": self.ticket_type,
            "title": self.title,
            "body": self.body,
            "what": self.what,
            "why_now": self.why_now,
            "user_story": self.user_story,
            "scope": list(self.scope),
            "out_of_scope": self.out_of_scope,
            "prd_section": self.prd_section,
            "ears_ids": list(self.ears_ids),
            "signals": list(self.signals),
            "acceptance_criteria": list(self.acceptance_criteria),
            "ac_inherited": self.ac_inherited,
            "subtasks": list(self.subtasks),
            "blocked_by": list(self.blocked_by),
            "blocks": list(self.blocks),
            "story_points": self.story_points,
            "labels": list(self.labels),
            "data_gaps": list(self.data_gaps),
            "priority": self.priority,
            "route": self.route,
            "decision": self.decision,
            "owner": self.owner,
            "decide_by": self.decide_by,
            "timebox": self.timebox,
            "exit_condition": self.exit_condition,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Story":
        sp = d.get("story_points")
        return cls(
            title=str(d.get("title") or "").strip(),
            body=str(d.get("body") or d.get("user_story") or "").strip(),
            acceptance_criteria=[str(x) for x in (d.get("acceptance_criteria") or [])],
            priority=(d.get("priority") or None),
            route=(d.get("route") or None),
            ticket_type=str(d.get("ticket_type") or "build"),
            what=str(d.get("what") or "").strip(),
            why_now=str(d.get("why_now") or "").strip(),
            user_story=str(d.get("user_story") or "").strip(),
            scope=_clean_str_list(d.get("scope")),
            out_of_scope=str(d.get("out_of_scope") or "").strip(),
            prd_section=str(d.get("prd_section") or "").strip(),
            ears_ids=_clean_str_list(d.get("ears_ids")),
            signals=_clean_str_list(d.get("signals")),
            ac_inherited=bool(d.get("ac_inherited")),
            subtasks=_clean_str_list(d.get("subtasks")),
            blocked_by=_clean_str_list(d.get("blocked_by")),
            blocks=_clean_str_list(d.get("blocks")),
            story_points=int(sp) if isinstance(sp, (int, float)) else None,
            labels=_clean_str_list(d.get("labels")),
            data_gaps=_clean_str_list(d.get("data_gaps")),
            decision=(d.get("decision") or None),
            owner=(d.get("owner") or None),
            decide_by=(d.get("decide_by") or None),
            timebox=(d.get("timebox") or None),
            exit_condition=(d.get("exit_condition") or None),
        )


class PRDNotFoundError(LookupError):
    """Raised when a prd_id has no row."""


def _build_input(*, prd: Optional[dict], insight: Optional[str]) -> str:
    """Assemble the model input from a PRD row (Part A + Part B) or a raw
    insight string."""
    if prd is not None:
        sections = [f"# PRD: {prd.get('title') or '(untitled)'}", ""]
        human = prd.get("payload_md") or ""
        if human:
            sections += ["## Part A (human-readable PRD)", human, ""]
        llm_part = prd.get("llm_part") or ""
        if llm_part:
            sections += [
                "## Part B (machine-readable Implementation Spec)",
                "Inherit acceptance criteria from this spec's tests.",
                llm_part,
            ]
        else:
            sections += [
                "_No Part B present — generate INVEST stories from the prose "
                "above and flag that criteria are generated, not inherited "
                "(ac_inherited=false)._"
            ]
        return "\n".join(sections)
    return f"# Insight\n\n{insight or ''}"


def generate_user_stories(
    enterprise_id: str,
    *,
    prd_id: Optional[int] = None,
    insight: Optional[str] = None,
    model: Optional[str] = None,
) -> list[Story]:
    """Generate tickets for a company from a PRD or a free-form insight.

    Exactly one of `prd_id` / `insight` must be given. The call is bound to the
    ticket skill and logged (agent="user_stories"). Returns a list of
    `Story`; this NEVER writes to a tracker — that's app.stories.push.
    """
    if (prd_id is None) == (insight is None):
        raise ValueError("provide exactly one of prd_id or insight")

    prd: Optional[dict] = None
    purpose = "from_insight"
    if prd_id is not None:
        prd = get_prd_rendered(prd_id)
        if prd is None:
            raise PRDNotFoundError(f"PRD {prd_id} not found")
        purpose = "from_prd"

    result = llm_call(
        enterprise_id=enterprise_id,
        agent="user_stories",
        purpose=purpose,
        prompt_version=PROMPT_VERSION,
        system=_SYSTEM,
        input=_build_input(prd=prd, insight=insight),
        json_schema=_SCHEMA,
        skill="user-stories",
        model=model,
        # Large structured output — stream on the long read timeout (was tripping
        # httpx.ReadTimeout on the default 120s non-streamed path).
        long_output=True,
    )
    raw = (result.output or {}).get("stories", []) if result.output else []
    stories = [Story.from_dict(s) for s in raw if s.get("title")]

    # Persist the generated set for a PRD so the Tickets tab can serve it without
    # re-running this multi-minute call until the PRD content actually changes.
    # Keyed by a content hash of the rendered PRD (see app.db.prd_tickets). Never
    # let a persistence write break generation — the stories are still returned.
    if prd_id is not None and prd is not None:
        try:
            from app.db.prd_tickets import hash_prd_row, save_tickets

            save_tickets(
                enterprise_id,
                prd_id,
                hash_prd_row(prd),  # hash the row we already rendered above
                [s.to_dict() for s in stories],
            )
        except Exception:  # noqa: BLE001
            logger.exception("persisting prd_tickets failed (continuing)")

    # Record the semantic decision (what was produced) alongside the gateway's
    # own llm_call telemetry row. Never let an audit-write break generation.
    try:
        from app.graph.decision_log import log_agent_decision

        log_agent_decision(
            enterprise_id=enterprise_id,
            agent="user_stories",
            decision_type="generate_user_stories",
            factors={"prd_id": prd_id, "from_insight": insight is not None},
            output={"count": len(stories),
                    "titles": [s.title for s in stories]},
            model=result.model,
            prompt_version=result.prompt_version,
        )
    except Exception:  # noqa: BLE001
        logger.exception("user_stories decision log write failed (continuing)")

    return stories
