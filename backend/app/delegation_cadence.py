"""Pure guardrail engine for the autonomous task follow-up cadence — the
LOCKED spec caps (send frequency, quiet hours, escalation) encoded as
constants + pure functions, not model judgment.

No DB, no clock of its own — every function takes `now`/`local_dt`
explicitly, mirroring `app.brief_schedule`'s pure-module shape. That makes
"is this send allowed right now?" assertable in a unit test without a real
clock, a real scheduler tick, or a rig.

Two call sites: this ticket's inbound classifier uses `clamp_next_check_in`
and `respect_stated_timeline` to reschedule `next_check_in` on every status
update; the (separately shipped) outbound follow-up sweep consumes the rest
(caps / quiet-hours / escalate) to decide whether and when to actually send
a reminder. Keeping the whole table pure here means neither call site needs
a rig to unit-test its cadence math.

The floor is a one-way ratchet: the model (or a human's stated timeline)
may only ever LENGTHEN the interval to the next check-in, never shorten it
below `MIN_INTERVAL` — `clamp_next_check_in` is the single place that rule
lives.
"""
from __future__ import annotations

from datetime import datetime, timedelta

# ── LOCKED cadence caps (spec §2) ───────────────────────────────────────
MIN_INTERVAL = timedelta(hours=24)  # floor: never re-ping a task sooner than this
PER_PERSON_DAILY_CAP = 1  # at most 1 outbound send/day per person, across all their tasks
PER_PERSON_WEEKLY_CAP = 3  # at most 3 outbound sends/week per person
QUIET_START_HOUR = 20  # no sends 8pm-8am recipient-local, or on a weekend
QUIET_END_HOUR = 8

# Mirrors `app.db.delegation_events.OPEN_STATES` — duplicated (not
# imported) so this module stays a zero-DB-import pure table, matching
# `app.brief_schedule`'s precedent. `db.delegation_events` is the source of
# truth for the real vocabulary; if that set ever changes, update here too.
_OPEN_STATUSES: frozenset[str] = frozenset({"assigned", "in_progress"})


def _floor(*, last_checked_in: datetime | None, now: datetime) -> datetime:
    """The earliest a task may next be checked in on: `MIN_INTERVAL` after
    whichever is later of `now` and `last_checked_in`."""
    base = max(now, last_checked_in) if last_checked_in is not None else now
    return base + MIN_INTERVAL


def clamp_next_check_in(
    proposed: datetime | None, *, last_checked_in: datetime | None, now: datetime
) -> datetime:
    """Enforce the floor. The model may only ever LENGTHEN: the returned
    instant is never sooner than `max(now, last_checked_in) + MIN_INTERVAL`.
    A `None`/too-soon proposal is raised to the floor; a later proposal is
    respected as-is."""
    floor = _floor(last_checked_in=last_checked_in, now=now)
    if proposed is None:
        return floor
    return proposed if proposed > floor else floor


def respect_stated_timeline(
    stated: datetime | None,
    proposed: datetime | None,
    *,
    now: datetime,
    last_checked_in: datetime | None,
) -> datetime:
    """If the human stated a timeline, `next_check_in` = that instant
    (clamped to the floor) — never before it. Falls back to `proposed`
    (clamped) when `stated` is None."""
    if stated is not None:
        floor = _floor(last_checked_in=last_checked_in, now=now)
        return stated if stated > floor else floor
    return clamp_next_check_in(proposed, last_checked_in=last_checked_in, now=now)


def in_quiet_hours(local_dt: datetime) -> bool:
    """True when `local_dt` is 20:00-07:59 recipient-local OR a Saturday/
    Sunday local calendar day. `weekday()`: Monday=0 ... Sunday=6."""
    if local_dt.weekday() >= 5:  # Saturday, Sunday
        return True
    hour = local_dt.hour
    return hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR


def next_send_window(local_dt: datetime) -> datetime:
    """Shift a send that lands in quiet hours / a weekend to the next
    08:00 weekday, recipient-local. Returns `local_dt` unchanged when it is
    already outside quiet hours. Mirrors `invite_reminders.next_workday`'s
    shape (a small deterministic day-walk), widened to also carry the
    time-of-day shift to 08:00 that a plain weekend shift doesn't need."""
    if not in_quiet_hours(local_dt):
        return local_dt

    candidate = local_dt.replace(hour=QUIET_END_HOUR, minute=0, second=0, microsecond=0)
    if candidate <= local_dt:
        candidate += timedelta(days=1)
    while in_quiet_hours(candidate):
        candidate += timedelta(days=1)
    return candidate


def is_capped(*, sends_today: int, sends_this_week: int) -> bool:
    """True when the per-PERSON caps are already hit (>= 1 today or >= 3
    this week)."""
    return sends_today >= PER_PERSON_DAILY_CAP or sends_this_week >= PER_PERSON_WEEKLY_CAP


def should_escalate(
    *,
    expected_completion: datetime | None,
    now: datetime,
    latest_status: str,
    cycles_since_status: int,
) -> bool:
    """True when a task is past `expected_completion` + one cycle with no
    status change (`latest_status` still open) — the escalation target is
    the REQUESTER, decided by the caller, not this function."""
    if expected_completion is None:
        return False
    if latest_status not in _OPEN_STATUSES:
        return False
    if cycles_since_status < 1:
        return False
    return now > expected_completion + MIN_INTERVAL
