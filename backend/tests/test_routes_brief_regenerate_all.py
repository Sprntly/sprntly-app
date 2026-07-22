"""Tests for POST /v1/brief/regenerate-all — the full regeneration pipeline
(KG ingest → brief → PRD → evidence) behind the Connectors settings
"Regenerate brief" button.

The route is fire-and-forget: it schedules `_full_pipeline_bg` and returns
{"started": True, ...} immediately. The route tests patch the background body so
no real LLM/DB work runs; separate unit tests drive the orchestrator directly to
assert the digest→brief→PRD→evidence ordering and per-insight fan-out.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import app.routes.brief as brief_routes


# ── Route behaviour + tenant isolation ─────────────────────────────────────


def test_regenerate_all_requires_dataset(tenant_client):
    """`dataset` is a required query param — omitting it is a 422."""
    t = tenant_client.make(slug="acme")
    assert t.client.post("/v1/brief/regenerate-all").status_code == 422


def test_regenerate_all_starts_pipeline_for_owned_dataset(tenant_client):
    t = tenant_client.make(slug="acme")
    with patch.object(brief_routes, "_full_pipeline_bg", new_callable=AsyncMock) as bg, \
         patch.object(brief_routes, "has_brief_data_source", return_value=True):
        r = t.client.post("/v1/brief/regenerate-all?dataset=acme")
    assert r.status_code == 200
    assert r.json() == {"started": True, "dataset": "acme"}
    # The full pipeline was scheduled for the caller's dataset.
    bg.assert_called_once_with("acme")


def test_regenerate_all_foreign_dataset_returns_404(tenant_client):
    """A slug that isn't the caller's company → 404 (no existence disclosure),
    and the pipeline is never scheduled."""
    tenant_client.make(slug="company-a")
    b = tenant_client.make(slug="company-b")
    with patch.object(brief_routes, "_full_pipeline_bg", new_callable=AsyncMock) as bg:
        r = b.client.post("/v1/brief/regenerate-all?dataset=company-a")
    assert r.status_code == 404
    bg.assert_not_called()


def test_regenerate_all_requires_auth(unauth_client):
    assert (
        unauth_client.post("/v1/brief/regenerate-all?dataset=acme").status_code == 401
    )


# ── Orchestrator: digest → brief → PRD → evidence ordering ──────────────────


def test_full_pipeline_bg_ingests_then_briefs_then_downstream(monkeypatch):
    """_full_pipeline_bg runs, in order: corpus-seed kickoff → brief synthesis →
    downstream PRD/evidence fan-out → drill-down warming, and marks the brief
    status generating→ready."""
    order: list = []

    monkeypatch.setattr(
        brief_routes, "set_status",
        lambda dataset, status, **k: order.append(("status", status)),
    )
    monkeypatch.setattr(brief_routes, "resolve_company", lambda d: ("co-1", d))
    monkeypatch.setattr(
        brief_routes, "kickoff_corpus_seed",
        lambda cid, slug: order.append(("kickoff", cid, slug)) or True,
    )
    monkeypatch.setattr(
        brief_routes, "generate_brief_for",
        lambda d, **k: order.append(("brief", d)),
    )

    async def _fake_downstream(dataset):
        order.append(("downstream", dataset))

    monkeypatch.setattr(brief_routes, "_generate_downstream_docs", _fake_downstream)
    monkeypatch.setattr(
        brief_routes, "warm_synthesis_drilldowns",
        lambda d: order.append(("warm", d)),
    )

    asyncio.run(brief_routes._full_pipeline_bg("acme"))

    names = [step[0] for step in order]
    assert names.index("kickoff") < names.index("brief") < names.index("downstream")
    assert ("kickoff", "co-1", "acme") in order
    assert ("brief", "acme") in order
    assert ("downstream", "acme") in order
    # Status transitions: flip to generating up front, then ready after synthesis.
    assert ("status", "generating") in order
    assert ("status", "ready") in order


def test_full_pipeline_bg_stops_after_empty_kg(monkeypatch):
    """When synthesis raises EmptyKnowledgeGraphError, the brief is marked failed
    and no PRD/evidence fan-out runs."""
    from app.synthesis.agent import EmptyKnowledgeGraphError

    statuses: list = []
    downstream_ran = []

    monkeypatch.setattr(
        brief_routes, "set_status",
        lambda dataset, status, **k: statuses.append(status),
    )
    monkeypatch.setattr(brief_routes, "resolve_company", lambda d: ("co-1", d))
    monkeypatch.setattr(brief_routes, "kickoff_corpus_seed", lambda cid, slug: True)

    def _raise(_d, **_k):
        raise EmptyKnowledgeGraphError("no themes")

    monkeypatch.setattr(brief_routes, "generate_brief_for", _raise)

    async def _fake_downstream(dataset):
        downstream_ran.append(dataset)

    monkeypatch.setattr(brief_routes, "_generate_downstream_docs", _fake_downstream)

    asyncio.run(brief_routes._full_pipeline_bg("acme"))

    assert statuses[-1] == "failed"
    assert downstream_ran == []


# ── Downstream fan-out: one PRD + one evidence doc per insight ──────────────


def test_generate_downstream_docs_fans_out_per_insight(monkeypatch):
    brief = {"id": 7, "insights": [{"title": "A"}, {"title": "B"}]}
    monkeypatch.setattr(brief_routes, "get_current_brief", lambda d: brief)
    monkeypatch.setattr(brief_routes, "find_existing_prd", lambda *a, **k: None)
    monkeypatch.setattr(brief_routes, "find_existing_evidence", lambda *a, **k: None)

    prd_calls: list = []
    ev_calls: list = []

    def _start_prd(**k):
        prd_calls.append(("start", k["insight_index"]))
        return 100 + k["insight_index"]

    def _start_evidence(**k):
        ev_calls.append(("start", k["insight_index"]))
        return 200 + k["insight_index"]

    monkeypatch.setattr(brief_routes, "start_prd", _start_prd)
    monkeypatch.setattr(brief_routes, "start_evidence", _start_evidence)

    async def _gen_prd(prd_id, brief_id, idx):
        prd_calls.append(("gen", prd_id, brief_id, idx))

    async def _gen_ev(ev_id, brief_id, idx):
        ev_calls.append(("gen", ev_id, brief_id, idx))

    monkeypatch.setattr(brief_routes, "generate_prd", _gen_prd)
    monkeypatch.setattr(brief_routes, "generate_evidence_kg", _gen_ev)

    asyncio.run(brief_routes._generate_downstream_docs("acme"))

    # Both insights produced a PRD and an evidence doc, keyed to brief 7.
    assert ("gen", 100, 7, 0) in prd_calls
    assert ("gen", 101, 7, 1) in prd_calls
    assert ("gen", 200, 7, 0) in ev_calls
    assert ("gen", 201, 7, 1) in ev_calls


def test_generate_downstream_docs_skips_existing(monkeypatch):
    """A (brief, insight) that already has a ready/generating doc isn't regenerated."""
    brief = {"id": 7, "insights": [{"title": "A"}]}
    monkeypatch.setattr(brief_routes, "get_current_brief", lambda d: brief)
    monkeypatch.setattr(brief_routes, "find_existing_prd", lambda *a, **k: {"id": 1})
    monkeypatch.setattr(brief_routes, "find_existing_evidence", lambda *a, **k: {"id": 2})

    started: list = []
    monkeypatch.setattr(brief_routes, "start_prd", lambda **k: started.append("prd"))
    monkeypatch.setattr(brief_routes, "start_evidence", lambda **k: started.append("ev"))

    asyncio.run(brief_routes._generate_downstream_docs("acme"))
    assert started == []


