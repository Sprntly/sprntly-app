"""Tests for app.routes.evidence — POST /v1/evidence/generate and GET /v1/evidence/{id}."""
from __future__ import annotations

import json


def _seed_corpus(data_dir, dataset="asurion", body="some corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


def _save_brief_with_insights(db_mod, dataset="asurion", insights=None):
    """Insert a brief row directly and return its id. Skips the LLM path."""
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


# ---- GET /v1/evidence/{id} --------------------------------------------------

def test_get_nonexistent_evidence_returns_404(app_client, isolated_settings):
    resp = app_client.get("/v1/evidence/9999")
    assert resp.status_code == 404


def test_get_evidence_without_auth_returns_401(unauth_client, isolated_settings):
    resp = unauth_client.get("/v1/evidence/1")
    assert resp.status_code == 401


def test_get_evidence_returns_row(app_client, isolated_settings):
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod)
    evidence_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="t", template_version=1
    )
    db_mod.complete_evidence(evidence_id, title="t", md="# Hello")

    resp = app_client.get(f"/v1/evidence/{evidence_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == evidence_id
    assert body["status"] == "ready"
    assert body["payload_md"] == "# Hello"


# ---- POST /v1/evidence/generate --------------------------------------------

def test_generate_without_auth_returns_401(unauth_client, isolated_settings):
    resp = unauth_client.post(
        "/v1/evidence/generate", json={"brief_id": 1, "insight_index": 0}
    )
    assert resp.status_code == 401


def test_generate_missing_brief_returns_404(app_client, isolated_settings):
    resp = app_client.post(
        "/v1/evidence/generate", json={"brief_id": 9999, "insight_index": 0}
    )
    assert resp.status_code == 404


def test_generate_out_of_range_insight_returns_400(app_client, isolated_settings):
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(
        db_mod, insights=[{"title": "only one"}]
    )
    resp = app_client.post(
        "/v1/evidence/generate", json={"brief_id": brief_id, "insight_index": 5}
    )
    assert resp.status_code == 400


def test_generate_happy_path_returns_generating_status(
    app_client, isolated_settings, fake_llm
):
    """A first-time generate request inserts a row in 'generating' state."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod)
    resp = app_client.post(
        "/v1/evidence/generate", json={"brief_id": brief_id, "insight_index": 0}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "evidence_id" in body
    # New row → status is 'generating' (or already-completed if the background
    # task raced through; we accept both).
    assert body["status"] in ("generating", "ready")
    assert body["title"] == "Insight A — number leads with $1M"


def test_generate_returns_existing_doc_when_not_forced(
    app_client, isolated_settings
):
    """If a ready evidence already exists for (brief, insight), POST returns it."""
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod)
    existing_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="t", template_version=1
    )
    db_mod.complete_evidence(existing_id, title="t", md="# Already here")

    resp = app_client.post(
        "/v1/evidence/generate",
        json={"brief_id": brief_id, "insight_index": 0, "force": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["evidence_id"] == existing_id
    assert body["status"] == "ready"
