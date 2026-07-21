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
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

# Sensible default when a company hasn't configured a timezone. UTC is the safe,
# DST-free choice — every company still gets a deterministic Monday-06:00 brief.
DEFAULT_TIMEZONE = "UTC"

# The brief fires Monday at 06:00 local time. Monday is weekday() == 0.
BRIEF_WEEKDAY = 0  # Monday
BRIEF_HOUR = 6
BRIEF_MINUTE = 0

# The configurable send day is Mon–Fri only (0-4). The brief is a work
# artefact, so the settings page stopped offering Saturday/Sunday; values
# outside this range — including rows written while the weekend was still
# selectable — resolve to BRIEF_WEEKDAY (Monday).
MAX_WEEKDAY = 4  # Friday

# ── Cadence ────────────────────────────────────────────────────────────────
# How often the brief fires. The configured weekday/hour/minute still apply
# (except for DAILY_WEEKDAYS, where the weekday is meaningless and ignored):
#
#   WEEKLY          — every week on `weekday`. The default, and the only
#                     behaviour that existed before this setting, so every
#                     pre-existing company resolves to it.
#   DAILY_WEEKDAYS  — Monday..Friday, no weekends. `weekday` is ignored.
#   BIWEEKLY        — every OTHER week on `weekday` (a 14-day cadence).
#   MONTHLY         — the FIRST `weekday` of each month (e.g. "Mondays" ⇒ the
#                     first Monday), i.e. a fire day whose day-of-month ≤ 7.
FREQ_DAILY_WEEKDAYS = "daily_weekdays"
FREQ_WEEKLY = "weekly"
FREQ_BIWEEKLY = "biweekly"
FREQ_MONTHLY = "monthly"
FREQUENCIES = (FREQ_DAILY_WEEKDAYS, FREQ_WEEKLY, FREQ_BIWEEKLY, FREQ_MONTHLY)
BRIEF_FREQUENCY = FREQ_WEEKLY

# BIWEEKLY needs an anchor to be deterministic — "every other week" is only
# well defined relative to a known ON week. The settings page stamps
# `brief_anchor_date` (the local date of the first fire after the setting was
# saved) whenever the cadence is saved. When it is missing or unparseable we
# fall back to the Unix-epoch Monday (1970-01-05), which is deterministic,
# stable across processes, and needs no storage — so a company that somehow
# ends up on BIWEEKLY without an anchor still gets a fixed, repeatable cadence
# rather than one that drifts with the clock.
DEFAULT_ANCHOR = date(1970, 1, 5)  # a Monday

# How far previous/next fire-time search walks. The sparsest cadence is
# MONTHLY, whose largest gap between consecutive fire days is 35 days (first
# Monday of one month to the first Monday of the next). 60 gives comfortable
# headroom while keeping the scan a trivial integer loop.
_SEARCH_DAYS = 60

# How long BEFORE the delivery time generation kicks off. Generation (KG seed +
# synthesis LLM calls) takes minutes and can queue behind other companies, so it
# starts with a head start; delivery then happens exactly at the configured
# time, never before (see app.scheduler's generate-then-deliver split).
GENERATION_LEAD = timedelta(hours=3)

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


