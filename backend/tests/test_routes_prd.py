"""Tests for app.routes.prd — POST /v1/prd/generate and GET /v1/prd/{id}.

Mirrors test_routes_evidence — the two endpoints have the same shape.
"""
from __future__ import annotations


def _save_brief_with_insights(db_mod, dataset="asurion", insights=None):
    if insights is None:
        insights = [
            {"title": "Finding leads with $42M"},
            {"title": "Another finding"},
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

def test_get_nonexistent_prd_returns_404(app_client, isolated_settings):
    resp = app_client.get("/v1/prd/9999")
    assert resp.status_code == 404


def test_get_prd_without_auth_returns_401(unauth_client, isolated_settings):
    resp = unauth_client.get("/v1/prd/1")
    assert resp.status_code == 401


def test_get_prd_returns_row(app_client, isolated_settings):
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod)
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t", template_version=1
    )
    db_mod.complete_prd(prd_id, title="t", md="# PRD body")

    resp = app_client.get(f"/v1/prd/{prd_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == prd_id
    assert body["status"] == "ready"
    assert body["payload_md"] == "# PRD body"


# ---- POST /v1/prd/generate --------------------------------------------------

def test_generate_without_auth_returns_401(unauth_client, isolated_settings):
    resp = unauth_client.post(
        "/v1/prd/generate", json={"brief_id": 1, "insight_index": 0}
    )
    assert resp.status_code == 401


def test_generate_missing_brief_returns_404(app_client, isolated_settings):
    resp = app_client.post(
        "/v1/prd/generate", json={"brief_id": 9999, "insight_index": 0}
    )
    assert resp.status_code == 404


def test_generate_out_of_range_insight_returns_400(app_client, isolated_settings):
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(
        db_mod, insights=[{"title": "only one"}]
    )
    resp = app_client.post(
        "/v1/prd/generate", json={"brief_id": brief_id, "insight_index": 99}
    )
    assert resp.status_code == 400


def test_generate_happy_path_returns_generating_status(
    app_client, isolated_settings
):
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod)
    resp = app_client.post(
        "/v1/prd/generate", json={"brief_id": brief_id, "insight_index": 0}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "prd_id" in body
    assert body["status"] in ("generating", "ready")
    assert body["title"] == "Finding leads with $42M"


def test_generate_returns_existing_prd_when_not_forced(
    app_client, isolated_settings
):
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod)
    existing_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t", template_version=1
    )
    db_mod.complete_prd(existing_id, title="t", md="# Already here")

    resp = app_client.post(
        "/v1/prd/generate",
        json={"brief_id": brief_id, "insight_index": 0, "force": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["prd_id"] == existing_id
    assert body["status"] == "ready"
