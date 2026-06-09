"""Tests for app.routes.prd — POST /v1/prd/generate, GET/PUT /v1/prd/{id},
versions, and the cross-tenant isolation gate.

After the tenant-isolation fix these routes sit behind `require_company`
(not the non-tenant `require_session`). The owning company of a prd is
resolved via prd → brief_id → brief.dataset (slug) → company. A dataset slug
IS a company slug, so each test seeds a company whose slug equals the brief's
dataset, then authenticates as a user in that company.
"""
from __future__ import annotations


def _seed_corpus(data_dir, dataset, body="some corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


def _save_brief_with_insights(db_mod, dataset, insights=None):
    if insights is None:
        insights = [
            {"title": "Insight A — number leads with $1M"},
            {"title": "Insight B — another finding"},
        ]
    payload = {
        "summary_headline": "stub",
        "insights": insights,
        "_schema_version": 1,
    }
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


# ---- GET /v1/prd/{id} -------------------------------------------------------

def test_get_nonexistent_prd_returns_404(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    resp = t.client.get("/v1/prd/9999")
    assert resp.status_code == 404


def test_get_prd_without_auth_returns_401(unauth_client, isolated_settings):
    resp = unauth_client.get("/v1/prd/1")
    assert resp.status_code == 401


def test_get_prd_returns_row(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=1, variant="v2",
    )
    db_mod.complete_prd(prd_id, title="t", md="# PRD body")

    resp = t.client.get(f"/v1/prd/{prd_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == prd_id
    assert body["status"] == "ready"
    assert body["payload_md"] == "# PRD body"


def test_get_permissive_on_v1_rows(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")
    v1_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t", template_version=1
    )  # default variant='v1'
    db_mod.complete_prd(v1_id, title="t", md="# legacy v1 body")

    resp = t.client.get(f"/v1/prd/{v1_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == v1_id
    assert body["variant"] == "v1"
    assert body["payload_md"] == "# legacy v1 body"


# ---- cross-tenant isolation -------------------------------------------------

def test_get_prd_cross_tenant_returns_404(tenant_client, isolated_settings):
    """Company B must NOT read company A's PRD — 404 (no existence disclosure)."""
    a = tenant_client.make(slug="company-a")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="company-a")
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=1, variant="v2",
    )
    db_mod.complete_prd(prd_id, title="t", md="# A's secret PRD")

    b = tenant_client.make(slug="company-b")
    resp = b.client.get(f"/v1/prd/{prd_id}")
    assert resp.status_code == 404
    # And the owner still succeeds (positive control).
    assert a.client.get(f"/v1/prd/{prd_id}").status_code == 200


def test_put_prd_cross_tenant_returns_404(tenant_client, isolated_settings):
    """Company B cannot edit company A's PRD."""
    tenant_client.make(slug="company-a")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="company-a")
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=1, variant="v2",
    )
    db_mod.complete_prd(prd_id, title="t", md="# A's PRD")

    b = tenant_client.make(slug="company-b")
    resp = b.client.put(
        f"/v1/prd/{prd_id}", json={"title": "hijack", "payload_md": "owned"}
    )
    assert resp.status_code == 404
    # The row must be untouched.
    assert db_mod.get_prd(prd_id)["payload_md"] == "# A's PRD"


def test_prd_versions_cross_tenant_returns_404(tenant_client, isolated_settings):
    tenant_client.make(slug="company-a")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="company-a")
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=1, variant="v2",
    )
    db_mod.complete_prd(prd_id, title="t", md="# A's PRD")

    b = tenant_client.make(slug="company-b")
    assert b.client.get(f"/v1/prd/{prd_id}/versions").status_code == 404
    assert b.client.post(
        f"/v1/prd/{prd_id}/versions",
        json={"title": "t", "payload_md": "x", "label": "m"},
    ).status_code == 404
    assert b.client.post(
        f"/v1/prd/{prd_id}/versions/1/restore"
    ).status_code == 404


