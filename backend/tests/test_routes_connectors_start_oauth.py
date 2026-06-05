"""Tests for POST /v1/connectors/{provider}/start-oauth.

Fetch-friendly variant of the GET .../authorize routes — returns the
OAuth authorize URL as JSON. The frontend calls it with a Bearer
header and then navigates the browser to the returned URL.

Multitenant: company_id is required on every call; the dep checks
membership before minting any state.
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
    """Configure all OAuth providers so we can exercise dispatch."""
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


# ───────────────────────── auth ─────────────────────────


def test_start_oauth_requires_auth(unauth_client, all_oauth_env):
    r = unauth_client.post(
        "/v1/connectors/google_drive/start-oauth",
    )
    assert r.status_code == 401


# ───────────────────────── dispatch per provider ─────────────────────────


def test_start_oauth_google_drive_returns_google_url(all_oauth_env, monkeypatch):
    ctx = company_client(monkeypatch)
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = (
        "https://accounts.google.com/o/oauth2/auth?test=1",
        None,
    )
    with patch(
        "app.routes.connectors.google_oauth.build_flow",
        return_value=mock_flow,
    ):
        r = ctx.client.post(
            "/v1/connectors/google_drive/start-oauth",
        )
    assert r.status_code == 200
    body = r.json()
    assert "authorize_url" in body
    assert "accounts.google.com" in body["authorize_url"]


def test_start_oauth_google_drive_passes_workspace_and_dataset_into_state(
    all_oauth_env, monkeypatch
):
    ctx = company_client(monkeypatch)
    captured = {}

    def fake_sign(*, company_id, dataset=None, return_to=None):
        captured["company_id"] = company_id
        captured["dataset"] = dataset
        captured["return_to"] = return_to
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
        r = ctx.client.post(
            "/v1/connectors/google_drive/start-oauth",
            json={"dataset": "meridian"},
        )
    assert r.status_code == 200
    assert captured["company_id"] == ctx.company_id
    assert captured["dataset"] == "meridian"


def test_start_oauth_figma_returns_figma_url(all_oauth_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/figma/start-oauth",
    )
    assert r.status_code == 200
    body = r.json()
    assert "authorize_url" in body
    assert "figma.com" in body["authorize_url"]


def test_start_oauth_github_returns_github_url(all_oauth_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/github/start-oauth",
    )
    assert r.status_code == 200
    body = r.json()
    assert "authorize_url" in body
    assert "github.com" in body["authorize_url"]


# ───────────────────────── unknown / misconfigured ─────────────────────────


def test_start_oauth_unknown_provider_404(all_oauth_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/notaprovider/start-oauth",
    )
    assert r.status_code == 404
