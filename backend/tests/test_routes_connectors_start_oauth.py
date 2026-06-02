"""Tests for POST /v1/connectors/{provider}/start-oauth (commit F).

This is a fetch-friendly variant of the existing GET .../authorize routes:
it returns the OAuth authorize URL as JSON instead of a 307 redirect, so
the frontend can call it with a Bearer header and then navigate the
browser to the returned URL. Fixes the "Not signed in" error you'd hit
if you tried to navigate to the GET .../authorize endpoint directly
(browsers can't send Authorization headers on URL-bar navigations).
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
        "app.connectors.google_oauth",
        "app.connectors.figma_oauth",
        "app.connectors.github_app",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def all_oauth_env(isolated_settings, monkeypatch):
    """Configure all three OAuth providers so we can exercise dispatch."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "g-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "g-client-secret")
    monkeypatch.setenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/google-drive/callback",
    )
    monkeypatch.setenv("FIGMA_CLIENT_ID", "figma-client-id")
    monkeypatch.setenv("FIGMA_CLIENT_SECRET", "figma-client-secret")
    monkeypatch.setenv(
        "FIGMA_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/figma/callback",
    )
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "gh-client-id")
    monkeypatch.setenv("GITHUB_APP_CLIENT_SECRET", "gh-client-secret")
    monkeypatch.setenv(
        "GITHUB_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/github/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    _reload_app_modules()
    yield


@pytest.fixture
def signed_in(all_oauth_env):
    import app.main as main_mod

    client = TestClient(main_mod.app)
    r = client.post("/v1/auth/login", json={"password": "test-pw"})
    assert r.status_code == 200, r.text
    return client


# ───────────────────────── auth ─────────────────────────


def test_start_oauth_requires_auth(unauth_client, all_oauth_env):
    r = unauth_client.post("/v1/connectors/google_drive/start-oauth")
    assert r.status_code == 401


# ───────────────────────── dispatch per provider ─────────────────────────


def test_start_oauth_google_drive_returns_google_url(signed_in):
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = (
        "https://accounts.google.com/o/oauth2/auth?test=1",
        None,
    )
    with patch(
        "app.routes.connectors.google_oauth.build_flow",
        return_value=mock_flow,
    ):
        r = signed_in.post("/v1/connectors/google_drive/start-oauth")
    assert r.status_code == 200
    body = r.json()
    assert "authorize_url" in body
    assert "accounts.google.com" in body["authorize_url"]


def test_start_oauth_google_drive_passes_dataset_into_state(signed_in):
    captured = {}

    def fake_sign(*, dataset=None):
        captured["dataset"] = dataset
        return "signed-state-token"

    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = (
        "https://accounts.google.com/o/oauth2/auth?state=abc",
        None,
    )
    with (
        patch(
            "app.routes.connectors.google_oauth.sign_oauth_state",
            side_effect=fake_sign,
        ),
        patch(
            "app.routes.connectors.google_oauth.build_flow",
            return_value=mock_flow,
        ),
    ):
        r = signed_in.post(
            "/v1/connectors/google_drive/start-oauth",
            json={"dataset": "meridian"},
        )
    assert r.status_code == 200
    assert captured["dataset"] == "meridian"


def test_start_oauth_figma_returns_figma_url(signed_in):
    r = signed_in.post("/v1/connectors/figma/start-oauth")
    assert r.status_code == 200
    body = r.json()
    assert "authorize_url" in body
    assert "figma.com" in body["authorize_url"]


def test_start_oauth_github_returns_github_url(signed_in):
    r = signed_in.post("/v1/connectors/github/start-oauth")
    assert r.status_code == 200
    body = r.json()
    assert "authorize_url" in body
    assert "github.com" in body["authorize_url"]


# ───────────────────────── unknown / misconfigured ─────────────────────────


def test_start_oauth_unknown_provider_404(signed_in):
    # Use an obviously-unsupported name (ClickUp is wired as of commit H).
    r = signed_in.post("/v1/connectors/notaprovider/start-oauth")
    assert r.status_code == 404
