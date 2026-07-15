"""Tests for the timezone-aware weekly-brief scheduler tick (v0 checklist 2.4).

These exercise the impure shell `app.scheduler._run_weekly_brief_tick` and its
two-phase design: GENERATION starts GENERATION_LEAD (3h) before each company's
configured local day/time (Comms & Brief settings, default Monday 06:00) so
synthesis has time to finish; DELIVERY (Slack + email) happens exactly AT the
configured instant via a one-shot job, with a tick-based catch-up fallback
after the fire time — and never before it. Brief generation/delivery are
mocked — no LLM / Supabase / network. The pure day/time/tz/DST logic is
unit-tested in test_brief_schedule.py.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _reset_ledgers():
    """Clear the in-memory once-per-cycle ledgers between tests so runs are
    isolated."""
    from app import scheduler as sched_mod

    sched_mod._last_brief_generation.clear()
    sched_mod._last_brief_delivery.clear()
    yield
    sched_mod._last_brief_generation.clear()
    sched_mod._last_brief_delivery.clear()


def _run_tick(now, companies):
    """Drive _run_weekly_brief_tick at `now` with a fixed company list,
    capturing which slugs got a brief generated / delivered and which exact
    delivery instants were scheduled. Returns (generated, delivered, exact)."""
    from app import scheduler as sched_mod

    generated: list[str] = []
    delivered: list[str] = []
    exact: list[tuple[str, datetime]] = []

    async def _fake_gen(slug):
        generated.append(slug)

    async def _fake_deliver(company_id, slug):
        delivered.append(slug)
        return True

    with patch.object(sched_mod, "list_companies", return_value=companies), \
         patch.object(sched_mod, "_generate_weekly_brief_for_company",
                      side_effect=_fake_gen), \
         patch.object(sched_mod, "_deliver_weekly_brief_for_company",
                      side_effect=_fake_deliver), \
         patch.object(sched_mod, "_schedule_exact_delivery",
                      side_effect=lambda cid, slug, fire: exact.append((slug, fire))):
        asyncio.run(sched_mod._run_weekly_brief_tick(now=now))
    return generated, delivered, exact


# ── generation: GENERATION_LEAD (3h) before the configured local time ────────


def test_tick_generates_3h_before_local_fire_time_without_delivering():
    """The NY company's brief fires Monday 06:00 EDT = 10:00 UTC, so generation
    starts at 07:00 UTC — and NOTHING is delivered at that early instant."""
    companies = [{"id": "co-ny", "slug": "acme", "owner_timezone": "America/New_York"}]
    # Monday 2026-06-08 07:00 UTC = 03:00 EDT = fire (06:00 EDT) − 3h.
    generated, delivered, exact = _run_tick(
        datetime(2026, 6, 8, 7, 0, tzinfo=UTC), companies)
    assert generated == ["acme"]
    assert delivered == []  # never before the configured time
    # ...and the exact-time one-shot is registered for the true fire instant.
    assert exact == [("acme", datetime(2026, 6, 8, 10, 0, tzinfo=UTC))]


def test_tick_generation_respects_each_companys_own_lead_window():
    """Two companies in different timezones: at Monday 07:00 UTC only NY's
    generation lead window (fire 10:00 UTC) is open; Sydney's fire (Monday
    06:00 AEST = Sunday 20:00 UTC) had its lead window at Sunday 17:00 UTC."""
    companies = [
        {"id": "co-ny", "slug": "acme", "owner_timezone": "America/New_York"},
        {"id": "co-syd", "slug": "globex", "owner_timezone": "Australia/Sydney"},
    ]
    generated, _, _ = _run_tick(datetime(2026, 6, 8, 7, 0, tzinfo=UTC), companies)
    assert generated == ["acme"]

    # Sunday 2026-06-07 17:00 UTC = Sydney's fire − 3h → only Sydney generates.
    generated, delivered, _ = _run_tick(
        datetime(2026, 6, 7, 17, 0, tzinfo=UTC), companies)
    assert generated == ["globex"]
    assert delivered == []


def test_tick_defaults_missing_timezone_to_utc():
    """A company whose owner has no timezone generates at Monday 03:00 UTC
    (06:00 UTC fire − 3h) — not at NY's lead instant."""
    companies = [{"id": "co-x", "slug": "initech", "owner_timezone": None}]
    generated, _, _ = _run_tick(datetime(2026, 6, 8, 3, 0, tzinfo=UTC), companies)
    assert generated == ["initech"]
    # Past both the lead window (03:00–04:00) and the delivery window
    # (06:00–07:00) → nothing runs.
    generated, delivered, _ = _run_tick(
        datetime(2026, 6, 8, 8, 0, tzinfo=UTC), companies)
    assert (generated, delivered) == ([], [])


