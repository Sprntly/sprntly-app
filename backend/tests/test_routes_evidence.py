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
        template_version=1, variant="v3",
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
        template_version=1, variant="v3",
    )
    db_mod.complete_evidence(evidence_id, title="t", md="# A's evidence")

    b = tenant_client.make(slug="company-b")
    assert b.client.get(f"/v1/evidence/{evidence_id}").status_code == 404
    # Owner still succeeds.
    assert a.client.get(f"/v1/evidence/{evidence_id}").status_code == 200


# ---- GET /v1/evidence/by-insight/{brief_id}/{insight_index} -----------------

def test_by_insight_returns_evidence(tenant_client, isolated_settings):
    # The read-by-insight lookup resolves a brief insight's evidence so the UI
    # can populate the Evidence tab for the PRD being viewed/generated.
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")
    evidence_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=1, title="t",
        template_version=1, variant="v3",
    )
    db_mod.complete_evidence(evidence_id, title="t", md="# insight-1 evidence")

    resp = t.client.get(f"/v1/evidence/by-insight/{brief_id}/1")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == evidence_id
    assert body["insight_index"] == 1
    assert body["payload_md"] == "# insight-1 evidence"


def test_by_insight_returns_404_when_none(tenant_client, isolated_settings):
    # Brief exists but the insight has no evidence yet → 404 (UI swallows → empty).
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")
    assert t.client.get(f"/v1/evidence/by-insight/{brief_id}/0").status_code == 404


def test_by_insight_cross_tenant_returns_404(tenant_client, isolated_settings):
    # Company B can't read company A's brief-insight evidence (ownership via brief).
    a = tenant_client.make(slug="company-a")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="company-a")
    evidence_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=1, variant="v3",
    )
    db_mod.complete_evidence(evidence_id, title="t", md="# A's evidence")

    b = tenant_client.make(slug="company-b")
    assert b.client.get(f"/v1/evidence/by-insight/{brief_id}/0").status_code == 404
    assert a.client.get(f"/v1/evidence/by-insight/{brief_id}/0").status_code == 200


def test_by_insight_without_auth_returns_401(unauth_client, isolated_settings):
    assert unauth_client.get("/v1/evidence/by-insight/1/0").status_code == 401


def test_by_insight_three_segment_not_shadowed(tenant_client, isolated_settings):
    # The 3-segment /by-insight/{brief}/{idx} path resolves to get_by_insight and
    # is never coerced into the single-segment GET /{evidence_id} (no 422).
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")
    evidence_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=1, variant="v3",
    )
    db_mod.complete_evidence(evidence_id, title="t", md="# body")
    resp = t.client.get(f"/v1/evidence/by-insight/{brief_id}/0")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == evidence_id


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
    assert body["variant"] == "v3"


def test_generate_returns_existing_doc_when_not_forced(
    tenant_client, isolated_settings
):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")
    existing_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=1, variant="v3",
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
