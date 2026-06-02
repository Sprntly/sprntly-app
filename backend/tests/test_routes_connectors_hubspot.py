"""Tests for the HubSpot OAuth connector (commit I).

HubSpot uses OAuth 2.0 with refresh tokens:
  authorize:  https://app.hubspot.com/oauth/authorize
              ?client_id=...&redirect_uri=...&scope=...&state=...
  token:      POST https://api.hubapi.com/oauth/v1/token
              content-type: application/x-www-form-urlencoded
              body: grant_type=authorization_code&code=...&redirect_uri=...
                    &client_id=...&client_secret=...
              returns: {access_token, refresh_token, expires_in, token_type}
  user info:  GET https://api.hubapi.com/oauth/v1/access-tokens/{access_token}
              returns: {user (= email), hub_id, hub_domain, scopes, ...}

We use the access-tokens metadata endpoint to derive `account_label` (the
`user` field is the authenticated user's email).

All outbound HTTP is mocked.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.hubspot_oauth",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def hubspot_env(isolated_settings, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("HUBSPOT_CLIENT_ID", "test-hubspot-client-id")
    monkeypatch.setenv("HUBSPOT_CLIENT_SECRET", "test-hubspot-client-secret")
    monkeypatch.setenv(
        "HUBSPOT_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/hubspot/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    _reload_app_modules()
    yield


def _signed_in_client(hubspot_env):
    import app.main as main_mod
    client = TestClient(main_mod.app)
    r = client.post("/v1/auth/login", json={"password": "test-pw"})
    assert r.status_code == 200, r.text
    return client


# ─────────────────────────── OAuth module unit tests ───────────────────────────


def test_hubspot_configured_reflects_env(hubspot_env, monkeypatch):
    from app.connectors import hubspot_oauth
    assert hubspot_oauth.hubspot_configured() is True

    # Empty-set overrides .env-file value (pydantic-settings reads both).
    monkeypatch.setenv("HUBSPOT_CLIENT_ID", "")
    _reload_app_modules()
    from app.connectors import hubspot_oauth as reloaded
    assert reloaded.hubspot_configured() is False


def test_sign_verify_oauth_state_round_trip(hubspot_env):
    from app.connectors import hubspot_oauth
    token = hubspot_oauth.sign_oauth_state()
    payload = hubspot_oauth.verify_oauth_state(token)
    assert payload["provider"] == "hubspot"


def test_verify_oauth_state_rejects_wrong_provider(hubspot_env):
    from app.connectors import hubspot_oauth, figma_oauth
    figma_state = figma_oauth.sign_oauth_state()
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        hubspot_oauth.verify_oauth_state(figma_state)


def test_authorize_url_has_required_params(hubspot_env):
    from app.connectors import hubspot_oauth
    url = hubspot_oauth.authorize_url(state="state-token")
    assert url.startswith("https://app.hubspot.com/oauth/authorize")
    assert "client_id=test-hubspot-client-id" in url
    assert "redirect_uri=" in url
    assert "state=state-token" in url
    assert "scope=" in url


def test_exchange_code_for_token_posts_form_urlencoded(hubspot_env):
    from app.connectors import hubspot_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "access_token": "hub-access",
        "refresh_token": "hub-refresh",
        "expires_in": 21600,
        "token_type": "bearer",
    }
    with patch("app.connectors.hubspot_oauth.requests.post", return_value=mock_resp) as mock_post:
        out = hubspot_oauth.exchange_code_for_token("auth-code-123")

    assert out["access_token"] == "hub-access"
    assert out["refresh_token"] == "hub-refresh"

    call_args = mock_post.call_args
    assert call_args.args[0] == "https://api.hubapi.com/oauth/v1/token"
    # HubSpot requires form-urlencoded — use `data=`, not `json=`.
    assert "data" in call_args.kwargs
    data = call_args.kwargs["data"]
    assert data["grant_type"] == "authorization_code"
    assert data["code"] == "auth-code-123"
    assert data["client_id"] == "test-hubspot-client-id"
    assert data["client_secret"] == "test-hubspot-client-secret"
    assert data["redirect_uri"] == "http://testserver/v1/connectors/hubspot/callback"


def test_exchange_code_for_token_handles_error(hubspot_env):
    from app.connectors import hubspot_oauth
    from fastapi import HTTPException

    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 400
    mock_resp.text = "bad code"
    with patch("app.connectors.hubspot_oauth.requests.post", return_value=mock_resp):
        with pytest.raises(HTTPException):
            hubspot_oauth.exchange_code_for_token("bad-code")


def test_fetch_token_info_returns_user_and_hub(hubspot_env):
    from app.connectors import hubspot_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "user": "sarah@meridian.health",
        "hub_id": 12345678,
        "hub_domain": "meridian.hubspotcrm.com",
        "scopes": ["oauth", "crm.objects.contacts.read"],
        "user_id": 99,
    }
    with patch("app.connectors.hubspot_oauth.requests.get", return_value=mock_resp) as mock_get:
        info = hubspot_oauth.fetch_token_info("hub-access")

    assert info["user"] == "sarah@meridian.health"
    assert info["hub_id"] == 12345678

    call_args = mock_get.call_args
    assert call_args.args[0] == (
        "https://api.hubapi.com/oauth/v1/access-tokens/hub-access"
    )


# ─────────────────────────── Route tests ───────────────────────────


def test_start_oauth_hubspot_returns_hubspot_url(hubspot_env):
    client = _signed_in_client(hubspot_env)
    r = client.post("/v1/connectors/hubspot/start-oauth")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "authorize_url" in body
    assert "hubspot.com" in body["authorize_url"]
    assert "client_id=test-hubspot-client-id" in body["authorize_url"]


def test_start_oauth_hubspot_500_when_not_configured(isolated_settings, monkeypatch):
    monkeypatch.setenv("HUBSPOT_CLIENT_ID", "")
    monkeypatch.setenv("HUBSPOT_CLIENT_SECRET", "")
    monkeypatch.setenv("HUBSPOT_OAUTH_REDIRECT_URI", "")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    _reload_app_modules()
    import app.main as main_mod
    client = TestClient(main_mod.app)
    client.post("/v1/auth/login", json={"password": "test-pw"})
    r = client.post("/v1/connectors/hubspot/start-oauth")
    assert r.status_code == 500


def test_callback_stores_connection(hubspot_env):
    client = _signed_in_client(hubspot_env)
    from app.connectors import hubspot_oauth
    state = hubspot_oauth.sign_oauth_state()

    mock_token_resp = MagicMock()
    mock_token_resp.ok = True
    mock_token_resp.json.return_value = {
        "access_token": "hub-access-real",
        "refresh_token": "hub-refresh-real",
        "expires_in": 21600,
        "token_type": "bearer",
    }

    mock_info_resp = MagicMock()
    mock_info_resp.ok = True
    mock_info_resp.json.return_value = {
        "user": "sarah@meridian.health",
        "hub_id": 12345678,
        "hub_domain": "meridian.hubspotcrm.com",
        "scopes": ["oauth", "crm.objects.contacts.read"],
    }

    with (
        patch("app.connectors.hubspot_oauth.requests.post", return_value=mock_token_resp),
        patch("app.connectors.hubspot_oauth.requests.get", return_value=mock_info_resp),
    ):
        r = client.get(
            "/v1/connectors/hubspot/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )

    # Redirects to Settings with ?section=connectors&connected=hubspot
    assert r.status_code == 307
    assert r.headers["location"].startswith(
        "http://localhost:3000/settings?section=connectors"
    )
    assert "connected=hubspot" in r.headers["location"]

    # Connection persisted, account_label set, no raw token leak.
    listed = client.get("/v1/connectors").json()
    rows = [c for c in listed["connections"] if c["provider"] == "hubspot"]
    assert len(rows) == 1
    assert rows[0]["account_label"] == "sarah@meridian.health"
    assert "token_json_encrypted" not in rows[0]


def test_callback_rejects_wrong_state(hubspot_env):
    client = _signed_in_client(hubspot_env)
    from app.connectors import figma_oauth
    wrong_state = figma_oauth.sign_oauth_state()
    r = client.get(
        "/v1/connectors/hubspot/callback",
        params={"code": "x", "state": wrong_state},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_delete_hubspot_disconnects(hubspot_env):
    client = _signed_in_client(hubspot_env)
    from app.connectors import hubspot_oauth

    state = hubspot_oauth.sign_oauth_state()
    mock_token = MagicMock()
    mock_token.ok = True
    mock_token.json.return_value = {
        "access_token": "x", "refresh_token": "y", "expires_in": 1, "token_type": "bearer",
    }
    mock_info = MagicMock()
    mock_info.ok = True
    mock_info.json.return_value = {"user": "x@y.com", "hub_id": 1, "hub_domain": "y.com"}

    with (
        patch("app.connectors.hubspot_oauth.requests.post", return_value=mock_token),
        patch("app.connectors.hubspot_oauth.requests.get", return_value=mock_info),
    ):
        client.get(
            "/v1/connectors/hubspot/callback",
            params={"code": "x", "state": state},
        )

    r = client.delete("/v1/connectors/hubspot")
    assert r.status_code == 200
    listed = client.get("/v1/connectors").json()
    assert not any(c["provider"] == "hubspot" for c in listed["connections"])


def test_delete_hubspot_404_when_not_connected(hubspot_env):
    client = _signed_in_client(hubspot_env)
    r = client.delete("/v1/connectors/hubspot")
    assert r.status_code == 404