def test_tick_generation_is_idempotent_within_the_window():
    """Two ticks inside the same lead window generate exactly once — the
    in-memory ledger records the first run and suppresses the second."""
    companies = [{"id": "co-x", "slug": "acme", "owner_timezone": "UTC"}]
    from app import scheduler as sched_mod

    generated: list[str] = []

    async def _fake_gen(slug):
        generated.append(slug)

    with patch.object(sched_mod, "list_companies", return_value=companies), \
         patch.object(sched_mod, "_generate_weekly_brief_for_company",
                      side_effect=_fake_gen), \
         patch.object(sched_mod, "_deliver_weekly_brief_for_company"), \
         patch.object(sched_mod, "_schedule_exact_delivery"):
        # 03:00 then 03:30 UTC, same Monday lead window (fire 06:00 UTC).
        asyncio.run(sched_mod._run_weekly_brief_tick(
            now=datetime(2026, 6, 8, 3, 0, tzinfo=UTC)))
        asyncio.run(sched_mod._run_weekly_brief_tick(
            now=datetime(2026, 6, 8, 3, 30, tzinfo=UTC)))

    assert generated == ["acme"]  # exactly once


def test_tick_dst_winter_vs_summer_for_new_york():
    """The NY company generates 3h before 06:00 local in BOTH seasons even
    though that's a different UTC instant: fire 11:00 UTC in winter (EST) →
    generate 08:00 UTC; fire 10:00 UTC in summer (EDT) → generate 07:00 UTC."""
    companies = [{"id": "co-ny", "slug": "acme", "owner_timezone": "America/New_York"}]

    # Winter Monday 2026-01-12: generation at 08:00 UTC, not 07:00 UTC.
    assert _run_tick(datetime(2026, 1, 12, 8, 0, tzinfo=UTC), companies)[0] == ["acme"]
    assert _run_tick(datetime(2026, 1, 12, 7, 0, tzinfo=UTC), companies)[0] == []

    # Summer Monday 2026-07-06: generation at 07:00 UTC, not 08:00 UTC (that
    # would already be inside the window's tail — assert the winter instant
    # minus a wider margin instead: 06:00 UTC is out of window).
    assert _run_tick(datetime(2026, 7, 6, 7, 0, tzinfo=UTC), companies)[0] == ["acme"]
    assert _run_tick(datetime(2026, 7, 6, 6, 0, tzinfo=UTC), companies)[0] == []


def test_tick_honors_custom_schedule_from_notification_settings():
    """Comms & Brief settings (brief_weekday/hour/minute + timezone) drive the
    cycle: Wednesday 15:00 UTC fire → generation due Wednesday 12:00 UTC."""
    companies = [{
        "id": "co-x", "slug": "acme", "owner_timezone": "America/New_York",
        "notification_settings": {
            "brief_weekday": 2, "brief_hour": 15, "brief_minute": 0,
            "timezone": "UTC",
        },
    }]
    # Wednesday 2026-06-10 12:00 UTC = fire (15:00 UTC) − 3h.
    generated, delivered, exact = _run_tick(
        datetime(2026, 6, 10, 12, 0, tzinfo=UTC), companies)
    assert generated == ["acme"]
    assert delivered == []
    assert exact == [("acme", datetime(2026, 6, 10, 15, 0, tzinfo=UTC))]


def test_tick_off_schedule_does_nothing():
    """A tick on a Friday (default Monday schedule) neither generates nor
    delivers for anyone."""
    companies = [
        {"id": "co-ny", "slug": "acme", "owner_timezone": "America/New_York"},
        {"id": "co-x", "slug": "initech", "owner_timezone": None},
    ]
    generated, delivered, exact = _run_tick(
        datetime(2026, 6, 12, 10, 0, tzinfo=UTC), companies)
    assert (generated, delivered, exact) == ([], [], [])


def test_tick_isolates_per_company_failure():
    """One company's generation blowing up must not stop the others."""
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
                      side_effect=_gen), \
         patch.object(sched_mod, "_deliver_weekly_brief_for_company"), \
         patch.object(sched_mod, "_schedule_exact_delivery"):
        asyncio.run(sched_mod._run_weekly_brief_tick(
            now=datetime(2026, 6, 8, 3, 0, tzinfo=UTC)))  # no raise

    assert seen == ["globex"]


def test_tick_no_companies_is_a_clean_noop():
    from app import scheduler as sched_mod

    with patch.object(sched_mod, "list_companies", return_value=[]), \
         patch.object(sched_mod, "_generate_weekly_brief_for_company") as gen:
        asyncio.run(sched_mod._run_weekly_brief_tick(
            now=datetime(2026, 6, 8, 3, 0, tzinfo=UTC)))
    gen.assert_not_called()


# ── delivery: exactly at the configured time, never before ───────────────────


def test_delivery_fallback_fires_at_the_configured_time():
    """A process that lost its one-shot job (restart) still delivers: the tick
    at/after the fire time catch-up-generates (refresh-gated no-op) and
    delivers the current brief."""
    companies = [{"id": "co-x", "slug": "acme", "owner_timezone": "UTC"}]
    # Monday 2026-06-08 06:00 UTC = the fire instant itself.
    generated, delivered, _ = _run_tick(
        datetime(2026, 6, 8, 6, 0, tzinfo=UTC), companies)
    assert delivered == ["acme"]
    assert generated == ["acme"]  # catch-up generation before delivering


