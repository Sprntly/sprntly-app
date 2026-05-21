"""Tests for /v1/connectors Google Drive OAuth routes."""
import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from google.oauth2.credentials import Credentials

from app.connectors import google_oauth


@pytest.fixture
def google_env(isolated_settings, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/google-drive/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.google_oauth",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    import app.db as db_mod

    db_mod.init_db()
    yield


@pytest.fixture
def connector_client(google_env):
    import app.main as main_mod

    client = TestClient(main_mod.app)
    resp = client.post("/v1/auth/login", json={"password": "test-pw"})
    assert resp.status_code == 200, resp.text
    return client


def test_list_requires_auth(unauth_client, google_env):
    r = unauth_client.get("/v1/connectors")
    assert r.status_code == 401


def test_list_empty(connector_client):
    r = connector_client.get("/v1/connectors")
    assert r.status_code == 200
    assert r.json() == {"connections": []}


def test_authorize_redirects(connector_client):
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = (
        "https://accounts.google.com/o/oauth2/auth?test=1",
        None,
    )
    with patch("app.routes.connectors.google_oauth.build_flow", return_value=mock_flow):
        r = connector_client.get(
            "/v1/connectors/google-drive/authorize?dataset=acme",
            follow_redirects=False,
        )
    assert r.status_code == 307
    assert "accounts.google.com" in r.headers["location"]


def test_callback_stores_connection(connector_client):
    state = google_oauth.sign_oauth_state(dataset="acme")
    creds = Credentials(
        token="access",
        refresh_token="refresh",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="test-client-id",
        client_secret="test-client-secret",
        scopes=[google_oauth.DRIVE_READONLY_SCOPE],
    )
    mock_flow = MagicMock()
    mock_flow.credentials = creds
    with (
        patch("app.routes.connectors.google_oauth.build_flow", return_value=mock_flow),
        patch(
            "app.routes.connectors.google_oauth.fetch_google_account_email",
            return_value="pm@company.com",
        ),
    ):
        r = connector_client.get(
            "/v1/connectors/google-drive/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )
    assert r.status_code == 307
    assert "connected=google_drive" in r.headers["location"]

    listed = connector_client.get("/v1/connectors").json()
    assert len(listed["connections"]) == 1
    conn = listed["connections"][0]
    assert conn["provider"] == "google_drive"
    assert conn["google_email"] == "pm@company.com"
    assert conn["config"]["dataset"] == "acme"
    assert "token_json_encrypted" not in conn


def test_disconnect(connector_client):
    state = google_oauth.sign_oauth_state(dataset=None)
    creds = Credentials(
        token="access",
        refresh_token="refresh",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="c",
        client_secret="s",
        scopes=[google_oauth.DRIVE_READONLY_SCOPE],
    )
    mock_flow = MagicMock()
    mock_flow.credentials = creds
    with (
        patch("app.routes.connectors.google_oauth.build_flow", return_value=mock_flow),
        patch(
            "app.routes.connectors.google_oauth.fetch_google_account_email",
            return_value=None,
        ),
        patch("app.routes.connectors.google_oauth.try_revoke_credentials"),
    ):
        connector_client.get(
            "/v1/connectors/google-drive/callback",
            params={"code": "x", "state": state},
        )
    r = connector_client.delete("/v1/connectors/google-drive")
    assert r.status_code == 200
    assert connector_client.get("/v1/connectors").json() == {"connections": []}
