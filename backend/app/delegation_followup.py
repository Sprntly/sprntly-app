"""Autonomous task follow-up sweep — the outbound half of the ambient task
engine (spec §3 part 3). A sibling of `app/invite_reminders.py`: wakes on
an interval (the `TASK_FOLLOWUP` scheduler job), finds tasks past their
`next_check_in` behind a cheap SQL pre-filter
(`db.delegation_followups.list_due_followups`), and for each one either
finalizes a soft-done marker, reschedules, pings the assignee, escalates
to the requester, or (after repeated unanswered pings) sends a one-way
transactional email — all within the LOCKED spec §2 guardrails
(`app.delegation_cadence`).

Per-task error isolation (mirrors `run_invite_reminder_cycle`): one
raising task is logged (identifiers only) and the loop continues. Never
raises itself; returns a summary dict for logs/tests.

Cost bound (spec §5): the ONLY things that run for a non-due task are the
SQL pre-filter row and, per due task, the pure cap/quiet-hours guards —
the ONE `call_json` per task is reached only once a task is due,
uncapped, in-hours, and not yet serviced this instant.

Never auto-invites anyone (spec §2) — every DM/email/escalation targets
an EXISTING party already on the delegation (the assignee or the
assigner). No `project_members` / `workspace_invites` write anywhere in
this module."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from app.db import conversations as conversations_db
from app.db import delegation_events as delegation_events_db
from app.db import delegation_followup_sends as sends_db
from app.db import delegation_followups as delegation_followups_db
from app.db import profiles as profiles_db
from app.db import projects as projects_db
from app.delegation_cadence import (
    MIN_INTERVAL,
    clamp_next_check_in,
    in_quiet_hours,
    is_capped,
    next_send_window,
    should_escalate,
)
from app.delegation_followup_email import send_followup_email
from app.llm import DEFAULT_MODEL, call_json
from app.llm_telemetry import RunUsage, log_llm_run
from app.project_context import assemble_project_context

logger = logging.getLogger(__name__)

# Prior unanswered DM sends at/above this count trigger the one-way email
# escalation on the NEXT ping (spec §2 channel step 2 — "≥2 unanswered
# DM cycles"). The DM that is about to be sent this cycle is not itself
# counted — this gates on what was already unanswered BEFORE it.
_EMAIL_AFTER_UNANSWERED_DMS = 2

_DEFAULT_PING_TEXT = "Just checking in on this task — any update?"

_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["ping", "reschedule", "escalate"],
            "description": (
                "what to do about this task right now — see the system "
                "prompt for the exact definition of each value"
            ),
        },
        "next_check_in": {
            "type": ["string", "null"],
            "description": (
                "ISO-8601 UTC timestamp for the next check-in. You may "
                "only LENGTHEN this interval, never shorten it below the "
                "stated floor — the caller enforces the floor either way."
            ),
        },
        "dm_text": {
            "type": ["string", "null"],
            "description": (
                "the ping message text, required when decision is 'ping', "
                "else null. Plain, short, no invented facts."
            ),
        },
    },
    "required": ["decision", "next_check_in", "dm_text"],
    "additionalProperties": False,
}

_DECISION_SYSTEM = """\
You decide what to do about ONE delegated task that is due for a
follow-up check-in, on behalf of Sprntly's autonomous task-chasing agent.

You are given: the task's full event history, the assignee's recent
private replies to Sprntly, relevant project context, the task's
expected completion (if any), and the current UTC time.

Pick EXACTLY ONE `decision`:
  - "ping"       — send the assignee a short check-in message now
                    (`dm_text` required — plain, concrete, no invented
                    facts, never end on a question if the task history
                    already answers it).
  - "reschedule" — nothing to send right now; just push the next
                    check-in further out (e.g. clear recent signal the
                    task is progressing on its own, or too soon to
                    meaningfully check again).
  - "escalate"   — the task looks stuck relative to its expected
                    completion with no recent status movement; recommend
                    surfacing this to the person who assigned it. You do
                    NOT contact the assignee yourself on this decision —
                    the caller handles escalation delivery.

