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
# After the group agent replies (`routes/projects.py::_respond_as_group_agent`),
# `maybe_promote_turn` runs ONE bounded classifier call over the clamped
# transcript the caller already built and decides whether the exchange holds
# a durable, project-relevant insight worth persisting. A promotion failure
# — classifier or DB — must never affect the chat turn that triggered it,
# which already persisted before this runs; every path below returns None
# rather than raising.

_PROMOTE_SYSTEM = """You read a short excerpt of a project team's group \
chat — including Sprntly's own last reply — and decide whether it contains \
a DURABLE, project-relevant insight worth remembering for teammates who \
were not in this conversation.

Promote ONLY a decision, guardrail, constraint, or durable fact the team \
has actually settled on. Never promote small talk, greetings, \
acknowledgements ("thanks", "sounds good", "got it"), plain questions with \
no answer yet, or ephemeral status updates ("still looking into it", \
"will check tomorrow").

When `should_promote` is true, write `insight` as a SUMMARIZED one-to-two \
sentence statement of the durable fact in your own words — never copy a \
transcript line verbatim. When nothing durable is present, set \
`should_promote` to false and leave `insight` empty.
"""

_PROMOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "should_promote": {"type": "boolean"},
        "insight": {"type": "string"},
    },
    "required": ["should_promote", "insight"],
    "additionalProperties": False,
}


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


def _is_duplicate_insight(project_id: int, insight: str) -> bool:
    """Case-insensitive exact-body match against the project's existing
    entries — the chosen v1 idempotency bar (no embeddings, no new deps), so
    a repeated theme across turns doesn't stack duplicate rows."""
    normalized = insight.strip().casefold()
    return any(
        str(entry.get("body", "")).strip().casefold() == normalized
        for entry in memory_db.list_entries(project_id)
    )


def maybe_promote_turn(project_id: int, conversation_id: int, transcript: str) -> dict | None:
    """Best-effort classifier writer, called at the end of a group agent
    reply. Never raises (AD-P7): on ANY classifier or DB failure this
    promotes nothing and returns None. On a genuine promotion, writes one
    `promoted_by='agent'` entry via `add_agent_promoted_entry`, schedules a
    summary regen (`schedule_regen` — the `stale` flip alone never
    regenerates, since this write happens outside the HTTP memory
    handlers), and returns the new row.
    """
    start = time.monotonic()
    meta: dict = {}
    try:
        out = call_json(
            system=_PROMOTE_SYSTEM,
            user=transcript,
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

    # One cost line per classifier call, whether or not it decided to
    # promote (AC9) — a decision not to promote still spent the tokens.
    _log_promotion_run(
        project_id=project_id, conversation_id=conversation_id, meta=meta,
        start=start, status="complete",
    )

    try:
        should_promote = bool(out.get("should_promote"))
        insight = str(out.get("insight") or "").strip()
        if not should_promote or not insight:
            return None
        if _is_duplicate_insight(project_id, insight):
            return None
        entry = memory_db.add_agent_promoted_entry(
            project_id, body=insight, source_conversation_id=conversation_id
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