def test_generate_downstream_docs_noop_without_brief(monkeypatch):
    """No current brief → nothing to fan out from; must not raise."""
    monkeypatch.setattr(brief_routes, "get_current_brief", lambda d: None)
    # Should complete without error.
    asyncio.run(brief_routes._generate_downstream_docs("acme"))


def test_generate_downstream_docs_isolates_prd_failure(monkeypatch):
    """A PRD generation failure for one insight must not block evidence for it
    or work on other insights."""
    brief = {"id": 7, "insights": [{"title": "A"}]}
    monkeypatch.setattr(brief_routes, "get_current_brief", lambda d: brief)
    monkeypatch.setattr(brief_routes, "find_existing_prd", lambda *a, **k: None)
    monkeypatch.setattr(brief_routes, "find_existing_evidence", lambda *a, **k: None)
    monkeypatch.setattr(brief_routes, "start_prd", lambda **k: 100)
    monkeypatch.setattr(brief_routes, "start_evidence", lambda **k: 200)

    ev_generated: list = []

    async def _gen_prd_fail(*a):
        raise RuntimeError("prd boom")

    async def _gen_ev(ev_id, brief_id, idx):
        ev_generated.append((ev_id, brief_id, idx))

    monkeypatch.setattr(brief_routes, "generate_prd", _gen_prd_fail)
    monkeypatch.setattr(brief_routes, "generate_evidence_kg", _gen_ev)

    # Does not raise despite the PRD failure, and evidence still runs.
    asyncio.run(brief_routes._generate_downstream_docs("acme"))
    assert ev_generated == [(200, 7, 0)]
