"""Tests for the timezone-aware weekly-brief schedule (v0 checklist 2.4).

The weekly brief must fire **Monday 06:00 in the company owner's timezone**. The
whole point of app.brief_schedule is that this decision is a PURE function of
(now, tz, last_run) — so we can assert "should it run?" across multiple
timezones AND across a DST boundary WITHOUT waiting for a real Monday.

Coverage:
  - resolve_user_timezone: default, explicit, malformed/unknown fallback (the
    per-user entry point the scheduler feeds profiles.timezone into).
  - resolve_timezone: legacy company-level wrapper still resolves the same way.
  - should_run_weekly_brief across ≥2 timezones (New York + Sydney) — the SAME
    UTC instant is "due" in one tz and "not due" in another, proving the tz
    setting drives the send time.
  - DST: America/New_York 06:00 local maps to 11:00 UTC in winter (EST, UTC-5)
    and 10:00 UTC in summer (EDT, UTC-4) → the UTC fire instant SHIFTS by an
    hour across the transition. We assert both the winter and summer instants.
  - once-per-week guard: last_run inside the window suppresses a second run.
  - next_fire_time / previous_fire_time round-trip + DST step correctness.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.brief_schedule import (
    DEFAULT_TIMEZONE,
    next_fire_time,
    previous_fire_time,
    resolve_schedule,
    resolve_timezone,
    resolve_user_timezone,
    should_run_weekly_brief,
)

UTC = timezone.utc


# ── resolve_schedule (user-chosen day/time from notification_settings) ───────


def test_resolve_schedule_defaults_to_monday_0600():
    assert resolve_schedule(None) == (0, 6, 0)
    assert resolve_schedule({}) == (0, 6, 0)


def test_resolve_schedule_reads_valid_values():
    ns = {"brief_weekday": 3, "brief_hour": 14, "brief_minute": 30}
    assert resolve_schedule(ns) == (3, 14, 30)


def test_resolve_schedule_falls_back_per_field_on_bad_values():
    # Out-of-range / wrong-type each fall back to their own default, not all-or-nothing.
    assert resolve_schedule({"brief_weekday": 9}) == (0, 6, 0)
    assert resolve_schedule({"brief_hour": 25, "brief_weekday": 2}) == (2, 6, 0)
    assert resolve_schedule({"brief_minute": -1}) == (0, 6, 0)
    assert resolve_schedule({"brief_hour": "8"}) == (0, 6, 0)  # str, not int
    assert resolve_schedule({"brief_weekday": True}) == (0, 6, 0)  # bool rejected


def test_configurable_day_time_shifts_the_fire_window():
    """A company that picks Wednesday 14:00 is due then — not Monday 06:00."""
    tz = ZoneInfo("America/New_York")
    weekday, hour, minute = resolve_schedule(
        {"brief_weekday": 2, "brief_hour": 14, "brief_minute": 0}  # Wed 14:00
    )
    # Wed 2026-07-01 14:30 local (within the 1h window after 14:00) → due.
    wed = datetime(2026, 7, 1, 18, 30, tzinfo=UTC)  # 14:30 EDT
    assert should_run_weekly_brief(
        wed, tz, None, weekday=weekday, hour=hour, minute=minute
    )
    # The old Monday-06:00 instant is NOT due under the new schedule.
    mon = datetime(2026, 6, 29, 10, 30, tzinfo=UTC)  # Mon 06:30 EDT
    assert not should_run_weekly_brief(
        mon, tz, None, weekday=weekday, hour=hour, minute=minute
    )


def test_next_fire_time_honors_custom_day_time():
    tz = ZoneInfo("UTC")
    # From Mon 2026-06-29, the next Wed-14:00 fire is Wed 2026-07-01 14:00 UTC.
    after = datetime(2026, 6, 29, 0, 0, tzinfo=UTC)
    nxt = next_fire_time(after, tz, weekday=2, hour=14, minute=0)
    assert nxt == datetime(2026, 7, 1, 14, 0, tzinfo=UTC)


# ── resolve_user_timezone (per-user entry point) ─────────────────────────────


def test_resolve_user_timezone_defaults_to_utc_when_unset():
    assert resolve_user_timezone(None).key == DEFAULT_TIMEZONE
    assert resolve_user_timezone("").key == DEFAULT_TIMEZONE
    assert resolve_user_timezone("   ").key == DEFAULT_TIMEZONE
    assert resolve_user_timezone(1234).key == DEFAULT_TIMEZONE  # non-string


def test_resolve_user_timezone_reads_explicit_iana_name():
    assert resolve_user_timezone("America/New_York").key == "America/New_York"
    assert resolve_user_timezone("Australia/Sydney").key == "Australia/Sydney"
    # whitespace is trimmed
    assert resolve_user_timezone(" Europe/London ").key == "Europe/London"


def test_resolve_user_timezone_falls_back_on_unknown():
    assert resolve_user_timezone("Mars/Olympus_Mons").key == DEFAULT_TIMEZONE


# ── resolve_timezone (legacy company-level wrapper) ──────────────────────────


def test_resolve_timezone_defaults_to_utc_when_unset():
    assert resolve_timezone(None).key == DEFAULT_TIMEZONE
    assert resolve_timezone({}).key == DEFAULT_TIMEZONE
    assert resolve_timezone({"timezone": ""}).key == DEFAULT_TIMEZONE


def test_resolve_timezone_reads_and_falls_back():
    assert resolve_timezone({"timezone": "America/New_York"}).key == "America/New_York"
    assert resolve_timezone({"timezone": "Mars/Olympus_Mons"}).key == DEFAULT_TIMEZONE
    assert resolve_timezone({"timezone": 1234}).key == DEFAULT_TIMEZONE  # non-string


# ── should_run across ≥2 timezones (tz drives the send time) ──────────────────


def test_due_at_monday_0600_in_each_timezone():
    """Monday 06:00 *local* is due in each tz — verified by feeding the matching
    UTC instant. UTC is the no-DST baseline."""
    ny = ZoneInfo("America/New_York")
    syd = ZoneInfo("Australia/Sydney")

    # 2026-06-08 is a Monday. NY in June is EDT (UTC-4) → 06:00 NY = 10:00 UTC.
    ny_fire = datetime(2026, 6, 8, 10, 0, tzinfo=UTC)
    assert should_run_weekly_brief(ny_fire, ny, None) is True

    # Sydney in June is AEST (UTC+10, no DST in June) → 06:00 Sydney Mon 2026-06-08
    # = 20:00 UTC on Sun 2026-06-07.
    syd_fire = datetime(2026, 6, 7, 20, 0, tzinfo=UTC)
    assert should_run_weekly_brief(syd_fire, syd, None) is True


def test_same_utc_instant_due_in_one_tz_not_another():
    """The defining property: timezone drives the send time. At 10:00 UTC on
    Monday 2026-06-08 it's 06:00 in New York (DUE) but 20:00 in Sydney (NOT)."""
    ny = ZoneInfo("America/New_York")
    syd = ZoneInfo("Australia/Sydney")
    instant = datetime(2026, 6, 8, 10, 0, tzinfo=UTC)

    assert should_run_weekly_brief(instant, ny, None) is True
    assert should_run_weekly_brief(instant, syd, None) is False


