"""Tests for the timezone-aware weekly-brief schedule (v0 checklist 2.4).

The weekly brief must fire **Monday 09:00 in each company's configured
timezone**. The whole point of app.brief_schedule is that this decision is a
PURE function of (now, tz, last_run) — so we can assert "should it run?" across
multiple timezones AND across a DST boundary WITHOUT waiting for a real Monday.

Coverage:
  - resolve_timezone: default, explicit, malformed/unknown fallback.
  - should_run_weekly_brief across ≥2 timezones (New York + Sydney) — the SAME
    UTC instant is "due" in one tz and "not due" in another, proving the tz
    setting drives the send time.
  - DST: America/New_York 09:00 local maps to 13:00 UTC in winter (EST, UTC-5)
    and 13:00 UTC in summer (EDT, UTC-4) → the UTC fire instant SHIFTS by an
    hour across the transition. We assert both the winter and summer instants.
  - once-per-week guard: last_run inside the window suppresses a second run.
  - next_fire_time / previous_fire_time round-trip + DST step correctness.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.brief_schedule import (
    DEFAULT_TIMEZONE,
    next_fire_time,
    previous_fire_time,
    resolve_timezone,
    should_run_weekly_brief,
)

UTC = timezone.utc


# ── resolve_timezone ─────────────────────────────────────────────────────────


def test_resolve_timezone_defaults_to_utc_when_unset():
    assert resolve_timezone(None).key == DEFAULT_TIMEZONE
    assert resolve_timezone({}).key == DEFAULT_TIMEZONE
    assert resolve_timezone({"timezone": ""}).key == DEFAULT_TIMEZONE
    assert resolve_timezone({"timezone": "   "}).key == DEFAULT_TIMEZONE


def test_resolve_timezone_reads_explicit_iana_name():
    assert resolve_timezone({"timezone": "America/New_York"}).key == "America/New_York"
    assert resolve_timezone({"timezone": "Australia/Sydney"}).key == "Australia/Sydney"
    # whitespace is trimmed
    assert resolve_timezone({"timezone": " Europe/London "}).key == "Europe/London"


def test_resolve_timezone_falls_back_on_unknown_or_bad_type():
    assert resolve_timezone({"timezone": "Mars/Olympus_Mons"}).key == DEFAULT_TIMEZONE
    assert resolve_timezone({"timezone": 1234}).key == DEFAULT_TIMEZONE  # non-string


# ── should_run across ≥2 timezones (tz drives the send time) ──────────────────


def test_due_at_monday_0900_in_each_timezone():
    """Monday 09:00 *local* is due in each tz — verified by feeding the matching
    UTC instant. UTC is the no-DST baseline."""
    ny = ZoneInfo("America/New_York")
    syd = ZoneInfo("Australia/Sydney")

    # 2026-06-08 is a Monday. NY in June is EDT (UTC-4) → 09:00 NY = 13:00 UTC.
    ny_fire = datetime(2026, 6, 8, 13, 0, tzinfo=UTC)
    assert should_run_weekly_brief(ny_fire, ny, None) is True

    # Sydney in June is AEST (UTC+10, no DST in June) → 09:00 Sydney Mon 2026-06-08
    # = 23:00 UTC on Sun 2026-06-07.
    syd_fire = datetime(2026, 6, 7, 23, 0, tzinfo=UTC)
    assert should_run_weekly_brief(syd_fire, syd, None) is True


def test_same_utc_instant_due_in_one_tz_not_another():
    """The defining property: timezone drives the send time. At 13:00 UTC on
    Monday 2026-06-08 it's 09:00 in New York (DUE) but 23:00 in Sydney (NOT)."""
    ny = ZoneInfo("America/New_York")
    syd = ZoneInfo("Australia/Sydney")
    instant = datetime(2026, 6, 8, 13, 0, tzinfo=UTC)

    assert should_run_weekly_brief(instant, ny, None) is True
    assert should_run_weekly_brief(instant, syd, None) is False


def test_not_due_off_monday_or_off_hour():
    ny = ZoneInfo("America/New_York")
    # Tuesday 09:00 NY → not due (wrong day)
    tue = datetime(2026, 6, 9, 13, 0, tzinfo=UTC)
    assert should_run_weekly_brief(tue, ny, None) is False
    # Monday 08:00 NY (before the window opens) → not due
    before = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
    assert should_run_weekly_brief(before, ny, None) is False
    # Monday 10:30 NY (well past the 1h window) → not due
    after = datetime(2026, 6, 8, 14, 30, tzinfo=UTC)
    assert should_run_weekly_brief(after, ny, None) is False


def test_due_within_the_firing_window():
    """A tick a little after 09:00 local (but inside the 1h window) still fires."""
    ny = ZoneInfo("America/New_York")
    # 09:30 NY = 13:30 UTC in June — inside the 1h window.
    assert should_run_weekly_brief(
        datetime(2026, 6, 8, 13, 30, tzinfo=UTC), ny, None
    ) is True


