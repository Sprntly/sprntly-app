"""Tests for the generic POST /v1/connectors/{provider}/test endpoint
(commit K).

The "Test connection" button in the Configure drawer re-runs the
provider's identity lookup using the stored (encrypted) token. It
proves the credential is still valid without needing to disconnect +
reconnect.

Dispatches per provider:
  google_drive → Drive folders browse (existing pattern)
  figma        → figma_oauth.fetch_me
  github       → github_app.fetch_authenticated_user
  clickup      → clickup_oauth.fetch_authenticated_user
  hubspot      → hubspot_oauth.fetch_token_info
  fireflies    → fireflies_apikey.fetch_authenticated_user

Returns 200 + {ok, account_label, tested_at} on success; 400 on
validation failure (token rejected); 404 if not connected.
"""
from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.clickup_oauth",
        "app.connectors.figma_oauth",
        "app.connectors.fireflies_apikey",
        "app.connectors.github_app",
        "app.connectors.hubspot_oauth",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def env_with_all_providers(isolated_settings, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("CLICKUP_CLIENT_ID", "x")
    monkeypatch.setenv("CLICKUP_CLIENT_SECRET", "x")
    monkeypatch.setenv("CLICKUP_OAUTH_REDIRECT_URI", "http://t/cb")
    monkeypatch.setenv("FIGMA_CLIENT_ID", "x")
    monkeypatch.setenv("FIGMA_CLIENT_SECRET", "x")
    monkeypatch.setenv("FIGMA_OAUTH_REDIRECT_URI", "http://t/cb")
    monkeypatch.setenv("HUBSPOT_CLIENT_ID", "x")
    monkeypatch.setenv("HUBSPOT_CLIENT_SECRET", "x")
    monkeypatch.setenv("HUBSPOT_OAUTH_REDIRECT_URI", "http://t/cb")
    monkeypatch.setenv("HUBSPOT_OAUTH_VERSION", "v3")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    _reload_app_modules()
    yield


def _client(env):
    import app.main as main_mod
    c = TestClient(main_mod.app)
    r = c.post("/v1/auth/login", json={"password": "test-pw"})
    assert r.status_code == 200, r.text
    return c


def _seed_connection(provider: str, token_blob: dict, label: str = "alice@co.com"):
    from app import db
    from app.connectors.tokens import encrypt_token_json
    enc = encrypt_token_json(json.dumps(token_blob))
    db.upsert_connection(
        provider=provider,
        token_encrypted=enc,
        scopes="",
        account_label=label,
        config_json="{}",
    )


# ─────────────────────────── Auth + 404 ───────────────────────────


def test_test_endpoint_requires_auth(unauth_client, env_with_all_providers):
    r = unauth_client.post("/v1/connectors/figma/test")
    assert r.status_code == 401


def test_test_endpoint_404_when_not_connected(env_with_all_providers):
    c = _client(env_with_all_providers)
    r = c.post("/v1/connectors/figma/test")
    assert r.status_code == 404


def test_test_endpoint_404_for_unknown_provider(env_with_all_providers):
    c = _client(env_with_all_providers)
    _seed_connection("figma", {"access_token": "x"})
    r = c.post("/v1/connectors/totally_made_up/test")
    assert r.status_code == 404


# ─────────────────────────── Per-provider success ───────────────────────────


def test_test_endpoint_figma_calls_fetch_me(env_with_all_providers):
    c = _client(env_with_all_providers)
    _seed_connection("figma", {"access_token": "fg-tok"})
    with patch(
        "app.routes.connectors.figma_oauth.fetch_me",
        return_value={"email": "alice@figma.test", "handle": "alice"},
    ) as mock_fetch:
        r = c.post("/v1/connectors/figma/test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert "alice@figma.test" in body["account_label"]
    assert "tested_at" in body
    mock_fetch.assert_called_once_with("fg-tok")


def test_test_endpoint_github_calls_fetch_user(env_with_all_providers):
    c = _client(env_with_all_providers)
    _seed_connection("github", {"access_token": "gh-tok"})
    with patch(
        "app.routes.connectors.github_app.fetch_authenticated_user",
        return_value={"login": "octocat"},
    ) as mock_fetch:
        r = c.post("/v1/connectors/github/test")
    assert r.status_code == 200
    assert "octocat" in r.json()["account_label"]
    mock_fetch.assert_called_once_with("gh-tok")


def test_test_endpoint_clickup_calls_user_lookup(env_with_all_providers):
    c = _client(env_with_all_providers)
    _seed_connection("clickup", {"access_token": "clk-tok"})
    with patch(
        "app.routes.connectors.clickup_oauth.fetch_authenticated_user",
        return_value={"email": "alice@clk.test", "username": "Alice"},
    ):
        r = c.post("/v1/connectors/clickup/test")
    assert r.status_code == 200
    assert "alice@clk.test" in r.json()["account_label"]


def test_test_endpoint_hubspot_calls_fetch_token_info(env_with_all_providers):
    c = _client(env_with_all_providers)
    _seed_connection("hubspot", {"access_token": "hs-tok"})
    with patch(
        "app.routes.connectors.hubspot_oauth.fetch_token_info",
        return_value={"user": "alice@hs.test", "hub_id": 1},
    ):
        r = c.post("/v1/connectors/hubspot/test")
    assert r.status_code == 200
    assert "alice@hs.test" in r.json()["account_label"]


def test_test_endpoint_fireflies_calls_graphql_user(env_with_all_providers):
    c = _client(env_with_all_providers)
    # Fireflies stores the api_key under that key in the token JSON.
    _seed_connection("fireflies", {"api_key": "ff-tok"})
    with patch(
        "app.routes.connectors.fireflies_apikey.fetch_authenticated_user",
        return_value={"email": "alice@ff.test", "name": "Alice"},
    ) as mock_fetch:
        r = c.post("/v1/connectors/fireflies/test")
    assert r.status_code == 200
    assert "alice@ff.test" in r.json()["account_label"]
    mock_fetch.assert_called_once_with("ff-tok")


# ─────────────────────────── Failure cases ───────────────────────────


def test_test_endpoint_returns_400_when_provider_rejects_token(env_with_all_providers):
    """If the provider's identity lookup returns empty (token rejected),
    the test endpoint surfaces a 400 — the UI shows "token invalid"."""
    c = _client(env_with_all_providers)
    _seed_connection("fireflies", {"api_key": "stale-tok"})
    with patch(
        "app.routes.connectors.fireflies_apikey.fetch_authenticated_user",
        return_value={},  # empty = key rejected
    ):
        r = c.post("/v1/connectors/fireflies/test")
    assert r.status_code == 400
