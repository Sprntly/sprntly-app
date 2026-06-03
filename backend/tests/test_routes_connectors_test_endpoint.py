"""Tests for the generic POST /v1/connectors/{provider}/test endpoint.

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

Post-multitenancy slice: every request now passes ?workspace_id=...,
the dep checks company_members, and seeded connections are scoped per
workspace via tests/_workspace_helpers.workspace_client.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from tests._workspace_helpers import seed_connection, workspace_client


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


# ─────────────────────────── Auth + 404 ───────────────────────────


def test_test_endpoint_requires_auth(unauth_client, env_with_all_providers):
    r = unauth_client.post("/v1/connectors/figma/test", params={"workspace_id": "x"})
    assert r.status_code == 401


def test_test_endpoint_404_when_not_connected(env_with_all_providers, monkeypatch):
    ctx = workspace_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/figma/test", params={"workspace_id": ctx.workspace_id}
    )
    assert r.status_code == 404


def test_test_endpoint_404_for_unknown_provider(env_with_all_providers, monkeypatch):
    ctx = workspace_client(monkeypatch)
    seed_connection(
        workspace_id=ctx.workspace_id, provider="figma", token_blob={"access_token": "x"}
    )
    r = ctx.client.post(
        "/v1/connectors/totally_made_up/test",
        params={"workspace_id": ctx.workspace_id},
    )
    assert r.status_code == 404


# ─────────────────────────── Per-provider success ───────────────────────────


def test_test_endpoint_figma_calls_fetch_me(env_with_all_providers, monkeypatch):
    ctx = workspace_client(monkeypatch)
    seed_connection(
        workspace_id=ctx.workspace_id, provider="figma", token_blob={"access_token": "fg-tok"}
    )
    with patch(
        "app.routes.connectors.figma_oauth.fetch_me",
        return_value={"email": "alice@figma.test", "handle": "alice"},
    ) as mock_fetch:
        r = ctx.client.post(
            "/v1/connectors/figma/test", params={"workspace_id": ctx.workspace_id}
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert "alice@figma.test" in body["account_label"]
    assert "tested_at" in body
    mock_fetch.assert_called_once_with("fg-tok")


def test_test_endpoint_github_calls_fetch_user(env_with_all_providers, monkeypatch):
    ctx = workspace_client(monkeypatch)
    seed_connection(
        workspace_id=ctx.workspace_id, provider="github", token_blob={"access_token": "gh-tok"}
    )
    with patch(
        "app.routes.connectors.github_app.fetch_authenticated_user",
        return_value={"login": "octocat"},
    ) as mock_fetch:
        r = ctx.client.post(
            "/v1/connectors/github/test", params={"workspace_id": ctx.workspace_id}
        )
    assert r.status_code == 200
    assert "octocat" in r.json()["account_label"]
    mock_fetch.assert_called_once_with("gh-tok")


def test_test_endpoint_clickup_calls_user_lookup(env_with_all_providers, monkeypatch):
    ctx = workspace_client(monkeypatch)
    seed_connection(
        workspace_id=ctx.workspace_id, provider="clickup", token_blob={"access_token": "clk-tok"}
    )
    with patch(
        "app.routes.connectors.clickup_oauth.fetch_authenticated_user",
        return_value={"email": "alice@clk.test", "username": "Alice"},
    ):
        r = ctx.client.post(
            "/v1/connectors/clickup/test", params={"workspace_id": ctx.workspace_id}
        )
    assert r.status_code == 200
    assert "alice@clk.test" in r.json()["account_label"]


def test_test_endpoint_hubspot_calls_fetch_token_info(env_with_all_providers, monkeypatch):
    ctx = workspace_client(monkeypatch)
    seed_connection(
        workspace_id=ctx.workspace_id, provider="hubspot", token_blob={"access_token": "hs-tok"}
    )
    with patch(
        "app.routes.connectors.hubspot_oauth.fetch_token_info",
        return_value={"user": "alice@hs.test", "hub_id": 1},
    ):
        r = ctx.client.post(
            "/v1/connectors/hubspot/test", params={"workspace_id": ctx.workspace_id}
        )
    assert r.status_code == 200
    assert "alice@hs.test" in r.json()["account_label"]


def test_test_endpoint_fireflies_calls_graphql_user(env_with_all_providers, monkeypatch):
    ctx = workspace_client(monkeypatch)
    seed_connection(
        workspace_id=ctx.workspace_id,
        provider="fireflies",
        token_blob={"api_key": "ff-tok"},
    )
    with patch(
        "app.routes.connectors.fireflies_apikey.fetch_authenticated_user",
        return_value={"email": "alice@ff.test", "name": "Alice"},
    ) as mock_fetch:
        r = ctx.client.post(
            "/v1/connectors/fireflies/test", params={"workspace_id": ctx.workspace_id}
        )
    assert r.status_code == 200
    assert "alice@ff.test" in r.json()["account_label"]
    mock_fetch.assert_called_once_with("ff-tok")


# ─────────────────────────── Failure cases ───────────────────────────


def test_test_endpoint_returns_400_when_provider_rejects_token(
    env_with_all_providers, monkeypatch
):
    """If the provider's identity lookup returns empty (token rejected),
    the test endpoint surfaces a 400 — the UI shows "token invalid"."""
    ctx = workspace_client(monkeypatch)
    seed_connection(
        workspace_id=ctx.workspace_id,
        provider="fireflies",
        token_blob={"api_key": "stale-tok"},
    )
    with patch(
        "app.routes.connectors.fireflies_apikey.fetch_authenticated_user",
        return_value={},  # empty = key rejected
    ):
        r = ctx.client.post(
            "/v1/connectors/fireflies/test", params={"workspace_id": ctx.workspace_id}
        )
    assert r.status_code == 400


# ─────────────────────────── Tenant isolation ───────────────────────────


def test_test_endpoint_returns_404_when_other_workspace_has_the_connection(
    env_with_all_providers, monkeypatch
):
    """The cross-tenant leak this slice closes — ws1 cannot see/test ws2's
    connection even if ws2 has the same provider connected."""
    ctx = workspace_client(monkeypatch)
    from tests._workspace_helpers import seed_workspace

    other_ws = seed_workspace(user_id=ctx.user_id, slug="globex")
    seed_connection(
        workspace_id=other_ws,
        provider="figma",
        token_blob={"access_token": "other-tok"},
    )
    r = ctx.client.post(
        "/v1/connectors/figma/test", params={"workspace_id": ctx.workspace_id}
    )
    assert r.status_code == 404


def test_test_endpoint_403_when_not_a_member_of_workspace(
    env_with_all_providers, monkeypatch
):
    """A signed-in user requesting a workspace they don't belong to is
    blocked at the membership dep — they can't even see whether the
    connector is configured."""
    ctx = workspace_client(monkeypatch)
    from tests._workspace_helpers import seed_workspace

    # A workspace ctx.user is NOT a member of (different owner).
    other_ws = seed_workspace(user_id="someone-else", slug="globex")
    r = ctx.client.post(
        "/v1/connectors/figma/test", params={"workspace_id": other_ws}
    )
    assert r.status_code == 403
