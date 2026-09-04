"""Project memory summary synthesis — the bounded, best-effort LLM writer
that regenerates the cached `project_memory_summary` row from a project's
discrete `project_memory_entries`.

Contract (AD-P7 — copied byte-for-byte in SHAPE from `app/artifact_summary.py`,
the project's first synthesis writer):
  - `regenerate_summary` never raises. Any LLM or DB failure returns None and
    leaves the pre-existing summary row (if any) untouched — the last-good
    summary stands. A synthesis failure must never block a chat turn or a
    memory edit.
  - Model tier is the default (`DEFAULT_MODEL`, not a haiku tier): the summary
    is the one line of record the team reads instead of the raw entry list.
  - Content beyond `_CONTENT_MAX_CHARS` is clipped before the call — same
    posture as `artifact_summary._CONTENT_MAX_CHARS`.

`schedule_regen(project_id)` is the ONE shared fire-and-forget trigger every
memory-mutation path uses to fire this off the request/turn path. It is
exposed here (not inlined in `routes/projects.py`) because entries can be
written OUTSIDE the HTTP route handlers too — agent-promoted memory and the
individual-chat memory hook both write directly to `project_memory_entries`
and need the exact same "flip stale, then eventually regenerate" trigger the
HTTP handlers use, or their entries would flip `stale` and never regenerate.
Mirrors the repo's established `asyncio.create_task` + pytest-inline pattern
(`app/routes/ask.py`, `app/routes/prd.py`) and the dedicated-testable-seam
idiom (`app/routes/business_context.py::_run_inline_for_tests`) rather than
an inline `"pytest" in sys.modules` check, so a test can monkeypatch the seam
to exercise the real fire-and-forget branch without touching the actual
`sys.modules` registry.
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
import time

from app.db import project_memory_entries as memory_db
from app.db.client import require_client, utc_now
from app.llm import DEFAULT_MODEL, call_json, call_md
from app.llm_telemetry import RunUsage, log_llm_run

logger = logging.getLogger(__name__)

# Same posture as `app.artifact_summary._CONTENT_MAX_CHARS`: bounds spend on a
# project whose memory trail has grown large, and keeps the prompt within a
# sane window for a 3-5 sentence synthesis.
_CONTENT_MAX_CHARS = 24_000

_SYSTEM = """You write the "what this project knows" summary for a product \
team's shared project memory. Below are discrete notes the team has \
recorded over time — decisions, guardrails, facts, open questions — each \
one a separate entry in its own voice. Synthesize them into ONE interpretive \
account of what the project currently knows; a teammate who has never seen \
the raw entries should come away knowing the guardrails and insights that \
actually matter.

Write 3-5 sentences of plain prose. Name the guardrails and insights the \
entries actually hold — what's decided, what's constrained, what's still \
open — not a restatement of each entry in order or a table of contents.

