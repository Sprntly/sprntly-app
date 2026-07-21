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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait as futures_wait
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.db.prds import get_prd_rendered
from app.graph.gateway import llm_call

logger = logging.getLogger(__name__)

PROMPT_VERSION = "user-stories-v4"
# The fan-out path decomposes then enriches in parallel; version its two legs
# distinctly so the decision log pins which method produced a given batch.
PLAN_PROMPT_VERSION = "user-stories-plan-v2"
ENRICH_PROMPT_VERSION = "user-stories-enrich-v2"

# Fan-out defaults. A batch is one enrich call; batches run concurrently up to
# max_parallel, itself bounded by the process-wide LLM concurrency gate
# (app.llm._llm_gate) — so raising TICKET_GEN_MAX_PARALLEL without also raising
# LLM_MAX_CONCURRENCY just makes batches queue on the gate. Kept small so a
# typical PRD (≈8-16 tickets) splits into 2-4 concurrent calls.
DEFAULT_BATCH_SIZE = 4
DEFAULT_MAX_PARALLEL = 4
# Fast first batch + prime-then-fanout (see _generate_fanout). A batch's
# latency is dominated by its OUTPUT tokens (~1K/ticket at model speed), so a
# 2-stub leading batch puts the first tickets on screen in roughly half a full
# batch's time. The stagger holds sibling batches until the first one's prompt
# cache write of the shared PRD prefix lands (a ~15K-token prefill takes
# 5-10s); launched simultaneously they ALL miss and each re-pays that prefill
# (measured live 2026-07-20: every shard cache_read=0). Siblings are larger and
# finish last anyway, so the stagger doesn't extend the total wall time.
DEFAULT_FIRST_BATCH_SIZE = 2
DEFAULT_PRIME_STAGGER_SECONDS = 12.0

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
                        "enum": ["build"],
                        "description": "Always 'build' — a deliverable ticket.",
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
    "Every ticket is a BUILD ticket (a deliverable) — do NOT emit decision or "
    "spike tickets. Preserve [NEED] markers verbatim in data_gaps — never invent "
    "numbers, owners, or criteria. Also mirror the user story into `body`. Return "
    "only the structured tickets."
)

# ── Fan-out: plan (decompose) then enrich (expand batches in parallel) ──
# Phase 1 asks the SAME bound skill to enumerate the full ticket set as
# lightweight stubs only (title + provenance anchor + one-line summary), never
# the heavy five-section body. Small output ⇒ fast. Phase 2 expands stubs in
# parallel batches into the full canonical ticket (_SCHEMA). Splitting the big
# 32k-token single generation this way turns most of the wall-clock (output
# tokens, which stream serially) into K concurrent shorter streams.
_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "part_b_detected": {
            "type": "boolean",
            "description": (
                "True when a machine-readable Part B (Implementation Spec) was "
                "provided; enrichment will inherit acceptance criteria from it."
            ),
        },
        "stubs": {
            "type": "array",
            "description": (
                "The COMPLETE set of BUILD tickets for this PRD — one or more per "
                "Part A §5 requirement row. Exhaustive: every §5 row is covered."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short ticket title (no key prefix). Unique within the set.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "One line naming the deliverable — enough to expand later.",
                    },
                    "prd_section": {
                        "type": "string",
                        "description": "Provenance anchor, e.g. 'Part A §5 R3' (empty if none).",
                    },
                    "ears_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Part B EARS ids this ticket traces to (e.g. ['E1']).",
                    },
                },
                "required": ["title"],
            },
        },
    },
    "required": ["stubs"],
}

_PLAN_SYSTEM = (
    "You are the Ticket planner. Apply the bound skill (the METHOD above) to "
    "decompose the given PRD (or insight) into the COMPLETE list of BUILD "
    "tickets — one or more per Part A §5 requirement row. Output ONLY a "
    "lightweight STUB for each: title, one-line summary, the provenance anchor "
    "(prd_section like 'Part A §5 R3'), and any Part B EARS ids it traces to. "
    "Do NOT write descriptions, acceptance criteria, scope, or subtasks yet — "
    "that happens in a later step. Be EXHAUSTIVE: every §5 requirement must be "
    "covered by at least one stub, and titles must be unique. Every ticket is a "
    "BUILD ticket. Return only the stubs."
)

