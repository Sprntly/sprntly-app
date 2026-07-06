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

import hashlib
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
                    "activity": {
                        "type": "string",
                        "description": (
                            "Story-map backbone: the user activity/step this "
                            "ticket serves (verbatim one of story_map.activities). "
                            "Empty when the map isn't built."
                        ),
                    },
                    "release": {
                        "type": "string",
                        "description": (
                            "Story-map slice: the release this ticket belongs to "
                            "(verbatim one of story_map.releases[].name). Empty "
                            "when the map isn't built."
                        ),
                    },
                },
                "required": ["title", "body", "acceptance_criteria"],
            },
        },
        "story_map": {
            "type": "object",
            "description": (
                "Jeff Patton story map over the SAME tickets — the backbone of "
                "user activities (Part A §4) with tickets placed under each and "
                "sliced into releases. Populate `activities`/`releases`/`gaps` "
                "when the feature is large; always report `sizing` honestly. The "
                "backend decides whether the map is built from `sizing` — never "
                "invent tickets to fill the map."
            ),
            "properties": {
                "sizing": {
                    "type": "object",
                    "description": "Signals the backend scores to size the feature.",
                    "properties": {
                        "activities_count": {
                            "type": "integer",
                            "description": "Distinct user activities in Part A §4.",
                        },
                        "requirements_count": {
                            "type": "integer",
                            "description": "Requirement rows in Part A §5.",
                        },
                        "releases_count": {
                            "type": "integer",
                            "description": "Distinct releases/sprints in the rollout.",
                        },
                        "phased_rollout": {
                            "type": "boolean",
                            "description": "PRD/spec names a phased rollout.",
                        },
                        "cross_team": {
                            "type": "boolean",
                            "description": "Delivery spans more than one team.",
                        },
                    },
                },
                "activities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Backbone: the user's activities left-to-right in "
                        "narrative journey order (NOT a feature list)."
                    ),
                },
                "releases": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "note": {
                                "type": "string",
                                "description": "e.g. 'walking skeleton — end-to-end'.",
                            },
                            "walking_skeleton": {
                                "type": "boolean",
                                "description": "True for Release 1 (crosses the whole journey).",
                            },
                        },
                        "required": ["name"],
                    },
                    "description": "Release slices, earliest first; R1 = walking skeleton.",
                },
                "gaps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "activity": {"type": "string"},
                            "release": {"type": "string"},
                            "note": {
                                "type": "string",
                                "description": "[edge]/[NEED] gap fed back to the PRD, not a ticket.",
                            },
                        },
                        "required": ["note"],
                    },
                    "description": "Missing steps / error paths — noted, never silently ticketed.",
                },
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
    "one edge or negative case. "
    "ALSO build the story map over the SAME tickets: report `story_map.sizing` "
    "honestly (count the Part A §4 activities, §5 requirements, releases in the "
    "rollout; flag phased rollout / cross-team). Lay out `story_map.activities` "
    "as the user's narrative journey (backbone, left-to-right — NOT a feature "
    "list) and `story_map.releases` as coherent end-to-end slices with Release 1 "
    "as the walking skeleton (walking_skeleton=true). Assign every ticket an "
    "`activity` (one of the backbone activities) and a `release` (one of the "
    "release names). Note missing steps / error paths in `story_map.gaps` — never "
    "invent extra tickets to fill the map. The backend decides from `sizing` "
    "whether the map is shown, so fill activities/releases whenever the feature is "
    "non-trivial. Return only the structured result."
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
    # Story-map placement: which backbone activity this ticket serves and which
    # release slice it lands in. Populated only when the map is built; both empty
    # for a flat (unsized) ticket set. They ride in the stories JSON, so the map
    # is reconstructable per-ticket without a separate lookup.
    activity: Optional[str] = None
    release: Optional[str] = None

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
            "id": self.stable_id(),
            "title": self.title,
            "body": self.body,
            "acceptance_criteria": list(self.acceptance_criteria),
            "priority": self.priority,
            "route": self.route,
            "activity": self.activity,
            "release": self.release,
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
            activity=(d.get("activity") or None),
            release=(d.get("release") or None),
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


# The sizing gate (from the user-stories skill): a story map is built when at
# least this many signals fire. Kept in code — deterministic and auditable — so
# the "map or not" call never depends on the model self-declaring; the model only
# supplies the raw counts/flags it extracts from the PRD.
_STORY_MAP_MIN_SIGNALS = 2


