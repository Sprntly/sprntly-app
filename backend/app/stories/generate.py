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

Full skill fidelity (v2): every ticket carries the traceability chain
(`task → R# → acceptance test → PRD goal`) in spec-aware mode, `[ESCALATE]`
items from Part B become distinct DECISION tickets (kind="decision",
needs-human, with an owner and the build tickets they block), and the spec's
dependency order, `[P]` parallel markers, and walking skeleton survive into
the output.

This module ONLY generates. Pushing stories into a tracker (ClickUp or Jira)
is a separate, explicit step (see app.stories.push) so the user reviews
before anything is written.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.db.prds import get_prd_rendered
from app.graph.gateway import llm_call

logger = logging.getLogger(__name__)

PROMPT_VERSION = "user-stories-v2"

# Output contract for the gateway. Each story is dual-shaped: a human-readable
# title + body ("As a <role>, I want <goal>, so that <benefit>.") plus a list of
# Given/When/Then acceptance criteria, an optional priority, and the skill's
# routing tag. priority maps to ClickUp's 1-4 scale at push time.
# v2 adds the skill's full output spec: kind (build vs decision tickets from
# [ESCALATE]), the traceability chain, dependency order, [P] markers, the
# walking skeleton, and decision-ticket ownership/blocking.
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
                    "kind": {
                        "type": "string",
                        "enum": ["build", "decision"],
                        "description": (
                            "'build' for implementation stories; 'decision' "
                            "for tickets minted from a Part B [ESCALATE] item "
                            "(always needs-human)."
                        ),
                    },
                    "trace": {
                        "type": "string",
                        "description": (
                            "Traceability chain in spec-aware mode, e.g. "
                            "'T4 -> R5,R7 -> acceptance tests -> PRD goal: "
                            "measured outcome'. Empty in prose mode."
                        ),
                    },
                    "dependencies": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Titles of tickets in THIS batch that must land "
                            "first (from the spec's dependency order)."
                        ),
                    },
                    "parallel": {
                        "type": "boolean",
                        "description": (
                            "True when the spec marks the underlying task(s) "
                            "[P] — safe to build alongside its siblings."
                        ),
                    },
                    "walking_skeleton": {
                        "type": "boolean",
                        "description": (
                            "True for the one thinnest end-to-end slice to "
                            "build first."
                        ),
                    },
                    "owner": {
                        "type": "string",
                        "description": (
                            "Decision tickets only: who must make the call "
                            "(role or name from the spec's [ESCALATE] item)."
                        ),
                    },
                    "blocks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Decision tickets only: titles of build tickets "
                            "in this batch blocked until the decision lands."
                        ),
                    },
                    "criteria_generated": {
                        "type": "boolean",
                        "description": (
                            "True when acceptance criteria were GENERATED "
                            "from prose (no Part B) rather than inherited — "
                            "the skill's weaker-guarantee flag."
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
    "You are the User Stories agent. Apply the bound user-stories skill (the "
    "METHOD above) to break the given PRD or insight into tracker-ready "
    "tickets. Each ticket is dual-layer: a human-readable story plus "
    "machine-readable Given/When/Then acceptance criteria.\n\n"
    "If a machine-readable Part B (Implementation Spec) is provided:\n"
    "- INHERIT acceptance criteria verbatim from the spec's tests — never "
    "rewrite them (rewriting drifts from what the coding agent builds "
    "against). Set criteria_generated=false.\n"
    "- Regroup the spec's agent-shaped tasks into vertically-sliced, "
    "user-valued stories (one story may span several tasks).\n"
    "- Carry the traceability chain on every ticket in `trace`: "
    "task id(s) -> requirement id(s) (R#) -> acceptance test(s) -> the PRD "
    "goal the work serves.\n"
    "- Turn EVERY [ESCALATE] item into its own ticket with kind='decision': "
    "state the decision needed in the body, name the `owner` who must make "
    "the call, list the build tickets it `blocks`, and route it "
    "needs-human. Never fold an escalation into a build story.\n"
    "- Preserve the spec's dependency order in `dependencies` (ticket "
    "titles), mark [P]-flagged work parallel=true, and set "
    "walking_skeleton=true on exactly one thinnest end-to-end slice.\n\n"
    "With prose only (no Part B): GENERATE INVEST stories with "
    "Given/When/Then criteria including at least one edge or negative case, "
    "set criteria_generated=true, leave trace empty, and still order by "
    "dependency + value with one walking-skeleton story.\n\n"
    "Route every ticket: agent-ready (fully specified, stakes-reversible) "
    "vs needs-human (escalation or stakes-gated/irreversible work). Return "
    "only the structured stories."
)

# ClickUp priority is an int 1-4 (1=urgent ... 4=low). Map the skill's
# human-readable label to that scale for the push step.
_PRIORITY_TO_CLICKUP = {"urgent": 1, "high": 2, "normal": 3, "low": 4}


@dataclass
class Story:
    """One generated ticket (build story or decision ticket),
    tracker-agnostic until pushed."""

    title: str
    body: str
    acceptance_criteria: list[str] = field(default_factory=list)
    priority: Optional[str] = None
    route: Optional[str] = None
    kind: str = "build"
    trace: Optional[str] = None
    dependencies: list[str] = field(default_factory=list)
    parallel: bool = False
    walking_skeleton: bool = False
    owner: Optional[str] = None
    blocks: list[str] = field(default_factory=list)
    criteria_generated: bool = False

    def clickup_priority(self) -> Optional[int]:
        """ClickUp's 1-4 priority for this story, or None if unset/unknown."""
        if not self.priority:
            return None
        return _PRIORITY_TO_CLICKUP.get(self.priority.lower())

    def meta_lines(self) -> list[str]:
        """Trace/route/dependency footer lines, tracker-agnostic."""
        lines: list[str] = []
        if self.trace:
            lines.append(f"Trace: {self.trace}")
        elif self.criteria_generated:
            lines.append(
                "Criteria generated from prose (no Part B spec) — weaker "
                "guarantee than inherited criteria."
            )
        if self.route:
            lines.append(f"Route: {self.route}")
        if self.kind == "decision":
            if self.owner:
                lines.append(f"Decision owner: {self.owner}")
            if self.blocks:
                lines.append("Blocks: " + ", ".join(self.blocks))
        if self.dependencies:
            lines.append("Depends on: " + ", ".join(self.dependencies))
        if self.parallel:
            lines.append("[P] parallel-safe")
        if self.walking_skeleton:
            lines.append("Walking skeleton — build this slice first")
        return lines

    def to_description(self) -> str:
        """Render the dual-layer ticket as markdown (ClickUp task
        description). Used by the push step."""
        parts = [self.body.strip()]
        if self.acceptance_criteria:
            parts.append("")
            parts.append("**Acceptance criteria**")
            for ac in self.acceptance_criteria:
                parts.append(f"- {ac}")
        meta = self.meta_lines()
        if meta:
            parts.append("")
            for line in meta:
                parts.append(f"_{line}_")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "body": self.body,
            "acceptance_criteria": list(self.acceptance_criteria),
            "priority": self.priority,
            "route": self.route,
            "kind": self.kind,
            "trace": self.trace,
            "dependencies": list(self.dependencies),
            "parallel": self.parallel,
            "walking_skeleton": self.walking_skeleton,
            "owner": self.owner,
            "blocks": list(self.blocks),
            "criteria_generated": self.criteria_generated,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Story":
        kind = str(d.get("kind") or "build").strip().lower()
        if kind not in ("build", "decision"):
            kind = "build"
        route = d.get("route") or None
        # The skill is explicit: escalation/decision tickets go to a human,
        # never the coding agent — enforce it even if the model slips.
        if kind == "decision":
            route = "needs-human"
        return cls(
            title=str(d.get("title") or "").strip(),
            body=str(d.get("body") or "").strip(),
            acceptance_criteria=[
                str(x) for x in (d.get("acceptance_criteria") or [])
            ],
            priority=(d.get("priority") or None),
            route=route,
            kind=kind,
            trace=(str(d.get("trace")).strip() or None)
            if d.get("trace") else None,
            dependencies=[str(x) for x in (d.get("dependencies") or [])],
            parallel=bool(d.get("parallel")),
            walking_skeleton=bool(d.get("walking_skeleton")),
            owner=(d.get("owner") or None),
            blocks=[str(x) for x in (d.get("blocks") or [])],
            criteria_generated=bool(d.get("criteria_generated")),
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
    `Story` (build stories + decision tickets); this NEVER writes to a
    tracker — that's app.stories.push.
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
                    "decision_tickets": sum(
                        1 for s in stories if s.kind == "decision"
                    ),
                    "titles": [s.title for s in stories]},
            model=result.model,
            prompt_version=result.prompt_version,
        )
    except Exception:  # noqa: BLE001
        logger.exception("user_stories decision log write failed (continuing)")

    return stories
