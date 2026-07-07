"""Tests for /v1/mcp-tokens (customer-facing MCP token management).

Covers: create returns the raw token exactly once, list never leaks it,
delete is scoped to the caller's own company (cross-tenant attempt 404s,
matching the existing no-existence-disclosure convention), and every route
requires require_company (tenant-scoped).
"""
from __future__ import annotations

import app.auth  # noqa: F401 — ensure app.config/app.auth in sys.modules

from tests._company_helpers import company_client, seed_company, supabase_bearer


def test_create_mcp_token_returns_raw_token_once(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)

    r = ctx.client.post("/v1/mcp-tokens", json={"name": "Claude Desktop"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Claude Desktop"
    assert body["token"].startswith("sprn_mcp_")
    assert body["token_prefix"] == body["token"][:20]


def test_list_mcp_tokens_never_includes_raw_token(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    ctx.client.post("/v1/mcp-tokens", json={"name": "Claude Desktop"})

    r = ctx.client.get("/v1/mcp-tokens")
    assert r.status_code == 200, r.text
    tokens = r.json()["tokens"]
    assert len(tokens) == 1
    # No "token" column exists at all (see mcp_tokens.sql) — the raw token is
    # never persisted, only synthesized in create's response — so this holds
    # regardless of the test fake's column-projection fidelity. The stronger
    # "list_mcp_tokens never SELECTs token_hash" guarantee is verified at the
    # call site in test_db_mcp_tokens.py (the fake doesn't enforce PostgREST's
    # column projection, so asserting hash-absence here would test the fake,
    # not the code).
    assert "token" not in tokens[0]
    assert tokens[0]["name"] == "Claude Desktop"


def test_delete_mcp_token_revokes_it(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    created = ctx.client.post("/v1/mcp-tokens", json={"name": "t"}).json()

    r = ctx.client.delete(f"/v1/mcp-tokens/{created['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    tokens = ctx.client.get("/v1/mcp-tokens").json()["tokens"]
    assert tokens[0]["revoked_at"] is not None


def test_delete_mcp_token_404s_on_unknown_id(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.delete("/v1/mcp-tokens/not-a-real-id")
    assert r.status_code == 404


def test_delete_mcp_token_404s_for_another_companys_token(isolated_settings, monkeypatch):
    """Cross-tenant delete attempt: same shape as an unknown id (404, not
    403) — never discloses that the token exists under another company.

    company_client() always seeds slug="acme", so a second company in the
    same test needs a distinct slug seeded directly (seed_company/
    supabase_bearer are the same helpers company_client composes)."""
    import uuid

    import app.main as main_mod
    from fastapi.testclient import TestClient

    owner_ctx = company_client(monkeypatch)
    created = owner_ctx.client.post("/v1/mcp-tokens", json={"name": "t"}).json()

    other_user_id = "test-user-" + uuid.uuid4().hex[:8]
    seed_company(user_id=other_user_id, slug="globex-" + uuid.uuid4().hex[:8])
    other_client = TestClient(main_mod.app, headers=supabase_bearer(other_user_id))

    r = other_client.delete(f"/v1/mcp-tokens/{created['id']}")
    assert r.status_code == 404

    # Confirmed untouched from the owner's side.
    tokens = owner_ctx.client.get("/v1/mcp-tokens").json()["tokens"]
    assert tokens[0]["revoked_at"] is None


def test_mcp_tokens_routes_require_company(isolated_settings, monkeypatch):
    import app.main as main_mod
    from fastapi.testclient import TestClient

    client = TestClient(main_mod.app)
    assert client.get("/v1/mcp-tokens").status_code in (401, 403)
    assert client.post("/v1/mcp-tokens", json={"name": "t"}).status_code in (401, 403)
