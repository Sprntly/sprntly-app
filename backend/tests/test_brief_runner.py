"""Tests for app.brief_runner.

The legacy corpus→Claude brief path (auto_generate_brief / auto_generate_all)
has been retired — synthesis is the only engine. brief_runner now provides
status tracking + the drill-down warming fan-out (evidence + Asks; PRDs are
on-demand). These tests pin that warming behaviour.
"""
from __future__ import annotations

import asyncio

import pytest

from app.prompts import BRIEF_SCHEMA_VERSION


def test_get_status_empty_for_unknown(isolated_settings):
    import importlib
    import app.brief_runner as br
    importlib.reload(br)
    assert br.get_status("ghost") == {"status": "empty"}


def _save_ready_brief(db, dataset: str = "acme") -> None:
    db.insert_dataset(dataset, dataset.title())
    db.save_brief(dataset, "Test Week", {"insights": [{"title": "A"}]})


def test_get_status_ready_has_no_regenerating_flag(isolated_settings):
    """A cached brief with no generation in flight reports plain "ready" — the
    additive regenerating flag must be absent so nothing shows the banner."""
    br = _reload_brief_runner()
    _save_ready_brief(isolated_settings["db"])
    assert br.get_status("acme") == {"status": "ready"}


def test_get_status_regenerating_over_cached_brief(isolated_settings):
    """A regen running OVER a still-cached brief keeps status "ready" (so the
    current brief stays on screen) but surfaces regenerating=True so the home
    surface can show the "refreshing your brief" banner. Without this the
    in-flight regen is masked by the cached-brief short-circuit."""
    br = _reload_brief_runner()
    _save_ready_brief(isolated_settings["db"])
    br.set_status("acme", "generating")
    assert br.get_status("acme") == {"status": "ready", "regenerating": True}


def test_get_status_generating_without_brief_is_not_regenerating(isolated_settings):
    """First-run generation (no brief yet) reports "generating", NOT the
    regenerating-over-existing flag — that path drives the full WIP state."""
    br = _reload_brief_runner()
    br.set_status("void", "generating")
    assert br.get_status("void") == {"status": "generating"}


# ── PRD pre-warming removed — warming covers evidence + Asks only ────────
#
# Perf optimization: a PRD is the most expensive drill-down (a large 2-part
# LLM gen), so warming one per insight floods the warm queue and a user's
# "Generate PRD" click stalls behind the backlog. PRDs are now generated
# strictly on-demand (routes/prd.py → prd_runner.generate_prd, which does NOT
# acquire _WARM_SEMA, so a click runs immediately). The `_warm_prd` helper and
# its PRD fan-out loop were removed; these tests pin that warming never creates
# a PRD row or calls generate_prd, while still warming evidence + Asks.


def _reload_brief_runner():
    import importlib
    import app.brief_runner as br
    importlib.reload(br)
    return br


def test_warm_prd_helper_is_gone():
    """The PRD-warming helper must no longer exist (PRDs are on-demand)."""
    br = _reload_brief_runner()
    assert not hasattr(br, "_warm_prd"), "_warm_prd must be removed"
    # And the on-demand generate_prd import must no longer be pulled in here.
    assert not hasattr(br, "generate_prd"), (
        "brief_runner must not import generate_prd — PRDs are on-demand only"
    )


def test_warm_drilldowns_does_not_create_any_prd_row(isolated_settings):
    """Warming a brief must NOT write a warm PRD row for any insight, so a
    later POST /v1/prd/generate finds no existing PRD and runs immediately."""
    br = _reload_brief_runner()
    db = isolated_settings["db"]

    db.insert_dataset("acme", "Acme")
    brief_id = db.save_brief(
        "acme", "Test Week",
        {"insights": [{"title": "A"}, {"title": "B"}, {"title": "C"}]},
        schema_version=BRIEF_SCHEMA_VERSION,
    )
    brief = db.get_current_brief("acme")

    async def _drive():
        br._warm_drilldowns(brief, dataset="acme")
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(_drive())

    # No warm PRD row for any insight, on either variant.
    for idx in range(3):
        assert db.find_existing_prd(brief_id, idx, variant="v2") is None
        assert db.find_existing_prd(brief_id, idx, variant="v1") is None


