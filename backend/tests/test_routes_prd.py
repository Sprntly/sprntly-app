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
    # Datasets are now validated by the PRD route; register the slug so
    # /v1/prd/generate doesn't 404 on the dataset check.
    db_mod.insert_dataset(slug=dataset, display_name=dataset.title())
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


# Minimal v2 PRD that satisfies the route's post-generation smoke check.
_VALID_PRD_MD = (
    "# Stub PRD\n\n"
    ":::tldr\n"
    '{"problem": "p", "fix": "f", "impact": "i"}\n'
    ":::\n\n"
    ':::problem\n{"user_story": "A user tries x", "impact": []}\n:::\n\n'
    ':::requirements\n[{"behavior": "x"}]\n:::\n\n'
    ':::acceptance-criteria\n[{"id": "AC1"}]\n:::\n'
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


def test_generate_missing_dataset_returns_404(app_client, isolated_settings):
    """A brief that references a since-deleted dataset must 404 from
    /v1/prd/generate rather than silently kick off a generation against
    a missing corpus."""
    db_mod = isolated_settings["db"]
    # Seed a brief whose dataset slug is NOT registered in the datasets
    # table — _save_brief_with_insights would register it, so insert the
    # brief directly without the dataset row.
    payload = {
        "summary_headline": "stub",
        "insights": [{"title": "Insight A"}],
        "_schema_version": 1,
    }
    brief_id = db_mod.save_brief(
        dataset="ghost-company",
        week_label="Week of stub",
        payload=payload,
        schema_version=1,
    )

    resp = app_client.post(
        "/v1/prd/generate",
        json={"brief_id": brief_id, "insight_index": 0},
    )
    assert resp.status_code == 404
    assert "ghost-company" in resp.json()["detail"]


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


def test_generate_renders_v2_semantic_blocks_via_canonical_path(
    app_client, isolated_settings, monkeypatch
):
    """A v2-style payload (typed `:::` blocks) flows end-to-end through
    the canonical /v1/prd/generate path. Confirms the runner picks up
    the v2 template + system prompt and stores the result for GET."""
    import asyncio
    from app import prd_runner

    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod)

    captured: dict = {}
    v2_md = (
        "# Claims — Move deductible upfront\n\n"
        ":::tldr\n"
        '{"problem": "57% abandon", "fix": "step 1", "impact": "+$143M"}\n'
        ":::\n\n"
        ':::problem\n{"user_story": "A claimant tries to file", "impact": []}\n:::\n\n'
        ':::requirements\n[{"behavior": "move deductible"}]\n:::\n\n'
        ':::acceptance-criteria\n[{"id": "AC1"}]\n:::\n'
    )

    def _capture(**kwargs):
        captured.update(kwargs)
        return v2_md

    monkeypatch.setattr(prd_runner, "call_md", _capture)

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

    # v2 system prompt carries this phrase; the now-deleted v1 system prompt
    # did not. Guards against accidentally re-introducing a v1 code path.
    assert ":::tldr" in captured["user"]
    assert "semantic blocks" in captured["system"]

    row = db_mod.get_prd(prd_id)
    assert row["status"] == "ready"
    assert row["variant"] == "v2"
    assert ":::tldr" in row["payload_md"]
