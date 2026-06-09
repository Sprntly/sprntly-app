"""Tests for app.routes.evidence — POST /v1/evidence/generate,
GET /v1/evidence/{id}, and the cross-tenant isolation gate.

After the tenant-isolation fix these routes sit behind `require_company`. An
evidence row is owned via evidence → brief_id → brief.dataset (slug) → company.
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
    payload = {"summary_headline": "stub", "insights": insights, "_schema_version": 1}
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


# ---- GET /v1/evidence/{id} -------------------------------------------------

def test_get_nonexistent_evidence_returns_404(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    resp = t.client.get("/v1/evidence/9999")
    assert resp.status_code == 404


def test_get_evidence_without_auth_returns_401(unauth_client, isolated_settings):
    resp = unauth_client.get("/v1/evidence/1")
    assert resp.status_code == 401


def test_get_evidence_returns_row(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")
    evidence_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=1, variant="v2",
    )
    db_mod.complete_evidence(evidence_id, title="t", md="# body")

    resp = t.client.get(f"/v1/evidence/{evidence_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == evidence_id
    assert body["status"] == "ready"
    assert body["payload_md"] == "# body"


def test_get_evidence_cross_tenant_returns_404(tenant_client, isolated_settings):
    """Company B must NOT read company A's evidence."""
    a = tenant_client.make(slug="company-a")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="company-a")
    evidence_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=1, variant="v2",
    )
    db_mod.complete_evidence(evidence_id, title="t", md="# A's evidence")

    b = tenant_client.make(slug="company-b")
    assert b.client.get(f"/v1/evidence/{evidence_id}").status_code == 404
    # Owner still succeeds.
    assert a.client.get(f"/v1/evidence/{evidence_id}").status_code == 200


# ---- POST /v1/evidence/generate --------------------------------------------

def test_generate_without_auth_returns_401(unauth_client, isolated_settings):
    resp = unauth_client.post(
        "/v1/evidence/generate", json={"brief_id": 1, "insight_index": 0}
    )
    assert resp.status_code == 401


def test_generate_missing_brief_returns_404(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    resp = t.client.post(
        "/v1/evidence/generate", json={"brief_id": 9999, "insight_index": 0}
    )
    assert resp.status_code == 404


def test_generate_cross_tenant_brief_returns_404(tenant_client, isolated_settings):
    tenant_client.make(slug="company-a")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="company-a")
    b = tenant_client.make(slug="company-b")
    resp = b.client.post(
        "/v1/evidence/generate", json={"brief_id": brief_id, "insight_index": 0}
    )
    assert resp.status_code == 404


def test_generate_out_of_range_insight_returns_400(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(
        db_mod, dataset="acme", insights=[{"title": "only one"}]
    )
    resp = t.client.post(
        "/v1/evidence/generate", json={"brief_id": brief_id, "insight_index": 5}
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
        "/v1/evidence/generate", json={"brief_id": brief_id, "insight_index": 0}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "evidence_id" in body
    assert body["status"] in ("generating", "ready")
    assert body["title"] == "Insight A — number leads with $1M"
    assert body["variant"] == "v2"


def test_generate_returns_existing_doc_when_not_forced(
    tenant_client, isolated_settings
):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")
    existing_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=1, variant="v2",
    )
    db_mod.complete_evidence(existing_id, title="t", md="# Already here")

    resp = t.client.post(
        "/v1/evidence/generate",
        json={"brief_id": brief_id, "insight_index": 0, "force": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["evidence_id"] == existing_id
    assert body["status"] == "ready"
