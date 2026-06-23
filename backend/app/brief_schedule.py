"""Timezone-aware schedule logic for the weekly brief (v0 checklist 2.4).

The weekly brief should generate automatically **Monday 06:00 in the company
owner's timezone**. The scheduler historically fired the whole pipeline every
``PIPELINE_INTERVAL_HOURS`` (~6h) regardless of day/time/timezone, so a company
in Sydney and a company in Los Angeles got identical, wall-clock-blind cadences.

This module is the *pure* core of the new schedule: no DB, no APScheduler, no
clock of its own — every function takes ``now``/``after`` explicitly. That makes
"should this company's brief run right now?" assertable in a unit test WITHOUT
waiting until Monday, and makes DST correctness verifiable by simply feeding in
the right instants. ``app.scheduler`` is the thin impure shell that ticks a
clock and drives these functions per company.

Timezone resolution: each user carries an optional ``profiles.timezone`` IANA
name (e.g. ``"America/New_York"``), captured at signup from the browser and
editable in settings. The brief is company-scoped, so the scheduler resolves the
timezone of the company **owner** via :func:`resolve_user_timezone`. When the
owner's timezone is absent, malformed, or unknown, we fall back to
:data:`DEFAULT_TIMEZONE` (UTC) so a misconfigured tenant still gets a
(deterministic) Monday-06:00 brief rather than silently never running.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

# Sensible default when a company hasn't configured a timezone. UTC is the safe,
# DST-free choice — every company still gets a deterministic Monday-06:00 brief.
DEFAULT_TIMEZONE = "UTC"

# The brief fires Monday at 06:00 local time. Monday is weekday() == 0.
BRIEF_WEEKDAY = 0  # Monday
BRIEF_HOUR = 6
BRIEF_MINUTE = 0

# How wide a window after the nominal fire time still counts as "due". The
# scheduler ticks on an interval (not exactly at :00), and a tick can be a little
# late under load, so a company is "due" if local now is within this window after
# Monday 06:00 AND the brief hasn't already run this week. Kept comfortably wider
# than the scheduler tick so a brief is never missed, while the once-per-week
# guard (last_run) prevents a second run inside the same window.
DUE_WINDOW = timedelta(hours=1)


def resolve_user_timezone(name: str | None) -> ZoneInfo:
    """Resolve a :class:`ZoneInfo` from a raw IANA timezone name.

    This is the per-user entry point: the scheduler passes the company owner's
    ``profiles.timezone`` string straight in. Falls back to
    :data:`DEFAULT_TIMEZONE` when the name is missing, blank, non-string, or
    isn't a known zone (so a typo can't wedge a tenant's brief). The unknown-zone
    case is logged at WARNING since that's a likely misconfiguration worth
    surfacing.
    """
    if not isinstance(name, str) or not name.strip():
        return ZoneInfo(DEFAULT_TIMEZONE)
    name = name.strip()

    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "brief-schedule: unknown timezone %r — falling back to %s",
            name, DEFAULT_TIMEZONE,
        )
        return ZoneInfo(DEFAULT_TIMEZONE)


def resolve_timezone(notification_settings: dict | None) -> ZoneInfo:
    """Resolve a :class:`ZoneInfo` from a company's ``notification_settings``.

    Legacy company-level resolver, kept for back-compat. Reads the optional
    ``timezone`` IANA name out of the JSONB and delegates to
    :func:`resolve_user_timezone`. New callers should resolve from the owner's
    ``profiles.timezone`` instead.
    """
    raw = None
    if isinstance(notification_settings, dict):
        raw = notification_settings.get("timezone")
    return resolve_user_timezone(raw if isinstance(raw, str) else None)


def _as_utc(dt: datetime) -> datetime:
    """Coerce a datetime to an aware UTC datetime.

    A naive datetime is *assumed* to already be UTC (the scheduler always passes
    ``datetime.now(timezone.utc)``); an aware one is converted. This keeps the
    pure functions forgiving of either convention in tests.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def previous_fire_time(now: datetime, tz: ZoneInfo) -> datetime:
    """The most recent Monday-06:00-local fire instant at or before ``now``.

    Returned as an aware UTC datetime. DST-correct: 06:00 *local* is resolved in
    ``tz`` first, then converted to UTC, so the UTC offset tracks whatever rule
    is in effect on that Monday (e.g. America/New_York is UTC-5 in winter, UTC-4
    in summer — the same 06:00 local maps to different UTC instants).
    """
    now_utc = _as_utc(now)
    local_now = now_utc.astimezone(tz)

    # Local midnight of the current local day, then walk back to Monday.
    local_day = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    days_since_monday = local_day.weekday()  # Mon=0 .. Sun=6
    this_week_monday = local_day - timedelta(days=days_since_monday)
    fire_local = datetime.combine(
        this_week_monday.date(), time(BRIEF_HOUR, BRIEF_MINUTE), tzinfo=tz
    )

    # If we're earlier in the week than this Monday's 06:00 (e.g. it's Monday
    # 05:00, or the local clock landed before the nominal time), the most recent
    # fire was last Monday.
    if fire_local > local_now:
        fire_local -= timedelta(days=7)
    return fire_local.astimezone(timezone.utc)