def _score_sizing(sizing: dict[str, Any]) -> tuple[dict[str, Any], bool, str]:
    """Score the five sizing signals into (signals, built, summary).

    `sizing` is the model's raw extraction ({activities_count, requirements_count,
    releases_count, phased_rollout, cross_team}). We apply the ≥2-signal threshold
    here so the decision is reproducible from the stored counts.
    """
    activities = int(sizing.get("activities_count") or 0)
    requirements = int(sizing.get("requirements_count") or 0)
    releases = int(sizing.get("releases_count") or 0)
    phased = bool(sizing.get("phased_rollout"))
    cross_team = bool(sizing.get("cross_team"))
    count = (
        int(activities > 1)
        + int(requirements > 12)
        + int(releases > 1)
        + int(phased)
        + int(cross_team)
    )
    built = count >= _STORY_MAP_MIN_SIGNALS
    signals = {
        "activities": activities,
        "requirements": requirements,
        "releases": releases,
        "phased_rollout": phased,
        "cross_team": cross_team,
        "count": count,
    }
    verdict = "built" if built else "not needed — sized flat"
    summary = (
        f"Story map: {verdict} — {activities} user "
        f"{'activity' if activities == 1 else 'activities'} · {requirements} "
        f"requirements · {releases} release{'' if releases == 1 else 's'} "
        f"(sizing gate: {count} of 5 signals)"
    )
    return signals, built, summary


def _build_story_map(raw_map: dict[str, Any]) -> dict[str, Any]:
    """Turn the model's raw `story_map` block into the persisted payload.

    Always carries `built` + `summary` + `signals`; the backbone/releases/gaps are
    included only when the sizing gate fires, so the flat branch stays lean and the
    Tickets tab only ever offers the Map toggle when there's a real map.
    """
    signals, built, summary = _score_sizing(raw_map.get("sizing") or {})
    payload: dict[str, Any] = {"built": built, "summary": summary, "signals": signals}
    if not built:
        return payload

    payload["activities"] = [
        str(a).strip() for a in (raw_map.get("activities") or []) if str(a).strip()
    ]
    releases = []
    for r in raw_map.get("releases") or []:
        name = str((r or {}).get("name") or "").strip()
        if not name:
            continue
        releases.append({
            "name": name,
            "note": str(r.get("note") or "").strip(),
            "walking_skeleton": bool(r.get("walking_skeleton")),
        })
    payload["releases"] = releases
    gaps = []
    for g in raw_map.get("gaps") or []:
        note = str((g or {}).get("note") or "").strip()
        if not note:
            continue
        gaps.append({
            "activity": str(g.get("activity") or "").strip(),
            "release": str(g.get("release") or "").strip(),
            "note": note,
        })
    payload["gaps"] = gaps
    return payload


@dataclass
class GenerationResult:
    """A generation run's output: the flat ticket set plus the story map that
    organizes it. `story_map` is None only when the model returned no sizing
    block at all (older prompts / degraded runs)."""

    stories: list[Story] = field(default_factory=list)
    story_map: Optional[dict[str, Any]] = None


def generate_tickets(
    enterprise_id: str,
    *,
    prd_id: Optional[int] = None,
    insight: Optional[str] = None,
    model: Optional[str] = None,
) -> GenerationResult:
    """Generate the ticket set + story map for a company from a PRD or insight.

    Exactly one of `prd_id` / `insight` must be given. Bound to the `user-stories`
    skill and logged (agent="user_stories"). NEVER writes to ClickUp — that's
    app.stories.push. `generate_user_stories` wraps this for callers that only
    want the flat list.
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
    output = result.output or {}
    raw = output.get("stories", []) if output else []
    stories = [Story.from_dict(s) for s in raw if s.get("title")]
    raw_map = output.get("story_map")
    story_map = _build_story_map(raw_map) if isinstance(raw_map, dict) else None
    # A story map with no real slices (e.g. the model built activities but placed
    # nothing) is worse than none — drop the map so the tab doesn't offer an empty
    # board. The flat tickets are unaffected.
    if story_map and story_map.get("built") and not story_map.get("releases"):
        story_map["built"] = False

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
                story_map=story_map,
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
            factors={
                "prd_id": prd_id,
                "from_insight": insight is not None,
                "story_map_built": bool(story_map and story_map.get("built")),
            },
            output={"count": len(stories),
                    "titles": [s.title for s in stories]},
            model=result.model,
            prompt_version=result.prompt_version,
        )
    except Exception:  # noqa: BLE001
        logger.exception("user_stories decision log write failed (continuing)")

    return GenerationResult(stories=stories, story_map=story_map)


def generate_user_stories(
    enterprise_id: str,
    *,
    prd_id: Optional[int] = None,
    insight: Optional[str] = None,
    model: Optional[str] = None,
) -> list[Story]:
    """Back-compat wrapper: the flat ticket list only (drops the story map).

    Kept for callers that don't render the map (the synthesis orchestrator's
    markdown roll-up). New surfaces that need the map call `generate_tickets`.
    """
    return generate_tickets(
        enterprise_id, prd_id=prd_id, insight=insight, model=model
    ).stories
