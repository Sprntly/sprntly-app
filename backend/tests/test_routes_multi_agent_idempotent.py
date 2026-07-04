"""POST /v1/multi-agent/generate must be idempotent per (brief, insight).

A repeat click ("Generate PRD") for the same insight must NOT restart the
orchestration — it should resolve the in-flight / completed run and return it.
The run is anchored on the PRD row (the run's first DB write), stamped with the
run_id at creation so a concurrent second click dedupes against it with no race.

`force=true` bypasses the guard; a single-PRD row (run_id NULL) never
short-circuits a full multi-agent run.

The background orchestrator is stubbed so these tests exercise the endpoint's
dedupe logic, not real generation.
"""
from __future__ import annotations

import pytest

from app import multi_agent_orchestrator as orch
from app.db.client import require_client


def _save_brief_with_insights(db_mod, dataset, insights=None):
    if insights is None:
        insights = [
            {"title": "Insight A — leads drop off at checkout"},
            {"title": "Insight B — another finding"},
        ]
    payload = {"summary_headline": "stub", "insights": insights, "_schema_version": 1}
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


def _count_prds(brief_id, insight_index):
    c = require_client()
    rows = (
        c.table("prds")
        .select("id")
        .eq("brief_id", brief_id)
        .eq("insight_index", insight_index)
        .execute()
        .data
    )
    return len(rows)


@pytest.fixture
def stub_orchestrator(monkeypatch):
    """Replace the heavy background orchestrator with a recording no-op."""
    calls: list[dict] = []

    async def _noop(**kwargs):
        calls.append(kwargs)
        return {"run_id": kwargs.get("run_id"), "status": "ready"}

    monkeypatch.setattr(orch, "run_multi_agent_generation", _noop)
    return calls


def test_generate_creates_prd_stamped_with_run_id(
    tenant_client, isolated_settings, stub_orchestrator
):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")

    resp = t.client.post(
        "/v1/multi-agent/generate", json={"brief_id": brief_id, "insight_index": 0}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "generating"
    assert body.get("reused") is not True
    run_id = body["run_id"]

    # Exactly one PRD row, stamped with the run_id.
    c = require_client()
    rows = (
        c.table("prds").select("*").eq("brief_id", brief_id).eq("insight_index", 0)
        .execute().data
    )
    assert len(rows) == 1
    assert rows[0]["run_id"] == run_id
    assert rows[0]["variant"] == "v3"


def test_repeat_generate_reuses_run_without_new_prd(
    tenant_client, isolated_settings, stub_orchestrator
):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")

    first = t.client.post(
        "/v1/multi-agent/generate", json={"brief_id": brief_id, "insight_index": 0}
    ).json()
    assert _count_prds(brief_id, 0) == 1

    # Second click for the same insight: same run, no new PRD, no new run task.
    second = t.client.post(
        "/v1/multi-agent/generate",
        json={"brief_id": brief_id, "insight_index": 0, "force": False},
    )
    assert second.status_code == 200
    body = second.json()
    assert body["reused"] is True
    assert body["run_id"] == first["run_id"]
    assert _count_prds(brief_id, 0) == 1
    # Only the first call reached the orchestrator.
    assert len(stub_orchestrator) == 1


def test_force_generate_starts_a_new_run(
    tenant_client, isolated_settings, stub_orchestrator
):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")

    first = t.client.post(
        "/v1/multi-agent/generate", json={"brief_id": brief_id, "insight_index": 0}
    ).json()

    forced = t.client.post(
        "/v1/multi-agent/generate",
        json={"brief_id": brief_id, "insight_index": 0, "force": True},
    ).json()

    assert forced["run_id"] != first["run_id"]
    assert forced.get("reused") is not True
    assert _count_prds(brief_id, 0) == 2
    assert len(stub_orchestrator) == 2


def test_does_not_reuse_single_prd_row_without_run_id(
    tenant_client, isolated_settings, stub_orchestrator
):
    """A PRD made by the single-PRD path (run_id NULL) must not short-circuit a
    full multi-agent run — that path produces no analysis docs to poll."""
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")
    legacy = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=1, variant="v3",  # run_id defaults to None
    )
    db_mod.complete_prd(legacy, title="t", md="# single-PRD body")

    resp = t.client.post(
        "/v1/multi-agent/generate", json={"brief_id": brief_id, "insight_index": 0}
    )
    body = resp.json()
    assert body.get("reused") is not True
    assert body["run_id"]
    # A fresh multi-agent PRD was created alongside the legacy one.
    assert _count_prds(brief_id, 0) == 2
    assert len(stub_orchestrator) == 1


def test_reuses_warmed_run_id_stamped_prd(
    tenant_client, isolated_settings, stub_orchestrator
):
    """A prefetch-warmed PRD is run_id-stamped, so clicking 'Generate PRD'
    resolves the warm run instead of restarting — the warm↔click unification."""
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")
    warmed = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=1, variant="v3", run_id="warm-run-1",
    )
    db_mod.complete_prd(warmed, title="t", md="# warmed human PRD")

    resp = t.client.post(
        "/v1/multi-agent/generate", json={"brief_id": brief_id, "insight_index": 0}
    )
    body = resp.json()
    assert body["reused"] is True
    assert body["run_id"] == "warm-run-1"
    assert _count_prds(brief_id, 0) == 1          # no restart, no duplicate
    assert len(stub_orchestrator) == 0            # orchestrator not re-run


def test_other_insight_is_independent(
    tenant_client, isolated_settings, stub_orchestrator
):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")

    r0 = t.client.post(
        "/v1/multi-agent/generate", json={"brief_id": brief_id, "insight_index": 0}
    ).json()
    r1 = t.client.post(
        "/v1/multi-agent/generate", json={"brief_id": brief_id, "insight_index": 1}
    ).json()

    assert r0["run_id"] != r1["run_id"]
    assert _count_prds(brief_id, 0) == 1
    assert _count_prds(brief_id, 1) == 1