# ── DST correctness ──────────────────────────────────────────────────────────


def test_dst_shifts_the_utc_fire_instant_for_new_york():
    """09:00 America/New_York maps to a DIFFERENT UTC instant in winter vs summer.

    Winter (EST, UTC-5): 09:00 NY = 14:00 UTC.
    Summer (EDT, UTC-4): 09:00 NY = 13:00 UTC.
    A naive fixed-offset scheduler would fire at the wrong local time for half
    the year; the tz-aware logic tracks the offset in effect on each Monday.
    """
    ny = ZoneInfo("America/New_York")

    # Monday 2026-01-12 is in EST (UTC-5). 09:00 NY = 14:00 UTC.
    winter_fire = datetime(2026, 1, 12, 14, 0, tzinfo=UTC)
    assert should_run_weekly_brief(winter_fire, ny, None) is True
    # The summer offset (13:00 UTC) would be 08:00 EST in winter → NOT due.
    assert should_run_weekly_brief(
        datetime(2026, 1, 12, 13, 0, tzinfo=UTC), ny, None
    ) is False

    # Monday 2026-07-06 is in EDT (UTC-4). 09:00 NY = 13:00 UTC.
    summer_fire = datetime(2026, 7, 6, 13, 0, tzinfo=UTC)
    assert should_run_weekly_brief(summer_fire, ny, None) is True
    # The winter offset (14:00 UTC) would be 10:00 EDT in summer → past window.
    assert should_run_weekly_brief(
        datetime(2026, 7, 6, 14, 30, tzinfo=UTC), ny, None
    ) is False


def test_previous_and_next_fire_time_are_dst_correct():
    """previous_fire_time / next_fire_time resolve 09:00 *local* and the 7-day
    step stays at 09:00 local even when it crosses a DST boundary.

    US DST 2026 begins Sunday 2026-03-08. Monday 2026-03-02 is EST (09:00 =
    14:00 UTC); the next Monday 2026-03-09 is EDT (09:00 = 13:00 UTC). A naive
    +7d-of-UTC step would land at 14:00 UTC = 10:00 EDT — wrong. We assert the
    step lands at 13:00 UTC = 09:00 EDT.
    """
    ny = ZoneInfo("America/New_York")
    # A Wednesday between the two Mondays, EST.
    mid_week = datetime(2026, 3, 4, 12, 0, tzinfo=UTC)

    prev = previous_fire_time(mid_week, ny)
    assert prev == datetime(2026, 3, 2, 14, 0, tzinfo=UTC)  # EST Monday 09:00
    assert prev.astimezone(ny).hour == 9

    nxt = next_fire_time(mid_week, ny)
    assert nxt == datetime(2026, 3, 9, 13, 0, tzinfo=UTC)  # EDT Monday 09:00
    assert nxt.astimezone(ny).hour == 9  # still 09:00 local, not 10:00


def test_next_fire_time_is_strictly_after():
    ny = ZoneInfo("America/New_York")
    # Exactly on a fire instant → next is a week later.
    on_fire = datetime(2026, 6, 8, 13, 0, tzinfo=UTC)
    nxt = next_fire_time(on_fire, ny)
    assert nxt > on_fire
    assert nxt == datetime(2026, 6, 15, 13, 0, tzinfo=UTC)
    assert nxt.astimezone(ny).hour == 9


# ── once-per-week guard ──────────────────────────────────────────────────────


def test_already_ran_this_week_is_not_due_again():
    """A second tick inside the same window must NOT re-fire once last_run is
    recorded for this week's fire instant."""
    ny = ZoneInfo("America/New_York")
    first_tick = datetime(2026, 6, 8, 13, 0, tzinfo=UTC)   # 09:00 NY → due
    assert should_run_weekly_brief(first_tick, ny, None) is True

    # Ledger now records the run; a later tick in the same window is suppressed.
    second_tick = datetime(2026, 6, 8, 13, 45, tzinfo=UTC)  # 09:45 NY, still window
    assert should_run_weekly_brief(second_tick, ny, first_tick) is False


def test_last_run_in_a_prior_week_does_not_suppress_this_week():
    ny = ZoneInfo("America/New_York")
    last_week = datetime(2026, 6, 1, 13, 0, tzinfo=UTC)    # previous Monday 09:00
    this_week = datetime(2026, 6, 8, 13, 0, tzinfo=UTC)    # this Monday 09:00
    assert should_run_weekly_brief(this_week, ny, last_week) is True


def test_naive_datetimes_are_treated_as_utc():
    """The scheduler passes datetime.now(timezone.utc), but the pure functions
    tolerate naive (assumed-UTC) inputs too, so tests can be terse."""
    ny = ZoneInfo("America/New_York")
    assert should_run_weekly_brief(datetime(2026, 6, 8, 13, 0), ny, None) is True
