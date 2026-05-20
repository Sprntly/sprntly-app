"""Tests for app.routes.prd_v2 — POST /v1/prd/v2/generate
and GET /v1/prd/v2/{id}.

Mirrors test_routes_prd.py, with the variant-scoping cases that
test_routes_evidence_v2.py introduced: v1 and v2 rows are stored in the
same table but must not dedupe against each other.
"""
from __future__ import annotations


def _seed_corpus(data_dir, dataset="asurion", body="some corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


def _save_brief_with_insights(db_mod, dataset="asurion", insights=None):
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


# ---- GET /v1/prd/v2/{id} ---------------------------------------------------

def test_get_nonexistent_v2_prd_returns_404(app_client, isolated_settings):
    resp = app_client.get("/v1/prd/v2/9999")
    assert resp.status_code == 404


def test_get_v2_prd_without_auth_returns_401(unauth_client, isolated_settings):
    resp = unauth_client.get("/v1/prd/v2/1")
    assert resp.status_code == 401


def test_get_v2_endpoint_rejects_v1_row(app_client, isolated_settings):
    """If a caller asks for a v2 row by id but the row is actually v1, 409."""
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod)
    v1_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t", template_version=1
    )  # default variant='v1'
    db_mod.complete_prd(v1_id, title="t", md="# v1 body")

    resp = app_client.get(f"/v1/prd/v2/{v1_id}")
    assert resp.status_code == 409


def test_get_v2_prd_returns_row(app_client, isolated_settings):
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod)
    v2_id = db_mod.start_prd(
        brief_id=brief_id,
        insight_index=0,
        title="t",
        template_version=1,
        variant="v2",
    )
    db_mod.complete_prd(v2_id, title="t", md="# v2 body")

    resp = app_client.get(f"/v1/prd/v2/{v2_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == v2_id
    assert body["status"] == "ready"
    assert body["payload_md"] == "# v2 body"
    assert body["variant"] == "v2"


# ---- POST /v1/prd/v2/generate ----------------------------------------------

def test_generate_v2_without_auth_returns_401(unauth_client, isolated_settings):
    resp = unauth_client.post(
        "/v1/prd/v2/generate", json={"brief_id": 1, "insight_index": 0}
    )
    assert resp.status_code == 401


def test_generate_v2_missing_brief_returns_404(app_client, isolated_settings):
    resp = app_client.post(
        "/v1/prd/v2/generate", json={"brief_id": 9999, "insight_index": 0}
    )
    assert resp.status_code == 404


def test_generate_v2_out_of_range_insight_returns_400(
    app_client, isolated_settings
):
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(
        db_mod, insights=[{"title": "only one"}]
    )
    resp = app_client.post(
        "/v1/prd/v2/generate",
        json={"brief_id": brief_id, "insight_index": 5},
    )
    assert resp.status_code == 400


def test_generate_v2_happy_path_returns_generating_status(
    app_client, isolated_settings, fake_llm
):
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod)
    resp = app_client.post(
        "/v1/prd/v2/generate",
        json={"brief_id": brief_id, "insight_index": 0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "prd_id" in body
    assert body["status"] in ("generating", "ready")
    assert body["title"] == "Insight A — number leads with $1M"
    assert body["variant"] == "v2"


def test_generate_v2_returns_existing_doc_when_not_forced(
    app_client, isolated_settings
):
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod)
    existing_id = db_mod.start_prd(
        brief_id=brief_id,
        insight_index=0,
        title="t",
        template_version=1,
        variant="v2",
    )
    db_mod.complete_prd(existing_id, title="t", md="# Already here (v2)")

    resp = app_client.post(
        "/v1/prd/v2/generate",
        json={"brief_id": brief_id, "insight_index": 0, "force": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["prd_id"] == existing_id
    assert body["status"] == "ready"
    assert body["variant"] == "v2"


def test_generate_v2_does_not_dedupe_against_v1_row(
    app_client, isolated_settings
):
    """A ready v1 PRD for the same (brief, insight) must NOT be returned by
    the v2 generate endpoint — variant scoping should make v2 start fresh."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod)
    v1_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t", template_version=1
    )
    db_mod.complete_prd(v1_id, title="t", md="# v1 body")

    resp = app_client.post(
        "/v1/prd/v2/generate",
        json={"brief_id": brief_id, "insight_index": 0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["prd_id"] != v1_id
    assert body["variant"] == "v2"
