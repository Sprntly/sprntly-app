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

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.brief_schedule import (
    DEFAULT_TIMEZONE,
    GENERATION_LEAD,
    generation_start_time,
    next_fire_time,
    previous_fire_time,
    resolve_schedule,
    resolve_timezone,
    resolve_user_timezone,
    should_generate_weekly_brief,
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


# ── generation lead: start GENERATION_LEAD (3h) before the fire time ─────────


def test_generation_lead_is_three_hours():
    assert GENERATION_LEAD == timedelta(hours=3)


def test_generation_start_time_is_lead_before_the_fire():
    """NY fires Monday 06:00 EDT = 10:00 UTC; generation starts 07:00 UTC. The
    start instant is returned even while its fire is still in the FUTURE."""
    ny = ZoneInfo("America/New_York")
    now = datetime(2026, 6, 8, 7, 30, tzinfo=UTC)  # inside the lead window
    assert generation_start_time(now, ny) == datetime(2026, 6, 8, 7, 0, tzinfo=UTC)


def test_generation_due_3h_before_fire_but_not_earlier():
    ny = ZoneInfo("America/New_York")
    # 07:00 UTC Monday = fire (10:00 UTC) − 3h → generation due.
    assert should_generate_weekly_brief(
        datetime(2026, 6, 8, 7, 0, tzinfo=UTC), ny, None) is True
    # 06:30 UTC — before the lead window opens → not due.
    assert should_generate_weekly_brief(
        datetime(2026, 6, 8, 6, 30, tzinfo=UTC), ny, None) is False
    # 08:30 UTC — past the (1h) window → not due; the fallback path owns late.
    assert should_generate_weekly_brief(
        datetime(2026, 6, 8, 8, 30, tzinfo=UTC), ny, None) is False


def test_generation_not_due_at_the_fire_time_itself():
    """At the delivery instant the lead window is long closed — generation must
    have happened earlier, delivery is a separate decision."""
    ny = ZoneInfo("America/New_York")
    assert should_generate_weekly_brief(
        datetime(2026, 6, 8, 10, 0, tzinfo=UTC), ny, None) is False


def test_generation_once_per_cycle_guard():
    ny = ZoneInfo("America/New_York")
    first = datetime(2026, 6, 8, 7, 0, tzinfo=UTC)
    assert should_generate_weekly_brief(first, ny, None) is True
    # A later tick in the same window, ledger recorded → suppressed.
    assert should_generate_weekly_brief(
        datetime(2026, 6, 8, 7, 45, tzinfo=UTC), ny, first) is False
    # Last cycle's generation does not suppress this cycle's.
    last_week = datetime(2026, 6, 1, 7, 0, tzinfo=UTC)
    assert should_generate_weekly_brief(first, ny, last_week) is True


def test_generation_honors_custom_schedule():
    """A Wednesday-15:00-UTC brief (Comms & Brief settings) generates at
    Wednesday 12:00 UTC."""
    utc = ZoneInfo("UTC")
    kw = {"weekday": 2, "hour": 15, "minute": 0}
    assert should_generate_weekly_brief(
        datetime(2026, 6, 10, 12, 0, tzinfo=UTC), utc, None, **kw) is True
    assert should_generate_weekly_brief(
        datetime(2026, 6, 10, 15, 0, tzinfo=UTC), utc, None, **kw) is False


def test_generation_lead_crosses_local_midnight():
    """A Monday-01:00-local fire generates Sunday 22:00 local — the lead window
    living on the PREVIOUS local day (and weekday) must still resolve."""
    utc = ZoneInfo("UTC")
    kw = {"weekday": 0, "hour": 1, "minute": 0}  # Monday 01:00 UTC
    # Sunday 2026-06-07 22:00 UTC = fire − 3h → due.
    assert should_generate_weekly_brief(
        datetime(2026, 6, 7, 22, 0, tzinfo=UTC), utc, None, **kw) is True
    # Sunday 20:00 UTC → not yet.
    assert should_generate_weekly_brief(
        datetime(2026, 6, 7, 20, 0, tzinfo=UTC), utc, None, **kw) is False


# ── Cadence (brief frequency) ──────────────────────────────────────────────
#
# The Comms settings page lets a company pick daily(weekdays) / weekly /
# biweekly / monthly. These assert the scheduler HONOURS that choice, and —
# critically — that an absent setting behaves EXACTLY like the old weekly-only
# code, so no existing company's schedule shifts as a side effect.
# Mirrors web/app/lib/briefSchedule.ts; keep the two suites in step.
from datetime import date as _date  # noqa: E402

from app.brief_schedule import (  # noqa: E402
    FREQ_BIWEEKLY,
    FREQ_DAILY_WEEKDAYS,
    FREQ_MONTHLY,
    FREQ_WEEKLY,
    is_fire_day,
    resolve_anchor,
    resolve_frequency,
)

UTC_ZONE = ZoneInfo("UTC")


class TestResolveFrequency:
    def test_missing_defaults_to_weekly(self):
        # The whole no-regression guarantee: rows written before this setting
        # existed carry no key and must keep firing weekly.
        assert resolve_frequency(None) == FREQ_WEEKLY
        assert resolve_frequency({}) == FREQ_WEEKLY
        assert resolve_frequency({"brief_hour": 6}) == FREQ_WEEKLY

    def test_unknown_or_wrong_type_falls_back_to_weekly(self):
        assert resolve_frequency({"brief_frequency": "fortnightly"}) == FREQ_WEEKLY
        assert resolve_frequency({"brief_frequency": 3}) == FREQ_WEEKLY
        assert resolve_frequency({"brief_frequency": None}) == FREQ_WEEKLY

    def test_reads_each_supported_value(self):
        for value in (FREQ_DAILY_WEEKDAYS, FREQ_WEEKLY, FREQ_BIWEEKLY, FREQ_MONTHLY):
            assert resolve_frequency({"brief_frequency": value}) == value


class TestResolveAnchor:
    def test_parses_iso_date(self):
        assert resolve_anchor({"brief_anchor_date": "2026-07-20"}) == _date(2026, 7, 20)

    def test_missing_or_bogus_falls_back_to_epoch_monday(self):
        assert resolve_anchor({}) == _date(1970, 1, 5)
        assert resolve_anchor({"brief_anchor_date": "not-a-date"}) == _date(1970, 1, 5)
        assert resolve_anchor({"brief_anchor_date": 20260720}) == _date(1970, 1, 5)


class TestIsFireDay:
    def test_daily_weekdays_skips_the_weekend(self):
        # 2026-07-20 is a Monday.
        fires = [
            is_fire_day(
                _date(2026, 7, 20) + timedelta(days=i),
                weekday=0, frequency=FREQ_DAILY_WEEKDAYS,
            )
            for i in range(7)
        ]
        assert fires == [True, True, True, True, True, False, False]

    def test_weekly_fires_only_on_the_chosen_day(self):
        assert is_fire_day(_date(2026, 7, 22), weekday=2, frequency=FREQ_WEEKLY)  # Wed
        assert not is_fire_day(_date(2026, 7, 23), weekday=2, frequency=FREQ_WEEKLY)

    def test_biweekly_alternates_around_the_anchor(self):
        anchor = _date(2026, 7, 20)

        def at(weeks: int) -> bool:
            return is_fire_day(
                anchor + timedelta(weeks=weeks),
                weekday=0, frequency=FREQ_BIWEEKLY, anchor=anchor,
            )

        assert [at(-2), at(-1), at(0), at(1), at(2)] == [True, False, True, False, True]

    def test_monthly_is_the_first_matching_weekday_only(self):
        # August 2026 Mondays: 3, 10, 17, 24, 31.
        assert is_fire_day(_date(2026, 8, 3), weekday=0, frequency=FREQ_MONTHLY)
        assert not is_fire_day(_date(2026, 8, 10), weekday=0, frequency=FREQ_MONTHLY)
        assert not is_fire_day(_date(2026, 8, 31), weekday=0, frequency=FREQ_MONTHLY)


class TestNextFireTimeByFrequency:
    def test_weekly_is_unchanged(self):
        nxt = next_fire_time(
            datetime(2026, 7, 20, 6, 0, tzinfo=UTC), UTC_ZONE,
            weekday=0, hour=6, frequency=FREQ_WEEKLY,
        )
        assert nxt == datetime(2026, 7, 27, 6, 0, tzinfo=UTC)

    def test_daily_weekdays_jumps_friday_to_monday(self):
        # Fri 2026-07-24 06:00 → Mon 2026-07-27, skipping Sat/Sun.
        nxt = next_fire_time(
            datetime(2026, 7, 24, 6, 0, tzinfo=UTC), UTC_ZONE,
            weekday=0, hour=6, frequency=FREQ_DAILY_WEEKDAYS,
        )
        assert nxt == datetime(2026, 7, 27, 6, 0, tzinfo=UTC)

    def test_daily_weekdays_advances_one_day_midweek(self):
        nxt = next_fire_time(
            datetime(2026, 7, 21, 6, 0, tzinfo=UTC), UTC_ZONE,
            weekday=0, hour=6, frequency=FREQ_DAILY_WEEKDAYS,
        )
        assert nxt == datetime(2026, 7, 22, 6, 0, tzinfo=UTC)

    def test_biweekly_steps_14_days_across_a_month_boundary(self):
        nxt = next_fire_time(
            datetime(2026, 7, 20, 6, 0, tzinfo=UTC), UTC_ZONE,
            weekday=0, hour=6, frequency=FREQ_BIWEEKLY, anchor=_date(2026, 7, 20),
        )
        assert nxt == datetime(2026, 8, 3, 6, 0, tzinfo=UTC)

    def test_monthly_crosses_a_year_boundary(self):
        # Dec 2026's first Monday is the 7th; Jan 2027's is the 4th.
        nxt = next_fire_time(
            datetime(2026, 12, 7, 6, 0, tzinfo=UTC), UTC_ZONE,
            weekday=0, hour=6, frequency=FREQ_MONTHLY,
        )
        assert nxt == datetime(2027, 1, 4, 6, 0, tzinfo=UTC)

    def test_monthly_when_the_month_starts_on_the_chosen_weekday(self):
        # 2027-02-01 is itself a Monday, so it IS February's first Monday.
        nxt = next_fire_time(
            datetime(2027, 1, 15, 6, 0, tzinfo=UTC), UTC_ZONE,
            weekday=0, hour=6, frequency=FREQ_MONTHLY,
        )
        assert nxt == datetime(2027, 2, 1, 6, 0, tzinfo=UTC)

    def test_dst_holds_the_local_wall_clock_hour(self):
        # US DST starts Sun 2026-03-08, so the next Monday 06:00 New York is
        # 10:00Z (EDT) not 11:00Z (EST) — a fixed UTC step would drift an hour.
        ny = ZoneInfo("America/New_York")
        nxt = next_fire_time(
            datetime(2026, 3, 6, 12, 0, tzinfo=UTC), ny,
            weekday=0, hour=6, frequency=FREQ_WEEKLY,
        )
        assert nxt.astimezone(ny).hour == 6
        assert nxt == datetime(2026, 3, 9, 10, 0, tzinfo=UTC)


class TestShouldRunByFrequency:
    def test_daily_weekdays_is_due_on_tuesday_but_not_saturday(self):
        kw = dict(weekday=0, hour=6, minute=0, frequency=FREQ_DAILY_WEEKDAYS)
        tue = datetime(2026, 7, 21, 6, 0, tzinfo=UTC)
        sat = datetime(2026, 7, 25, 6, 0, tzinfo=UTC)
        assert should_run_weekly_brief(tue, UTC_ZONE, None, **kw) is True
        # Saturday isn't a fire day at all, so the most recent fire is Friday's
        # — far outside the 1h due window.
        assert should_run_weekly_brief(sat, UTC_ZONE, None, **kw) is False

    def test_daily_weekdays_reruns_the_next_day(self):
        kw = dict(weekday=0, hour=6, minute=0, frequency=FREQ_DAILY_WEEKDAYS)
        mon = datetime(2026, 7, 20, 6, 0, tzinfo=UTC)
        tue = datetime(2026, 7, 21, 6, 0, tzinfo=UTC)
        # Monday's run suppresses a second Monday run but NOT Tuesday's.
        assert should_run_weekly_brief(mon, UTC_ZONE, mon, **kw) is False
        assert should_run_weekly_brief(tue, UTC_ZONE, mon, **kw) is True

    def test_biweekly_skips_the_off_week(self):
        kw = dict(
            weekday=0, hour=6, minute=0,
            frequency=FREQ_BIWEEKLY, anchor=_date(2026, 7, 20),
        )
        on = datetime(2026, 7, 20, 6, 0, tzinfo=UTC)
        off = datetime(2026, 7, 27, 6, 0, tzinfo=UTC)
        next_on = datetime(2026, 8, 3, 6, 0, tzinfo=UTC)
        assert should_run_weekly_brief(on, UTC_ZONE, None, **kw) is True
        assert should_run_weekly_brief(off, UTC_ZONE, on, **kw) is False
        assert should_run_weekly_brief(next_on, UTC_ZONE, on, **kw) is True

    def test_monthly_runs_once_per_month(self):
        kw = dict(weekday=0, hour=6, minute=0, frequency=FREQ_MONTHLY)
        first_mon_aug = datetime(2026, 8, 3, 6, 0, tzinfo=UTC)
        second_mon_aug = datetime(2026, 8, 10, 6, 0, tzinfo=UTC)
        first_mon_sep = datetime(2026, 9, 7, 6, 0, tzinfo=UTC)
        assert should_run_weekly_brief(first_mon_aug, UTC_ZONE, None, **kw) is True
        assert should_run_weekly_brief(
            second_mon_aug, UTC_ZONE, first_mon_aug, **kw) is False
        assert should_run_weekly_brief(
            first_mon_sep, UTC_ZONE, first_mon_aug, **kw) is True

    def test_absent_frequency_behaves_exactly_like_weekly(self):
        kw = dict(weekday=0, hour=6, minute=0)
        mon = datetime(2026, 7, 20, 6, 0, tzinfo=UTC)
        tue = datetime(2026, 7, 21, 6, 0, tzinfo=UTC)
        assert should_run_weekly_brief(mon, UTC_ZONE, None, **kw) is True
        assert should_run_weekly_brief(tue, UTC_ZONE, None, **kw) is False

    def test_generation_lead_still_applies_to_a_non_weekly_cadence(self):
        # Monthly: generation starts GENERATION_LEAD (3h) before the first
        # Monday of the month, i.e. 03:00 UTC on 2026-08-03.
        kw = dict(weekday=0, hour=6, minute=0, frequency=FREQ_MONTHLY)
        gen = datetime(2026, 8, 3, 6, 0, tzinfo=UTC) - GENERATION_LEAD
        assert should_generate_weekly_brief(gen, UTC_ZONE, None, **kw) is True
        assert should_generate_weekly_brief(
            gen - timedelta(hours=2), UTC_ZONE, None, **kw) is False


class TestWeekdayOnlySendDays:
    """The Day picker offers Mon–Fri only; legacy weekend values resolve to
    Monday so the scheduler and the settings UI can never disagree."""

    def test_weekdays_pass_through(self):
        for wd in range(5):
            assert resolve_schedule({"brief_weekday": wd})[0] == wd

    def test_saturday_and_sunday_resolve_to_monday(self):
        assert resolve_schedule({"brief_weekday": 5})[0] == 0
        assert resolve_schedule({"brief_weekday": 6})[0] == 0

    def test_out_of_range_still_resolves_to_monday(self):
        assert resolve_schedule({"brief_weekday": -1})[0] == 0
        assert resolve_schedule({"brief_weekday": 99})[0] == 0

    def test_a_stored_weekend_day_fires_on_monday_not_saturday(self):
        # End-to-end through the resolver: a company left on Saturday gets a
        # Monday brief rather than a weekend one.
        weekday, hour, minute = resolve_schedule({"brief_weekday": 5, "brief_hour": 6})
        sat = datetime(2026, 7, 25, 6, 0, tzinfo=UTC)
        mon = datetime(2026, 7, 27, 6, 0, tzinfo=UTC)
        kw = dict(weekday=weekday, hour=hour, minute=minute)
        assert should_run_weekly_brief(sat, UTC_ZONE, None, **kw) is False
        assert should_run_weekly_brief(mon, UTC_ZONE, None, **kw) is True

    def test_no_offerable_weekday_can_ever_fire_on_a_weekend(self):
        # Exhaustive across every offerable day and every cadence.
        for frequency in (FREQ_WEEKLY, FREQ_BIWEEKLY, FREQ_MONTHLY, FREQ_DAILY_WEEKDAYS):
            for weekday in range(5):
                nxt = next_fire_time(
                    datetime(2026, 7, 21, 12, 0, tzinfo=UTC), UTC_ZONE,
                    weekday=weekday, hour=6, frequency=frequency,
                    anchor=_date(2026, 7, 20),
                )
                assert nxt.weekday() <= 4, (frequency, weekday, nxt)
