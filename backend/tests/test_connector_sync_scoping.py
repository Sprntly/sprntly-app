"""Tenant-scoping + token-refresh tests for connector sync routes.

Covers the gaps closed in fix/connector-sync-scoping:

  * GET  /v1/connectors/sync-status      → require_company; returns only
    the caller's company's connections (cross-tenant denial).
  * POST /v1/connectors/figma/sync-to-corpus    → require_company; threads
    company_id into the figma token lookup (no TypeError; scoped).
  * POST /v1/connectors/hubspot/sync            → require_company; threads
    company_id into sync_hubspot (no TypeError; scoped).
  * POST /v1/connectors/hubspot/sync-to-corpus  → same.
  * A foreign company's connection is never synced for the caller.
  * Figma token refresh-on-expiry in `_figma_access_token`: expired stored
    token → refresh called, new token persisted + returned; non-expired →
    no refresh; refresh failure → clear error (no dead token handed back).

All outbound HTTP is mocked; the fake in-memory Supabase backs the DB.
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from tests._company_helpers import company_client, seed_connection


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.figma_oauth",
        "app.connectors.hubspot_oauth",
        "app.connectors.hubspot_sync",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def sync_env(isolated_settings, monkeypatch):
    """Configure Figma + HubSpot creds and a token encryption key, then
    reload the app so the routes pick everything up."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("FIGMA_CLIENT_ID", "figma-client-id")
    monkeypatch.setenv("FIGMA_CLIENT_SECRET", "figma-client-secret")
    monkeypatch.setenv(
        "FIGMA_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/figma/callback",
    )
    monkeypatch.setenv("HUBSPOT_CLIENT_ID", "hubspot-client-id")
    monkeypatch.setenv("HUBSPOT_CLIENT_SECRET", "hubspot-client-secret")
    monkeypatch.setenv(
        "HUBSPOT_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/hubspot/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    _reload_app_modules()
    import app.db as db_mod
    db_mod.init_db()
    yield


def _figma_token_blob(*, expires_in: int, age_s: int = 0) -> dict:
    """A stored Figma token blob whose obtained_at is `age_s` seconds old."""
    return {
        "access_token": "figma-access-old",
        "refresh_token": "figma-refresh-old",
        "expires_in": expires_in,
        "obtained_at": int(time.time()) - age_s,
    }


# ───────────────────────── /sync-status scoping ─────────────────────────


def test_sync_status_requires_company(sync_env, monkeypatch):
    """No auth → not 200 (require_company gate, not require_session)."""
    import app.main as main_mod
    from fastapi.testclient import TestClient

    anon = TestClient(main_mod.app)
    r = anon.get("/v1/connectors/sync-status")
    assert r.status_code in (401, 403), r.text


def test_sync_status_returns_only_callers_connections(sync_env, monkeypatch):
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob={"access_token": "mine"},
        label="mine@co.com",
    )

    r = ctx.client.get("/v1/connectors/sync-status")
    assert r.status_code == 200, r.text
    providers = [c["provider"] for c in r.json()["connectors"]]
    assert providers == ["figma"]


def test_sync_status_excludes_foreign_company_connections(sync_env, monkeypatch):
    """A connection owned by another company must not leak into the
    caller's sync-status (cross-tenant denial)."""
    from tests._company_helpers import seed_company

    ctx = company_client(monkeypatch)
    other_company = seed_company(user_id="other-user", slug="other")
    seed_connection(
        company_id=other_company,
        provider="hubspot",
        token_blob={"access_token": "foreign"},
        label="foreign@other.com",
    )
    # Caller has its own, distinct connection.
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob={"access_token": "mine"},
        label="mine@co.com",
    )

    r = ctx.client.get("/v1/connectors/sync-status")
    assert r.status_code == 200, r.text
    providers = {c["provider"] for c in r.json()["connectors"]}
    assert providers == {"figma"}
    assert "hubspot" not in providers