def resolve_schedule(notification_settings: dict | None) -> tuple[int, int, int]:
    """Resolve ``(weekday, hour, minute)`` for the weekly brief from a company's
    ``notification_settings`` JSONB, falling back to the Monday-06:00 defaults.

    Users pick the brief's day + time on the Comms & Brief settings page, which
    writes ``brief_weekday`` (0=Mon..6=Sun), ``brief_hour`` (0-23) and
    ``brief_minute`` (0-59). Each is validated independently — an out-of-range or
    non-int value falls back to its default rather than wedging the schedule, so
    a bad write can't stop a tenant's brief from ever firing.

    ``brief_weekday`` is additionally clamped to Mon–Fri (0-4). The settings
    page no longer offers a weekend — the brief is a work artefact, so a
    Saturday send has no audience — and rows written before that rule can still
    hold 5/6. Those resolve to Monday (:data:`BRIEF_WEEKDAY`), matching the
    coercion the settings page applies on load, so the schedule the scheduler
    honours and the one the UI displays can never disagree.
    """
    ns = notification_settings if isinstance(notification_settings, dict) else {}

    def _int_in(key: str, default: int, lo: int, hi: int) -> int:
        v = ns.get(key)
        if isinstance(v, bool) or not isinstance(v, int):
            return default
        return v if lo <= v <= hi else default

    return (
        _int_in("brief_weekday", BRIEF_WEEKDAY, 0, MAX_WEEKDAY),
        _int_in("brief_hour", BRIEF_HOUR, 0, 23),
        _int_in("brief_minute", BRIEF_MINUTE, 0, 59),
    )


def resolve_frequency(notification_settings: dict | None) -> str:
    """Resolve the brief cadence from a company's ``notification_settings``.

    Reads ``brief_frequency``; anything missing, non-string or unrecognised
    falls back to :data:`BRIEF_FREQUENCY` (weekly). That fallback is what makes
    this change a no-op for every pre-existing company: rows written before the
    cadence setting existed have no ``brief_frequency`` key and keep firing
    weekly exactly as before.
    """
    ns = notification_settings if isinstance(notification_settings, dict) else {}
    raw = ns.get("brief_frequency")
    if isinstance(raw, str) and raw in FREQUENCIES:
        return raw
    return BRIEF_FREQUENCY


def resolve_anchor(notification_settings: dict | None) -> date:
    """Resolve the BIWEEKLY anchor date from ``notification_settings``.

    Reads ``brief_anchor_date`` as an ISO ``YYYY-MM-DD`` string. Missing or
    unparseable values fall back to :data:`DEFAULT_ANCHOR`. Only consulted for
    :data:`FREQ_BIWEEKLY`; the other cadences are anchor-free by construction.
    """
    ns = notification_settings if isinstance(notification_settings, dict) else {}
    raw = ns.get("brief_anchor_date")
    if isinstance(raw, str) and raw.strip():
        try:
            return date.fromisoformat(raw.strip()[:10])
        except ValueError:
            logger.warning(
                "brief-schedule: unparseable brief_anchor_date %r — falling back to %s",
                raw, DEFAULT_ANCHOR.isoformat(),
            )
    return DEFAULT_ANCHOR