def test_not_due_off_monday_or_off_hour():
    ny = ZoneInfo("America/New_York")
    # Tuesday 06:00 NY → not due (wrong day)
    tue = datetime(2026, 6, 9, 10, 0, tzinfo=UTC)
    assert should_run_weekly_brief(tue, ny, None) is False
    # Monday 05:00 NY (before the window opens) → not due
    before = datetime(2026, 6, 8, 9, 0, tzinfo=UTC)
    assert should_run_weekly_brief(before, ny, None) is False
    # Monday 07:30 NY (well past the 1h window) → not due
    after = datetime(2026, 6, 8, 11, 30, tzinfo=UTC)
    assert should_run_weekly_brief(after, ny, None) is False


def test_due_within_the_firing_window():
    """A tick a little after 06:00 local (but inside the 1h window) still fires."""
    ny = ZoneInfo("America/New_York")
    # 06:30 NY = 10:30 UTC in June — inside the 1h window.
    assert should_run_weekly_brief(
        datetime(2026, 6, 8, 10, 30, tzinfo=UTC), ny, None
    ) is True


# ── DST correctness ──────────────────────────────────────────────────────────


def test_dst_shifts_the_utc_fire_instant_for_new_york():
    """06:00 America/New_York maps to a DIFFERENT UTC instant in winter vs summer.

    Winter (EST, UTC-5): 06:00 NY = 11:00 UTC.
    Summer (EDT, UTC-4): 06:00 NY = 10:00 UTC.
    A naive fixed-offset scheduler would fire at the wrong local time for half
    the year; the tz-aware logic tracks the offset in effect on each Monday.
    """
    ny = ZoneInfo("America/New_York")

    # Monday 2026-01-12 is in EST (UTC-5). 06:00 NY = 11:00 UTC.
    winter_fire = datetime(2026, 1, 12, 11, 0, tzinfo=UTC)
    assert should_run_weekly_brief(winter_fire, ny, None) is True
    # The summer offset (10:00 UTC) would be 05:00 EST in winter → before window.
    assert should_run_weekly_brief(
        datetime(2026, 1, 12, 10, 0, tzinfo=UTC), ny, None
    ) is False

    # Monday 2026-07-06 is in EDT (UTC-4). 06:00 NY = 10:00 UTC.
    summer_fire = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)
    assert should_run_weekly_brief(summer_fire, ny, None) is True
    # The winter offset (11:00 UTC) would be 07:00 EDT in summer → past window.
    assert should_run_weekly_brief(
        datetime(2026, 7, 6, 11, 30, tzinfo=UTC), ny, None
    ) is False