def test_warm_drilldowns_warms_evidence_but_not_prd(isolated_settings, monkeypatch):
    """generate_evidence IS called during warming; generate_prd is NEVER
    imported/called from the warm path."""
    br = _reload_brief_runner()
    db = isolated_settings["db"]

    db.insert_dataset("acme", "Acme")
    db.save_brief(
        "acme", "Test Week",
        {"insights": [{"title": "A"}, {"title": "B"}]},
        schema_version=BRIEF_SCHEMA_VERSION,
    )
    brief = db.get_current_brief("acme")

    ev_calls: list[tuple] = []

    async def fake_generate_evidence(ev_id, b_id, idx):
        ev_calls.append((ev_id, b_id, idx))

    monkeypatch.setattr(br, "generate_evidence", fake_generate_evidence)

    # If anything tries to warm a PRD via the runner, blow up loudly.
    import app.prd_runner as prd_runner

    async def boom_generate_prd(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("generate_prd must NOT be called during warming")

    monkeypatch.setattr(prd_runner, "generate_prd", boom_generate_prd)

    async def _drive():
        br._warm_drilldowns(brief, dataset="acme")
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(_drive())

    # Evidence warmed for both insights; no PRD generation occurred.
    assert len(ev_calls) == 2, "expected evidence warmed for every insight"


def test_warm_drilldowns_warms_predefined_and_dynamic_asks(isolated_settings, monkeypatch):
    """Ask warming (predefined + per-insight dynamic) is untouched by the
    PRD removal — both warmers fire with the shared semaphore."""
    br = _reload_brief_runner()
    db = isolated_settings["db"]

    db.insert_dataset("acme", "Acme")
    db.save_brief(
        "acme", "Test Week", {"insights": [{"title": "A"}]},
        schema_version=BRIEF_SCHEMA_VERSION,
    )
    brief = db.get_current_brief("acme")

    # Don't actually run evidence LLM calls.
    async def noop_evidence(*a, **k):
        return None

    monkeypatch.setattr(br, "generate_evidence", noop_evidence)

    predefined: list[tuple] = []
    dynamic: list[tuple] = []
    monkeypatch.setattr(
        br, "warm_predefined_asks",
        lambda ds, sema: predefined.append((ds, sema)),
    )
    monkeypatch.setattr(
        br, "warm_brief_dynamic_asks",
        lambda ds, b, sema: dynamic.append((ds, b, sema)),
    )

    async def _drive():
        br._warm_drilldowns(brief, dataset="acme")
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(_drive())

    assert len(predefined) == 1 and predefined[0][0] == "acme", "predefined Ask warming must still fire"
    assert len(dynamic) == 1 and dynamic[0][0] == "acme", "dynamic Ask warming must still fire"
    # Both warmers share ONE per-loop semaphore instance (per-loop accessor —
    # replaces the old module-level _WARM_SEMA that broke across asyncio.run loops).
    sema_pre = predefined[0][1]
    assert isinstance(sema_pre, asyncio.Semaphore)
    assert dynamic[0][2] is sema_pre


def test_warm_drilldowns_skips_asks_without_dataset(isolated_settings, monkeypatch):
    """Ask warming is dataset-gated; with no dataset only evidence is warmed."""
    br = _reload_brief_runner()
    db = isolated_settings["db"]

    db.insert_dataset("acme", "Acme")
    db.save_brief(
        "acme", "Test Week", {"insights": [{"title": "A"}]},
        schema_version=BRIEF_SCHEMA_VERSION,
    )
    brief = db.get_current_brief("acme")

    async def noop_evidence(*a, **k):
        return None

    monkeypatch.setattr(br, "generate_evidence", noop_evidence)
    pre, dyn = [], []
    monkeypatch.setattr(br, "warm_predefined_asks", lambda *a: pre.append(a))
    monkeypatch.setattr(br, "warm_brief_dynamic_asks", lambda *a: dyn.append(a))

    async def _drive():
        br._warm_drilldowns(brief, dataset=None)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(_drive())
    assert pre == [] and dyn == [], "Ask warming must be skipped without a dataset"


def test_warm_drilldowns_noop_without_brief_id():
    """A brief with no DB id can't warm anything (no id to key rows on)."""
    br = _reload_brief_runner()
    # Should simply return without scheduling tasks or raising.
    br._warm_drilldowns({"insights": [{"title": "A"}]}, dataset="acme")


@pytest.mark.asyncio
async def test_on_demand_prd_generate_runs_immediately_with_no_existing(
    isolated_settings, monkeypatch
):
    """A user POST /v1/prd/generate with no existing PRD must start + dispatch
    generate_prd right away (the on-demand path). Since warming no longer
    creates a warm PRD row, find_existing_prd returns nothing and the click
    is served immediately."""
    from app.routes import prd as prd_routes

    db = isolated_settings["db"]
    db.insert_dataset("acme", "Acme")
    brief_id = db.save_brief(
        "acme", "Test Week", {"insights": [{"title": "A"}]},
        schema_version=BRIEF_SCHEMA_VERSION,
    )
    brief = db.get_current_brief("acme")

    # No warm PRD pre-exists.
    assert db.find_existing_prd(brief_id, 0, variant="v2") is None

    started: list[tuple] = []

    async def fake_generate_prd(prd_id, b_id, idx):
        started.append((prd_id, b_id, idx))

    monkeypatch.setattr(prd_routes, "generate_prd", fake_generate_prd)
    monkeypatch.setattr(prd_routes, "require_owned_brief", lambda bid, cid: brief)

    class _Ctx:
        company_id = "acme"

    body = prd_routes.GenerateIn(brief_id=brief_id, insight_index=0)
    resp = await prd_routes.generate(body, company=_Ctx())

    assert resp["status"] == "generating"
    # Let the scheduled task run.
    await asyncio.sleep(0)
    assert len(started) == 1, "on-demand generate_prd must dispatch immediately"


def test_on_demand_prd_path_does_not_acquire_warm_sema():
    """The on-demand PRD route must NOT be throttled by the warm semaphore:
    routes/prd.py and prd_runner.py must not reference brief_runner._WARM_SEMA."""
    import inspect
    import app.routes.prd as prd_routes
    import app.prd_runner as prd_runner

    for mod in (prd_routes, prd_runner):
        src = inspect.getsource(mod)
        assert "_WARM_SEMA" not in src, (
            f"{mod.__name__} must not couple to the warm semaphore"
        )
