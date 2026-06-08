"""Generate user stories from a PRD (or a free-form insight).

Binds the vendored `user-stories` skill (backend/skills/user-stories/) through
the LLM gateway so every story batch is produced by the same INVEST + Gherkin
method the founder mapped to this capability, and is recorded in the decision
log under agent="user_stories".

The skill is spec-aware: when the PRD carries a machine-readable Part B
(`prds.llm_part`), it INHERITS acceptance criteria from the spec's tests rather
than re-deriving them; with prose only (`payload_md`) it generates INVEST
stories with Given/When/Then criteria from scratch. We pass both parts to the
model and let the skill decide.

This module ONLY generates. Pushing stories into ClickUp is a separate,
explicit step (see app.stories.push) so the user reviews before anything is
written to their tracker.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.db.prds import get_prd_rendered
from app.graph.gateway import llm_call

logger = logging.getLogger(__name__)

PROMPT_VERSION = "user-stories-v1"

# Output contract for the gateway. Each story is dual-shaped: a human-readable
# title + body ("As a <role>, I want <goal>, so that <benefit>.") plus a list of
# Given/When/Then acceptance criteria, an optional priority, and the skill's
# routing tag. priority maps to ClickUp's 1-4 scale at push time.
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short ticket title (no key prefix).",
                    },
                    "body": {
                        "type": "string",
                        "description": (
                            "User story: 'As a <role>, I want <goal>, so that "
                            "<benefit>.' plus 1-2 lines of context."
                        ),
                    },
                    "acceptance_criteria": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Given/When/Then scenarios, incl. >=1 edge/negative "
                            "case. Inherited verbatim from Part B when present."
                        ),
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["urgent", "high", "normal", "low"],
                        "description": "Suggested priority; optional.",
                    },
                    "route": {
                        "type": "string",
                        "enum": ["agent-ready", "needs-human"],
                        "description": "Skill routing: agent-ready vs needs-human.",
                    },
                },
                "required": ["title", "body", "acceptance_criteria"],
            },
        },
    },
    "required": ["stories"],
}

_SYSTEM = (
    "You are the User Stories agent. Apply the bound user-stories skill (the "
    "METHOD above) to break the given PRD or insight into tracker-ready "
    "tickets. Each ticket is dual-layer: a human-readable story plus "
    "machine-readable Given/When/Then acceptance criteria. If a machine-"
    "readable Part B (Implementation Spec) is provided, INHERIT acceptance "
    "criteria from its tests rather than rewriting them; with prose only, "
    "GENERATE INVEST stories with Given/When/Then criteria including at least "
    "one edge or negative case. Return only the structured stories."
)

# ClickUp priority is an int 1-4 (1=urgent ... 4=low). Map the skill's
# human-readable label to that scale for the push step.
_PRIORITY_TO_CLICKUP = {"urgent": 1, "high": 2, "normal": 3, "low": 4}


@dataclass
class Story:
    """One generated user story, tracker-agnostic until pushed."""

    title: str
    body: str
    acceptance_criteria: list[str] = field(default_factory=list)
    priority: Optional[str] = None
    route: Optional[str] = None

    def clickup_priority(self) -> Optional[int]:
        """ClickUp's 1-4 priority for this story, or None if unset/unknown."""
        if not self.priority:
            return None
        return _PRIORITY_TO_CLICKUP.get(self.priority.lower())

    def to_description(self) -> str:
        """Render the story body + acceptance criteria as a ClickUp task
        description (markdown). Used by the push step."""
        parts = [self.body.strip()]
        if self.acceptance_criteria:
            parts.append("")
            parts.append("**Acceptance criteria**")
            for ac in self.acceptance_criteria:
                parts.append(f"- {ac}")
        if self.route:
            parts.append("")
            parts.append(f"_Route: {self.route}_")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "body": self.body,
            "acceptance_criteria": list(self.acceptance_criteria),
            "priority": self.priority,
            "route": self.route,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Story":
        return cls(
            title=str(d.get("title") or "").strip(),
            body=str(d.get("body") or "").strip(),
            acceptance_criteria=[
                str(x) for x in (d.get("acceptance_criteria") or [])
            ],
            priority=(d.get("priority") or None),
            route=(d.get("route") or None),
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
                "above and flag that criteria are generated, not inherited._"
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
    """Generate user stories for a company from a PRD or a free-form insight.

    Exactly one of `prd_id` / `insight` must be given. The call is bound to the
    `user-stories` skill and logged (agent="user_stories"). Returns a list of
    `Story`; this NEVER writes to ClickUp — that's app.stories.push.
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
    )
    raw = (result.output or {}).get("stories", []) if result.output else []
    stories = [Story.from_dict(s) for s in raw if s.get("title")]

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