_ENRICH_SYSTEM = (
    _SYSTEM
    + "\n\nSCOPE FOR THIS CALL: expand ONLY the tickets listed under 'Tickets to "
    "expand in THIS batch' into full canonical tickets. The COMPLETE roster of "
    "ticket titles across the whole PRD is given under 'Full ticket roster' so "
    "your blocked_by / blocks reference REAL sibling titles from that roster "
    "(never invent a dependency on a title not in the roster). Emit exactly one "
    "ticket per stub in this batch — do not add, drop, or merge tickets."
)

# ClickUp priority is an int 1-4 (1=urgent ... 4=low). Map the skill's
# human-readable label to that scale for the push step.
_PRIORITY_TO_CLICKUP = {"urgent": 1, "high": 2, "normal": 3, "low": 4}
# Jira's default named-priority scheme (Highest…Lowest). Projects can rename
# these, in which case create_issue omits an unknown one rather than 400.
_PRIORITY_TO_JIRA = {
    "urgent": "Highest", "high": "High", "normal": "Medium", "low": "Low",
}


def _clean_str_list(value: Any) -> list[str]:
    """Coerce a schema array into a list of trimmed non-empty strings."""
    if not isinstance(value, list):
        return []
    return [s for s in (str(x).strip() for x in value) if s]


def _stories_from_output(output: Any) -> list[Story]:
    """Parse the `stories` array from an LLM tool-call output into `Story`s,
    tolerating a non-conforming shape.

    Forced tool-use validates against the schema loosely — the model can still
    return `stories` as a non-list, or a list with stray string/None items
    (observed on some real PRDs). Iterating those into `Story.from_dict` blew up
    with `'str' object has no attribute 'get'`. Skip anything that isn't a
    titled dict (logging how many were dropped) so a malformed batch degrades to
    fewer tickets instead of failing the whole generation."""
    raw = (output or {}).get("stories", []) if isinstance(output, dict) else []
    if not isinstance(raw, list):
        logger.warning("ticket output.stories was %s, not a list — dropping",
                       type(raw).__name__)
        return []
    stories: list[Story] = []
    dropped = 0
    for s in raw:
        if isinstance(s, dict) and str(s.get("title") or "").strip():
            stories.append(Story.from_dict(s))
        else:
            dropped += 1
    if dropped:
        logger.warning("dropped %d malformed ticket item(s) from a model response",
                       dropped)
    return stories


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
    # Story-map placement (empty for a flat/unsized set)
    activity: str = ""
    release: str = ""
    # Decision-ticket fields
    decision: Optional[str] = None
    owner: Optional[str] = None
    decide_by: Optional[str] = None
    # Spike fields
    timebox: Optional[str] = None
    exit_condition: Optional[str] = None
    # The id stamped into the stored dict at generation time. When a persisted
    # story is rehydrated (from_dict) this pins stable_id() to the ORIGINAL id,
    # so applying edit overrides (changed title/body) never re-hashes it into a
    # different identity — tracker mappings and edit keys stay attached.
    pinned_id: Optional[str] = None
    # Push-time only: the Atlassian accountId to assign the issue to when pushing
    # to Jira (from the per-ticket assignee picker). NOT a generated property —
    # deliberately excluded from to_dict()/stable_id() so it never lands in the
    # ticket cache or shifts the content hash; the client sends it fresh on push.
    assignee_account_id: Optional[str] = None

    def stable_id(self) -> str:
        """A content-derived id (hash of title + body). Stable across list
        reordering and identical regenerations; a genuinely different story
        (changed title/body) hashes differently, so per-ticket edit overrides
        keyed off this id never misattach to the wrong ticket. A rehydrated
        story keeps its stored id (`pinned_id`) so edits don't change it."""
        if self.pinned_id:
            return self.pinned_id
        seed = f"{self.title}\x1f{self.body}".encode("utf-8")
        return hashlib.sha256(seed).hexdigest()[:12]

    def clickup_priority(self) -> Optional[int]:
        """ClickUp's 1-4 priority for this story, or None if unset/unknown."""
        if not self.priority:
            return None
        return _PRIORITY_TO_CLICKUP.get(self.priority.lower())

    def jira_priority(self) -> Optional[str]:
        """Jira's named priority for this story, or None if unset/unknown."""
        if not self.priority:
            return None
        return _PRIORITY_TO_JIRA.get(self.priority.lower())

    def to_description(self, *, include_subtasks: bool = True) -> str:
        """Render the ticket as a tracker task description (markdown). Uses the
        five-section body when present, falling back to the legacy story body.
        Used by the push step. `include_subtasks=False` drops the Child issues
        section — the Jira push uses it when the children are created as REAL
        sub-tasks (listing them twice would read as duplication)."""
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
        if self.subtasks and include_subtasks:
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
            "activity": self.activity,
            "release": self.release,
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
            pinned_id=(str(d.get("id")).strip() or None) if d.get("id") else None,
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
            activity=str(d.get("activity") or "").strip(),
            release=str(d.get("release") or "").strip(),
            decision=(d.get("decision") or None),
            owner=(d.get("owner") or None),
            decide_by=(d.get("decide_by") or None),
            timebox=(d.get("timebox") or None),
            exit_condition=(d.get("exit_condition") or None),
            assignee_account_id=(d.get("assignee_account_id") or None),
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


