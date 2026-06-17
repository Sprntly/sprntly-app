"""Phase 2 weekly-brief lifecycle — route-level behavior.

  - POST /v1/prd/generate records the insight's theme as 'prd_created'
  - POST /v1/brief/dismiss records the finding as 'dismissed'
  - GET  /v1/backlog/completed returns only prd_created/done, scoped to the company

The owning company of a brief is resolved via brief.dataset (slug) → company, so
each test seeds a company whose slug equals the brief's dataset (the tenant_client
fixture pattern shared with test_routes_prd.py). enterprise_id == company_id.
"""
from __future__ import annotations


def _save_brief(db_mod, dataset, insights):
    payload = {"summary_headline": "stub", "insights": insights, "_schema_version": 1}
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


def _finding_rows(db, company_id):
    return (
        db.table("brief_finding_state").select("*")
        .eq("enterprise_id", company_id).execute().data
    )


# ── PRD generation records 'prd_created' ─────────────────────────────────────

def test_prd_generate_records_prd_created_for_insight_theme(
    tenant_client, isolated_settings, fake_llm
):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief(db_mod, "acme", [
        {"title": "Insight A", "theme_id": "theme-aaa"},
        {"title": "Insight B", "theme_id": "theme-bbb"},
    ])

    resp = t.client.post(
        "/v1/prd/generate", json={"brief_id": brief_id, "insight_index": 1}
    )
    assert resp.status_code == 200

    rows = _finding_rows(isolated_settings["supabase"], t.company_id)
    assert len(rows) == 1
    assert rows[0]["theme_id"] == "theme-bbb"
    assert rows[0]["action"] == "prd_created"


def test_prd_generate_without_theme_id_does_not_break(
    tenant_client, isolated_settings, fake_llm
):
    """A legacy brief insight with no theme_id still generates a PRD (best-effort
    action recording is a no-op)."""
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief(db_mod, "acme", [{"title": "Insight A"}])

    resp = t.client.post(
        "/v1/prd/generate", json={"brief_id": brief_id, "insight_index": 0}
    )
    assert resp.status_code == 200
    assert _finding_rows(isolated_settings["supabase"], t.company_id) == []


# ── dismiss endpoint records 'dismissed' ─────────────────────────────────────

def test_dismiss_by_theme_id_records_dismissed(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    resp = t.client.post("/v1/brief/dismiss", json={"theme_id": "theme-xyz"})
    assert resp.status_code == 200
    assert resp.json()["theme_id"] == "theme-xyz"

    rows = _finding_rows(isolated_settings["supabase"], t.company_id)
    assert len(rows) == 1
    assert rows[0]["theme_id"] == "theme-xyz"
    assert rows[0]["action"] == "dismissed"


def test_dismiss_by_brief_insight_resolves_theme_id(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief(db_mod, "acme", [
        {"title": "Insight A", "theme_id": "theme-aaa"},
        {"title": "Insight B", "theme_id": "theme-bbb"},
    ])

    resp = t.client.post(
        "/v1/brief/dismiss", json={"brief_id": brief_id, "insight_index": 0}
    )
    assert resp.status_code == 200
    rows = _finding_rows(isolated_settings["supabase"], t.company_id)
    assert len(rows) == 1
    assert rows[0]["theme_id"] == "theme-aaa"
    assert rows[0]["action"] == "dismissed"


def test_dismiss_missing_identifiers_is_400(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    assert t.client.post("/v1/brief/dismiss", json={}).status_code == 400


def test_dismiss_foreign_brief_is_404(tenant_client, isolated_settings):
    tenant_client.make(slug="company-a")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief(db_mod, "company-a", [{"title": "A", "theme_id": "t1"}])
    b = tenant_client.make(slug="company-b")
    resp = b.client.post(
        "/v1/brief/dismiss", json={"brief_id": brief_id, "insight_index": 0}
    )
    assert resp.status_code == 404


# ── completed read endpoint ──────────────────────────────────────────────────

def test_completed_returns_only_prd_created_and_done(tenant_client, isolated_settings):
    from app.db.finding_state import set_finding_action

    t = tenant_client.make(slug="acme")
    db = isolated_settings["supabase"]
    set_finding_action(t.company_id, "t-prd", "prd_created", client=db)
    set_finding_action(t.company_id, "t-done", "done", client=db)
    set_finding_action(t.company_id, "t-dismissed", "dismissed", client=db)
    set_finding_action(t.company_id, "t-surfaced", "surfaced", client=db)

    resp = t.client.get("/v1/backlog/completed")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    theme_ids = {i["theme_id"] for i in body["items"]}
    assert theme_ids == {"t-prd", "t-done"}
    # Title falls back to theme_id when there's no backlog row.
    for item in body["items"]:
        assert item["title"] == item["theme_id"]
        assert item["action"] in ("prd_created", "done")
        assert "last_surfaced_at" in item


def test_completed_uses_backlog_title_when_available(tenant_client, isolated_settings):
    from app.db.finding_state import set_finding_action

    t = tenant_client.make(slug="acme")
    db = isolated_settings["supabase"]
    db.table("backlog_items").insert({
        "id": "bi-1", "enterprise_id": t.company_id, "theme_id": "t-prd",
        "rank": 1, "score": 1.0, "title": "SSO support", "status": "backlog",
    }).execute()
    set_finding_action(t.company_id, "t-prd", "prd_created", client=db)

    resp = t.client.get("/v1/backlog/completed")
    item = resp.json()["items"][0]
    assert item["title"] == "SSO support"


def test_completed_is_empty_for_new_company(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    resp = t.client.get("/v1/backlog/completed")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "count": 0}


def test_completed_scoped_to_company(tenant_client, isolated_settings):
    from app.db.finding_state import set_finding_action

    a = tenant_client.make(slug="company-a")
    db = isolated_settings["supabase"]
    set_finding_action(a.company_id, "t-a", "prd_created", client=db)

    b = tenant_client.make(slug="company-b")
    set_finding_action(b.company_id, "t-b", "done", client=db)

    resp = b.client.get("/v1/backlog/completed")
    body = resp.json()
    assert {i["theme_id"] for i in body["items"]} == {"t-b"}


def test_completed_requires_auth(unauth_client, isolated_settings):
    assert unauth_client.get("/v1/backlog/completed").status_code == 401
