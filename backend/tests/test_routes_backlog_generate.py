"""Tests for the backlog → PRD/prototype wiring:

  POST /v1/prd/generate-from-backlog   — generate a PRD from a backlog item
  POST /v1/backlog                      — create a user-added backlog item
  POST /v1/backlog/reorder              — persist a new rank order

Backlog themes (rank ≥ 4) aren't in brief.insights, so generate-from-backlog
synthesizes an insight from the backlog row and anchors the PRD to the company's
current brief. Rows are marked source='backlog' + theme_id so they dedupe and
group per theme (not per insight_index). Ownership resolves via the same
dataset-slug == company-slug chain the other tenant suites use.
"""
from __future__ import annotations

from app.db import backlog as bl
from app.db.client import require_client


def _seed_corpus(data_dir, dataset, body="some corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


def _save_current_brief(db_mod, dataset):
    payload = {
        "summary_headline": "stub",
        "insights": [{"title": "Brief insight 0", "theme_id": "brief-theme"}],
        "_schema_version": 1,
    }
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


def _seed_backlog_theme(company_id, *, theme_id="theme-x", title="Bulk onboarding",
                        rank=4, score=9.0, reasoning="Churn evidence."):
    """Insert a synthesis-style backlog item and return its row (with id)."""
    bl.upsert_backlog_item(
        company_id, theme_id=theme_id, title=title, rank=rank, score=score,
        reasoning=reasoning,
    )
    items = bl.list_backlog_items(company_id)
    return next(i for i in items if i["theme_id"] == theme_id)


def _prd_row(prd_id):
    return require_client().table("prds").select("*").eq("id", prd_id).execute().data[0]


# ── POST /v1/prd/generate-from-backlog ──────────────────────────────────────

def test_generate_from_backlog_happy_path(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")
    item = _seed_backlog_theme(t.company_id, theme_id="theme-x", title="Bulk onboarding")

    resp = t.client.post(
        "/v1/prd/generate-from-backlog", json={"backlog_item_id": item["id"]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("generating", "ready")
    assert body["title"] == "Bulk onboarding"
    assert body["variant"] == "v2"

    # The row is discriminated as a backlog PRD, keyed on the theme.
    row = _prd_row(body["prd_id"])
    assert row["source"] == "backlog"
    assert row["theme_id"] == "theme-x"


def test_generate_from_backlog_dedup_when_not_forced(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_current_brief(db_mod, dataset="acme")
    item = _seed_backlog_theme(t.company_id, theme_id="theme-x")

    existing = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="Bulk onboarding",
        template_version=1, variant="v2", source="backlog", theme_id="theme-x",
    )
    db_mod.complete_prd(existing, title="Bulk onboarding", md="# Already here")

    resp = t.client.post(
        "/v1/prd/generate-from-backlog",
        json={"backlog_item_id": item["id"], "force": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["prd_id"] == existing
    assert body["status"] == "ready"


def test_generate_from_backlog_no_brief_returns_409(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    item = _seed_backlog_theme(t.company_id, theme_id="theme-x")
    # No brief saved for this company → nothing to ground a PRD on.
    resp = t.client.post(
        "/v1/prd/generate-from-backlog", json={"backlog_item_id": item["id"]}
    )
    assert resp.status_code == 409


def test_generate_from_backlog_unknown_item_returns_404(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")
    resp = t.client.post(
        "/v1/prd/generate-from-backlog", json={"backlog_item_id": "does-not-exist"}
    )
    assert resp.status_code == 404


def test_generate_from_backlog_cross_tenant_returns_404(tenant_client, isolated_settings):
    a = tenant_client.make(slug="company-a")
    _save_current_brief(isolated_settings["db"], dataset="company-a")
    item = _seed_backlog_theme(a.company_id, theme_id="theme-a")

    b = tenant_client.make(slug="company-b")
    _save_current_brief(isolated_settings["db"], dataset="company-b")
    resp = b.client.post(
        "/v1/prd/generate-from-backlog", json={"backlog_item_id": item["id"]}
    )
    assert resp.status_code == 404


def test_generate_from_backlog_without_auth_returns_401(unauth_client, isolated_settings):
    resp = unauth_client.post(
        "/v1/prd/generate-from-backlog", json={"backlog_item_id": "x"}
    )
    assert resp.status_code == 401


def test_generate_from_backlog_grounds_on_synthesized_insight(
    tenant_client, isolated_settings, monkeypatch
):
    """The override path feeds the backlog row's title into the PRD prompt —
    proof the synthetic insight (not a brief insight) grounds the generation."""
    import asyncio
    from app import prd_runner
    from app.graph.gateway import LLMResult

    t = tenant_client.make(slug="acme")
    _seed_corpus(isolated_settings["data_dir"], dataset="acme")
    brief_id = _save_current_brief(isolated_settings["db"], dataset="acme")
    item = _seed_backlog_theme(
        t.company_id, theme_id="theme-x", title="Bulk CSV onboarding"
    )

    seen_inputs: list[str] = []

    def _capture(**kwargs):
        seen_inputs.append(kwargs.get("input", ""))
        return LLMResult(
            output="# Bulk CSV onboarding PRD\n## Problem\nx",
            model="claude-sonnet-4-6",
            prompt_version=kwargs["prompt_version"] + "+prd-author@abc123",
            input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
            cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=1,
            stop_reason="end_turn",
        )

    monkeypatch.setattr(prd_runner, "llm_call", _capture)

    resp = t.client.post(
        "/v1/prd/generate-from-backlog", json={"backlog_item_id": item["id"]}
    )
    prd_id = resp.json()["prd_id"]

    insight = {
        "theme_id": "theme-x", "title": "Bulk CSV onboarding",
        "summary": "Churn evidence.", "hypothesis_id": None,
    }
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            prd_runner.generate_prd(prd_id, brief_id, 0, insight_override=insight)
        )
    finally:
        loop.close()

    assert seen_inputs, "the PRD author was never called"
    assert "Bulk CSV onboarding" in seen_inputs[0]


# ── POST /v1/backlog + /v1/backlog/reorder ──────────────────────────────────

def test_create_backlog_item_persists(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")  # GET needs a brief

    resp = t.client.post("/v1/backlog", json={"title": "New idea", "tag": "something_new"})
    assert resp.status_code == 200
    created = resp.json()
    assert created["title"] == "New idea"
    assert created["tag"] == "something_new"
    assert created["status"] == "backlog"

    listed = t.client.get("/v1/backlog").json()["items"]
    assert any(i["id"] == created["id"] for i in listed)


def test_create_backlog_item_rejects_bad_tag(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    resp = t.client.post("/v1/backlog", json={"title": "x", "tag": "nonsense"})
    assert resp.status_code == 400


def test_reorder_backlog_persists_new_order(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    _save_current_brief(isolated_settings["db"], dataset="acme")
    a = _seed_backlog_theme(t.company_id, theme_id="t-a", title="A", rank=4, score=9)
    b = _seed_backlog_theme(t.company_id, theme_id="t-b", title="B", rank=5, score=8)
    c = _seed_backlog_theme(t.company_id, theme_id="t-c", title="C", rank=6, score=7)

    # Reverse the order.
    resp = t.client.post(
        "/v1/backlog/reorder",
        json={"ordered_ids": [c["id"], b["id"], a["id"]]},
    )
    assert resp.status_code == 200
    ranks = {i["id"]: i["rank"] for i in resp.json()["items"]}
    assert ranks[c["id"]] == 1
    assert ranks[b["id"]] == 2
    assert ranks[a["id"]] == 3


def test_reorder_ignores_foreign_ids(tenant_client, isolated_settings):
    a = tenant_client.make(slug="company-a")
    _save_current_brief(isolated_settings["db"], dataset="company-a")
    mine = _seed_backlog_theme(a.company_id, theme_id="t-a", title="A", rank=4, score=9)

    b = tenant_client.make(slug="company-b")
    other = _seed_backlog_theme(b.company_id, theme_id="t-b", title="B", rank=4, score=9)

    # company-a reorders with a foreign id mixed in — the foreign row is ignored,
    # never re-ranked into company-a's list.
    resp = a.client.post(
        "/v1/backlog/reorder",
        json={"ordered_ids": [other["id"], mine["id"]]},
    )
    assert resp.status_code == 200
    ids = {i["id"] for i in resp.json()["items"]}
    assert ids == {mine["id"]}