HARD RULES (never violate these — the caller enforces them regardless of
what you return, so respecting them keeps your output meaningful):
  - `next_check_in`, if you set one, may only ever be LATER than the
    task's current schedule — you may never propose shortening the
    interval to check in sooner than the system's minimum cadence.
  - Never invent a fact, a name, a deadline, or an event that is not in
    the material given to you.
  - Never fabricate an assignee reply that was not actually given to you.
"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_iso(value) -> datetime | None:
    """Parse an ISO-8601 timestamp (with or without a 'Z' suffix) to an
    aware UTC datetime. Any missing/unparseable value degrades to None —
    a malformed model-emitted date must never crash the sweep."""
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


def _log_followup_run(
    *,
    project_id: int,
    delegation_id: int | None,
    meta: dict,
    start: float,
    status: str,
    error_class: str | None = None,
) -> None:
    """The one structured cost-summary line per followup decision call
    (AD-P15). Never raises — a logging hiccup must never break the sweep."""
    try:
        log_llm_run(
            operation="projects.delegation.followup",
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
    except Exception:  # noqa: BLE001 — observability must never break the sweep
        logger.warning(
            "delegation_followup_cost_log_failed project_id=%s", project_id
        )


def _render_decision_user(
    *,
    task_summary: str,
    events: list[dict],
    dm_turns: list[dict],
    context_block: str,
    expected_completion: datetime | None,
    now: datetime,
) -> str:
    event_lines = (
        "\n".join(f"- {e.get('event')} at {e.get('created_at')}" for e in events)
        or "(no events yet)"
    )
    dm_lines = (
        "\n".join(f"- [{t.get('role')}] {t.get('content', '')}" for t in dm_turns[-10:])
        or "(no replies yet)"
    )
    parts = [
        f"Task: {task_summary}",
        f"Current UTC datetime: {now.isoformat()}",
        f"Expected completion: {_iso(expected_completion) or '(not stated)'}",
        f"\nEvent history:\n{event_lines}",
        f"\nRecent private replies from the assignee:\n{dm_lines}",
    ]
    if context_block:
        parts.append(f"\nProject context:\n{context_block}")
    return "\n".join(parts)


def _unanswered_dm_count(delegation_id: int, events: list[dict]) -> int:
    """How many prior `channel='dm'` sends have gone unanswered — a DM
    counts as answered once an `in_progress`/`completed` event lands
    after it (status-ingestion, TE-03, already advances/clears status on
    any substantive reply). With no status-changing event at all, every
    prior DM is unanswered."""
    dm_sends = sends_db.sends_for_delegation(delegation_id, channel="dm")
    if not dm_sends:
        return 0
    status_change_ats = [
        e["created_at"] for e in events if e.get("event") in ("in_progress", "completed")
    ]
    latest_status_change = max(status_change_ats, default=None)
    if latest_status_change is None:
        return len(dm_sends)
    return sum(1 for s in dm_sends if s.get("sent_at", "") > latest_status_change)


def _cycles_since_status(delegation_id: int, events: list[dict]) -> int:
    """How many prior sends (any channel) happened since the latest
    in_progress/completed event — `should_escalate`'s `cycles_since_status`
    input. With no status-changing event, every prior send counts."""
    sends = sends_db.sends_for_delegation(delegation_id)
    if not sends:
        return 0
    status_change_ats = [
        e["created_at"] for e in events if e.get("event") in ("in_progress", "completed")
    ]
    latest_status_change = max(status_change_ats, default=None)
    if latest_status_change is None:
        return len(sends)
    return sum(1 for s in sends if s.get("sent_at", "") > latest_status_change)


def _process_one(row: dict, *, now: datetime, tz_map: dict[str, str], summary: dict) -> None:
    from app.brief_schedule import resolve_user_timezone

    delegation_id = row["delegation_id"]
    project_id = row["project_id"]
    assignee_user_id = row["assignee_user_id"]
    assigner_user_id = row.get("assigner_user_id")
    task_summary = row.get("task_summary") or ""

    # ── Step 0: soft-done finalization SHORT-CIRCUIT (before any other
    # guard/LLM/send this cycle — finalize XOR follow-up, never both). ──
    if row.get("pending_done_since"):
        delegation_events_db.record_event(
            delegation_id=delegation_id, event="completed", actor_user_id=assigner_user_id,
        )
        delegation_followups_db.upsert_followup(delegation_id, pending_done_since=None)
        summary["finalized"] += 1
        return

    last_checked_in = _parse_iso(row.get("last_checked_in"))
    expected_completion = _parse_iso(row.get("expected_completion"))
    next_check_in = _parse_iso(row.get("next_check_in")) or now

    # ── Step 1: per-person cap guard (pure, cheap, no LLM). ──
    sends_today = sends_db.sends_for_person_since(assignee_user_id, now - timedelta(hours=24))
    sends_week = sends_db.sends_for_person_since(assignee_user_id, now - timedelta(days=7))
    if is_capped(sends_today=len(sends_today), sends_this_week=len(sends_week)):
        tz = resolve_user_timezone(tz_map.get(assignee_user_id))
        local_dt = (now + MIN_INTERVAL).astimezone(tz)
        delegation_followups_db.upsert_followup(
            delegation_id, next_check_in=next_send_window(local_dt),
        )
        summary["rescheduled"] += 1
        return

    # ── Step 2: quiet-hours guard (pure, cheap, no LLM). ──
    tz = resolve_user_timezone(tz_map.get(assignee_user_id))
    now_local = now.astimezone(tz)
    if in_quiet_hours(now_local):
        delegation_followups_db.upsert_followup(
            delegation_id, next_check_in=next_send_window(now_local),
        )
        summary["rescheduled"] += 1
        return

    # ── Step 3: idempotency — already serviced this exact instant. ──
    check_key = next_check_in.isoformat()
    if sends_db.send_exists(delegation_id, check_key, "dm"):
        summary["skipped"] += 1
        return

    # ── Step 4: the bounded LLM decision — reached only for a due,
    # uncapped, in-hours, not-yet-serviced task. ──
    events = delegation_events_db.list_events(delegation_id)
    conv_id = row.get("delivered_conversation_id")
    try:
        dm_turns = (
            conversations_db.list_individual_turns(conv_id, assignee_user_id)
            if conv_id else []
        )
    except Exception:  # noqa: BLE001 — best-effort context fold
        dm_turns = []
    try:
        context_block = assemble_project_context(project_id, assignee_user_id)
    except Exception:  # noqa: BLE001 — best-effort context fold
        context_block = ""

    start = time.monotonic()
    meta: dict = {}
    try:
        out = call_json(
            system=_DECISION_SYSTEM,
            user=_render_decision_user(
                task_summary=task_summary,
                events=events,
                dm_turns=dm_turns,
                context_block=context_block,
                expected_completion=expected_completion,
                now=now,
            ),
            model=DEFAULT_MODEL,
            schema=_DECISION_SCHEMA,
            meta_out=meta,
        )
    except Exception as exc:  # noqa: BLE001 — per-task isolation, caller also wraps
        _log_followup_run(
            project_id=project_id, delegation_id=delegation_id, meta=meta,
            start=start, status="error", error_class=type(exc).__name__,
        )
        raise

    _log_followup_run(
        project_id=project_id, delegation_id=delegation_id, meta=meta,
        start=start, status="complete",
    )

    decision = str(out.get("decision") or "reschedule").strip().lower()
    model_next = _parse_iso(out.get("next_check_in"))

    # ── Step 5: apply the decision — guardrails win over the model. ──
    if decision == "ping":
        dm_text = (out.get("dm_text") or "").strip() or _DEFAULT_PING_TEXT
        prior_unanswered = _unanswered_dm_count(delegation_id, events)

        conv = conversations_db.create_individual_project_chat(project_id, assignee_user_id)
        conversations_db.post_individual_turn(conv["id"], "assistant", dm_text)
        sends_db.record_send(
            delegation_id=delegation_id,
            company_id=(projects_db.get_project(project_id) or {}).get("company_id"),
            assignee_user_id=assignee_user_id,
            check_key=check_key,
            channel="dm",
            status="sent",
        )
        delegation_followups_db.upsert_followup(
            delegation_id,
            last_checked_in=now,
            next_check_in=clamp_next_check_in(model_next, last_checked_in=last_checked_in, now=now),
        )
        summary["pinged"] += 1

        if prior_unanswered >= _EMAIL_AFTER_UNANSWERED_DMS:
            emails = profiles_db.emails_for_user_ids([assignee_user_id])
            to_email = emails.get(assignee_user_id)
            if to_email:
                first_name = profiles_db.first_name_for_user(assignee_user_id)
                ok = send_followup_email(
                    to_email=to_email, first_name=first_name, project_id=project_id,
                )
                sends_db.record_send(
                    delegation_id=delegation_id,
                    company_id=(projects_db.get_project(project_id) or {}).get("company_id"),
                    assignee_user_id=assignee_user_id,
                    check_key=check_key,
                    channel="email",
                    status="sent" if ok else "skipped",
                )
                summary["emailed"] += 1
        return

    if decision == "escalate":
        cycles = _cycles_since_status(delegation_id, events)
        latest_status = row.get("status") or "assigned"
        if should_escalate(
            expected_completion=expected_completion, now=now,
            latest_status=latest_status, cycles_since_status=cycles,
        ) and assigner_user_id:
            conv = conversations_db.create_individual_project_chat(project_id, assigner_user_id)
            conversations_db.post_individual_turn(
                conv["id"], "assistant",
                f"No confirmed progress on: {task_summary}. Reassign or nudge harder?",
            )
            sends_db.record_send(
                delegation_id=delegation_id,
                company_id=(projects_db.get_project(project_id) or {}).get("company_id"),
                assignee_user_id=assignee_user_id,
                check_key=check_key,
                channel="escalation",
                status="sent",
            )
            delegation_followups_db.upsert_followup(
                delegation_id,
                next_check_in=clamp_next_check_in(model_next, last_checked_in=last_checked_in, now=now),
            )
            summary["escalated"] += 1
            return
        # Guardrail not yet met — the model's escalate call degrades to a
        # plain reschedule; never DM the assignee on this path either way.
        delegation_followups_db.upsert_followup(
            delegation_id,
            next_check_in=clamp_next_check_in(model_next, last_checked_in=last_checked_in, now=now),
        )
        summary["rescheduled"] += 1
        return

    # "reschedule" (or an unrecognized decision, degrading safely).
    delegation_followups_db.upsert_followup(
        delegation_id,
        next_check_in=clamp_next_check_in(model_next, last_checked_in=last_checked_in, now=now),
    )
    summary["rescheduled"] += 1


def run_task_followup_cycle() -> dict:
    """One pass of the autonomous task follow-up sweep. Per-task error
    isolation; safe to call repeatedly (idempotent send-ledger). Gated at
    the scheduler level by `settings.task_followup_enabled` +
    `settings.scheduler_enabled`; this function additionally re-checks the
    request-time Projects gate itself (`routes.projects._projects_enabled`)
    so a dark-Projects environment makes zero LLM calls even if a stray
    call reaches this function directly.

    Returns a small summary dict for logging + tests:
    `{due, finalized, pinged, rescheduled, escalated, emailed, skipped}`."""
    # Imported here (not at module load) so the test config reload +
    # request-time env read are in effect, mirroring every other cycle
    # wrapper in this codebase.
    from app.routes.projects import _projects_enabled

    summary = {
        "due": 0, "finalized": 0, "pinged": 0, "rescheduled": 0,
        "escalated": 0, "emailed": 0, "skipped": 0,
    }

    if not _projects_enabled():
        logger.info("task-followup: Projects disabled — skipping cycle")
        return summary

    now = datetime.now(timezone.utc)
    try:
        due = delegation_followups_db.list_due_followups(now)
    except Exception:
        logger.exception("task-followup: failed to list due followups")
        return summary
    if not due:
        return summary

    summary["due"] = len(due)
    try:
        tz_map = delegation_followups_db.timezones_for_user_ids(
            [row.get("assignee_user_id") for row in due]
        )
    except Exception:  # noqa: BLE001 — best-effort; degrades every row to UTC
        tz_map = {}

    for row in due:
        try:
            _process_one(row, now=now, tz_map=tz_map, summary=summary)
        except Exception:
            logger.exception(
                "task-followup: failed for delegation %s", row.get("delegation_id")
            )
            continue

    logger.info("task-followup cycle: %s", summary)
    return summary