Hard rules:
- No markdown headings, no bullet lists. Plain sentences (bold is fine).
- Do NOT end with a question or an offer of next steps ("Want me to…", \
"Shall I…", "Let me know…"). State what the project knows; stop.
- Do not address the reader with pleasantries; open directly with substance.
"""


def _clip(text: str) -> str:
    return text if len(text) <= _CONTENT_MAX_CHARS else text[:_CONTENT_MAX_CHARS]


def _render_entries(entries: list[dict]) -> str:
    """Concatenate entry bodies, most-recently-updated first (matches
    `list_entries`'s own order), one per line — the SOURCE material fed to
    the model, clipped to the content cap."""
    lines = [f"- {entry['body']}" for entry in entries]
    return _clip("\n".join(lines))


def _usage_from_meta(meta: dict) -> RunUsage:
    return RunUsage(
        cache_creation_input_tokens=meta.get("cache_creation_input_tokens", 0),
        cache_read_input_tokens=meta.get("cache_read_input_tokens", 0),
        input_tokens=meta.get("input_tokens", 0),
        output_tokens=meta.get("output_tokens", 0),
    )


def _log_run(*, project_id: int, meta: dict, start: float, status: str,
             error_class: str | None = None) -> None:
    """Emit the one structured cost-summary line for a regen attempt. Never
    raises (wraps `log_llm_run`, which fails closed on an unpriced model) —
    a logging hiccup must never be the reason `regenerate_summary` raises."""
    try:
        log_llm_run(
            operation="projects.memory.synthesis",
            identifier={"project_id": project_id},
            usage=_usage_from_meta(meta),
            duration_ms=int((time.monotonic() - start) * 1000),
            status=status,
            model=meta.get("model") or DEFAULT_MODEL,
            error_class=error_class,
        )
    except Exception:  # noqa: BLE001 — observability must never break the writer
        logger.warning("memory_summary_cost_log_failed project_id=%s", project_id)


def regenerate_summary(project_id: int) -> str | None:
    """Regenerate the cached `project_memory_summary` row from the
    project's CURRENT `project_memory_entries`. Never raises.

    Zero entries: makes NO LLM call, deletes any existing summary row (so
    `get_summary` falls back to its own computed `{summary_md: None,
    entry_count: 0}` reply instead of serving a stale summary of removed
    entries), and returns None — no cost-log line either, since no LLM call
    was attempted.

    >=1 entry: one bounded `call_md` call, then an upsert of exactly one row
    keyed on `project_id`. On ANY failure (LLM or DB) the pre-existing row is
    left byte-identical and this returns None — the last-good summary
    stands (AD-P7).
    """
    entries = memory_db.list_entries(project_id)
    if not entries:
        try:
            require_client().table("project_memory_summary").delete().eq(
                "project_id", project_id
            ).execute()
        except Exception:  # noqa: BLE001 — best-effort, AD-P7
            logger.warning("memory_summary_delete_failed project_id=%s", project_id)
        return None

    start = time.monotonic()
    meta: dict = {}
    try:
        summary = call_md(
            system=_SYSTEM,
            user=_render_entries(entries),
            model=DEFAULT_MODEL,
            meta_out=meta,
        )
        summary = summary.strip() if isinstance(summary, str) else ""
        if not summary:
            raise ValueError("empty synthesis output")
        require_client().table("project_memory_summary").upsert(
            {
                "project_id": project_id,
                "summary_md": summary,
                "entry_count": len(entries),
                "generated_at": utc_now(),
                "stale": False,
            },
            on_conflict="project_id",
        ).execute()
    except Exception as exc:  # noqa: BLE001 — best-effort, AD-P7: last-good stands
        logger.warning(
            "memory_summary_synthesis_failed project_id=%s error=%s",
            project_id, type(exc).__name__,
        )
        _log_run(
            project_id=project_id, meta=meta, start=start,
            status="error", error_class=type(exc).__name__,
        )
        return None

    _log_run(project_id=project_id, meta=meta, start=start, status="complete")
    return summary


# De-dup: at most one live regen task per project. Keyed on project_id so a
# burst of mutations across DIFFERENT projects still regenerates each one;
# only a burst for the SAME project collapses.
_inflight: dict[int, asyncio.Task] = {}


def _run_inline_for_tests() -> bool:
    """The TestClient does not keep the app's event loop alive between
    requests, so a fire-and-forget `create_task` would never actually run
    before the test asserts against it — mirrors `routes/ask.py`'s and
    `routes/business_context.py::_run_inline_for_tests`'s identical
    test-mode handling. A function (not an inline `"pytest" in sys.modules`
    check) so a test can monkeypatch it to exercise the real fire-and-forget
    branch without touching the actual `sys.modules` registry."""
    return "pytest" in sys.modules


def schedule_regen(project_id: int) -> None:
    """Fire-and-forget regen off the request/turn path. Callable from ANY
    context — a route handler, agent-promotion, or a bare sync path.

    Under pytest: runs `regenerate_summary` inline so tests are
    deterministic (the TestClient keeps no loop alive between requests).

    In prod: schedules on the running loop and de-dups so a burst of
    mutations collapses to at most one in-flight regen per project — a
    concurrent schedule while one is already running/queued for the SAME
    project is dropped (the running regen already reads the latest entries,
    so the drop loses no information; last-writer-wins, eventually
    consistent). No running loop (a bare sync context) → run inline as a
    last resort.

    Never raises.
    """
    if _run_inline_for_tests():
        regenerate_summary(project_id)
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        regenerate_summary(project_id)
        return
    if project_id in _inflight and not _inflight[project_id].done():
        return  # a regen is already queued/running for this project
    task = loop.create_task(asyncio.to_thread(regenerate_summary, project_id))
    _inflight[project_id] = task
    task.add_done_callback(lambda t: _inflight.pop(project_id, None))


# ── Agent memory promotion (AD-P7 best-effort, AD-P3 provenance) ───────────
#
# After the project agent replies (`routes/projects.py::_respond_as_group_agent`),
# `maybe_promote_turn` runs ONE bounded classifier call over the clamped
# transcript the caller already built PLUS the project's existing memory
# entries, and decides whether the exchange holds a durable, project-relevant
# insight worth persisting — and, if so, whether it is genuinely new or a
# semantic near-duplicate/revision of something already recorded. A
# promotion failure — classifier or DB — must never affect the chat turn
# that triggered it, which already persisted before this runs; every path
# below returns None rather than raising.
#
# v1 dedup was case-insensitive EXACT-STRING match against existing entry
# bodies (`_is_duplicate_insight`, since removed). That bar missed
# near-duplicates by construction: a trivial follow-up turn ("thanks!")
# about a fact still inside the classifier's OWN clamped transcript window
# got re-summarized in slightly different words and re-promoted as a new
# row, because the classifier never saw what memory already held. This
# feeds the existing entries into the SAME call the classifier already
# makes (still one LLM call) so the model can compare candidate-vs-memory
# by MEANING, not by string equality.

_PROMOTE_SYSTEM = """You read a short excerpt of a project team's project \
chat — including Sprntly's own last reply — plus the project's EXISTING \
memory entries recorded so far. Decide whether the exchange holds a \
DURABLE, project-relevant insight worth remembering for teammates who were \
not in this conversation, and if so, how it relates to what is already \
recorded.

Promote ONLY a decision, guardrail, constraint, or durable fact the team \
has actually settled on. Never promote small talk, greetings, \
acknowledgements ("thanks", "sounds good", "got it"), plain questions with \
no answer yet, or ephemeral status updates ("still looking into it", \
"will check tomorrow") — for these, set action to "none".

A DECISION the team reaches in this excerpt is a FIRST-CLASS durable \
insight — capture it. A choice made, a direction set, a tradeoff resolved, \
a plan agreed ("we'll go with X", "let's use Y instead of Z", "decided to \
ship without W", "the call is to...") all count, whether a teammate stated \
it or Sprntly confirmed it in its own reply. Fold the decision into project \
memory as a "new" (or "update") entry like any other insight — there is NO \
separate decisions log. Write `body` as a DISTILLED one-to-two sentence \
statement of WHAT was decided and, briefly, why; never copy the deciding \
line verbatim, and never include personal or off-topic detail from the \
conversation — a distilled decision keeps teammates informed without \
flooding the shared memory.

Beyond key decisions, promote a durable insight from ANY of these \
categories when the excerpt genuinely holds one — never invent content to \
fill a category that isn't actually there:
- key decisions — see above; always first-class.
- why / goal / origin — the reason the project exists, or a goal the team \
states for it.
- status — where something has gotten to ("shipped to staging", "blocked \
on design review", "in progress").
- what's been done — concrete work a teammate reports as completed.
- open questions — something the team has explicitly flagged as unresolved.
- assignments — who owns what, INCLUDING the reader's own "my items" \
("I'm taking the API work", "assign the migration to Ada").
- user-supplied richness / constraints — a preference, detail, or \
constraint a teammate states by hand that should shape how the work goes.
Each of these follows the SAME "new"/"update"/"duplicate" dispatch as a \
decision — there is no separate store per category, only the flat entry \
list. A category with nothing genuinely present in the excerpt simply \
contributes nothing; do not manufacture a status or an assignment to check \
a box.

A request or instruction is NOT automatically a settled assignment or \
decision. Promote an assignment or decision only when the excerpt confirms \
it is settled — actually happened, agreed, or in effect — not merely asked \
for. A bare request or instruction ("assign the auth ticket to David", \
"have Ada look into X", "have Z do W") whose fulfilment the excerpt does \
NOT confirm is NOT a durable fact — set action to "none" for these — \
ESPECIALLY when Sprntly's own reply indicates the action did not or could \
not happen (the ticket, person, or artifact named doesn't exist, or the \
request was declined). "David asked to assign the ticket" or "please \
assign it to David" is a DIFFERENT fact from "the ticket is assigned to \
David" — only promote the latter, and only when the excerpt itself \
confirms it happened. This exclusion is narrow: it targets requests the \
excerpt does not confirm or that Sprntly's reply refutes, never imperative \
phrasing itself — a plainly stated, confirmed assignment or decision \
("we'll ship without SSO", "assign the migration to Ada" when the reply \
confirms it) still promotes exactly as the categories above describe.