def _call_stat(label: str, result: Any) -> dict:
    """Per-call timing/token line for the benchmark, drawn from the LLMResult."""
    return {
        "label": label,
        "latency_ms": getattr(result, "latency_ms", 0),
        "input_tokens": getattr(result, "input_tokens", 0),
        "output_tokens": getattr(result, "output_tokens", 0),
        "cache_read_input_tokens": getattr(result, "cache_read_input_tokens", 0),
        "cost_usd": getattr(result, "cost_usd", 0.0),
        "model": getattr(result, "model", ""),
        "prompt_version": getattr(result, "prompt_version", ""),
        "stop_reason": getattr(result, "stop_reason", None),
    }


def _generate_single(
    enterprise_id: str,
    *,
    prd_input: str,
    purpose: str,
    model: Optional[str],
    stats_out: Optional[dict] = None,
) -> list[Story]:
    """Baseline: one big streamed call produces the whole ticket set."""
    t0 = time.monotonic()
    result = llm_call(
        enterprise_id=enterprise_id,
        agent="user_stories",
        purpose=purpose,
        prompt_version=PROMPT_VERSION,
        system=_SYSTEM,
        input=prd_input,
        json_schema=_SCHEMA,
        skill="user-stories",
        model=model,
        # Deterministic structured extraction — temperature 0 removes needless
        # variability (and the truncation-driven retries that empty the tool
        # call) for a schema-forced generation. Quality is set by the method +
        # schema, not sampling entropy.
        temperature=0,
        # Large structured output — stream on the long read timeout (was tripping
        # httpx.ReadTimeout on the default 120s non-streamed path). The canonical
        # ticket is big (five-section description, provenance, inherited AC,
        # subtasks, deps, story-map placement), so a real PRD's full set easily
        # exceeds the gateway's 16k default and the tool-call gets TRUNCATED —
        # which surfaces as an empty parse (0 tickets). Give it a generous budget.
        max_tokens=32000,
        long_output=True,
    )
    stories = _stories_from_output(result.output)
    if stats_out is not None:
        stats_out.update(
            strategy="single",
            wall_ms=int((time.monotonic() - t0) * 1000),
            n_stories=len(stories),
            calls=[_call_stat("generate", result)],
        )
    return stories


