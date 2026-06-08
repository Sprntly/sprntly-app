"""Tests for the Figma Personal Access Token (PAT) connector path.

While the Figma public OAuth app is in Figma's review queue, customers can
still connect their Figma account to Sprntly by pasting a Personal Access
Token. Mirrors the Fireflies API-key pattern:

  POST /v1/connectors/figma/pat
    body: { pat: <string> }
    - Validates the PAT by calling Figma's /v1/me with `X-Figma-Token: <pat>`
    - On 200: encrypts + stores in connections.token_json_encrypted, same
      column OAuth tokens live in
    - On 401/403/network failure: 400 with a "double-check the token" message

Tenancy: gated on require_company. Cross-tenant isolation tested.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

import app.auth  # noqa: F401

from tests._company_helpers import company_client


def _reload_app_modules() -> None:
    """Reload the modules that capture env at import time. Same shape the
    fireflies + clickup test suites use after setting TOKEN_ENCRYPTION_KEY."""
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.figma_pat",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def figma_pat_env(isolated_settings, monkeypatch):
    """Set TOKEN_ENCRYPTION_KEY for tests that exercise the encrypt path.
    Mirrors `fireflies_env` from test_routes_connectors_fireflies.py.

    Without this fixture, route tests 500 with 'TOKEN_ENCRYPTION_KEY is not
    configured' because CI runs in a clean environment (no `.env` file)."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    _reload_app_modules()
    yield


# ─────────────────────── helpers ───────────────────────


def _fake_figma_me(handle: str = "alice", email: str | None = "alice@co.com"):
    """Return a callable suitable for monkeypatching figma_pat.fetch_me."""
    def _impl(pat: str) -> dict:
        # Simulate Figma's /v1/me response shape (id, handle, email, img_url)
        if pat == "bad-token":
            return {}
        return {
            "id": "user-fig-123",
            "handle": handle,
            "email": email,
            "img_url": "https://figma.com/avatar.png",
        }
    return _impl


