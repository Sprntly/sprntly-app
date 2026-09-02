"""Inbound reply classifier — the other half of the task cadence spine.

`maybe_ingest_status` is the entry point, called at the individual-chat
turn seam right beside `project_memory.py::maybe_promote_turn`
(`ask_job_runner.py`). It reads an assignee's reply in their own private
project chat and classifies what it means for one of their currently OPEN
delegated tasks — status update, a stated timeline, or a route-back to the
requester ("blocked" / "I can't do this") — then applies the write.

Best-effort, never raises (AD-P7): on ANY failure (classify or apply) this
logs a warning (identifiers only) and returns, exactly like
`maybe_promote_turn`. Structured like that module throughout: a cheap
pre-filter before any LLM call, ONE bounded `call_json`, one cost line per
call (AD-P15) via `_log_ingest_run`.

Cost guard (spec §5, AC10): the pre-filter reads the replier's OPEN
delegations delivered into THIS conversation via
`app.db.delegation_events.list_status_for_assignee` — if that list is
empty, `maybe_ingest_status` returns WITHOUT ever calling the LLM. This is
what keeps the classifier from firing on ordinary chat traffic.

Soft-done contradiction rule (the inbound half of the soft-done handshake
the outbound follow-up sweep depends on): ANY status-changing reply is
fresh evidence contradicting an earlier INFERRED completion. So every
non-`none` intent EXCEPT `done_inferred` itself clears `pending_done_since`
back to NULL — `done_inferred` is the only intent that ever SETS it, and a
`none` reply (not a status change) never touches it.

Ships no scheduler, no DM/email send, no agent execution of the task
itself — those are a separate, later piece of work built on top of this
spine.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from app.db import conversations as conversations_db
from app.db import delegation_events as delegation_events_db
from app.db import delegation_followups as delegation_followups_db
from app.db.client import require_client
from app.delegation_cadence import (
    MIN_INTERVAL,
    clamp_next_check_in,
    respect_stated_timeline,
)
from app.llm import DEFAULT_MODEL, call_json
from app.llm_telemetry import RunUsage, log_llm_run
# Reused, not reimplemented (AD-P21/AD-P22): the SAME best-effort publish
# helpers `project_delegation.handle_delegate_task` already uses on the
# creation path — see `_post_to_own_chat`/`_publish_status_change` below for
# why the inbound reply path needs them too. No new realtime channel/event
# name; both broadcast on the existing per-user `project:{id}:user:{uid}`
# topics.
from app.project_delegation import (
    _notify_assigner_task_completed_email,
    _publish_brief_delivered,
    _publish_delegation_event,
)

logger = logging.getLogger(__name__)

# The soft-confirm turn posted into the REPLIER's own chat on an inferred
# (not explicit) completion — plain, no trailing question (the confirmation
# window itself, not a live turn a reply needs to land on).
_SOFT_CONFIRM_TEXT = "I'll mark this done unless you tell me otherwise."

_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "delegation_id": {
            "type": ["integer", "null"],
            "description": (
                "which of the OPEN tasks listed below this reply is about — "
                "must be one of the given delegation_id values, or null if "
                "the reply does not clearly match any of them"
            ),
        },
        "intent": {
            "type": "string",
            "enum": [
                "in_progress",
                "done_explicit",
                "done_inferred",
                "timeline",
                "blocked",
                "cant_do",
                "none",
            ],
            "description": (
                "what the reply means for that task's status — see the "
                "system prompt for the exact definition of each value, "
                "including when to use 'none'"
            ),
        },
        "stated_completion": {
            "type": ["string", "null"],
            "description": (
                "ISO-8601 UTC timestamp if the reply names a deadline, "
                "resolved from the given current date/time and recipient "
                "timezone (e.g. 'Friday' -> the next Friday's date), else null"
            ),
        },
        "proposed_next_check_in": {
            "type": ["string", "null"],
            "description": (
                "ISO-8601 UTC timestamp for when to next check in on this "
                "task, if you have a sense of it beyond the default cadence, "
                "else null"
            ),
        },
    },
    "required": ["delegation_id", "intent", "stated_completion", "proposed_next_check_in"],
    "additionalProperties": False,
}

_CLASSIFY_SYSTEM = """\
You read one reply a project teammate sent in their own private chat with
Sprntly, in response to a task they were assigned, and classify what it
means for that task's status.

You are given: the reply text, a list of this person's currently OPEN
tasks (each with a delegation_id and a short task_summary), the current
UTC date/time, and the recipient's local timezone.

Pick EXACTLY ONE task from the given list that the reply is about — never
invent a delegation_id that is not in the list — or return null for
delegation_id if the reply does not clearly refer to any of them.