def _plan_tickets(
    enterprise_id: str,
    *,
    prd_input: str,
    purpose: str,
    model: Optional[str],
) -> tuple[list[dict], Any]:
    """Phase 1: enumerate the full ticket set as lightweight stubs (fast).

    The PRD rides `user_cacheable_prefix` (not `input`) so the ~5-15 KB document
    lands in a prompt-cache block instead of being re-processed as fresh input.
    Plan and enrich use different tools/system prompts so they never share an
    entry with each other — the sharing is among the ENRICH calls (see
    `_enrich_once`), which all send an identical prefix."""
    result = llm_call(
        enterprise_id=enterprise_id,
        agent="user_stories",
        purpose=f"{purpose}_plan",
        prompt_version=PLAN_PROMPT_VERSION,
        system=_PLAN_SYSTEM,
        input="Plan the full ticket set for the PRD above.",
        user_cacheable_prefix=prd_input,
        json_schema=_PLAN_SCHEMA,
        skill="user-stories",
        model=model,
        temperature=0,
        # Stubs are small; a normal budget is plenty and keeps this leg fast.
        max_tokens=8000,
    )
    stubs = (result.output or {}).get("stubs", []) if result.output else []
    clean = [
        s for s in stubs
        if isinstance(s, dict) and str(s.get("title") or "").strip()
    ]
    return clean, result


def _enrich_input(all_titles: list[str], batch: list[dict]) -> str:
    """Assemble the enrich-batch input tail: the complete title roster (for
    cross-ticket dependency linking) and the stubs to expand now. The PRD itself
    is NOT here — it goes in `user_cacheable_prefix` (see `_enrich_once`), so
    every batch shares one prompt-cached copy instead of re-sending it."""
    roster = "\n".join(f"- {t}" for t in all_titles)
    lines: list[str] = []
    for s in batch:
        anchor = str(s.get("prd_section") or "").strip()
        ears = ", ".join(str(e) for e in (s.get("ears_ids") or []))
        summary = str(s.get("summary") or "").strip()
        meta = " | ".join(x for x in (anchor, f"EARS: {ears}" if ears else "") if x)
        head = f"- {s.get('title')}"
        if summary:
            head += f" — {summary}"
        if meta:
            head += f"  ({meta})"
        lines.append(head)
    return (
        f"## Full ticket roster (for dependency linking; do not expand these here)\n"
        f"{roster}\n\n"
        f"## Tickets to expand in THIS batch\n"
        + "\n".join(lines)
    )


# Temperature for the enrich RETRY only. The first pass runs at 0 (deterministic
# structured extraction); a retry at 0 would return the identical output, so the
# retry samples at a small non-zero temperature to escape a malformed response.
_ENRICH_RETRY_TEMPERATURE = 0.4


def _enrich_once(
    enterprise_id: str,
    *,
    prd_input: str,
    all_titles: list[str],
    batch: list[dict],
    purpose: str,
    model: Optional[str],
    temperature: float,
) -> tuple[list[Story], Any]:
    """One enrich call over a batch of stubs → parsed tickets + the raw result.

    All batches (and the shortfall retry) send the PRD as an IDENTICAL
    `user_cacheable_prefix`, so any enrich call that starts after another's
    prefill completes — gate-queued shards, later batches, retries — cache-reads
    the PRD instead of re-processing it, cutting its time-to-first-token."""
    result = llm_call(
        enterprise_id=enterprise_id,
        agent="user_stories",
        purpose=f"{purpose}_enrich",
        prompt_version=ENRICH_PROMPT_VERSION,
        system=_ENRICH_SYSTEM,
        input=_enrich_input(all_titles, batch),
        user_cacheable_prefix=prd_input,
        json_schema=_SCHEMA,
        skill="user-stories",
        model=model,
        temperature=temperature,
        # A batch is a few tickets, not the whole PRD — a smaller budget than the
        # single path, still generous enough that a batch never truncates.
        max_tokens=16000,
        long_output=True,
    )
    return _stories_from_output(result.output), result


