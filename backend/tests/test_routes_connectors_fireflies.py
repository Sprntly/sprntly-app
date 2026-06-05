"""Tests for the Fireflies.ai connector (API-key auth).

Fireflies uses an API key (not OAuth). All outbound HTTP is mocked.
Routes are multitenant: every authenticated request passes
?company_id=..., seeded via tests/_company_helpers.company_client.
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
        "app.connectors.fireflies_apikey",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def fireflies_env(isolated_settings, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    _reload_app_modules()
    yield


# ─────────────────────────── Module unit tests ───────────────────────────


def test_fetch_authenticated_user_posts_graphql_query(fireflies_env):
    from app.connectors import fireflies_apikey

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "data": {"user": {"name": "Sarah Chen", "email": "sarah@meridian.health"}},
    }
    with patch("app.connectors.fireflies_apikey.requests.post", return_value=mock_resp) as mock_post:
        user = fireflies_apikey.fetch_authenticated_user("ff-api-key-xyz")

    assert user["email"] == "sarah@meridian.health"
    assert user["name"] == "Sarah Chen"

    call_args = mock_post.call_args
    assert call_args.args[0] == "https://api.fireflies.ai/graphql"
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer ff-api-key-xyz"
    body = call_args.kwargs.get("json") or {}
    assert "user" in body.get("query", "")


def test_fetch_authenticated_user_returns_empty_on_invalid_key(fireflies_env):
    from app.connectors import fireflies_apikey

    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized"
    with patch("app.connectors.fireflies_apikey.requests.post", return_value=mock_resp):
        user = fireflies_apikey.fetch_authenticated_user("bad-key")

    assert user == {}


def test_fetch_authenticated_user_handles_graphql_error(fireflies_env):
    from app.connectors import fireflies_apikey

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"errors": [{"message": "Unauthorized"}]}
    with patch("app.connectors.fireflies_apikey.requests.post", return_value=mock_resp):
        user = fireflies_apikey.fetch_authenticated_user("expired-key")

    assert user == {}


# ─────────────────────────── Route tests ───────────────────────────


def test_apikey_route_requires_auth(unauth_client, fireflies_env):
    r = unauth_client.post(
        "/v1/connectors/fireflies/apikey",
        json={"api_key": "ff-key"},
    )
    assert r.status_code == 401


def test_apikey_route_stores_connection_with_email_label(fireflies_env, monkeypatch):
    ctx = company_client(monkeypatch)

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "data": {"user": {"name": "Sarah", "email": "sarah@meridian.health"}},
    }
    with patch("app.connectors.fireflies_apikey.requests.post", return_value=mock_resp):
        r = ctx.client.post(
            "/v1/connectors/fireflies/apikey",
            json={"api_key": "ff-valid-key"},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body.get("provider") == "fireflies"

    listed = ctx.client.get(
        "/v1/connectors"
    ).json()
    rows = [c for c in listed["connections"] if c["provider"] == "fireflies"]
    assert len(rows) == 1
    assert rows[0]["account_label"] == "sarah@meridian.health"
    assert "token_json_encrypted" not in rows[0]


def test_apikey_route_rejects_invalid_key(fireflies_env, monkeypatch):
    ctx = company_client(monkeypatch)

    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized"
    with patch("app.connectors.fireflies_apikey.requests.post", return_value=mock_resp):
        r = ctx.client.post(
            "/v1/connectors/fireflies/apikey",
            json={"api_key": "bad-key"},
        )

    assert r.status_code == 400
    listed = ctx.client.get(
        "/v1/connectors"
    ).json()
    assert not any(c["provider"] == "fireflies" for c in listed["connections"])


def test_apikey_route_rejects_empty_key(fireflies_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/fireflies/apikey",
        json={"api_key": ""},
    )
    assert r.status_code == 422


def test_apikey_route_rejects_missing_field(fireflies_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/fireflies/apikey",
        json={},
    )
    assert r.status_code == 422


def test_apikey_route_updates_existing_connection(fireflies_env, monkeypatch):
    """Re-posting with a new key overwrites the existing one (re-key flow)."""
    ctx = company_client(monkeypatch)

    first = MagicMock()
    first.ok = True
    first.json.return_value = {"data": {"user": {"email": "first@test.com", "name": "First"}}}

    second = MagicMock()
    second.ok = True
    second.json.return_value = {"data": {"user": {"email": "second@test.com", "name": "Second"}}}

    with patch("app.connectors.fireflies_apikey.requests.post", return_value=first):
        ctx.client.post(
            "/v1/connectors/fireflies/apikey",
            json={"api_key": "key1"},
        )
    with patch("app.connectors.fireflies_apikey.requests.post", return_value=second):
        ctx.client.post(
            "/v1/connectors/fireflies/apikey",
            json={"api_key": "key2"},
        )

    listed = ctx.client.get(
        "/v1/connectors"
    ).json()
    rows = [c for c in listed["connections"] if c["provider"] == "fireflies"]
    assert len(rows) == 1
    assert rows[0]["account_label"] == "second@test.com"


def test_delete_fireflies_disconnects(fireflies_env, monkeypatch):
    ctx = company_client(monkeypatch)

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"data": {"user": {"email": "x@y.com"}}}
    with patch("app.connectors.fireflies_apikey.requests.post", return_value=mock_resp):
        ctx.client.post(
            "/v1/connectors/fireflies/apikey",
            json={"api_key": "k"},
        )

    r = ctx.client.delete(
        "/v1/connectors/fireflies"
    )
    assert r.status_code == 200
    listed = ctx.client.get(
        "/v1/connectors"
    ).json()
    assert not any(c["provider"] == "fireflies" for c in listed["connections"])


def test_delete_fireflies_404_when_not_connected(fireflies_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.delete(
        "/v1/connectors/fireflies"
    )
    assert r.status_code == 404


# ─────────────────────────── Sanity ───────────────────────────


def test_fireflies_does_not_appear_in_start_oauth_dispatch(fireflies_env, monkeypatch):
    """Fireflies is API-key based, not OAuth — the start-oauth endpoint
    should NOT recognise it (returns 404)."""
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/fireflies/start-oauth",
    )
    assert r.status_code == 404
