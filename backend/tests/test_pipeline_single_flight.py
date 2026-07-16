"""Fix B: "whip up brief" single-flight.

The pipeline trigger gave no instant feedback and a full run takes ~5 min, so
users click it repeatedly — which used to spawn N concurrent runs that race on
the same deterministic KG ids and exhaust the httpx pool, tipping synthesis into
an empty brief. The trigger now collapses repeat clicks onto the in-flight run
and re-arms once it finishes (via a `finally`, so a crashed run never wedges it).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace


async def test_trigger_pipeline_collapses_repeat_clicks(monkeypatch):
    from app.routes import pipeline as route

    # Bypass the tenant-ownership guard (not under test here). *a soaks up the
    # workspace_id the route now passes alongside dataset + company_id.
    monkeypatch.setattr(route, "require_owned_dataset", lambda *a, **k: None)

    started = asyncio.Event()
    release = asyncio.Event()
    calls = {"n": 0}

    async def _fake_run(dataset, trigger="manual"):
        calls["n"] += 1
        started.set()
        await release.wait()  # stay "in flight" until the test releases us
        return {"status": "completed", "dataset": dataset}

    # The route does `from app.pipeline import run_full_pipeline` at call time,
    # so patch the attribute on the source module.
    monkeypatch.setattr("app.pipeline.run_full_pipeline", _fake_run)
    route._INFLIGHT.discard("ds-1")
    company = SimpleNamespace(company_id="co-1", workspace_id=None)

    # First click → a run starts.
    r1 = await route.trigger_pipeline("ds-1", company=company)
    assert r1["started"] is True
    await asyncio.wait_for(started.wait(), timeout=1)  # bg run now in flight

    # Two more rapid clicks WHILE it's in flight → collapsed, no new runs.
    r2 = await route.trigger_pipeline("ds-1", company=company)
    r3 = await route.trigger_pipeline("ds-1", company=company)
    assert r2 == {
        "started": False, "already_running": True,
        "dataset": "ds-1",
        "message": "A pipeline run is already in progress for this dataset.",
    }
    assert r3["started"] is False
    assert calls["n"] == 1  # still exactly ONE run despite three clicks

    # Let the in-flight run finish; the guard clears in its `finally`.
    release.set()
    for _ in range(100):
        if "ds-1" not in route._INFLIGHT:
            break
        await asyncio.sleep(0.01)
    assert "ds-1" not in route._INFLIGHT  # re-armed

    # A fresh click after completion starts a new run (guard was released).
    r4 = await route.trigger_pipeline("ds-1", company=company)
    assert r4["started"] is True
    for _ in range(100):
        if calls["n"] == 2:
            break
        await asyncio.sleep(0.01)
    assert calls["n"] == 2

    # Cleanup: let the last bg task drain so it doesn't outlive the test.
    for _ in range(100):
        if "ds-1" not in route._INFLIGHT:
            break
        await asyncio.sleep(0.01)
