"""Restart-interrupted pipeline runs get a real, durable status.

A deploy restart kills the in-flight regenerate task silently (observed on
staging 2026-07-27): the in-memory brief status dies with the process and
nothing records the interruption. Fix under test:

  - the regenerate routes bracket their work in a durable pipeline_runs row;
  - starting a new run supersedes any stale 'running' row for the SAME dataset
    (precise, immediate — no age gate needed within one dataset);
  - an age-gated sweep (startup + the scheduler's 5-minute heal job) fails
    abandoned rows nobody retried. Age-gated because staging and prod share
    one Supabase project (see db/pipeline_runs.fail_orphan_running_runs).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.pipeline_runs import (
    INTERRUPTED_RUN_ERROR,
    create_run,
    fail_orphan_running_runs,
    get_latest_run,
    supersede_running_runs,
)


def _rows(db, dataset=None):
    q = db.table("pipeline_runs").select("*")
    if dataset:
        q = q.eq("dataset", dataset)
    return q.execute().data or []


def test_supersede_fails_running_rows_for_dataset_only(isolated_settings):
    db = isolated_settings["supabase"]
    stale = create_run("acme", trigger="regenerate")
    other = create_run("globex", trigger="regenerate")

    n = supersede_running_runs("acme")
    assert n == 1
    acme = {r["id"]: r for r in _rows(db, "acme")}
    assert acme[stale]["status"] == "failed"
    assert INTERRUPTED_RUN_ERROR in (acme[stale]["error"] or "")
    # The other dataset's live run is untouched.
    assert _rows(db, "globex")[0]["status"] == "running"
    assert _rows(db, "globex")[0]["id"] == other


def test_orphan_sweep_is_age_gated(isolated_settings):
    db = isolated_settings["supabase"]
    fresh = create_run("acme", trigger="regenerate")
    old = create_run("acme", trigger="regenerate-all")
    stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
    db.table("pipeline_runs").update({"started_at": stale_ts}).eq("id", old).execute()

    n = fail_orphan_running_runs()
    assert n == 1
    rows = {r["id"]: r for r in _rows(db, "acme")}
    # The old row is failed with the interrupt message…
    assert rows[old]["status"] == "failed"
    assert INTERRUPTED_RUN_ERROR in (rows[old]["error"] or "")
    # …the fresh one (possibly owned by a live process) is untouched.
    assert rows[fresh]["status"] == "running"


def test_regenerate_route_brackets_a_durable_run(
    tenant_client, isolated_settings, monkeypatch
):
    """POST /v1/brief/regenerate opens a pipeline_runs row and completes it on
    success — so an interruption would leave a 'running' row behind as durable
    evidence instead of vanishing with the process."""
    import asyncio

    from app.routes import brief as brief_routes

    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(brief_routes, "generate_brief_for",
                        lambda dataset, deliver: {"insights": []})
    monkeypatch.setattr(brief_routes, "_notify_brief_ready",
                        lambda dataset, brief: None)
    monkeypatch.setattr(brief_routes, "warm_synthesis_drilldowns",
                        lambda dataset: None)

    asyncio.run(brief_routes._synthesis_generate_bg("acme"))

    run = get_latest_run("acme")
    assert run is not None
    assert run["trigger"] == "regenerate"
    assert run["status"] == "completed"


def test_regenerate_supersedes_prior_interrupted_run(
    tenant_client, isolated_settings, monkeypatch
):
    """A retry after a restart marks the orphaned prior run failed immediately
    (no waiting for the age sweep)."""
    import asyncio

    from app.routes import brief as brief_routes

    tenant_client.make(slug="acme")
    orphan = create_run("acme", trigger="regenerate")

    monkeypatch.setattr(brief_routes, "generate_brief_for",
                        lambda dataset, deliver: {"insights": []})
    monkeypatch.setattr(brief_routes, "_notify_brief_ready",
                        lambda dataset, brief: None)
    monkeypatch.setattr(brief_routes, "warm_synthesis_drilldowns",
                        lambda dataset: None)
    asyncio.run(brief_routes._synthesis_generate_bg("acme"))

    db = isolated_settings["supabase"]
    rows = {r["id"]: r for r in _rows(db, "acme")}
    assert rows[orphan]["status"] == "failed"
    assert INTERRUPTED_RUN_ERROR in (rows[orphan]["error"] or "")
    # And the retry's own run completed. (Not get_latest_run: both rows can
    # share a same-millisecond started_at in the fake, making "latest" a tie.)
    retry_rows = [r for rid, r in rows.items() if rid != orphan]
    assert len(retry_rows) == 1
    assert retry_rows[0]["status"] == "completed"


def test_failed_generation_fails_the_durable_run(
    tenant_client, isolated_settings, monkeypatch
):
    import asyncio

    from app.routes import brief as brief_routes

    tenant_client.make(slug="acme")

    def _boom(dataset, deliver):
        raise RuntimeError("compose exploded")

    monkeypatch.setattr(brief_routes, "generate_brief_for", _boom)
    asyncio.run(brief_routes._synthesis_generate_bg("acme"))

    run = get_latest_run("acme")
    assert run["status"] == "failed"
    assert "failed" in (run["error"] or "").lower()