Sprntly's OWN descriptions of what IT can do, what Sprntly the product can \
do, or its role/capabilities/meta-behaviour — for example "I'm your project \
agent", "I can edit the PRD", "here's what I can do", "I can read your \
memory and artifacts" — are NOT durable project facts. These describe the \
ASSISTANT, not the project; a teammate learns nothing about the PROJECT \
from them. Set action to "none" for these too, exactly like small talk.

Each existing entry is tagged with its source: "agent" (something Sprntly \
itself recorded previously) or "user" (something a teammate typed by \
hand). Choose exactly one action:

- "none": nothing durable is present in this excerpt at all.
- "duplicate": the excerpt holds a durable insight, but it is ALREADY \
substantively captured by an existing entry — same meaning, even if worded \
differently — whether that entry's source is "agent" or "user". Do not \
create or touch anything.
- "update": the excerpt REVISES or EXTENDS an entry Sprntly itself \
previously recorded (source="agent") — set target_entry_id to that \
entry's id and write the FULL revised body. You may only target an \
"agent" entry; if the excerpt instead relates to a "user" entry, treat \
that as "duplicate" — a teammate already captured it in their own words, \
and the agent must never overwrite a teammate's entry.
- "new": the excerpt is a genuinely new durable fact not covered by \
anything above.