def test_generate_cross_tenant_brief_returns_404(tenant_client, isolated_settings):
    """POST /generate must reject a brief_id that belongs to another company."""
    tenant_client.make(slug="company-a")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="company-a")

    b = tenant_client.make(slug="company-b")
    resp = b.client.post(
        "/v1/prd/generate", json={"brief_id": brief_id, "insight_index": 0}
    )
    assert resp.status_code == 404


# ---- POST /v1/prd/generate --------------------------------------------------

def test_generate_without_auth_returns_401(unauth_client, isolated_settings):
    resp = unauth_client.post(
        "/v1/prd/generate", json={"brief_id": 1, "insight_index": 0}
    )
    assert resp.status_code == 401


def test_generate_missing_brief_returns_404(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    resp = t.client.post(
        "/v1/prd/generate", json={"brief_id": 9999, "insight_index": 0}
    )
    assert resp.status_code == 404


def test_generate_out_of_range_insight_returns_400(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(
        db_mod, dataset="acme", insights=[{"title": "only one"}]
    )
    resp = t.client.post(
        "/v1/prd/generate", json={"brief_id": brief_id, "insight_index": 99}
    )
    assert resp.status_code == 400


def test_generate_happy_path_returns_generating_status(
    tenant_client, isolated_settings, fake_llm
):
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")
    resp = t.client.post(
        "/v1/prd/generate", json={"brief_id": brief_id, "insight_index": 0}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "prd_id" in body
    assert body["status"] in ("generating", "ready")
    assert body["title"] == "Insight A — number leads with $1M"
    assert body["variant"] == "v2"


def test_generate_returns_existing_prd_when_not_forced(
    tenant_client, isolated_settings
):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")
    existing_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=1, variant="v2",
    )
    db_mod.complete_prd(existing_id, title="t", md="# Already here")

    resp = t.client.post(
        "/v1/prd/generate",
        json={"brief_id": brief_id, "insight_index": 0, "force": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["prd_id"] == existing_id
    assert body["status"] == "ready"


def test_generate_does_not_dedupe_against_v1_row(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")
    v1_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t", template_version=1
    )
    db_mod.complete_prd(v1_id, title="t", md="# v1 body")

    resp = t.client.post(
        "/v1/prd/generate", json={"brief_id": brief_id, "insight_index": 0}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["prd_id"] != v1_id
    assert body["variant"] == "v2"


def test_generate_via_prd_author_skill_through_canonical_path(
    tenant_client, isolated_settings, monkeypatch
):
    """A 2-part document flows end-to-end through the canonical
    /v1/prd/generate path."""
    import asyncio
    from app import prd_runner
    from app.graph.gateway import LLMResult

    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")

    captured: dict = {}
    # Two-call generation: each call returns only its assigned half, keyed by
    # the call's purpose (Part A vs Part B).
    part_a = (
        "# Claims — Move deductible upfront\n\n"
        "# Part A — Product Requirements Document (human-readable)\n"
        "## 1. Problem & evidence\n57% abandon.\n"
    )
    part_b = (
        "# Part B — Implementation Spec (LLM-readable / agent-executable)\n"
        "WHEN x THE SYSTEM SHALL y.\n"
    )

    def _capture(**kwargs):
        captured.update(kwargs)
        output = part_b if kwargs.get("purpose") == "generate_prd_part_b" else part_a
        return LLMResult(
            output=output, model="claude-sonnet-4-6",
            prompt_version=kwargs["prompt_version"] + "+prd-author@abc123",
            input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
            cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=1,
            stop_reason="end_turn",
        )

    monkeypatch.setattr(prd_runner, "llm_call", _capture)

    resp = t.client.post(
        "/v1/prd/generate",
        json={"brief_id": brief_id, "insight_index": 0, "force": True},
    )
    assert resp.status_code == 200
    prd_id = resp.json()["prd_id"]

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(prd_runner.generate_prd(prd_id, brief_id, 0))
    finally:
        loop.close()

    assert captured["skill"] == "prd-author"
    row = db_mod.get_prd(prd_id)
    assert row["status"] == "ready"
    assert row["variant"] == "v2"
    assert "Part A — Product Requirements Document" in row["payload_md"]
    assert "Part B — Implementation Spec" not in row["payload_md"]
    assert "Part B — Implementation Spec" in row["llm_part"]
