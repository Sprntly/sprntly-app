"""Route-level coverage for the PRD canonical share token: `GET /{prd_id}`
and `GET /latest` carry a stable `share_token` read from the pre-existing
`artifact_shares` row, `POST /generate` eager-mints one, and a legacy PRD
with none gets a one-time backfill on first read. Mirrors the harness in
tests/test_routes_prd.py (`tenant_client`, `isolated_settings`)."""
from __future__ import annotations

import logging


def _save_brief_with_insights(db_mod, dataset, insights=None):
    if insights is None:
        insights = [{"title": "Insight A"}, {"title": "Insight B"}]
    payload = {
        "summary_headline": "stub",
        "insights": insights,
        "_schema_version": 1,
    }
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


def _canonical_rows(dataset_client, prd_id: int) -> list[dict]:
    from app.db.client import require_client

    return (
        require_client().table("artifact_shares").select("*")
        .eq("artifact_type", "prd").eq("artifact_id", prd_id).execute().data
    )


# ---- GET /v1/prd/{prd_id} ---------------------------------------------------


def test_prd_get_response_includes_stable_share_token(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t", template_version=1, variant="v2",
    )
    db_mod.complete_prd(prd_id, title="t", md="# PRD body")

    resp1 = t.client.get(f"/v1/prd/{prd_id}")
    resp2 = t.client.get(f"/v1/prd/{prd_id}")

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    token1 = resp1.json()["share_token"]
    token2 = resp2.json()["share_token"]
    assert token1
    assert token1 == token2
    assert len(_canonical_rows(t, prd_id)) == 1


# ---- GET /v1/prd/latest -----------------------------------------------------


def test_prd_latest_response_includes_share_token(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t", template_version=1, variant="v2",
    )
    db_mod.complete_prd(prd_id, title="t", md="# PRD body")

    resp = t.client.get(f"/v1/prd/latest?dataset={t.slug}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["share_token"]
    rows = _canonical_rows(t, prd_id)
    assert len(rows) == 1
    assert body["share_token"] == rows[0]["token"]


# ---- POST /v1/prd/generate ---------------------------------------------------


def test_prd_generate_pre_mints_canonical_token(tenant_client, isolated_settings, monkeypatch):
    """The eager mint happens synchronously inside the generate() handler
    itself (before the fire-and-forget background task), so the token
    already exists by the time POST /generate returns — no LLM call is on
    the path being tested, but the background task is still neutralized
    (mirrors test_generate_via_prd_author_skill_through_canonical_path's own
    rationale) so it can't race this test with a real generation."""
    from app.routes import prd as prd_routes

    async def _noop_warm(*args, **kwargs):
        return None

    monkeypatch.setattr(prd_routes, "generate_prd_and_warm", _noop_warm)

    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")

    resp = t.client.post(
        "/v1/prd/generate", json={"brief_id": brief_id, "insight_index": 0}
    )
    assert resp.status_code == 200
    prd_id = resp.json()["prd_id"]

    rows = _canonical_rows(t, prd_id)
    assert len(rows) == 1
    minted_token = rows[0]["token"]

    get_resp = t.client.get(f"/v1/prd/{prd_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["share_token"] == minted_token
    assert len(_canonical_rows(t, prd_id)) == 1  # the GET minted nothing new


# ---- Legacy backfill ---------------------------------------------------------


def test_prd_get_backfills_legacy_prd_without_share(tenant_client, isolated_settings):
    """A PRD created through a path that never eager-mints (here: seeded
    directly via start_prd/complete_prd, standing in for any pre-existing
    row with no artifact_shares row) gets exactly one backfill token on its
    first GET; a second GET reuses it."""
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t", template_version=1, variant="v2",
    )
    db_mod.complete_prd(prd_id, title="t", md="# legacy body")
    assert _canonical_rows(t, prd_id) == []

    first = t.client.get(f"/v1/prd/{prd_id}")
    assert first.status_code == 200
    first_token = first.json()["share_token"]
    assert first_token
    assert len(_canonical_rows(t, prd_id)) == 1

    second = t.client.get(f"/v1/prd/{prd_id}")
    assert second.status_code == 200
    assert second.json()["share_token"] == first_token
    assert len(_canonical_rows(t, prd_id)) == 1


# ---- Best-effort eager mint (AC20) -------------------------------------------


def test_prd_generate_survives_share_mint_failure(tenant_client, isolated_settings, monkeypatch, caplog):
    """A raising get_or_mint_canonical_share inside POST /generate must not
    break PRD creation — the route still returns {prd_id, status, ...} — and
    the failure is logged at warning level with identifiers only (prd_id),
    never the PRD title/body, token value, or company id/name."""
    from app.routes import prd as prd_routes

    async def _noop_warm(*args, **kwargs):
        return None

    def _raise(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(prd_routes, "generate_prd_and_warm", _noop_warm)
    monkeypatch.setattr(prd_routes, "get_or_mint_canonical_share", _raise)

    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _save_brief_with_insights(db_mod, dataset="acme")

    with caplog.at_level(logging.WARNING, logger="app.routes.prd"):
        resp = t.client.post(
            "/v1/prd/generate", json={"brief_id": brief_id, "insight_index": 0}
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "prd_id" in body
    assert body["status"] == "generating"

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "expected a warning-level log line for the eager-mint failure"
    message = warnings[-1].getMessage()
    assert str(body["prd_id"]) in message
    assert t.company_id not in message
    assert "acme" not in message.lower()
