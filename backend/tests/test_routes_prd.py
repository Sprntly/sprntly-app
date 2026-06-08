"""Tests for app.routes.prd — POST /v1/prd/generate and GET /v1/prd/{id}.

Mirrors test_routes_evidence — the two endpoints have the same shape.
New rows are stored with variant='v2' (the canonical PRD format); GET
is permissive on variant so historical v1 rows still resolve for old
bookmarks.
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
        brief_id=brief_id,
        insight_index=0,
        title="t",
        template_version=1,
        variant="v2",
    )
    db_mod.complete_prd(prd_id, title="t", md="# PRD body")

    resp = app_client.get(f"/v1/prd/{prd_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == prd_id
    assert body["status"] == "ready"
    assert body["payload_md"] == "# PRD body"


def test_get_permissive_on_v1_rows(app_client, isolated_settings):
    """Historical v1 rows still resolve — the GET is permissive on variant
    so old bookmarks don't 409."""
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod)
    v1_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t", template_version=1
    )  # default variant='v1'
    db_mod.complete_prd(v1_id, title="t", md="# legacy v1 body")

    resp = app_client.get(f"/v1/prd/{v1_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == v1_id
    assert body["variant"] == "v1"
    assert body["payload_md"] == "# legacy v1 body"


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
    app_client, isolated_settings, fake_llm
):
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod)
    resp = app_client.post(
        "/v1/prd/generate", json={"brief_id": brief_id, "insight_index": 0}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "prd_id" in body
    assert body["status"] in ("generating", "ready")
    assert body["title"] == "Insight A — number leads with $1M"
    # New rows are stored with variant='v2' — single assertion to lock
    # in the storage variant for the canonical PRD path.
    assert body["variant"] == "v2"


def test_generate_returns_existing_prd_when_not_forced(
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
    db_mod.complete_prd(existing_id, title="t", md="# Already here")

    resp = app_client.post(
        "/v1/prd/generate",
        json={"brief_id": brief_id, "insight_index": 0, "force": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["prd_id"] == existing_id
    assert body["status"] == "ready"


def test_generate_does_not_dedupe_against_v1_row(
    app_client, isolated_settings
):
    """A ready v1 PRD for the same (brief, insight) must NOT be returned by
    the canonical generate endpoint — variant scoping keeps v1 rows isolated
    so a fresh v2 row is created."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod)
    v1_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t", template_version=1
    )
    db_mod.complete_prd(v1_id, title="t", md="# v1 body")

    resp = app_client.post(
        "/v1/prd/generate",
        json={"brief_id": brief_id, "insight_index": 0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["prd_id"] != v1_id
    assert body["variant"] == "v2"


def test_generate_via_prd_author_skill_through_canonical_path(
    app_client, isolated_settings, monkeypatch
):
    """A 2-part document flows end-to-end through the canonical
    /v1/prd/generate path. Confirms the runner binds the prd-author skill at
    the gateway and stores Part A (human) for GET + Part B (LLM) alongside."""
    import asyncio
    from app import prd_runner
    from app.graph.gateway import LLMResult

    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod)

    captured: dict = {}
    two_part = (
        "# Claims — Move deductible upfront\n\n"
        "# Part A — Product Requirements Document (human-readable)\n"
        "## 1. Problem & evidence\n57% abandon.\n"
        "\n---\n"
        "# Part B — Implementation Spec (LLM-readable / agent-executable)\n"
        "WHEN x THE SYSTEM SHALL y.\n"
    )

    def _capture(**kwargs):
        captured.update(kwargs)
        return LLMResult(
            output=two_part, model="claude-sonnet-4-6",
            prompt_version=kwargs["prompt_version"] + "+prd-author@abc123",
            input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
            cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=1,
            stop_reason="end_turn",
        )

    monkeypatch.setattr(prd_runner, "llm_call", _capture)

    resp = app_client.post(
        "/v1/prd/generate",
        json={"brief_id": brief_id, "insight_index": 0, "force": True},
    )
    assert resp.status_code == 200
    prd_id = resp.json()["prd_id"]

    # Drain the background task that was scheduled by the route.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            prd_runner.generate_prd(prd_id, brief_id, 0)
        )
    finally:
        loop.close()

    # The gateway was driven with the prd-author skill binding.
    assert captured["skill"] == "prd-author"

    row = db_mod.get_prd(prd_id)
    assert row["status"] == "ready"
    assert row["variant"] == "v2"
    # Part A renders as the human PRD; Part B is stored alongside, not in it.
    assert "Part A — Product Requirements Document" in row["payload_md"]
    assert "Part B — Implementation Spec" not in row["payload_md"]
    assert "Part B — Implementation Spec" in row["llm_part"]