Classify `intent` as exactly one of:
  - "in_progress"   — they say they're working on it / have started it
  - "done_explicit" — they clearly and explicitly say it's done, finished,
                       or complete
  - "done_inferred" — the reply strongly implies completion without saying
                       so explicitly ("shipped that", "all set", "sent it
                       over")
  - "timeline"       — they name a completion date or time ("I'll have it
                       Friday", "by end of day tomorrow")
  - "blocked"        — they say they're stuck, waiting on something, or
                       need help before they can proceed
  - "cant_do"        — they say they can't take this on, or decline it
  - "none"           — the reply is not about any open task at all — small
                       talk, an unrelated question, or a reply to something
                       else entirely. Use "none" liberally whenever the
                       reply is ambiguous or off-topic — never force one of
                       the other intents onto a reply that isn't actually
                       about a task.

When the reply names a deadline, resolve it to an absolute ISO-8601 UTC
timestamp yourself using the given current date/time and the recipient's
local timezone, and put it in `stated_completion`. If you have a genuine
sense of when to check in next but no stated deadline, put your best-guess
ISO-8601 UTC instant in `proposed_next_check_in` — otherwise leave both
fields null. Never invent a date that isn't grounded in the reply or the
given current time.
"""


def _render_classify_user(reply_text: str, open_rows: list[dict], now: datetime, tz_name: str) -> str:
    tasks = (
        "\n".join(
            f"- delegation_id={row['delegation_id']}: {row.get('task_summary', '')}"
            for row in open_rows
        )
        or "(none)"
    )
    return (
        f"Current UTC datetime: {now.isoformat()}\n"
        f"Recipient timezone: {tz_name}\n\n"
        f"Open tasks:\n{tasks}\n\n"
        f"Reply:\n{reply_text}"
    )


def _replier_timezone(user_id: str) -> str:
    """Best-effort IANA timezone name for the replier, degrading to UTC on
    any failure (missing profile row, legacy schema, the fake test
    Supabase) — mirrors `db.companies._attach_owner_timezones`'s own
    degrade-to-UTC posture."""
    try:
        from app.brief_schedule import resolve_user_timezone

        rows = (
            require_client()
            .table("profiles")
            .select("timezone")
            .eq("id", user_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        raw = rows[0].get("timezone") if rows else None
        zone = resolve_user_timezone(raw)
        return getattr(zone, "key", None) or "UTC"
    except Exception:  # noqa: BLE001 — best-effort, never blocks classification
        return "UTC"


def _log_ingest_run(
    *,
    project_id: int,
    delegation_id: int | None,
    meta: dict,
    start: float,
    status: str,
    error_class: str | None = None,
) -> None:
    """The one structured cost-summary line per classifier call (AC18).
    Never raises — a logging hiccup must never be the reason ingestion
    breaks."""
    try:
        log_llm_run(
            operation="projects.delegation.status_ingest",
            identifier={"project_id": project_id, "delegation_id": delegation_id},
            usage=RunUsage(
                cache_creation_input_tokens=meta.get("cache_creation_input_tokens", 0),
                cache_read_input_tokens=meta.get("cache_read_input_tokens", 0),
                input_tokens=meta.get("input_tokens", 0),
                output_tokens=meta.get("output_tokens", 0),
            ),
            duration_ms=int((time.monotonic() - start) * 1000),
            status=status,
            model=meta.get("model") or DEFAULT_MODEL,
            error_class=error_class,
        )
    except Exception:  # noqa: BLE001 — observability must never break ingestion
        logger.warning("delegation_status_ingest_cost_log_failed project_id=%s", project_id)


def _display_first_name(project_id: int, user_id: str) -> str:
    """Best-effort first name for a route-back message; degrades to a
    generic label on any lookup failure."""
    try:
        from app.db.projects import list_members

        roster = list_members(project_id)
        member = next((m for m in roster if m.get("user_id") == user_id), None)
        name = (member or {}).get("name")
        return name.split()[0] if name else "Someone"
    except Exception:  # noqa: BLE001 — best-effort, never blocks the route-back
        return "Someone"


def _post_to_own_chat(project_id: int, user_id: str, text: str) -> None:
    """Post one turn into `user_id`'s own individual project chat AND
    broadcast it live, the same way a delivered brief already does
    (`project_delegation._publish_brief_delivered`) — this is the SAME
    mechanic (a one-way notification turn landing in someone's own chat),
    just a different caller. Without this, every route-back this module
    posts (a blocked/can't-do notice, the completion notice) sat in the
    recipient's chat until their next reconcile/refetch; the ledger's own
    creation-time publish had no counterpart here. Best-effort like the
    publish helper itself — a broadcast hiccup never rolls back the write,
    which has already committed by the time this is called."""
    conv = conversations_db.create_individual_project_chat(project_id, user_id)
    turn = conversations_db.post_individual_turn(conv["id"], "assistant", text)
    _publish_brief_delivered(project_id, user_id, conv["id"], turn)


def _publish_status_change(
    *, project_id: int, delegation_id: int, assigner_user_id: str | None, assignee_user_id: str,
) -> None:
    """Best-effort: broadcast the derived status DTO to both parties right
    after a `record_event` write in this module, mirroring the creation-time
    publish `project_delegation.handle_delegate_task` already does
    (`_publish_delegation_event`) — so the assigner's Task ledger updates
    LIVE on an INBOUND status change too (in_progress / done_explicit), not
    only on the delegation's creation. Its own try/except (not just the
    caller's outer one): a publish hiccup here must never stop the
    `delegation_followups` row update that follows it in
    `_apply_classification`."""
    if not assigner_user_id:
        return
    try:
        dto = delegation_events_db.status_dto(delegation_id)
        if dto is not None:
            _publish_delegation_event(
                project_id=project_id,
                assigner_user_id=assigner_user_id,
                assignee_user_id=assignee_user_id,
                dto=dto,
            )
    except Exception as exc:  # noqa: BLE001 — best-effort, mirrors project_delegation's own publish call sites
        logger.warning(
            "delegation_status_publish_failed delegation_id=%s error_class=%s",
            delegation_id, type(exc).__name__,
        )


def _route_to_requester(project_id: int, delegation_id: int, text: str) -> None:
    fact = delegation_events_db.load_delegation_for_authz(delegation_id)
    if not fact:
        return
    assigner_id = fact.get("assigner_user_id")
    if not assigner_id:
        return
    _post_to_own_chat(project_id, assigner_id, text)


def notify_requester_task_completed(
    project_id: int, delegation_id: int, *, assignee_user_id: str, task_summary: str | None
) -> None:
    """Post ONE low-noise completion notice to the requester's (assigner's)
    private project chat. Best-effort / never raises: a failed post must not
    roll back a durably-recorded completion (mirrors the delegation handler's
    non-fatal posture). Reuses `_route_to_requester` (loads the assigner +
    posts) + `_display_first_name` (the completer's first name).

    Also fires the shared best-effort transactional completion email
    to the assigner via `project_delegation._notify_assigner_task_completed_email`
    — the SAME DRY helper `handle_complete_task` calls directly (that path
    has no in-app notice today), so all three completion paths email
    identically. Called unconditionally, independent of whether the
    in-app notice above succeeded — the email helper is entirely
    self-contained/never-raising."""
    try:
        name = _display_first_name(project_id, assignee_user_id)
        summary = (task_summary or "").strip()
        text = f"✓ {name} finished: {summary}" if summary else f"✓ {name} finished the task."
        _route_to_requester(project_id, delegation_id, text)
    except Exception:  # noqa: BLE001 — best-effort, never blocks a recorded completion
        logger.warning(
            "delegation_completion_notice_failed project_id=%s delegation_id=%s",
            project_id, delegation_id,
        )
    _notify_assigner_task_completed_email(
        project_id, delegation_id, assignee_user_id=assignee_user_id
    )


def _apply_classification(
    *,
    project_id: int,
    replier_user_id: str,
    open_map: dict[int, dict],
    out: dict,
) -> None:
    """Apply the classifier's decision. Never called with a `delegation_id`
    outside `open_map` (see caller) or `intent == "none"` — both are no-ops
    handled by the caller before this is reached."""
    intent = out["intent"]
    delegation_id = out["delegation_id"]
    row = open_map[delegation_id]

    now = datetime.now(timezone.utc)
    followup = delegation_followups_db.get_followup(delegation_id)
    last_checked_in = _parse_iso((followup or {}).get("last_checked_in"))
    stated = _parse_iso(out.get("stated_completion"))
    proposed = _parse_iso(out.get("proposed_next_check_in"))

    if intent == "in_progress":
        if delegation_events_db.is_legal_transition(row["status"], "in_progress"):
            delegation_events_db.record_event(
                delegation_id=delegation_id, event="in_progress", actor_user_id=replier_user_id
            )
            # Ledger liveness (3a): the requester's Task ledger otherwise only
            # ever moves on their next reconcile/refetch.
            _publish_status_change(
                project_id=project_id, delegation_id=delegation_id,
                assigner_user_id=row.get("assigner_user_id"),
                assignee_user_id=replier_user_id,
            )
        delegation_followups_db.upsert_followup(
            delegation_id,
            next_check_in=clamp_next_check_in(proposed, last_checked_in=last_checked_in, now=now),
            pending_done_since=None,
        )
    elif intent == "done_explicit":
        if delegation_events_db.is_legal_transition(row["status"], "completed"):
            delegation_events_db.record_event(
                delegation_id=delegation_id, event="completed", actor_user_id=replier_user_id
            )
            # Ledger liveness (3a), same as the in_progress branch above.
            _publish_status_change(
                project_id=project_id, delegation_id=delegation_id,
                assigner_user_id=row.get("assigner_user_id"),
                assignee_user_id=replier_user_id,
            )
            notify_requester_task_completed(
                project_id, delegation_id,
                assignee_user_id=replier_user_id, task_summary=row.get("task_summary"),
            )
        delegation_followups_db.upsert_followup(delegation_id, pending_done_since=None)
    elif intent == "done_inferred":
        delegation_followups_db.upsert_followup(
            delegation_id,
            pending_done_since=now,
            next_check_in=clamp_next_check_in(
                now + MIN_INTERVAL, last_checked_in=last_checked_in, now=now
            ),
        )
        _post_to_own_chat(project_id, replier_user_id, _SOFT_CONFIRM_TEXT)
    elif intent == "timeline":
        delegation_followups_db.upsert_followup(
            delegation_id,
            expected_completion=stated,
            next_check_in=respect_stated_timeline(
                stated, proposed, now=now, last_checked_in=last_checked_in
            ),
            pending_done_since=None,
        )
    elif intent == "blocked":
        delegation_followups_db.upsert_followup(
            delegation_id,
            next_check_in=clamp_next_check_in(
                now + MIN_INTERVAL, last_checked_in=last_checked_in, now=now
            ),
            pending_done_since=None,
        )
        name = _display_first_name(project_id, replier_user_id)
        _route_to_requester(
            project_id, delegation_id,
            f"{name} reported they're blocked on: {row.get('task_summary', '')}.",
        )
    elif intent == "cant_do":
        delegation_followups_db.upsert_followup(delegation_id, pending_done_since=None)
        name = _display_first_name(project_id, replier_user_id)
        _route_to_requester(
            project_id, delegation_id,
            f"{name} says they can't take this on — reassign or clear it?",
        )
    # "none" never reaches this function (guarded by the caller).


def _parse_iso(value) -> datetime | None:
    """Parse an ISO-8601 timestamp (with or without a 'Z' suffix) to an
    aware UTC datetime. Any missing/unparseable value degrades to None
    rather than raising — a malformed model-emitted date must never crash
    the write path."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def maybe_ingest_status(
    project_id: int, conversation_id: int, replier_user_id: str | None, reply_text: str
) -> None:
    """Best-effort inbound status classifier + writer. Never raises
    (AD-P7): on ANY failure this logs a warning (identifiers only, no
    reply body, no task content) and returns."""
    if not replier_user_id or not (reply_text or "").strip():
        return

    try:
        open_rows = delegation_events_db.list_status_for_assignee(project_id, replier_user_id)
    except Exception as exc:  # noqa: BLE001 — best-effort, AD-P7
        logger.warning(
            "delegation_status_ingest_prefilter_failed project_id=%s error=%s",
            project_id, type(exc).__name__,
        )
        return

    open_rows = [
        row
        for row in open_rows
        if row.get("status") in delegation_events_db.OPEN_STATES
        and row.get("delivered_conversation_id") == conversation_id
    ]
    if not open_rows:
        return  # AC10 — zero LLM calls when there is no open delegation to classify against

    open_map = {row["delegation_id"]: row for row in open_rows}

    start = time.monotonic()
    meta: dict = {}
    try:
        out = call_json(
            system=_CLASSIFY_SYSTEM,
            user=_render_classify_user(
                reply_text, open_rows, datetime.now(timezone.utc), _replier_timezone(replier_user_id)
            ),
            model=DEFAULT_MODEL,
            schema=_CLASSIFY_SCHEMA,
            meta_out=meta,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, AD-P7
        _log_ingest_run(
            project_id=project_id, delegation_id=None, meta=meta,
            start=start, status="error", error_class=type(exc).__name__,
        )
        logger.warning(
            "delegation_status_ingest_classify_failed project_id=%s error=%s",
            project_id, type(exc).__name__,
        )
        return

    delegation_id = out.get("delegation_id")
    _log_ingest_run(
        project_id=project_id, delegation_id=delegation_id, meta=meta, start=start, status="complete",
    )

    intent = str(out.get("intent") or "none").strip().lower()
    if intent == "none" or delegation_id not in open_map:
        return  # AC15 — no-op; never touches an existing pending_done_since

    try:
        _apply_classification(
            project_id=project_id,
            replier_user_id=replier_user_id,
            open_map=open_map,
            out={**out, "intent": intent, "delegation_id": delegation_id},
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, AD-P7
        logger.warning(
            "delegation_status_ingest_apply_failed project_id=%s delegation_id=%s error=%s",
            project_id, delegation_id, type(exc).__name__,
        )
        return
