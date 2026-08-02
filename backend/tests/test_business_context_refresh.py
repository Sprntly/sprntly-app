"""Async business-context refresh: job mechanics.

Mirrors the coverage shape of two proven references in this codebase:
  - tests/test_ask_job_heartbeat.py (the heartbeat loop itself)
  - tests/test_company_research.py's "restart mid-run — orphan sweep +
    in-flight guard" section (the singleton-per-tenant start-guard, its
    self-heal-on-stale retry, and the age-gated orphan sweep)

No network / no Anthropic: `run_business_context` is patched in the
`app.business_context_refresh_runner` namespace; the fake Supabase client
from conftest backs `companies` (including the four
business_context_refresh_* columns — mirrors
20260802140000_business_context_refresh_status.sql).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import app.business_context_refresh_runner as runner
from app.db.business_context_refresh import (
    ORPHAN_BUSINESS_CONTEXT_REFRESH_AFTER_MINUTES,
    business_context_refresh_state,
    complete_business_context_refresh,
    fail_business_context_refresh,
    fail_orphan_business_context_refreshes,
    start_business_context_refresh,
    touch_business_context_refresh,
)
from app.db.client import require_client

_COMPANY_ID = "co-bc-refresh"


def _seed_company(db, cid: str = _COMPANY_ID) -> None:
    if not db.table("companies").select("id").eq("id", cid).execute().data:
        db.table("companies").insert(
            {"id": cid, "slug": f"slug-{cid}", "display_name": cid}
        ).execute()


@pytest.fixture
def seeded_company(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db)
    return db


def _iso(minutes_ago: float) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    ).isoformat()


def _row(cid: str = _COMPANY_ID) -> dict:
    return (
        require_client()
        .table("companies")
        .select(
            "business_context_refresh_status, business_context_refresh_error, "
            "business_context_refresh_started_at, "
            "business_context_refresh_heartbeat_at"
        )
        .eq("id", cid)
        .execute()
        .data[0]
    )


# --------------------------------------------------------------------------- #
# 1. Heartbeat loop
# --------------------------------------------------------------------------- #
async def test_heartbeat_beats_until_the_row_leaves_generating(monkeypatch):
    beats = []

    def _touch(company_id: str) -> bool:
        beats.append(company_id)
        return len(beats) < 3  # third beat finds a terminal row

    monkeypatch.setattr(runner, "touch_business_context_refresh", _touch)
    monkeypatch.setattr(
        runner, "ORPHAN_BUSINESS_CONTEXT_REFRESH_HEARTBEAT_SECONDS", 0
    )
    import asyncio

    await asyncio.wait_for(runner._heartbeat("co-x"), timeout=5)
    assert beats == ["co-x", "co-x", "co-x"], \
        "the loop must stop when the row is terminal"


async def test_heartbeat_survives_a_transient_db_error(monkeypatch):
    calls = {"n": 0}

    def _touch(_cid: str) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db blip")
        return False

    monkeypatch.setattr(runner, "touch_business_context_refresh", _touch)
    monkeypatch.setattr(
        runner, "ORPHAN_BUSINESS_CONTEXT_REFRESH_HEARTBEAT_SECONDS", 0
    )
    import asyncio

    await asyncio.wait_for(runner._heartbeat("co-y"), timeout=5)
    assert calls["n"] >= 1


async def test_heartbeat_is_cancellable(monkeypatch):
    monkeypatch.setattr(
        runner, "touch_business_context_refresh", lambda _cid: True
    )
    monkeypatch.setattr(
        runner, "ORPHAN_BUSINESS_CONTEXT_REFRESH_HEARTBEAT_SECONDS", 0.01
    )
    import asyncio

    task = asyncio.create_task(runner._heartbeat("co-z"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_slow_refresh_is_beaten_for_its_whole_duration(monkeypatch):
    """The shape of the real failure this ticket is designed around: a
    refresh that outlives the sweep window. With a fake clock, a 'slow' run
    must be beaten while it runs and the beat must stop as soon as it
    returns."""
    beats = []
    monkeypatch.setattr(
        runner, "touch_business_context_refresh",
        lambda cid: beats.append(cid) or True,
    )
    monkeypatch.setattr(
        runner, "ORPHAN_BUSINESS_CONTEXT_REFRESH_HEARTBEAT_SECONDS", 0.02
    )
    monkeypatch.setattr(runner, "complete_business_context_refresh", lambda *a: None)
    monkeypatch.setattr(runner, "GraphFacade", lambda *a, **k: object())

    import time as _time

    def _slow_run(facade, company_id):
        _time.sleep(0.3)

    monkeypatch.setattr(runner, "run_business_context", _slow_run)

    await runner.run_business_context_refresh_job("co-slow")

    assert len(beats) >= 2, f"a slow refresh was beaten only {len(beats)} time(s)"
    before = len(beats)
    import asyncio

    # Generous grace window: under CPU contention a beat's asyncio.to_thread
    # call already in flight when beat.cancel() fires can still land ONE more
    # result after the job returns (Python's ThreadPoolExecutor can't cancel
    # already-running work) — real, not a race in the code under test, mirrors
    # the same tolerance test_ask_job_heartbeat.py's equivalent test accepts.
    await asyncio.sleep(0.2)
    assert len(beats) <= before + 1, "the heartbeat outlived the job by more than one late beat"


# --------------------------------------------------------------------------- #
# 2. run_business_context_refresh_job — completes/fails without raising
# --------------------------------------------------------------------------- #
async def test_job_completes_and_writes_done(seeded_company, monkeypatch):
    monkeypatch.setattr(
        runner, "run_business_context", lambda facade, cid: {"version": 2}
    )
    monkeypatch.setattr(runner, "GraphFacade", lambda *a, **k: object())

    assert start_business_context_refresh(_COMPANY_ID) is True
    await runner.run_business_context_refresh_job(_COMPANY_ID)

    row = _row()
    assert row["business_context_refresh_status"] == "done"
    assert row["business_context_refresh_error"] is None


async def test_job_never_raises_on_failure(seeded_company, monkeypatch):
    def boom(facade, cid):
        raise RuntimeError("web tool unavailable")

    monkeypatch.setattr(runner, "run_business_context", boom)
    monkeypatch.setattr(runner, "GraphFacade", lambda *a, **k: object())

    assert start_business_context_refresh(_COMPANY_ID) is True
    await runner.run_business_context_refresh_job(_COMPANY_ID)  # must not raise

    row = _row()
    assert row["business_context_refresh_status"] == "error"
    assert "web tool unavailable" in row["business_context_refresh_error"]


# --------------------------------------------------------------------------- #
# 3. Atomic singleton-per-tenant start-guard
# --------------------------------------------------------------------------- #
def test_second_start_is_a_noop_while_the_first_is_live(seeded_company):
    assert start_business_context_refresh(_COMPANY_ID) is True
    assert start_business_context_refresh(_COMPANY_ID) is False
    row = _row()
    assert row["business_context_refresh_status"] == "generating"


def test_a_finished_refresh_does_not_block_the_next_one(seeded_company):
    assert start_business_context_refresh(_COMPANY_ID) is True
    complete_business_context_refresh(_COMPANY_ID)
    assert start_business_context_refresh(_COMPANY_ID) is True  # not blocked


def test_a_failed_refresh_does_not_block_the_next_one(seeded_company):
    assert start_business_context_refresh(_COMPANY_ID) is True
    fail_business_context_refresh(_COMPANY_ID, "boom")
    assert start_business_context_refresh(_COMPANY_ID) is True  # not blocked


def test_stale_generating_row_does_not_block_a_new_refresh(seeded_company):
    """A restart mid-refresh leaves the row 'generating' with a stale
    heartbeat. Without the self-heal retry inside start_business_context_refresh,
    that row would lock this company out of refreshing until the periodic
    sweep caught it (mirrors company_research_runs' insert-conflict heal)."""
    c = require_client()
    stale = _iso(ORPHAN_BUSINESS_CONTEXT_REFRESH_AFTER_MINUTES * 4)
    c.table("companies").update({
        "business_context_refresh_status": "generating",
        "business_context_refresh_started_at": stale,
        "business_context_refresh_heartbeat_at": stale,
    }).eq("id", _COMPANY_ID).execute()

    assert start_business_context_refresh(_COMPANY_ID) is True
    row = _row()
    assert row["business_context_refresh_status"] == "generating"
    # healed: started_at moved off the stale timestamp
    assert row["business_context_refresh_started_at"] != stale


def test_two_companies_do_not_interfere(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db, "co-bc-a")
    _seed_company(db, "co-bc-b")
    assert start_business_context_refresh("co-bc-a") is True
    assert start_business_context_refresh("co-bc-b") is True  # unrelated tenant
    assert start_business_context_refresh("co-bc-a") is False  # still live


# --------------------------------------------------------------------------- #
# 4. Orphan sweep — heartbeat-gated, not just age-since-start
# --------------------------------------------------------------------------- #
def test_orphan_sweep_fails_only_stale_heartbeats(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db, "co-bc-old")
    _seed_company(db, "co-bc-young")
    c = require_client()
    old_beat = _iso(ORPHAN_BUSINESS_CONTEXT_REFRESH_AFTER_MINUTES * 4)
    young_beat = _iso(1)
    c.table("companies").update({
        "business_context_refresh_status": "generating",
        "business_context_refresh_started_at": old_beat,
        "business_context_refresh_heartbeat_at": old_beat,
    }).eq("id", "co-bc-old").execute()
    c.table("companies").update({
        "business_context_refresh_status": "generating",
        "business_context_refresh_started_at": old_beat,  # STARTED long ago…
        "business_context_refresh_heartbeat_at": young_beat,  # …but still beating
    }).eq("id", "co-bc-young").execute()

    assert fail_orphan_business_context_refreshes() == 1

    assert _row("co-bc-old")["business_context_refresh_status"] == "error"
    assert _row("co-bc-young")["business_context_refresh_status"] == "generating", (
        "a long-but-healthy refresh (started long ago, heartbeat still fresh) "
        "must NOT be reaped — this is the exact ask_jobs incident this ticket "
        "exists to not repeat"
    )


def test_orphan_sweep_scoped_to_one_company(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db, "co-bc-1")
    _seed_company(db, "co-bc-2")
    c = require_client()
    stale = _iso(ORPHAN_BUSINESS_CONTEXT_REFRESH_AFTER_MINUTES * 4)
    for cid in ("co-bc-1", "co-bc-2"):
        c.table("companies").update({
            "business_context_refresh_status": "generating",
            "business_context_refresh_started_at": stale,
            "business_context_refresh_heartbeat_at": stale,
        }).eq("id", cid).execute()

    assert fail_orphan_business_context_refreshes(company_id="co-bc-1") == 1
    assert _row("co-bc-1")["business_context_refresh_status"] == "error"
    assert _row("co-bc-2")["business_context_refresh_status"] == "generating"


def test_heartbeat_keeps_a_row_alive_across_the_sweep(seeded_company):
    """touch_business_context_refresh (the runner's own heartbeat call) is
    what the sweep actually reads — not a manual timestamp write. Prove the
    two are wired to the same column."""
    assert start_business_context_refresh(_COMPANY_ID) is True
    # Simulate the row aging past the window without a beat…
    stale = _iso(ORPHAN_BUSINESS_CONTEXT_REFRESH_AFTER_MINUTES * 4)
    require_client().table("companies").update({
        "business_context_refresh_heartbeat_at": stale,
    }).eq("id", _COMPANY_ID).execute()
    # …then beat it, exactly as the running worker's heartbeat task would.
    assert touch_business_context_refresh(_COMPANY_ID) is True
    assert fail_orphan_business_context_refreshes() == 0
    assert _row()["business_context_refresh_status"] == "generating"


# --------------------------------------------------------------------------- #
# 5. Status read + defaults
# --------------------------------------------------------------------------- #
def test_state_defaults_to_idle(seeded_company):
    assert business_context_refresh_state(_COMPANY_ID) == {
        "status": "idle", "error": None,
    }


def test_state_reflects_error(seeded_company):
    assert start_business_context_refresh(_COMPANY_ID) is True
    fail_business_context_refresh(_COMPANY_ID, "x" * 600)
    state = business_context_refresh_state(_COMPANY_ID)
    assert state["status"] == "error"
    assert len(state["error"]) == 500  # truncated, same cap as ask_jobs


# --------------------------------------------------------------------------- #
# 6. Guard: a trailing write from an abandoned worker can't clobber a
#    terminal row (mirrors complete_ask_job/fail_ask_job's own guard tests).
# --------------------------------------------------------------------------- #
def test_complete_does_not_resurrect_an_already_failed_row(seeded_company):
    assert start_business_context_refresh(_COMPANY_ID) is True
    fail_business_context_refresh(_COMPANY_ID, "sweep got here first")
    # A late completion from the (already-reaped) original worker must no-op.
    complete_business_context_refresh(_COMPANY_ID)
    row = _row()
    assert row["business_context_refresh_status"] == "error"
    assert row["business_context_refresh_error"] == "sweep got here first"


def test_fail_does_not_clobber_an_already_done_row(seeded_company):
    assert start_business_context_refresh(_COMPANY_ID) is True
    complete_business_context_refresh(_COMPANY_ID)
    fail_business_context_refresh(_COMPANY_ID, "a stray late failure")
    row = _row()
    assert row["business_context_refresh_status"] == "done"
    assert row["business_context_refresh_error"] is None
