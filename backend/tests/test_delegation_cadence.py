"""Pure-function unit tests for `app/delegation_cadence.py` — the LOCKED
spec §2 cadence caps (min interval, per-person daily/weekly caps, quiet
hours, escalation) as a table, not model judgment. No DB, no clock — every
assertion feeds an explicit `now`/`local_dt`, mirroring
`test_brief_schedule.py`'s own pure-module test shape.

Runs on every PR (no rig, no LLM).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import delegation_cadence as cadence

NOW = datetime(2026, 8, 12, 12, 0, 0, tzinfo=timezone.utc)  # a Wednesday


# ── clamp_next_check_in (AC5) ────────────────────────────────────────────


def test_clamp_none_returns_floor():
    result = cadence.clamp_next_check_in(None, last_checked_in=None, now=NOW)
    assert result == NOW + timedelta(hours=24)


def test_clamp_too_soon_raised_to_floor():
    proposed = NOW + timedelta(hours=1)
    result = cadence.clamp_next_check_in(proposed, last_checked_in=NOW, now=NOW)
    assert result == NOW + timedelta(hours=24)


def test_clamp_later_proposal_respected():
    proposed = NOW + timedelta(hours=72)
    result = cadence.clamp_next_check_in(proposed, last_checked_in=NOW, now=NOW)
    assert result == proposed


def test_clamp_uses_later_of_now_and_last_checked_in():
    """The floor is anchored to whichever is LATER of `now` and
    `last_checked_in` — a stale `last_checked_in` in the past must not
    push the floor earlier than `now` itself."""
    stale_last = NOW - timedelta(days=10)
    result = cadence.clamp_next_check_in(None, last_checked_in=stale_last, now=NOW)
    assert result == NOW + timedelta(hours=24)


# ── respect_stated_timeline (AC6) ────────────────────────────────────────


def test_respect_stated_timeline_uses_stated():
    stated = NOW + timedelta(days=3)  # well past the floor
    proposed = NOW + timedelta(hours=2)
    result = cadence.respect_stated_timeline(stated, proposed, now=NOW, last_checked_in=None)
    assert result == stated


def test_respect_stated_timeline_clamps_stated_below_floor():
    """A stated timeline earlier than the floor is raised to the floor —
    never respected as-is if it's too soon."""
    stated = NOW + timedelta(minutes=30)
    proposed = NOW + timedelta(hours=1)
    result = cadence.respect_stated_timeline(stated, proposed, now=NOW, last_checked_in=None)
    assert result == NOW + timedelta(hours=24)


def test_respect_stated_timeline_none_falls_back():
    proposed = NOW + timedelta(hours=48)
    result = cadence.respect_stated_timeline(None, proposed, now=NOW, last_checked_in=None)
    assert result == cadence.clamp_next_check_in(proposed, last_checked_in=None, now=NOW)
    assert result == proposed


# ── in_quiet_hours / next_send_window (AC7) ──────────────────────────────


def test_in_quiet_hours_evening_night_and_weekend():
    wednesday = datetime(2026, 8, 12, tzinfo=timezone.utc)  # weekday()==2
    for hour in (20, 21, 22, 23, 0, 1, 6, 7):
        assert cadence.in_quiet_hours(wednesday.replace(hour=hour)) is True, hour

    saturday = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)  # weekday()==5
    sunday = datetime(2026, 8, 16, 10, 0, tzinfo=timezone.utc)  # weekday()==6
    assert cadence.in_quiet_hours(saturday) is True
    assert cadence.in_quiet_hours(sunday) is True


def test_in_quiet_hours_false_wednesday_daytime():
    wednesday = datetime(2026, 8, 12, tzinfo=timezone.utc)
    for hour in range(9, 20):
        assert cadence.in_quiet_hours(wednesday.replace(hour=hour)) is False, hour


def test_next_send_window_shifts_to_monday_0800():
    friday_2200 = datetime(2026, 8, 14, 22, 0, tzinfo=timezone.utc)  # Friday
    saturday_1000 = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)  # Saturday
    expected_monday_0800 = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)

    assert cadence.next_send_window(friday_2200) == expected_monday_0800
    assert cadence.next_send_window(saturday_1000) == expected_monday_0800


def test_next_send_window_unchanged_outside_quiet_hours():
    wednesday_1400 = datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc)
    assert cadence.next_send_window(wednesday_1400) == wednesday_1400


# ── is_capped (AC8) ───────────────────────────────────────────────────────


def test_is_capped_daily_and_weekly():
    assert cadence.is_capped(sends_today=1, sends_this_week=1) is True
    assert cadence.is_capped(sends_today=0, sends_this_week=3) is True
    assert cadence.is_capped(sends_today=0, sends_this_week=2) is False


# ── should_escalate (AC9) ────────────────────────────────────────────────


def test_should_escalate_past_expected_plus_cycle():
    expected = NOW - cadence.MIN_INTERVAL - timedelta(hours=1)  # past expected+cycle
    assert cadence.should_escalate(
        expected_completion=expected, now=NOW, latest_status="assigned", cycles_since_status=1,
    ) is True

    # Not yet past the +1-cycle window.
    recent_expected = NOW - timedelta(hours=1)
    assert cadence.should_escalate(
        expected_completion=recent_expected, now=NOW, latest_status="in_progress",
        cycles_since_status=1,
    ) is False

    # No prior cycle yet.
    assert cadence.should_escalate(
        expected_completion=expected, now=NOW, latest_status="assigned", cycles_since_status=0,
    ) is False


def test_should_escalate_false_when_closed_or_no_expected():
    expected = NOW - cadence.MIN_INTERVAL - timedelta(hours=1)
    assert cadence.should_escalate(
        expected_completion=expected, now=NOW, latest_status="completed", cycles_since_status=2,
    ) is False
    assert cadence.should_escalate(
        expected_completion=expected, now=NOW, latest_status="cleared", cycles_since_status=2,
    ) is False
    assert cadence.should_escalate(
        expected_completion=None, now=NOW, latest_status="assigned", cycles_since_status=2,
    ) is False