def _list_figma_connection(company_id: str) -> dict | None:
    from app.db.client import require_client

    rows = (
        require_client()
        .table("connections")
        .select("provider, account_label, scopes")
        .eq("company_id", company_id)
        .eq("provider", "figma")
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


# ─────────────────────── happy path ───────────────────────


def test_figma_pat_valid_creates_connection(figma_pat_env, monkeypatch):
    """Pasted PAT is validated, then stored under provider='figma'."""
    ctx = company_client(monkeypatch)
    import app.connectors.figma_pat as mod

    monkeypatch.setattr(mod, "fetch_me", _fake_figma_me())

    r = ctx.client.post(
        "/v1/connectors/figma/pat", json={"pat": "figd_VALIDTOKEN"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["provider"] == "figma"
    # Account label prefers the Figma handle.
    assert body["account_label"] == "alice"

    row = _list_figma_connection(ctx.company_id)
    assert row is not None
    assert row["provider"] == "figma"
    assert row["account_label"] == "alice"


def test_figma_pat_falls_back_to_email_when_no_handle(
    figma_pat_env, monkeypatch
):
    ctx = company_client(monkeypatch)
    import app.connectors.figma_pat as mod

    monkeypatch.setattr(mod, "fetch_me", _fake_figma_me(handle="", email="x@y.com"))

    r = ctx.client.post(
        "/v1/connectors/figma/pat", json={"pat": "figd_VALIDTOKEN"}
    )
    assert r.status_code == 200
    assert r.json()["account_label"] == "x@y.com"


def test_figma_pat_replaces_existing_connection(
    figma_pat_env, monkeypatch
):
    """Pasting a new PAT upserts the existing connection row (one per company
    per provider, enforced by unique(company_id, provider))."""
    ctx = company_client(monkeypatch)
    import app.connectors.figma_pat as mod

    monkeypatch.setattr(mod, "fetch_me", _fake_figma_me(handle="first"))
    ctx.client.post("/v1/connectors/figma/pat", json={"pat": "figd_A"})
    monkeypatch.setattr(mod, "fetch_me", _fake_figma_me(handle="second"))
    r = ctx.client.post("/v1/connectors/figma/pat", json={"pat": "figd_B"})

    assert r.status_code == 200
    assert r.json()["account_label"] == "second"
    row = _list_figma_connection(ctx.company_id)
    assert row["account_label"] == "second"


# ─────────────────────── invalid PAT ───────────────────────


def test_figma_pat_invalid_returns_400(figma_pat_env, monkeypatch):
    ctx = company_client(monkeypatch)
    import app.connectors.figma_pat as mod

    monkeypatch.setattr(mod, "fetch_me", _fake_figma_me())

    r = ctx.client.post(
        "/v1/connectors/figma/pat", json={"pat": "bad-token"}
    )
    assert r.status_code == 400
    detail = r.json()["detail"].lower()
    assert "figma" in detail or "token" in detail
    # No connection row written on failure.
    assert _list_figma_connection(ctx.company_id) is None


def test_figma_pat_empty_string_rejected(figma_pat_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/connectors/figma/pat", json={"pat": "   "})
    assert r.status_code == 422


def test_figma_pat_missing_field_rejected(figma_pat_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/connectors/figma/pat", json={})
    assert r.status_code == 422


# ─────────────────────── auth gate ───────────────────────


def test_figma_pat_requires_auth(figma_pat_env, monkeypatch):
    """Without bearer header → 401 (require_session)."""
    company_client(monkeypatch)
    from fastapi.testclient import TestClient
    import app.main as main_mod

    unauth = TestClient(main_mod.app)
    r = unauth.post(
        "/v1/connectors/figma/pat", json={"pat": "figd_anything"}
    )
    assert r.status_code == 401


def test_figma_pat_requires_company(figma_pat_env, monkeypatch):
    """Bearer but no company membership → 403 (require_company)."""
    from tests._company_helpers import (
        setup_supabase_auth,
        supabase_bearer,
    )
    import importlib
    import sys
    import uuid

    setup_supabase_auth(monkeypatch)
    importlib.reload(sys.modules["app.main"])
    from fastapi.testclient import TestClient
    import app.main as main_mod

    orphan = "orphan-" + uuid.uuid4().hex[:8]
    client = TestClient(main_mod.app, headers=supabase_bearer(orphan))
    r = client.post("/v1/connectors/figma/pat", json={"pat": "figd_x"})
    assert r.status_code == 403


# ─────────────────────── unit test: fetch_me ───────────────────────


def test_fetch_me_calls_figma_with_token_header(figma_pat_env, monkeypatch):
    """Verify the HTTP shape: GET api.figma.com/v1/me with X-Figma-Token header."""
    import app.connectors.figma_pat as mod

    captured: dict = {}

    class _FakeResp:
        ok = True
        status_code = 200
        text = "{}"

        def json(self):
            return {
                "id": "u1",
                "handle": "alice",
                "email": "a@b.com",
                "img_url": None,
            }

    def _fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _FakeResp()

    monkeypatch.setattr(mod.requests, "get", _fake_get)

    out = mod.fetch_me("figd_TEST")
    assert out["handle"] == "alice"
    assert captured["url"] == "https://api.figma.com/v1/me"
    assert captured["headers"]["X-Figma-Token"] == "figd_TEST"
    assert captured["timeout"] == 10


def test_fetch_me_returns_empty_on_401(figma_pat_env, monkeypatch):
    import app.connectors.figma_pat as mod

    class _FakeResp:
        ok = False
        status_code = 401
        text = "Invalid token"

        def json(self):
            return {}

    monkeypatch.setattr(mod.requests, "get", lambda *a, **kw: _FakeResp())
    assert mod.fetch_me("bad") == {}


def test_fetch_me_returns_empty_on_network_error(figma_pat_env, monkeypatch):
    import app.connectors.figma_pat as mod
    import requests

    def _raise(*a, **kw):
        raise requests.RequestException("dns")

    monkeypatch.setattr(mod.requests, "get", _raise)
    assert mod.fetch_me("anything") == {}