def _enrich_batch(
    enterprise_id: str,
    *,
    prd_input: str,
    all_titles: list[str],
    batch: list[dict],
    purpose: str,
    model: Optional[str],
) -> tuple[list[Story], Any]:
    """Phase 2 (one batch): expand a subset of stubs into full canonical tickets.

    Retries the batch ONCE when the first pass returns fewer tickets than it has
    stubs — the enrich contract is one ticket per stub, so a shortfall means the
    model returned malformed items that `_stories_from_output` dropped (the class
    of bug that failed a live generation). The retry samples at a non-zero
    temperature (a temperature-0 retry would just repeat the bad output) and we
    keep whichever attempt produced more tickets. Bounded to one retry so a
    persistently-short batch never loops; a batch that still yields nothing is
    backstopped by the whole-run single fallback in `_generate_fanout`. Each
    batch retries on its OWN worker thread, so a retry never blocks sibling
    batches — they keep generating in parallel."""
    expected = len(batch)
    stories, result = _enrich_once(
        enterprise_id, prd_input=prd_input, all_titles=all_titles, batch=batch,
        purpose=purpose, model=model, temperature=0,
    )
    if len(stories) >= expected:
        return stories, result

    logger.warning(
        "enrich batch returned %d/%d tickets — retrying once with a fresh sample",
        len(stories), expected,
    )
    retry_stories, retry_result = _enrich_once(
        enterprise_id, prd_input=prd_input, all_titles=all_titles, batch=batch,
        purpose=purpose, model=model, temperature=_ENRICH_RETRY_TEMPERATURE,
    )
    # Keep the better attempt (more tickets); a tie keeps the first (temp-0) run.
    if len(retry_stories) > len(stories):
        return retry_stories, retry_result
    return stories, result


def _generate_fanout(
    enterprise_id: str,
    *,
    prd_input: str,
    purpose: str,
    model: Optional[str],
    batch_size: int,
    max_parallel: int,
    first_batch_size: int = DEFAULT_FIRST_BATCH_SIZE,
    prime_stagger_s: float = DEFAULT_PRIME_STAGGER_SECONDS,
    stats_out: Optional[dict] = None,
    on_batch: Optional[Callable[[list[Story], int, int], None]] = None,
) -> list[Story]:
    """Fan-out: plan the ticket set, then expand batches in parallel.

    Falls back to the single path on an empty plan so we never regress to zero
    tickets (a real PRD always yields some). Dependency links stay correct
    because every batch is given the full title roster.

    `first_batch_size` carves a small LEADING batch (0 disables) so the first
    tickets land early and the UI streams visibly batch-by-batch;
    `prime_stagger_s` delays the sibling batches behind the first by up to that
    many seconds (or until the first completes, whichever is sooner) so the
    first batch's prompt-cache write of the shared PRD prefix is readable by
    every sibling instead of all of them racing to a cache miss.

    `on_batch(stories_so_far, done, total)` — when given, fires once per enrich
    batch as it completes (on THIS orchestrating thread, in the as_completed
    loop — never from the enrich sub-threads), carrying the deduped tickets
    accumulated so far. Lets the caller stream partial results to the UI instead
    of blocking on the whole set. Exceptions from the callback are swallowed so
    a display hiccup never breaks generation.
    """
    t0 = time.monotonic()
    stubs, plan_result = _plan_tickets(
        enterprise_id, prd_input=prd_input, purpose=purpose, model=model
    )
    if not stubs:
        logger.warning("fan-out plan returned 0 stubs — falling back to single call")
        return _generate_single(
            enterprise_id, prd_input=prd_input, purpose=purpose, model=model,
            stats_out=stats_out,
        )

    bs = max(1, batch_size)
    # Carve the fast first batch only when it actually splits work off (a plan
    # already at/below the first-batch size just runs as one batch).
    fb = min(max(0, first_batch_size), bs)
    if fb and len(stubs) > fb:
        rest = stubs[fb:]
        batches = [stubs[:fb]] + [rest[i : i + bs] for i in range(0, len(rest), bs)]
    else:
        batches = [stubs[i : i + bs] for i in range(0, len(stubs), bs)]
    all_titles = [str(s.get("title")).strip() for s in stubs]
    total = len(batches)

    enriched: list[tuple[list[Story], Any]] = []
    # Dedup by content id (stable_id) as batches land — batches are disjoint by
    # design, but a stub restated across batches would otherwise double-count.
    seen: set[str] = set()
    stories: list[Story] = []
    workers = max(1, min(max_parallel, total))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        def _submit(b: list[dict]):
            return ex.submit(
                _enrich_batch,
                enterprise_id,
                prd_input=prd_input,
                all_titles=all_titles,
                batch=b,
                purpose=purpose,
                model=model,
            )

        # PRIME-THEN-FANOUT: the first batch goes out alone; siblings follow
        # after `prime_stagger_s` (or the moment the first batch completes —
        # futures_wait returns early on a done future, so a tiny/fast first
        # batch never over-waits). By then the first call's prefill has written
        # the shared PRD prefix to the prompt cache, so every sibling
        # cache-reads it instead of re-processing ~15K tokens. A first batch
        # that FAILS still unblocks here (a failed future counts as done); its
        # exception surfaces in the as_completed loop below, unchanged.
        futs = [_submit(batches[0])]
        if len(batches) > 1:
            if prime_stagger_s > 0:
                futures_wait(futs, timeout=prime_stagger_s)
            futs += [_submit(b) for b in batches[1:]]
        done = 0
        for f in as_completed(futs):
            batch_stories, _ = result = f.result()
            enriched.append(result)
            for s in batch_stories:
                key = s.stable_id()
                if key in seen:
                    continue
                seen.add(key)
                stories.append(s)
            done += 1
            if on_batch is not None:
                try:
                    on_batch(list(stories), done, total)
                except Exception:  # noqa: BLE001 — a display hiccup never breaks gen
                    logger.exception("ticket on_batch callback failed (continuing)")

    # Safety net: if every enrich batch came back empty/malformed (0 tickets from
    # a real PRD that DID plan stubs), don't hand back an empty set — fall back to
    # the single call, which reliably returns objects. Never caches empty upstream.
    if not stories:
        logger.warning(
            "fan-out enrich produced 0 tickets across %d batch(es) — falling back "
            "to single call", len(batches),
        )
        return _generate_single(
            enterprise_id, prd_input=prd_input, purpose=purpose, model=model,
            stats_out=stats_out,
        )

    if stats_out is not None:
        stats_out.update(
            strategy="fanout",
            wall_ms=int((time.monotonic() - t0) * 1000),
            n_stubs=len(stubs),
            n_batches=len(batches),
            batch_size=bs,
            first_batch_size=fb,
            prime_stagger_s=prime_stagger_s,
            max_parallel=workers,
            n_stories=len(stories),
            calls=(
                [_call_stat("plan", plan_result)]
                + [_call_stat(f"enrich[{i}]", r) for i, (_, r) in enumerate(enriched)]
            ),
        )
    return stories


