"""Tests for the ClickUp OAuth connector (commit H).

ClickUp uses OAuth 2.0:
  authorize:  https://app.clickup.com/api?client_id=...&redirect_uri=...&state=...
  token:      POST https://api.clickup.com/api/v2/oauth/token
              body: {client_id, client_secret, code}
              returns: {access_token: "..."}
  user info:  GET https://api.clickup.com/api/v2/user
              header: Authorization: <access_token>   (raw, no Bearer prefix)
              returns: {user: {id, username, email, ...}}

No refresh tokens (ClickUp access tokens don't expire by default).

All outbound HTTP is mocked. We assert:
  - sign/verify oauth state round-trips and rejects wrong-provider
  - authorize_url has the right base + required params
  - exchange_code_for_token posts the right body and parses the response
  - fetch_authenticated_user returns the username/email for account_label
  - clickup_configured() reflects env presence
  - start-oauth dispatch returns a clickup.com URL
  - callback exchanges code, stores encrypted connection, redirects to frontend
  - delete removes the connection
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
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def clickup_env(isolated_settings, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("CLICKUP_CLIENT_ID", "test-clickup-client-id")
    monkeypatch.setenv("CLICKUP_CLIENT_SECRET", "test-clickup-client-secret")
    monkeypatch.setenv(
        "CLICKUP_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/clickup/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    _reload_app_modules()
    yield


def _signed_in_client(clickup_env):
    import app.main as main_mod
    client = TestClient(main_mod.app)
    r = client.post("/v1/auth/login", json={"password": "test-pw"})
    assert r.status_code == 200, r.text
    return client


# ─────────────────────────── OAuth module unit tests ───────────────────────────


def test_clickup_configured_reflects_env(clickup_env, monkeypatch):
    from app.connectors import clickup_oauth
    assert clickup_oauth.clickup_configured() is True

    # Empty-set overrides .env-file value (pydantic-settings reads both).
    monkeypatch.setenv("CLICKUP_CLIENT_ID", "")
    _reload_app_modules()
    from app.connectors import clickup_oauth as reloaded
    assert reloaded.clickup_configured() is False


def test_sign_verify_oauth_state_round_trip(clickup_env):
    from app.connectors import clickup_oauth
    token = clickup_oauth.sign_oauth_state()
    payload = clickup_oauth.verify_oauth_state(token)
    assert payload["provider"] == "clickup"


def test_verify_oauth_state_rejects_wrong_provider(clickup_env):
    from app.connectors import clickup_oauth, figma_oauth
    figma_state = figma_oauth.sign_oauth_state()
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        clickup_oauth.verify_oauth_state(figma_state)


def test_authorize_url_has_required_params(clickup_env):
    from app.connectors import clickup_oauth
    url = clickup_oauth.authorize_url(state="state-token")
    assert url.startswith("https://app.clickup.com/api")
    assert "client_id=test-clickup-client-id" in url
    assert "redirect_uri=" in url
    assert "state=state-token" in url


def test_exchange_code_for_token_posts_correctly(clickup_env):
    from app.connectors import clickup_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"access_token": "clk-token-xyz"}
    with patch("app.connectors.clickup_oauth.requests.post", return_value=mock_resp) as mock_post:
        out = clickup_oauth.exchange_code_for_token("auth-code-123")
    assert out["access_token"] == "clk-token-xyz"

    call_args = mock_post.call_args
    assert call_args.args[0] == "https://api.clickup.com/api/v2/oauth/token"
    body = call_args.kwargs.get("json") or call_args.kwargs.get("data") or {}
    assert body.get("client_id") == "test-clickup-client-id"
    assert body.get("client_secret") == "test-clickup-client-secret"
    assert body.get("code") == "auth-code-123"


def test_exchange_code_for_token_handles_error(clickup_env):
    from app.connectors import clickup_oauth
    from fastapi import HTTPException

    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 400
    mock_resp.text = "bad code"
    with patch("app.connectors.clickup_oauth.requests.post", return_value=mock_resp):
        with pytest.raises(HTTPException):
            clickup_oauth.exchange_code_for_token("bad-code")


def test_fetch_authenticated_user_returns_user_dict(clickup_env):
    from app.connectors import clickup_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "user": {
            "id": 42,
            "username": "Sarah Chen",
            "email": "sarah@meridian.health",
        },
    }
    with patch("app.connectors.clickup_oauth.requests.get", return_value=mock_resp) as mock_get:
        user = clickup_oauth.fetch_authenticated_user("clk-token-xyz")
    assert user["email"] == "sarah@meridian.health"
    assert user["username"] == "Sarah Chen"

    call_args = mock_get.call_args
    assert call_args.args[0] == "https://api.clickup.com/api/v2/user"
    # ClickUp uses raw access token in Authorization header (no Bearer prefix).
    assert call_args.kwargs["headers"]["Authorization"] == "clk-token-xyz"


# ─────────────────────────── Route tests ───────────────────────────


def test_start_oauth_clickup_returns_clickup_url(clickup_env):
    client = _signed_in_client(clickup_env)
    r = client.post("/v1/connectors/clickup/start-oauth")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "authorize_url" in body
    assert "clickup.com" in body["authorize_url"]
    assert "client_id=test-clickup-client-id" in body["authorize_url"]


def test_start_oauth_clickup_500_when_not_configured(isolated_settings, monkeypatch):
    # Empty-set rather than delenv — pydantic-settings reads from the real
    # backend/.env file in addition to os.environ.
    monkeypatch.setenv("CLICKUP_CLIENT_ID", "")
    monkeypatch.setenv("CLICKUP_CLIENT_SECRET", "")
    monkeypatch.setenv("CLICKUP_OAUTH_REDIRECT_URI", "")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    _reload_app_modules()
    import app.main as main_mod
    client = TestClient(main_mod.app)
    client.post("/v1/auth/login", json={"password": "test-pw"})
    r = client.post("/v1/connectors/clickup/start-oauth")
    assert r.status_code == 500


def test_callback_stores_connection(clickup_env):
    client = _signed_in_client(clickup_env)
    from app.connectors import clickup_oauth
    state = clickup_oauth.sign_oauth_state()

    mock_token_resp = MagicMock()
    mock_token_resp.ok = True
    mock_token_resp.json.return_value = {"access_token": "clk-token-real"}

    mock_user_resp = MagicMock()
    mock_user_resp.ok = True
    mock_user_resp.json.return_value = {
        "user": {"id": 42, "username": "Sarah Chen", "email": "sarah@meridian.health"},
    }

    with (
        patch("app.connectors.clickup_oauth.requests.post", return_value=mock_token_resp),
        patch("app.connectors.clickup_oauth.requests.get", return_value=mock_user_resp),
    ):
        r = client.get(
            "/v1/connectors/clickup/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )

    # Redirects to frontend with ?connected=clickup
    assert r.status_code == 307
    assert "connected=clickup" in r.headers["location"]

    # Connection persisted, account_label set, no raw token leak.
    listed = client.get("/v1/connectors").json()
    rows = [c for c in listed["connections"] if c["provider"] == "clickup"]
    assert len(rows) == 1
    assert rows[0]["account_label"] == "sarah@meridian.health"
    assert "token_json_encrypted" not in rows[0]


def test_callback_rejects_wrong_state(clickup_env):
    client = _signed_in_client(clickup_env)
    from app.connectors import figma_oauth
    wrong_state = figma_oauth.sign_oauth_state()  # signed for figma, not clickup
    r = client.get(
        "/v1/connectors/clickup/callback",
        params={"code": "x", "state": wrong_state},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_delete_clickup_disconnects(clickup_env):
    client = _signed_in_client(clickup_env)
    from app.connectors import clickup_oauth

    state = clickup_oauth.sign_oauth_state()
    mock_token_resp = MagicMock()
    mock_token_resp.ok = True
    mock_token_resp.json.return_value = {"access_token": "clk-token"}
    mock_user_resp = MagicMock()
    mock_user_resp.ok = True
    mock_user_resp.json.return_value = {"user": {"email": "x@y.com"}}

    with (
        patch("app.connectors.clickup_oauth.requests.post", return_value=mock_token_resp),
        patch("app.connectors.clickup_oauth.requests.get", return_value=mock_user_resp),
    ):
        client.get(
            "/v1/connectors/clickup/callback",
            params={"code": "x", "state": state},
        )

    r = client.delete("/v1/connectors/clickup")
    assert r.status_code == 200
    listed = client.get("/v1/connectors").json()
    assert not any(c["provider"] == "clickup" for c in listed["connections"])


def test_delete_clickup_404_when_not_connected(clickup_env):
    client = _signed_in_client(clickup_env)
    r = client.delete("/v1/connectors/clickup")
    assert r.status_code == 404