def is_fire_day(
    day: date,
    *,
    weekday: int = BRIEF_WEEKDAY,
    frequency: str = BRIEF_FREQUENCY,
    anchor: date | None = None,
) -> bool:
    """Does the brief fire on this LOCAL calendar date?

    The single source of truth for cadence, shared by every fire-time search
    below (and mirrored one-for-one by the web settings preview). Pure: date in,
    bool out — no clock, no timezone, no DST (the *time of day* is applied by
    the caller, which is where DST is resolved).

    BIWEEKLY uses ``(day - anchor).days // 7 % 2 == 0`` rather than
    ``% 14 == 0``: floor-dividing into whole weeks first means the rule still
    alternates correctly when the user later changes the weekday, which makes
    the offset from the anchor no longer a clean multiple of 7. Floor division
    also alternates consistently for dates BEFORE the anchor, so
    :func:`previous_fire_time` stays coherent for a freshly-saved schedule.
    """
    if frequency == FREQ_DAILY_WEEKDAYS:
        return day.weekday() <= 4  # Mon..Fri
    if day.weekday() != weekday:
        return False
    if frequency == FREQ_BIWEEKLY:
        return ((day - (anchor or DEFAULT_ANCHOR)).days // 7) % 2 == 0
    if frequency == FREQ_MONTHLY:
        return day.day <= 7  # the FIRST `weekday` of the month
    return True  # FREQ_WEEKLY


def _as_utc(dt: datetime) -> datetime:
    """Coerce a datetime to an aware UTC datetime.

    A naive datetime is *assumed* to already be UTC (the scheduler always passes
    ``datetime.now(timezone.utc)``); an aware one is converted. This keeps the
    pure functions forgiving of either convention in tests.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def previous_fire_time(
    now: datetime,
    tz: ZoneInfo,
    *,
    weekday: int = BRIEF_WEEKDAY,
    hour: int = BRIEF_HOUR,
    minute: int = BRIEF_MINUTE,
    frequency: str = BRIEF_FREQUENCY,
    anchor: date | None = None,
) -> datetime:
    """The most recent ``hour``:``minute``-local fire instant at or before
    ``now`` for this cadence (defaults to weekly Monday 06:00).

    Returned as an aware UTC datetime. DST-correct: the fire time is resolved in
    ``tz`` first, then converted to UTC, so the UTC offset tracks whatever rule
    is in effect on that day (e.g. America/New_York is UTC-5 in winter, UTC-4
    in summer — the same local time maps to different UTC instants).

    Implemented as a backwards scan over local calendar dates asking
    :func:`is_fire_day`. A scan (rather than the closed-form week arithmetic
    this used to do) is what lets one code path serve all four cadences,
    including the irregular ones (first-Monday-of-month has no constant
    period). ``_SEARCH_DAYS`` bounds it well beyond the sparsest gap.
    """
    now_utc = _as_utc(now)
    local_now = now_utc.astimezone(tz)
    today = local_now.date()

    for back in range(_SEARCH_DAYS):
        day = today - timedelta(days=back)
        if not is_fire_day(day, weekday=weekday, frequency=frequency, anchor=anchor):
            continue
        fire_local = datetime.combine(day, time(hour, minute), tzinfo=tz)
        if fire_local <= local_now:
            return fire_local.astimezone(timezone.utc)

    # Unreachable for the supported cadences (see _SEARCH_DAYS). Degrade to a
    # far-past instant so callers read "nothing is due" rather than raising.
    return now_utc - timedelta(days=_SEARCH_DAYS)


def next_fire_time(
    after: datetime,
    tz: ZoneInfo,
    *,
    weekday: int = BRIEF_WEEKDAY,
    hour: int = BRIEF_HOUR,
    minute: int = BRIEF_MINUTE,
    frequency: str = BRIEF_FREQUENCY,
    anchor: date | None = None,
) -> datetime:
    """The next ``hour``:``minute``-local fire instant strictly after ``after``
    for this cadence (defaults to weekly Monday 06:00).

    Aware UTC datetime, DST-correct: each candidate is built as a LOCAL
    datetime and then converted, so a cadence spanning a DST transition keeps
    the same wall-clock send time instead of drifting an hour (which a fixed
    +7-day UTC step would do). Drives "when will this company's brief next
    run?" surfaces and is the inverse of :func:`previous_fire_time` in tests.
    """
    after_utc = _as_utc(after)
    local_after = after_utc.astimezone(tz)
    today = local_after.date()

    for fwd in range(_SEARCH_DAYS):
        day = today + timedelta(days=fwd)
        if not is_fire_day(day, weekday=weekday, frequency=frequency, anchor=anchor):
            continue
        fire_local = datetime.combine(day, time(hour, minute), tzinfo=tz)
        if fire_local > local_after:
            return fire_local.astimezone(timezone.utc)

    # Unreachable for the supported cadences (see _SEARCH_DAYS).
    return after_utc + timedelta(days=_SEARCH_DAYS)


def should_run_weekly_brief(
    now: datetime,
    tz: ZoneInfo,
    last_run: datetime | None,
    *,
    weekday: int = BRIEF_WEEKDAY,
    hour: int = BRIEF_HOUR,
    minute: int = BRIEF_MINUTE,
    frequency: str = BRIEF_FREQUENCY,
    anchor: date | None = None,
    window: timedelta = DUE_WINDOW,
) -> bool:
    """Pure decision: is this company's brief due right now?

    A brief is due when BOTH hold:

      1. ``now`` is within ``window`` after the most recent fire instant for
         this cadence (i.e. we're inside the firing window for *this* cycle),
         and
      2. the brief hasn't already run for that fire instant (``last_run`` is
         None, or strictly before the most recent fire).

    This is the assertable heart of the feature: callers can feed any ``now`` +
    ``tz`` + ``last_run`` and get a deterministic yes/no without waiting for a
    real Monday. The once-per-cycle guard means a tick storm inside the window
    still produces exactly one run (the first tick records ``last_run``).

    Because the guard compares against the most recent fire instant rather than
    a fixed 7-day period, it holds for every cadence unchanged: on
    DAILY_WEEKDAYS yesterday's ``last_run`` is older than today's fire, so
    today is due; on MONTHLY last month's is older than this month's.

    ``last_run`` is the instant the brief was last generated for this company
    (aware or naive-UTC). ``now`` is "right now" (aware or naive-UTC).
    """
    now_utc = _as_utc(now)
    fire = previous_fire_time(
        now_utc, tz, weekday=weekday, hour=hour, minute=minute,
        frequency=frequency, anchor=anchor,
    )

    # Outside the firing window for this cycle → not due.
    if not (fire <= now_utc <= fire + window):
        return False

    # Already ran for (or after) this cycle's fire → not due again.
    if last_run is not None and _as_utc(last_run) >= fire:
        return False

    return True


def generation_start_time(
    now: datetime,
    tz: ZoneInfo,
    *,
    weekday: int = BRIEF_WEEKDAY,
    hour: int = BRIEF_HOUR,
    minute: int = BRIEF_MINUTE,
    frequency: str = BRIEF_FREQUENCY,
    anchor: date | None = None,
    lead: timedelta = GENERATION_LEAD,
) -> datetime:
    """The most recent generation-start instant (delivery fire time minus
    ``lead``) at or before ``now``, as an aware UTC datetime.

    The delivery fire time is resolved DST-correctly in ``tz`` (like
    :func:`previous_fire_time`); the lead is then a plain UTC offset — "start 3
    hours before delivery" means 3 real hours, whatever the local clock does.
    Shifting ``now`` forward by ``lead`` before asking for the previous fire
    makes the returned instant the generation start whose window ``now`` can be
    inside, even though its delivery fire is still in the future.
    """
    fire = previous_fire_time(
        _as_utc(now) + lead, tz, weekday=weekday, hour=hour, minute=minute,
        frequency=frequency, anchor=anchor,
    )
    return fire - lead


def should_generate_weekly_brief(
    now: datetime,
    tz: ZoneInfo,
    last_generation: datetime | None,
    *,
    weekday: int = BRIEF_WEEKDAY,
    hour: int = BRIEF_HOUR,
    minute: int = BRIEF_MINUTE,
    frequency: str = BRIEF_FREQUENCY,
    anchor: date | None = None,
    lead: timedelta = GENERATION_LEAD,
    window: timedelta = DUE_WINDOW,
) -> bool:
    """Pure decision: should this company's brief START GENERATING now?

    The mirror of :func:`should_run_weekly_brief`, shifted ``lead`` earlier:
    generation is due when ``now`` is within ``window`` after (fire − ``lead``)
    and generation hasn't already run for this cycle. Generating early gives the
    brief time to synthesize so delivery can happen exactly at the configured
    fire time (delivery itself is decided separately, never before the fire).

    ``last_generation`` is the instant generation last started for this company
    (aware or naive-UTC), the once-per-cycle guard.
    """
    now_utc = _as_utc(now)
    gen_start = generation_start_time(
        now_utc, tz, weekday=weekday, hour=hour, minute=minute,
        frequency=frequency, anchor=anchor, lead=lead,
    )

    if not (gen_start <= now_utc <= gen_start + window):
        return False

    if last_generation is not None and _as_utc(last_generation) >= gen_start:
        return False

    return True