def test_sync_status_empty_for_company_with_no_connections(sync_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/connectors/sync-status")
    assert r.status_code == 200, r.text
    assert r.json()["connectors"] == []


# ───────────────────── figma/sync-to-corpus scoping ─────────────────────


def test_figma_sync_to_corpus_requires_company(sync_env, monkeypatch):
    import app.main as main_mod
    from fastapi.testclient import TestClient

    anon = TestClient(main_mod.app)
    r = anon.post(
        "/v1/connectors/figma/sync-to-corpus",
        json={"file_key": "abc", "dataset": "acme"},
    )
    assert r.status_code in (401, 403), r.text


def test_figma_sync_to_corpus_scoped_no_typeerror(sync_env, monkeypatch):
    """The route threads company.company_id into _figma_access_token — a
    regression of the old arity bug (`_figma_access_token()`) would raise
    a TypeError → 500. A fresh (non-expired) token must sync cleanly."""
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob=_figma_token_blob(expires_in=7776000),
        label="mine@co.com",
    )

    fake_file = {"name": "Design", "lastModified": "x", "document": {"children": []}}
    fake_styles = {"meta": {"styles": []}}
    with (
        patch("app.routes.connectors.figma_oauth.fetch_file", return_value=fake_file),
        patch(
            "app.routes.connectors.figma_oauth.fetch_file_styles",
            return_value=fake_styles,
        ),
    ):
        r = ctx.client.post(
            "/v1/connectors/figma/sync-to-corpus",
            json={"file_key": "abc", "dataset": "acme"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_figma_sync_to_corpus_404_when_caller_not_connected(sync_env, monkeypatch):
    """A foreign company's Figma connection isn't usable by the caller —
    the caller has none, so the scoped lookup 404s rather than borrowing
    another tenant's token."""
    from tests._company_helpers import seed_company

    ctx = company_client(monkeypatch)
    other_company = seed_company(user_id="other-user", slug="other")
    seed_connection(
        company_id=other_company,
        provider="figma",
        token_blob=_figma_token_blob(expires_in=7776000),
        label="foreign@other.com",
    )

    r = ctx.client.post(
        "/v1/connectors/figma/sync-to-corpus",
        json={"file_key": "abc", "dataset": "acme"},
    )
    assert r.status_code == 404, r.text


# ───────────────────── hubspot sync scoping ─────────────────────


@pytest.mark.parametrize("path", ["/hubspot/sync", "/hubspot/sync-to-corpus"])
def test_hubspot_sync_requires_company(sync_env, monkeypatch, path):
    import app.main as main_mod
    from fastapi.testclient import TestClient

    anon = TestClient(main_mod.app)
    r = anon.post(f"/v1/connectors{path}", json={"dataset": "acme"})
    assert r.status_code in (401, 403), r.text


@pytest.mark.parametrize("path", ["/hubspot/sync", "/hubspot/sync-to-corpus"])
def test_hubspot_sync_threads_company_id(sync_env, monkeypatch, path):
    """The route must call sync_hubspot(dataset, company_id=...). The old
    code called sync_hubspot(dataset) which, given the new required kwarg,
    would TypeError. We patch sync_hubspot and assert it received the
    caller's company_id."""
    ctx = company_client(monkeypatch)

    captured = {}

    class _Result:
        def to_dict(self):
            return {"ok": True}

    def fake_sync(dataset, *, company_id):
        captured["dataset"] = dataset
        captured["company_id"] = company_id
        return _Result()

    with patch("app.connectors.hubspot_sync.sync_hubspot", side_effect=fake_sync):
        r = ctx.client.post(f"/v1/connectors{path}", json={"dataset": "acme"})

    assert r.status_code == 200, r.text
    assert captured["dataset"] == "acme"
    assert captured["company_id"] == ctx.company_id


def test_hubspot_sync_uses_callers_own_connection(sync_env, monkeypatch):
    """End-to-end through the real sync_hubspot: the access-token lookup is
    company-scoped, so a foreign company's HubSpot connection isn't synced
    for the caller (the caller has none → 404)."""
    from tests._company_helpers import seed_company

    ctx = company_client(monkeypatch)
    other_company = seed_company(user_id="other-user", slug="other")
    seed_connection(
        company_id=other_company,
        provider="hubspot",
        token_blob={
            "access_token": "foreign-access",
            "refresh_token": "foreign-refresh",
            "expires_in": 1800,
            "obtained_at": int(time.time()),
        },
        label="foreign@other.com",
    )

    # No outbound HTTP should happen — caller has no HubSpot connection.
    r = ctx.client.post("/v1/connectors/hubspot/sync", json={"dataset": "acme"})
    assert r.status_code == 404, r.text


# ───────────────────── figma token refresh-on-expiry ─────────────────────


def test_figma_access_token_refreshes_when_expired(sync_env, monkeypatch):
    """Stored token past expiry → refresh_access_token called; the fresh
    token is persisted to the connection config AND returned."""
    from app.routes import connectors as routes
    from app import db
    from app.connectors import figma_oauth
    from app.connectors.tokens import decrypt_token_json

    ctx = company_client(monkeypatch)
    # expires_in 100s, obtained 1000s ago → well past expiry.
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob=_figma_token_blob(expires_in=100, age_s=1000),
        label="mine@co.com",
    )

    fresh = {
        "access_token": "figma-access-NEW",
        "refresh_token": "figma-refresh-NEW",
        "expires_in": 7776000,
    }
    with patch.object(
        figma_oauth, "refresh_access_token", return_value=fresh
    ) as mock_refresh:
        token = routes._figma_access_token(ctx.company_id)

    # Refresh was called with the stored refresh token.
    mock_refresh.assert_called_once_with("figma-refresh-old")
    # Fresh access token is returned.
    assert token == "figma-access-NEW"

    # Fresh token persisted back onto the connection (encrypted).
    row = db.get_connection(ctx.company_id, figma_oauth.FIGMA_PROVIDER)
    stored = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    assert stored["access_token"] == "figma-access-NEW"
    assert stored["refresh_token"] == "figma-refresh-NEW"
    assert stored["expires_in"] == 7776000
    # obtained_at re-stamped to ~now (not the stale value).
    assert stored["obtained_at"] >= int(time.time()) - 5


def test_figma_access_token_no_refresh_when_valid(sync_env, monkeypatch):
    """A token comfortably within its lifetime is returned as-is; refresh
    is never called."""
    from app.routes import connectors as routes
    from app.connectors import figma_oauth

    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob=_figma_token_blob(expires_in=7776000, age_s=10),
        label="mine@co.com",
    )

    with patch.object(figma_oauth, "refresh_access_token") as mock_refresh:
        token = routes._figma_access_token(ctx.company_id)

    mock_refresh.assert_not_called()
    assert token == "figma-access-old"