def generate_from_input(
    enterprise_id: str,
    *,
    prd_input: str,
    purpose: str = "from_prd",
    model: Optional[str] = None,
    strategy: str = "single",
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_parallel: int = DEFAULT_MAX_PARALLEL,
    first_batch_size: int = DEFAULT_FIRST_BATCH_SIZE,
    prime_stagger_s: float = DEFAULT_PRIME_STAGGER_SECONDS,
    stats_out: Optional[dict] = None,
    on_batch: Optional[Callable[[list[Story], int, int], None]] = None,
) -> list[Story]:
    """Generate tickets from an already-assembled model input string.

    The strategy-dispatch core shared by the DB-backed `generate_user_stories`
    and the benchmark harness (which feeds a PRD markdown fixture directly, no
    DB). `strategy` is "single" (one big call, the baseline) or "fanout" (plan →
    parallel enrich). `on_batch` streams partial results (fanout only). Never
    persists — callers own persistence.
    """
    if strategy == "fanout":
        return _generate_fanout(
            enterprise_id, prd_input=prd_input, purpose=purpose, model=model,
            batch_size=batch_size, max_parallel=max_parallel,
            first_batch_size=first_batch_size, prime_stagger_s=prime_stagger_s,
            stats_out=stats_out, on_batch=on_batch,
        )
    return _generate_single(
        enterprise_id, prd_input=prd_input, purpose=purpose, model=model,
        stats_out=stats_out,
    )