def next_fire_time(after: datetime, tz: ZoneInfo) -> datetime:
    """The next Monday-06:00-local fire instant strictly after ``after``.

    Aware UTC datetime, DST-correct (06:00 local is resolved in ``tz`` then
    converted). Useful for "when will this company's brief next run?" surfaces
    and as the inverse of :func:`previous_fire_time` in tests.
    """
    prev = previous_fire_time(after, tz)
    # prev is the most recent fire <= after; the next one is exactly 7 local days
    # later. Resolve in local time so the 7-day step crosses DST boundaries
    # correctly (a +7-day UTC step would drift by an hour across a transition).
    prev_local = prev.astimezone(tz)
    nxt_local = (prev_local + timedelta(days=7)).replace(
        hour=BRIEF_HOUR, minute=BRIEF_MINUTE, second=0, microsecond=0
    )
    nxt = nxt_local.astimezone(timezone.utc)
    after_utc = _as_utc(after)
    # Guard: if `after` sits exactly on a fire instant, previous_fire_time returns
    # it and +7d is correct; if `after` is just before this week's fire, prev is
    # last week's and +7d is this week's — still strictly after. Belt-and-braces.
    if nxt <= after_utc:
        nxt_local = (nxt_local + timedelta(days=7)).replace(
            hour=BRIEF_HOUR, minute=BRIEF_MINUTE, second=0, microsecond=0
        )
        nxt = nxt_local.astimezone(timezone.utc)
    return nxt


def should_run_weekly_brief(
    now: datetime,
    tz: ZoneInfo,
    last_run: datetime | None,
    *,
    window: timedelta = DUE_WINDOW,
) -> bool:
    """Pure decision: is this company's weekly brief due right now?

    A brief is due when BOTH hold:

      1. ``now`` is within ``window`` after the most recent Monday-06:00-local
         fire instant (i.e. we're inside the firing window for *this* week's
         brief), and
      2. the brief hasn't already run for this week's fire instant
         (``last_run`` is None, or strictly before the most recent fire).

    This is the assertable heart of the feature: callers can feed any ``now`` +
    ``tz`` + ``last_run`` and get a deterministic yes/no without waiting for a
    real Monday. The once-per-week guard means a tick storm inside the window
    still produces exactly one run (the first tick records ``last_run``).

    ``last_run`` is the instant the brief was last generated for this company
    (aware or naive-UTC). ``now`` is "right now" (aware or naive-UTC).
    """
    now_utc = _as_utc(now)
    fire = previous_fire_time(now_utc, tz)

    # Outside the firing window for this week → not due.
    if not (fire <= now_utc <= fire + window):
        return False

    # Already ran for (or after) this week's fire → not due again.
    if last_run is not None and _as_utc(last_run) >= fire:
        return False

    return True