When action is "new" or "update", write `body` as a SUMMARIZED one-to-two \
sentence statement of the durable fact in your own words — never copy a \
transcript line verbatim. For "none" or "duplicate", leave `body` empty \
and `target_entry_id` null. For "new", leave `target_entry_id` null.
"""

_PROMOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["new", "duplicate", "update", "none"]},
        "target_entry_id": {"type": ["integer", "null"]},
        "body": {"type": "string"},
    },
    "required": ["action", "target_entry_id", "body"],
    "additionalProperties": False,
}


# Deterministic self-reference guard (backstop) — mirrors the "update"
# path's belt-and-braces shape: a prompt rule (above) PLUS a deterministic
# check that fires regardless of what the classifier returned, exactly like
# `update_agent_promoted_entry`'s own `promoted_by='agent'` WHERE-clause
# backstops its own prompt rule. Casefolded, first-person/agent-subject
# capability phrasing ONLY — the classifier writes `body` as a SUMMARIZED
# statement in its own words (per `_PROMOTE_SYSTEM` above), so a genuine
# project fact is phrased in third person ("the team decided…", "the API
# rate limit is…") and never matches this pattern set. Kept small and
# documented; AC-8's non-regression test is the guard against over-broad
# matching on real project facts.
_SELF_CAPABILITY_PATTERNS = (
    re.compile(r"\bi can(?:not|['’]t)?\b"),  # "I can…" / "I cannot…" / "I can't…"
    re.compile(r"\bi['’]m your\b"),
    re.compile(r"\bi am your\b"),
    re.compile(r"\bas your\b[^.]{0,40}\bassistant\b"),
    re.compile(r"\bsprntly can\b"),
    re.compile(r"\bmy role\b"),
    re.compile(r"\bi have tools\b"),
)


def _is_self_capability(body: str) -> bool:
    """True when `body` (the classifier's OWN generated candidate text) is
    Sprntly describing ITS OWN capabilities, role, or meta-behaviour rather
    than a genuine project fact — the deterministic backstop applied in
    `maybe_promote_turn` AFTER the classifier returns but BEFORE any write,
    so a mis-classification can't poison memory (defense in depth alongside
    the `_PROMOTE_SYSTEM` exclusion rule)."""
    if not body:
        return False
    lowered = body.casefold()
    return any(pattern.search(lowered) for pattern in _SELF_CAPABILITY_PATTERNS)


def _render_existing_entries(entries: list[dict]) -> str:
    """Existing project memory, tagged with id + provenance, fed alongside
    the candidate transcript so the classifier can compare by MEANING
    against real rows — the fix for the exact-string dedup gap. Clipped to
    the same content cap as `_render_entries` above."""
    if not entries:
        return "(none)"
    lines = [
        f"- id={entry['id']} source={'agent' if entry.get('promoted_by') == 'agent' else 'user'}: "
        f"{entry.get('body', '')}"
        for entry in entries
    ]
    return _clip("\n".join(lines))


def _render_promotion_user(transcript: str, entries: list[dict]) -> str:
    return (
        f"Existing project memory entries:\n{_render_existing_entries(entries)}\n\n"
        f"New excerpt to consider:\n{transcript}"
    )


def _log_promotion_run(*, project_id: int, conversation_id: int, meta: dict,
                        start: float, status: str,
                        error_class: str | None = None) -> None:
    """The one structured cost-summary line per classifier call (AC9). Never
    raises — a logging hiccup must never be the reason promotion breaks."""
    try:
        log_llm_run(
            operation="projects.memory.promotion",
            identifier={"project_id": project_id, "conversation_id": conversation_id},
            usage=_usage_from_meta(meta),
            duration_ms=int((time.monotonic() - start) * 1000),
            status=status,
            model=meta.get("model") or DEFAULT_MODEL,
            error_class=error_class,
        )
    except Exception:  # noqa: BLE001 — observability must never break the writer
        logger.warning("memory_promotion_cost_log_failed project_id=%s", project_id)


def maybe_promote_turn(project_id: int, conversation_id: int, transcript: str) -> dict | None:
    """Best-effort classifier writer, called at the end of a project agent
    reply. Never raises (AD-P7): on ANY classifier or DB failure this
    promotes nothing and returns None.

    The classifier makes ONE bounded call that sees both the candidate
    transcript AND the project's existing memory entries, and returns a
    three-way decision (`action`):

      - "none" / "duplicate": no row is created or touched — a semantic
        near-duplicate of ANY existing entry (agent- or user-authored) is
        treated the same as "nothing new to say" (AC: the "thanks!"
        regression this replaces).
      - "update": revises an existing AGENT-promoted entry in place via
        `update_agent_promoted_entry` (never a user-authored one — the
        classifier is instructed not to target one, and the DB helper's
        own WHERE clause enforces it regardless of what the classifier
        returns), then schedules a regen.
      - "new": writes a fresh `promoted_by='agent'` entry via
        `add_agent_promoted_entry`, then schedules a regen.

    `schedule_regen` fires on both "update" and "new" — the `stale` flip
    alone never regenerates, since this write happens outside the HTTP
    memory handlers.
    """
    start = time.monotonic()
    meta: dict = {}
    try:
        existing_entries = memory_db.list_entries(project_id)
        out = call_json(
            system=_PROMOTE_SYSTEM,
            user=_render_promotion_user(transcript, existing_entries),
            model=DEFAULT_MODEL,
            schema=_PROMOTE_SCHEMA,
            meta_out=meta,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, AD-P7
        logger.warning(
            "memory_promotion_classify_failed project_id=%s conversation_id=%s error=%s",
            project_id, conversation_id, type(exc).__name__,
        )
        _log_promotion_run(
            project_id=project_id, conversation_id=conversation_id, meta=meta,
            start=start, status="error", error_class=type(exc).__name__,
        )
        return None

    # One cost line per classifier call, whatever the decision (AC9) — a
    # decision not to promote/update still spent the tokens.
    _log_promotion_run(
        project_id=project_id, conversation_id=conversation_id, meta=meta,
        start=start, status="complete",
    )

    try:
        action = str(out.get("action") or "").strip().lower()
        body = str(out.get("body") or "").strip()
        target_entry_id = out.get("target_entry_id")

        if action in ("new", "update") and _is_self_capability(body):
            # Deterministic backstop — fires REGARDLESS of what the
            # classifier decided, so a mis-classification (the classifier
            # forced/tricked into "new" or "update" on a self-capability
            # excerpt) still can't poison memory. Coerced to a no-op: no
            # write, no regen, exactly as if the classifier had said "none".
            logger.info(
                "memory_promotion_self_capability_skipped project_id=%s "
                "conversation_id=%s",
                project_id, conversation_id,
            )
            return None

        if action == "update":
            if not body or target_entry_id is None:
                return None  # malformed response — fail safe, skip
            target = next(
                (
                    entry
                    for entry in existing_entries
                    if entry.get("id") == target_entry_id
                    and entry.get("promoted_by") == "agent"
                ),
                None,
            )
            if target is None:
                # Guardrail: never trust target_entry_id blindly — a
                # hallucinated id or (despite the prompt) a user-authored
                # one fails safe to skip rather than silently promoting.
                logger.warning(
                    "memory_promotion_update_target_invalid project_id=%s "
                    "conversation_id=%s target_entry_id=%s",
                    project_id, conversation_id, target_entry_id,
                )
                return None
            updated = memory_db.update_agent_promoted_entry(
                project_id, target_entry_id, body=body,
                source_conversation_id=conversation_id,
            )
            if updated is None:
                return None
            logger.info(
                "memory_entry_updated project_id=%s entry_id=%s source_conversation_id=%s",
                project_id, updated["id"], conversation_id,
            )
            schedule_regen(project_id)
            return updated

        if action != "new" or not body:
            return None  # "none" / "duplicate" / malformed — no row change

        entry = memory_db.add_agent_promoted_entry(
            project_id, body=body, source_conversation_id=conversation_id
        )
        logger.info(
            "memory_entry_promoted project_id=%s entry_id=%s source_conversation_id=%s",
            project_id, entry["id"], conversation_id,
        )
        schedule_regen(project_id)
        return entry
    except Exception as exc:  # noqa: BLE001 — best-effort, AD-P7: never block the chat
        logger.warning(
            "memory_promotion_write_failed project_id=%s conversation_id=%s error=%s",
            project_id, conversation_id, type(exc).__name__,
        )
        return None
