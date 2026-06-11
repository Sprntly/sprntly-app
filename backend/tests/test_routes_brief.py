"""Tests for /v1/brief routes — dataset-required default + tenant isolation.

After the tenant-isolation fix these routes sit behind `require_company`. The
`dataset` query param (a slug) must resolve to the caller's company; an
unowned/foreign slug is 404. `/{brief_id}` resolves brief → dataset → company.
"""
from __future__ import annotations


def _save_brief(db, dataset, insights=None):
    payload = {"insights": insights or [], "_schema_version": 1}
    return db.save_brief(dataset, "Week 1", payload, schema_version=1)


def test_dataset_query_param_now_required(tenant_client):
    # dataset is a required query param — omitting it is a validation error.
    t = tenant_client.make(slug="acme")
    r = t.client.get("/v1/brief/status")
    assert r.status_code == 422


def test_status_owned_dataset_is_empty(tenant_client):
    t = tenant_client.make(slug="acme")
    r = t.client.get("/v1/brief/status?dataset=acme")
    assert r.status_code == 200
    body = r.json()
    assert body["dataset"] == "acme"
    assert body["status"] == "empty"


def test_status_foreign_dataset_returns_404(tenant_client):
    """A slug that isn't the caller's company → 404 (no existence disclosure)."""
    tenant_client.make(slug="company-a")
    b = tenant_client.make(slug="company-b")
    assert b.client.get("/v1/brief/status?dataset=company-a").status_code == 404


def test_current_404_when_no_brief(tenant_client):
    t = tenant_client.make(slug="acme")
    r = t.client.get("/v1/brief/current?dataset=acme")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["message"] == "No brief generated yet"


def test_current_returns_saved_brief(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    brief_id = _save_brief(db, "acme")
    r = t.client.get("/v1/brief/current?dataset=acme")
    assert r.status_code == 200
    assert r.json()["id"] == brief_id


def test_current_includes_company_display_name(tenant_client, isolated_settings):
    """The UI renders company_name — the dataset slug is an internal key only."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    _save_brief(db, "acme")
    r = t.client.get("/v1/brief/current?dataset=acme")
    assert r.status_code == 200
    body = r.json()
    assert body["dataset"] == "acme"
    assert body["company_name"] == "Acme"  # companies.display_name, not the slug


def test_brief_by_id_includes_company_display_name(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db = isolated_settings["db"]
    brief_id = _save_brief(db, "acme")
    r = t.client.get(f"/v1/brief/{brief_id}")
    assert r.status_code == 200
    assert r.json()["company_name"] == "Acme"


def test_current_cross_tenant_returns_404(tenant_client, isolated_settings):
    """Company B cannot read company A's current brief via A's slug."""
    tenant_client.make(slug="company-a")
    db = isolated_settings["db"]
    _save_brief(db, "company-a")
    b = tenant_client.make(slug="company-b")
    assert b.client.get("/v1/brief/current?dataset=company-a").status_code == 404


def test_brief_by_id_cross_tenant_returns_404(tenant_client, isolated_settings):
    """GET /v1/brief/{brief_id} resolves brief → dataset → company; foreign 404."""
    a = tenant_client.make(slug="company-a")
    db = isolated_settings["db"]
    brief_id = _save_brief(db, "company-a")
    b = tenant_client.make(slug="company-b")
    assert b.client.get(f"/v1/brief/{brief_id}").status_code == 404
    # Owner still succeeds.
    assert a.client.get(f"/v1/brief/{brief_id}").status_code == 200


def test_brief_routes_require_auth(unauth_client):
    r = unauth_client.get("/v1/brief/status?dataset=acme")
    assert r.status_code == 401
    r = unauth_client.get("/v1/brief/current?dataset=acme")
    assert r.status_code == 401
