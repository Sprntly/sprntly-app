"""Tests for the timezone-aware weekly-brief scheduler tick (v0 checklist 2.4).

These exercise the impure shell `app.scheduler._run_weekly_brief_tick`: it ticks
a (injected) clock, resolves each company's timezone from the company owner's
timezone (the `owner_timezone` field list_companies attaches from
profiles.timezone), and generates the brief ONLY for companies whose local
Monday-06:00 window is open. brief generation is mocked — no LLM / Supabase /
network. The pure day/time/tz/DST logic is unit-tested in test_brief_schedule.py.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _reset_ledger():
    """Clear the in-memory once-per-week ledger between tests so runs are
    isolated."""
    from app import scheduler as sched_mod

    sched_mod._last_brief_run.clear()
    yield
    sched_mod._last_brief_run.clear()


def _run_tick(now, companies):
    """Drive _run_weekly_brief_tick at `now` with a fixed company list, capturing
    which slugs got a brief generated. Returns the list of generated slugs."""
    from app import scheduler as sched_mod

    generated: list[str] = []

    async def _fake_gen(slug):
        generated.append(slug)

    with patch.object(sched_mod, "list_companies", return_value=companies), \
         patch.object(sched_mod, "_generate_weekly_brief_for_company",
                      side_effect=_fake_gen):
        asyncio.run(sched_mod._run_weekly_brief_tick(now=now))
    return generated


def test_tick_generates_only_companies_whose_local_window_is_open():
    """Two companies whose owners are in different timezones; at 10:00 UTC Monday
    it's 06:00 in New York (due) but 20:00 in Sydney (not). Only the NY company's
    brief runs — proving the owner's timezone drives the send time."""
    companies = [
        {"id": "co-ny", "slug": "acme", "owner_timezone": "America/New_York"},
        {"id": "co-syd", "slug": "globex", "owner_timezone": "Australia/Sydney"},
    ]
    # Monday 2026-06-08 10:00 UTC = 06:00 EDT (NY) = 20:00 AEST (Sydney).
    generated = _run_tick(datetime(2026, 6, 8, 10, 0, tzinfo=UTC), companies)
    assert generated == ["acme"]


def test_tick_fires_sydney_at_its_own_local_monday_0600():
    """Same companies, a different UTC instant: 20:00 UTC Sunday is 06:00 Monday
    in Sydney → only Sydney's brief runs (NY is still Sunday afternoon)."""
    companies = [
        {"id": "co-ny", "slug": "acme", "owner_timezone": "America/New_York"},
        {"id": "co-syd", "slug": "globex", "owner_timezone": "Australia/Sydney"},
    ]
    # 2026-06-07 20:00 UTC = Mon 2026-06-08 06:00 AEST (Sydney) = Sun 16:00 EDT (NY)
    generated = _run_tick(datetime(2026, 6, 7, 20, 0, tzinfo=UTC), companies)
    assert generated == ["globex"]


def test_tick_defaults_missing_timezone_to_utc():
    """A company whose owner has no timezone fires at Monday 06:00 UTC."""
    companies = [{"id": "co-x", "slug": "initech", "owner_timezone": None}]
    # Monday 2026-06-08 06:00 UTC.
    assert _run_tick(datetime(2026, 6, 8, 6, 0, tzinfo=UTC), companies) == ["initech"]
    # ...and NOT at 10:00 UTC (that's 06:00 NY, irrelevant to a UTC company).
    assert _run_tick(datetime(2026, 6, 8, 10, 0, tzinfo=UTC), companies) == []


def test_tick_is_idempotent_within_the_window():
    """Two ticks inside the same firing window generate the brief exactly once —
    the in-memory ledger records the first run and suppresses the second."""
    from app import scheduler as sched_mod

    companies = [{"id": "co-x", "slug": "acme", "owner_timezone": "UTC"}]
    generated: list[str] = []

    async def _fake_gen(slug):
        generated.append(slug)

    with patch.object(sched_mod, "list_companies", return_value=companies), \
         patch.object(sched_mod, "_generate_weekly_brief_for_company",
                      side_effect=_fake_gen):
        # 06:00 then 06:30 UTC, same Monday window.
        asyncio.run(sched_mod._run_weekly_brief_tick(
            now=datetime(2026, 6, 8, 6, 0, tzinfo=UTC)))
        asyncio.run(sched_mod._run_weekly_brief_tick(
            now=datetime(2026, 6, 8, 6, 30, tzinfo=UTC)))

    assert generated == ["acme"]  # exactly once


def test_tick_dst_winter_vs_summer_for_new_york():
    """The NY company fires at 06:00 local in BOTH seasons even though that's a
    different UTC instant: 11:00 UTC in winter (EST), 10:00 UTC in summer (EDT)."""
    companies = [{"id": "co-ny", "slug": "acme", "owner_timezone": "America/New_York"}]

    # Winter Monday 2026-01-12: 06:00 EST = 11:00 UTC.
    assert _run_tick(datetime(2026, 1, 12, 11, 0, tzinfo=UTC), companies) == ["acme"]
    # The summer instant (10:00 UTC) is 05:00 EST in winter → not due.
    assert _run_tick(datetime(2026, 1, 12, 10, 0, tzinfo=UTC), companies) == []

    # Summer Monday 2026-07-06: 06:00 EDT = 10:00 UTC.
    assert _run_tick(datetime(2026, 7, 6, 10, 0, tzinfo=UTC), companies) == ["acme"]
    # The winter instant (11:00 UTC) is 07:00 EDT in summer → past window.
    assert _run_tick(datetime(2026, 7, 6, 11, 0, tzinfo=UTC), companies) == []


def test_tick_isolates_per_company_failure():
    """One company's brief blowing up must not stop the others in the tick."""
    from app import scheduler as sched_mod

    companies = [
        {"id": "co-a", "slug": "acme", "owner_timezone": "UTC"},
        {"id": "co-b", "slug": "globex", "owner_timezone": "UTC"},
    ]
    seen: list[str] = []

    async def _gen(slug):
        if slug == "acme":
            raise RuntimeError("brief blew up for acme")
        seen.append(slug)

    with patch.object(sched_mod, "list_companies", return_value=companies), \
         patch.object(sched_mod, "_generate_weekly_brief_for_company",
                      side_effect=_gen):
        asyncio.run(sched_mod._run_weekly_brief_tick(
            now=datetime(2026, 6, 8, 6, 0, tzinfo=UTC)))  # no raise

    assert seen == ["globex"]


def test_tick_no_companies_is_a_clean_noop():
    from app import scheduler as sched_mod

    with patch.object(sched_mod, "list_companies", return_value=[]), \
         patch.object(sched_mod, "_generate_weekly_brief_for_company") as gen:
        asyncio.run(sched_mod._run_weekly_brief_tick(
            now=datetime(2026, 6, 8, 6, 0, tzinfo=UTC)))
    gen.assert_not_called()


def test_tick_off_schedule_generates_nothing():
    """A tick on a Wednesday generates no briefs for anyone."""
    companies = [
        {"id": "co-ny", "slug": "acme", "owner_timezone": "America/New_York"},
        {"id": "co-x", "slug": "initech", "owner_timezone": None},
    ]
    # Wednesday 2026-06-10 10:00 UTC.
    assert _run_tick(datetime(2026, 6, 10, 10, 0, tzinfo=UTC), companies) == []