def generate_user_stories(
    enterprise_id: str,
    *,
    prd_id: Optional[int] = None,
    insight: Optional[str] = None,
    model: Optional[str] = None,
    strategy: str = "single",
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_parallel: int = DEFAULT_MAX_PARALLEL,
    first_batch_size: int = DEFAULT_FIRST_BATCH_SIZE,
    prime_stagger_s: float = DEFAULT_PRIME_STAGGER_SECONDS,
    stats_out: Optional[dict] = None,
    on_batch: Optional[Callable[[list[Story], int, int], None]] = None,
) -> list[Story]:
    """Generate tickets for a company from a PRD or a free-form insight.

    Exactly one of `prd_id` / `insight` must be given. The call is bound to the
    ticket skill and logged (agent="user_stories"). Returns a list of
    `Story`; this NEVER writes to a tracker — that's app.stories.push.

    `strategy` selects the generation path: "single" (baseline, one big call) or
    "fanout" (decompose then enrich batches in parallel). Output contract is
    identical; only latency differs. `on_batch` (fanout only) streams partial
    tickets as each batch completes.
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

    stats: dict = {} if stats_out is None else stats_out
    stories = generate_from_input(
        enterprise_id,
        prd_input=_build_input(prd=prd, insight=insight),
        purpose=purpose,
        model=model,
        strategy=strategy,
        batch_size=batch_size,
        max_parallel=max_parallel,
        first_batch_size=first_batch_size,
        prime_stagger_s=prime_stagger_s,
        stats_out=stats,
        on_batch=on_batch,
    )
    # Resolved model / prompt-version for the decision log come from the last
    # underlying call (both paths populate `stats["calls"]`).
    _calls = stats.get("calls") or [{}]
    _last = _calls[-1]

    # Persist the generated set for a PRD so the Tickets tab can serve it without
    # re-running this multi-minute call until the PRD content actually changes.
    # Keyed by a content hash of the rendered PRD (see app.db.prd_tickets). Never
    # let a persistence write break generation — the stories are still returned.
    #
    # NEVER cache an EMPTY result: a real PRD always yields tickets, so 0 means a
    # transient failure (a truncated/empty tool-call). Persisting it as `ready`
    # would stick the tab on "0 tickets" forever; skipping the write leaves the
    # cache absent so the next open retries.
    if prd_id is not None and prd is not None and stories:
        try:
            from app.db.prd_tickets import hash_prd_row, save_tickets

            # Hash the row AS IT STANDS NOW, not the pre-call snapshot: the
            # impl-spec pre-warm kicked off with this job fills `llm_part`
            # while the (multi-minute) ticket call runs, so the snapshot's
            # hash never matches a later read and every panel open spuriously
            # regenerated ("The PRD changed" with no actual edit). If the PRD
            # BODY (title / Part A) genuinely changed mid-run, keep the
            # snapshot hash — the set is truly stale and must regenerate.
            current = get_prd_rendered(prd_id)
            body_changed = current is None or (
                (current.get("title"), current.get("payload_md"))
                != (prd.get("title"), prd.get("payload_md"))
            )
            save_tickets(
                enterprise_id,
                prd_id,
                hash_prd_row(prd if body_changed or current is None else current),
                [s.to_dict() for s in stories],
            )
        except Exception:  # noqa: BLE001
            logger.exception("persisting prd_tickets failed (continuing)")
    elif prd_id is not None and not stories:
        logger.warning(
            "ticket generation returned 0 tickets for prd_id=%s — not caching so "
            "the next open retries", prd_id,
        )

    # Record the semantic decision (what was produced) alongside the gateway's
    # own llm_call telemetry row. Never let an audit-write break generation.
    try:
        from app.graph.decision_log import log_agent_decision

        log_agent_decision(
            enterprise_id=enterprise_id,
            agent="user_stories",
            decision_type="generate_user_stories",
            factors={"prd_id": prd_id, "from_insight": insight is not None,
                     "strategy": stats.get("strategy", strategy)},
            output={"count": len(stories),
                    "titles": [s.title for s in stories]},
            model=_last.get("model", model),
            prompt_version=_last.get("prompt_version", PROMPT_VERSION),
        )
    except Exception:  # noqa: BLE001
        logger.exception("user_stories decision log write failed (continuing)")

    return stories
