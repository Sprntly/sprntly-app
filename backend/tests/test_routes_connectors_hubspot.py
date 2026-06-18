"""Tests for the HubSpot OAuth connector (modular v1/v3).

`hubspot_oauth.py` dispatches on `settings.hubspot_oauth_version`
(default "v3"). Public function signatures (authorize_url,
exchange_code_for_token, fetch_token_info, etc.) are identical across
versions — only the URLs and introspection response shape differ.

All outbound HTTP is mocked. Routes are multitenant — every authenticated
request passes ?company_id=...; the membership dep + signed state
ensure tokens land in the right workspace.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from tests._company_helpers import company_client


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


def _base_env(monkeypatch, version: str):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("HUBSPOT_CLIENT_ID", "test-hubspot-client-id")
    monkeypatch.setenv("HUBSPOT_CLIENT_SECRET", "test-hubspot-client-secret")
    monkeypatch.setenv(
        "HUBSPOT_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/hubspot/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    monkeypatch.setenv("HUBSPOT_OAUTH_VERSION", version)
    _reload_app_modules()


@pytest.fixture
def hubspot_env_v3(isolated_settings, monkeypatch):
    _base_env(monkeypatch, "v3")
    yield


@pytest.fixture
def hubspot_env_v1(isolated_settings, monkeypatch):
    _base_env(monkeypatch, "v1")
    yield


# ─────────────────────────── Version-agnostic OAuth module tests ───────────────────────────


def test_hubspot_configured_reflects_env(hubspot_env_v3, monkeypatch):
    from app.connectors import hubspot_oauth
    assert hubspot_oauth.hubspot_configured() is True

    monkeypatch.setenv("HUBSPOT_CLIENT_ID", "")
    _reload_app_modules()
    from app.connectors import hubspot_oauth as reloaded
    assert reloaded.hubspot_configured() is False


def test_sign_verify_oauth_state_round_trip(hubspot_env_v3):
    from app.connectors import hubspot_oauth
    token = hubspot_oauth.sign_oauth_state(company_id="ws-x")
    payload = hubspot_oauth.verify_oauth_state(token)
    assert payload["provider"] == "hubspot"
    assert payload["company_id"] == "ws-x"


def test_verify_oauth_state_rejects_wrong_provider(hubspot_env_v3):
    from app.connectors import hubspot_oauth, figma_oauth
    figma_state = figma_oauth.sign_oauth_state(company_id="ws-x")
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        hubspot_oauth.verify_oauth_state(figma_state)


def test_authorize_url_has_required_params(hubspot_env_v3):
    from app.connectors import hubspot_oauth
    url = hubspot_oauth.authorize_url(state="state-token")
    assert url.startswith("https://app.hubspot.com/oauth/authorize")
    assert "client_id=test-hubspot-client-id" in url
    assert "redirect_uri=" in url
    assert "state=state-token" in url
    assert "scope=" in url


def test_exchange_code_for_token_handles_error(hubspot_env_v3):
    from app.connectors import hubspot_oauth
    from fastapi import HTTPException

    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 400
    mock_resp.text = "bad code"
    with patch("app.connectors.hubspot_oauth.requests.post", return_value=mock_resp):
        with pytest.raises(HTTPException):
            hubspot_oauth.exchange_code_for_token("bad-code")


# ─────────────────────────── v3 (default) specific ───────────────────────────


def test_v3_exchange_code_for_token_posts_to_hubspot_v3_endpoint(hubspot_env_v3):
    from app.connectors import hubspot_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "access_token": "hub-v3-access",
        "refresh_token": "hub-v3-refresh",
        "expires_in": 1800,
        "token_type": "bearer",
    }
    with patch("app.connectors.hubspot_oauth.requests.post", return_value=mock_resp) as mock_post:
        out = hubspot_oauth.exchange_code_for_token("auth-code")

    assert out["access_token"] == "hub-v3-access"
    call_args = mock_post.call_args
    assert call_args.args[0] == "https://api.hubspot.com/oauth/v3/token"
    data = call_args.kwargs["data"]
    assert data["grant_type"] == "authorization_code"
    assert data["code"] == "auth-code"
    assert data["client_id"] == "test-hubspot-client-id"
    assert data["client_secret"] == "test-hubspot-client-secret"


def test_v3_fetch_token_info_uses_introspect_endpoint(hubspot_env_v3):
    """v3 follows RFC 7662 — POST /oauth/v3/introspect with token in body."""
    from app.connectors import hubspot_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "active": True,
        "username": "sarah@meridian.health",
        "hub_id": 12345678,
        "hub_domain": "meridian.hubspotcrm.com",
        "scope": "oauth crm.objects.contacts.read",
        "user_id": 99,
    }
    with patch("app.connectors.hubspot_oauth.requests.post", return_value=mock_resp) as mock_post:
        info = hubspot_oauth.fetch_token_info("hub-v3-access")

    assert info["user"] == "sarah@meridian.health"
    assert info["hub_id"] == 12345678
    assert info["scopes"] == ["oauth", "crm.objects.contacts.read"]

    call_args = mock_post.call_args
    assert call_args.args[0] == "https://api.hubspot.com/oauth/v3/introspect"
    assert call_args.kwargs["data"] == {"token": "hub-v3-access"}


def test_v3_start_oauth_returns_hubspot_url(hubspot_env_v3, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/hubspot/start-oauth",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "authorize_url" in body
    assert "hubspot.com" in body["authorize_url"]


def test_v3_callback_stores_connection_with_normalised_label(hubspot_env_v3, monkeypatch):
    """End-to-end on v3."""
    ctx = company_client(monkeypatch)
    from app.connectors import hubspot_oauth
    state = hubspot_oauth.sign_oauth_state(company_id=ctx.company_id)

    mock_token = MagicMock()
    mock_token.ok = True
    mock_token.json.return_value = {
        "access_token": "v3-access",
        "refresh_token": "v3-refresh",
        "expires_in": 1800,
        "token_type": "bearer",
    }
    mock_introspect = MagicMock()
    mock_introspect.ok = True
    mock_introspect.json.return_value = {
        "active": True,
        "username": "sarah@meridian.health",
        "hub_id": 99,
        "hub_domain": "meridian.hubspotcrm.com",
        "scope": "oauth",
    }

    def post_side_effect(url, *args, **kwargs):
        if "/v3/token" in url:
            return mock_token
        if "/v3/introspect" in url:
            return mock_introspect
        raise AssertionError(f"Unexpected POST to {url}")

    with patch("app.connectors.hubspot_oauth.requests.post", side_effect=post_side_effect):
        r = ctx.client.get(
            "/v1/connectors/hubspot/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )

    assert r.status_code == 307
    # Routes through the lightweight return page (closes the OAuth tab).
    assert r.headers["location"].startswith(
        "http://localhost:3000/connectors/return?"
    )
    assert "connected=hubspot" in r.headers["location"]

    listed = ctx.client.get(
        "/v1/connectors"
    ).json()
    rows = [c for c in listed["connections"] if c["provider"] == "hubspot"]
    assert len(rows) == 1
    assert rows[0]["account_label"] == "sarah@meridian.health"
    assert "token_json_encrypted" not in rows[0]


# ─────────────────────────── v1 (legacy) specific ───────────────────────────


def test_v1_exchange_code_for_token_posts_to_hubapi_v1_endpoint(hubspot_env_v1):
    from app.connectors import hubspot_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "access_token": "hub-v1-access",
        "refresh_token": "hub-v1-refresh",
        "expires_in": 21600,
        "token_type": "bearer",
    }
    with patch("app.connectors.hubspot_oauth.requests.post", return_value=mock_resp) as mock_post:
        out = hubspot_oauth.exchange_code_for_token("auth-code")

    assert out["access_token"] == "hub-v1-access"
    call_args = mock_post.call_args
    assert call_args.args[0] == "https://api.hubapi.com/oauth/v1/token"
    data = call_args.kwargs["data"]
    assert data["grant_type"] == "authorization_code"
    assert data["code"] == "auth-code"


def test_v1_fetch_token_info_uses_access_tokens_path_endpoint(hubspot_env_v1):
    """v1 uses path-param GET /oauth/v1/access-tokens/{token}."""
    from app.connectors import hubspot_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "user": "sarah@meridian.health",
        "hub_id": 12345678,
        "hub_domain": "meridian.hubspotcrm.com",
        "scopes": ["oauth", "crm.objects.contacts.read"],
    }
    with patch("app.connectors.hubspot_oauth.requests.get", return_value=mock_resp) as mock_get:
        info = hubspot_oauth.fetch_token_info("hub-v1-access")

    assert info["user"] == "sarah@meridian.health"
    call_args = mock_get.call_args
    assert call_args.args[0] == (
        "https://api.hubapi.com/oauth/v1/access-tokens/hub-v1-access"
    )


def test_v1_callback_stores_connection(hubspot_env_v1, monkeypatch):
    """End-to-end on v1."""
    ctx = company_client(monkeypatch)
    from app.connectors import hubspot_oauth
    state = hubspot_oauth.sign_oauth_state(company_id=ctx.company_id)

    mock_token = MagicMock()
    mock_token.ok = True
    mock_token.json.return_value = {
        "access_token": "v1-access",
        "refresh_token": "v1-refresh",
        "expires_in": 21600,
        "token_type": "bearer",
    }
    mock_info = MagicMock()
    mock_info.ok = True
    mock_info.json.return_value = {
        "user": "sarah@meridian.health",
        "hub_id": 99,
        "hub_domain": "meridian.hubspotcrm.com",
        "scopes": ["oauth"],
    }

    with (
        patch("app.connectors.hubspot_oauth.requests.post", return_value=mock_token),
        patch("app.connectors.hubspot_oauth.requests.get", return_value=mock_info),
    ):
        r = ctx.client.get(
            "/v1/connectors/hubspot/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )

    assert r.status_code == 307
    listed = ctx.client.get(
        "/v1/connectors"
    ).json()
    rows = [c for c in listed["connections"] if c["provider"] == "hubspot"]
    assert len(rows) == 1
    assert rows[0]["account_label"] == "sarah@meridian.health"


# ─────────────────────────── Misconfiguration / negative ───────────────────────────


def test_start_oauth_500_when_not_configured(isolated_settings, monkeypatch):
    monkeypatch.setenv("HUBSPOT_CLIENT_ID", "")
    monkeypatch.setenv("HUBSPOT_CLIENT_SECRET", "")
    monkeypatch.setenv("HUBSPOT_OAUTH_REDIRECT_URI", "")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    _reload_app_modules()
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/hubspot/start-oauth",
    )
    assert r.status_code == 500


def test_callback_rejects_wrong_state(hubspot_env_v3, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import figma_oauth
    wrong_state = figma_oauth.sign_oauth_state(company_id=ctx.company_id)
    r = ctx.client.get(
        "/v1/connectors/hubspot/callback",
        params={"code": "x", "state": wrong_state},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_delete_hubspot_disconnects(hubspot_env_v3, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import hubspot_oauth

    state = hubspot_oauth.sign_oauth_state(company_id=ctx.company_id)
    mock_token = MagicMock()
    mock_token.ok = True
    mock_token.json.return_value = {
        "access_token": "x", "refresh_token": "y", "expires_in": 1, "token_type": "bearer",
    }
    mock_introspect = MagicMock()
    mock_introspect.ok = True
    mock_introspect.json.return_value = {"active": True, "username": "x@y.com", "hub_id": 1}

    def post_side_effect(url, *args, **kwargs):
        return mock_introspect if "/introspect" in url else mock_token

    with patch("app.connectors.hubspot_oauth.requests.post", side_effect=post_side_effect):
        ctx.client.get(
            "/v1/connectors/hubspot/callback",
            params={"code": "x", "state": state},
        )

    r = ctx.client.delete(
        "/v1/connectors/hubspot"
    )
    assert r.status_code == 200
    listed = ctx.client.get(
        "/v1/connectors"
    ).json()
    assert not any(c["provider"] == "hubspot" for c in listed["connections"])


def test_delete_hubspot_404_when_not_connected(hubspot_env_v3, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.delete(
        "/v1/connectors/hubspot"
    )
    assert r.status_code == 404