def test_delivery_fallback_is_idempotent_within_the_window():
    """Two ticks inside the same post-fire window deliver exactly once."""
    from app import scheduler as sched_mod

    companies = [{"id": "co-x", "slug": "acme", "owner_timezone": "UTC"}]
    delivered: list[str] = []

    async def _fake_deliver(company_id, slug):
        delivered.append(slug)
        return True

    with patch.object(sched_mod, "list_companies", return_value=companies), \
         patch.object(sched_mod, "_generate_weekly_brief_for_company"), \
         patch.object(sched_mod, "_deliver_weekly_brief_for_company",
                      side_effect=_fake_deliver), \
         patch.object(sched_mod, "_schedule_exact_delivery"):
        asyncio.run(sched_mod._run_weekly_brief_tick(
            now=datetime(2026, 6, 8, 6, 0, tzinfo=UTC)))
        asyncio.run(sched_mod._run_weekly_brief_tick(
            now=datetime(2026, 6, 8, 6, 30, tzinfo=UTC)))

    assert delivered == ["acme"]  # exactly once


def test_delivery_fallback_stands_down_while_one_shot_job_pending():
    """When the exact-time one-shot job is still registered, the tick fallback
    must not double-deliver."""
    from app import scheduler as sched_mod

    companies = [{"id": "co-x", "slug": "acme", "owner_timezone": "UTC"}]
    delivered: list[str] = []

    async def _fake_deliver(company_id, slug):
        delivered.append(slug)
        return True

    with patch.object(sched_mod, "list_companies", return_value=companies), \
         patch.object(sched_mod, "_generate_weekly_brief_for_company"), \
         patch.object(sched_mod, "_deliver_weekly_brief_for_company",
                      side_effect=_fake_deliver), \
         patch.object(sched_mod, "_delivery_job_pending", return_value=True):
        asyncio.run(sched_mod._run_weekly_brief_tick(
            now=datetime(2026, 6, 8, 6, 0, tzinfo=UTC)))

    assert delivered == []


def test_exact_delivery_job_body_delivers_and_records_the_cycle():
    """_run_exact_delivery pushes the current brief and records the delivery so
    the tick fallback stands down."""
    from app import scheduler as sched_mod

    delivered: list[tuple[str, str]] = []

    async def _fake_deliver(company_id, slug):
        delivered.append((company_id, slug))
        return True

    with patch.object(sched_mod, "_deliver_weekly_brief_for_company",
                      side_effect=_fake_deliver):
        asyncio.run(sched_mod._run_exact_delivery(
            "co-1", "acme", datetime(2026, 6, 8, 6, 0, tzinfo=UTC)))

    assert delivered == [("co-1", "acme")]
    assert "co-1" in sched_mod._last_brief_delivery


def test_exact_delivery_job_body_leaves_ledger_unset_when_no_brief():
    """No current brief → nothing delivered → the fallback may retry later."""
    from app import scheduler as sched_mod

    async def _fake_deliver(company_id, slug):
        return False

    with patch.object(sched_mod, "_deliver_weekly_brief_for_company",
                      side_effect=_fake_deliver):
        asyncio.run(sched_mod._run_exact_delivery(
            "co-1", "acme", datetime(2026, 6, 8, 6, 0, tzinfo=UTC)))

    assert "co-1" not in sched_mod._last_brief_delivery


# ── the scheduled generation must not deliver inline ─────────────────────────


def test_scheduled_generation_suppresses_inline_delivery():
    """_generate_weekly_brief_for_company generates with deliver=False — the
    scheduled push happens at the fire time, never at generation time."""
    from app import scheduler as sched_mod

    calls: list[tuple] = []
    with patch("app.synthesis_brief.generate_brief_for",
               side_effect=lambda slug, **kw: calls.append((slug, kw)) or {"id": 1}), \
         patch("app.brief_runner.warm_synthesis_drilldowns"):
        asyncio.run(sched_mod._generate_weekly_brief_for_company("acme"))
    assert calls == [("acme", {"deliver": False})]


def test_deliver_weekly_brief_pushes_current_brief():
    """_deliver_weekly_brief_for_company reads the CURRENT brief and hands it to
    deliver_brief; returns False (no attempt) when there is no brief yet."""
    from app import scheduler as sched_mod

    pushed: list[tuple[str, dict]] = []
    with patch("app.db.briefs.get_current_brief",
               return_value={"id": 7}), \
         patch("app.synthesis.delivery.deliver_brief",
               side_effect=lambda cid, brief: pushed.append((cid, brief)) or {}):
        ok = asyncio.run(
            sched_mod._deliver_weekly_brief_for_company("co-1", "acme"))
    assert ok is True
    assert pushed == [("co-1", {"id": 7})]

    with patch("app.db.briefs.get_current_brief", return_value=None), \
         patch("app.synthesis.delivery.deliver_brief") as push:
        ok = asyncio.run(
            sched_mod._deliver_weekly_brief_for_company("co-1", "acme"))
    assert ok is False
    push.assert_not_called()