def test_figma_access_token_refresh_failure_raises_clear_error(sync_env, monkeypatch):
    """If refresh fails, surface a clear error — never hand back the dead
    token. The stored (dead) token must not be returned."""
    from app.routes import connectors as routes
    from app.connectors import figma_oauth

    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob=_figma_token_blob(expires_in=100, age_s=1000),
        label="mine@co.com",
    )

    def boom(_refresh_token):
        raise HTTPException(400, "Figma token refresh failed")

    with patch.object(figma_oauth, "refresh_access_token", side_effect=boom):
        with pytest.raises(HTTPException) as exc:
            routes._figma_access_token(ctx.company_id)

    assert exc.value.status_code == 502
    assert "reconnect" in exc.value.detail.lower()


def test_figma_access_token_expired_without_refresh_token_errors(sync_env, monkeypatch):
    """Expired token with no refresh_token → clear 401, not a dead token."""
    from app.routes import connectors as routes

    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob={
            "access_token": "dead",
            "expires_in": 100,
            "obtained_at": int(time.time()) - 1000,
        },
        label="mine@co.com",
    )

    with pytest.raises(HTTPException) as exc:
        routes._figma_access_token(ctx.company_id)
    assert exc.value.status_code == 401


def test_figma_sync_to_corpus_triggers_refresh(sync_env, monkeypatch):
    """End-to-end: an expired token at sync time refreshes, and the sync
    proceeds with the fresh token (no degraded/silent failure)."""
    from app.connectors import figma_oauth

    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob=_figma_token_blob(expires_in=100, age_s=1000),
        label="mine@co.com",
    )

    fresh = {"access_token": "figma-access-NEW", "expires_in": 7776000}
    fake_file = {"name": "D", "document": {"children": []}}
    fake_styles = {"meta": {"styles": []}}

    seen_tokens = []

    def capture_fetch(token, *a, **k):
        seen_tokens.append(token)
        return fake_file

    with (
        patch.object(figma_oauth, "refresh_access_token", return_value=fresh),
        patch("app.routes.connectors.figma_oauth.fetch_file", side_effect=capture_fetch),
        patch(
            "app.routes.connectors.figma_oauth.fetch_file_styles",
            return_value=fake_styles,
        ),
    ):
        r = ctx.client.post(
            "/v1/connectors/figma/sync-to-corpus",
            json={"file_key": "abc", "dataset": "acme"},
        )

    assert r.status_code == 200, r.text
    # The fetch used the refreshed token, not the stale one.
    assert seen_tokens and seen_tokens[0] == "figma-access-NEW"
