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
from datetime import datetime, timedelta, timezone
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
                      side_effect=lambda cid, slug, fire, ledger_key=None:
                      exact.append((slug, fire))):
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


def test_tick_recurs_across_consecutive_weeks_honoring_custom_minute():
    """The brief recurs EVERY week at the exact user-configured instant:
    generation 3h before each week's fire, delivery at each week's fire, and
    week N's ledgers never suppress week N+1. Uses a non-zero brief_minute
    (Wednesday 15:45) to pin the minute, and an owner timezone that differs
    from notification_settings.timezone to pin the settings-tz precedence."""
    from app import scheduler as sched_mod

    companies = [{
        "id": "co-x", "slug": "acme", "owner_timezone": "America/New_York",
        "notification_settings": {
            "brief_weekday": 2, "brief_hour": 15, "brief_minute": 45,
            "timezone": "UTC",
        },
    }]

    # Week 1, Wednesday 2026-06-10 12:45 UTC = fire (15:45) − 3h: generate and
    # register the one-shot for the exact fire instant; deliver nothing yet.
    generated, delivered, exact = _run_tick(
        datetime(2026, 6, 10, 12, 45, tzinfo=UTC), companies)
    assert (generated, delivered) == (["acme"], [])
    assert exact == [("acme", datetime(2026, 6, 10, 15, 45, tzinfo=UTC))]
    fire_w1 = exact[0][1]

    # Week 1 fire instant: the fallback delivers (no real one-shot job in
    # tests); its catch-up generation before delivering is by design.
    generated, delivered, exact = _run_tick(fire_w1, companies)
    assert (generated, delivered, exact) == (["acme"], ["acme"], [])

    # Mid-cycle Saturday: nothing runs between weeks.
    assert _run_tick(
        datetime(2026, 6, 13, 12, 45, tzinfo=UTC), companies) == ([], [], [])

    # Week 2, Wednesday 2026-06-17: last week's ledgers do NOT suppress this
    # cycle — generation is due again 3h before the new fire...
    generated, delivered, exact = _run_tick(
        datetime(2026, 6, 17, 12, 45, tzinfo=UTC), companies)
    assert (generated, delivered) == (["acme"], [])
    assert exact == [("acme", datetime(2026, 6, 17, 15, 45, tzinfo=UTC))]
    fire_w2 = exact[0][1]

    # ...and delivery fires again at week 2's exact instant.
    generated, delivered, exact = _run_tick(fire_w2, companies)
    assert (generated, delivered, exact) == (["acme"], ["acme"], [])
    assert sched_mod._last_brief_delivery["co-x"] == fire_w2

    # The two fire instants are the same local schedule, exactly a week apart.
    assert fire_w2 - fire_w1 == timedelta(days=7)
    assert fire_w1.minute == fire_w2.minute == 45
    assert fire_w1.weekday() == fire_w2.weekday() == 2  # Wednesday


def test_tick_dead_zone_between_generation_window_and_fire_does_nothing():
    """After the generation window closes (fire − 2h) and right up to the last
    minute before the fire, the tick neither generates nor delivers — delivery
    happens AT the configured time, never early. Complements
    test_tick_defaults_missing_timezone_to_utc (past the delivery window) and
    test_delivery_fallback_fires_at_the_configured_time (at the fire itself)."""
    companies = [{"id": "co-x", "slug": "initech", "owner_timezone": None}]
    # Default schedule Monday 06:00 UTC: gen window [03:00, 04:00], fire 06:00.
    for now in (
        datetime(2026, 6, 8, 4, 30, tzinfo=UTC),  # dead zone
        datetime(2026, 6, 8, 5, 59, tzinfo=UTC),  # fire − 1 minute
    ):
        assert _run_tick(now, companies) == ([], [], [])


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


# ── the exact-time one-shot job, against a real APScheduler ──────────────────


def test_delivery_pending_false_and_schedule_noop_without_scheduler(monkeypatch):
    """Without a running APScheduler (tests / manual invocation) the pending
    check is False and registering an exact delivery is a silent no-op — the
    tick's post-fire fallback owns delivery then."""
    from app import scheduler as sched_mod

    monkeypatch.setattr(sched_mod, "_scheduler", None)
    assert sched_mod._delivery_job_pending("ws-1") is False
    sched_mod._schedule_exact_delivery(
        "co-1", "acme", datetime(2030, 1, 7, 6, 0, tzinfo=UTC),
        ledger_key="ws-1")
    assert sched_mod._delivery_job_pending("ws-1") is False


async def test_schedule_exact_delivery_registers_one_shot_on_real_apscheduler():
    """_schedule_exact_delivery registers a real one-shot DateTrigger job AT
    the exact fire instant (per-workspace id, misfire grace, replace_existing)
    and _delivery_job_pending sees it — the seam every tick test mocks, closed
    against a real (paused, so never executing) scheduler."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from app import scheduler as sched_mod

    s = AsyncIOScheduler()
    s.start(paused=True)  # real jobstore writes, nothing ever executes
    sched_mod._scheduler = s
    try:
        fire1 = datetime(2030, 1, 7, 6, 0, tzinfo=UTC)
        sched_mod._schedule_exact_delivery(
            "co-1", "acme", fire1, ledger_key="ws-1")

        job = s.get_job("weekly_brief_delivery_ws-1")
        assert job is not None
        assert job.trigger.run_date == fire1  # exactly AT the fire instant
        assert job.misfire_grace_time == 3600  # busy loop delays, never drops
        assert job.args == ("co-1", "acme", fire1, "ws-1")
        assert sched_mod._delivery_job_pending("ws-1") is True
        assert sched_mod._delivery_job_pending("ws-other") is False  # per-ws

        # Re-generation inside one window REPLACES the one-shot (no dupes).
        fire2 = fire1 + timedelta(minutes=30)
        sched_mod._schedule_exact_delivery(
            "co-1", "acme", fire2, ledger_key="ws-1")
        jobs = s.get_jobs()
        assert [j.id for j in jobs] == ["weekly_brief_delivery_ws-1"]
        assert jobs[0].trigger.run_date == fire2
    finally:
        sched_mod._scheduler = None  # every other test assumes no scheduler
        s.shutdown(wait=False)