def test_previous_and_next_fire_time_are_dst_correct():
    """previous_fire_time / next_fire_time resolve 06:00 *local* and the 7-day
    step stays at 06:00 local even when it crosses a DST boundary.

    US DST 2026 begins Sunday 2026-03-08. Monday 2026-03-02 is EST (06:00 =
    11:00 UTC); the next Monday 2026-03-09 is EDT (06:00 = 10:00 UTC). A naive
    +7d-of-UTC step would land at 11:00 UTC = 07:00 EDT — wrong. We assert the
    step lands at 10:00 UTC = 06:00 EDT.
    """
    ny = ZoneInfo("America/New_York")
    # A Wednesday between the two Mondays, EST.
    mid_week = datetime(2026, 3, 4, 12, 0, tzinfo=UTC)

    prev = previous_fire_time(mid_week, ny)
    assert prev == datetime(2026, 3, 2, 11, 0, tzinfo=UTC)  # EST Monday 06:00
    assert prev.astimezone(ny).hour == 6

    nxt = next_fire_time(mid_week, ny)
    assert nxt == datetime(2026, 3, 9, 10, 0, tzinfo=UTC)  # EDT Monday 06:00
    assert nxt.astimezone(ny).hour == 6  # still 06:00 local, not 07:00


def test_next_fire_time_is_strictly_after():
    ny = ZoneInfo("America/New_York")
    # Exactly on a fire instant → next is a week later.
    on_fire = datetime(2026, 6, 8, 10, 0, tzinfo=UTC)
    nxt = next_fire_time(on_fire, ny)
    assert nxt > on_fire
    assert nxt == datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
    assert nxt.astimezone(ny).hour == 6


# ── once-per-week guard ──────────────────────────────────────────────────────


def test_already_ran_this_week_is_not_due_again():
    """A second tick inside the same window must NOT re-fire once last_run is
    recorded for this week's fire instant."""
    ny = ZoneInfo("America/New_York")
    first_tick = datetime(2026, 6, 8, 10, 0, tzinfo=UTC)   # 06:00 NY → due
    assert should_run_weekly_brief(first_tick, ny, None) is True

    # Ledger now records the run; a later tick in the same window is suppressed.
    second_tick = datetime(2026, 6, 8, 10, 45, tzinfo=UTC)  # 06:45 NY, still window
    assert should_run_weekly_brief(second_tick, ny, first_tick) is False


def test_last_run_in_a_prior_week_does_not_suppress_this_week():
    ny = ZoneInfo("America/New_York")
    last_week = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)    # previous Monday 06:00
    this_week = datetime(2026, 6, 8, 10, 0, tzinfo=UTC)    # this Monday 06:00
    assert should_run_weekly_brief(this_week, ny, last_week) is True


def test_naive_datetimes_are_treated_as_utc():
    """The scheduler passes datetime.now(timezone.utc), but the pure functions
    tolerate naive (assumed-UTC) inputs too, so tests can be terse."""
    ny = ZoneInfo("America/New_York")
    assert should_run_weekly_brief(datetime(2026, 6, 8, 10, 0), ny, None) is True
