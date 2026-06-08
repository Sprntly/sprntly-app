"""Tests for the Figma + GitHub connector OAuth routes.

All outbound HTTP (token exchange, user lookup) is mocked. Routes are
multitenant: every authenticated request passes ?company_id=...,
seeded via tests/_company_helpers.company_client.
"""
from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from tests._company_helpers import seed_connection, company_client


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.figma_oauth",
        "app.connectors.github_app",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def figma_env(isolated_settings, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("FIGMA_CLIENT_ID", "figma-client-id")
    monkeypatch.setenv("FIGMA_CLIENT_SECRET", "figma-client-secret")
    monkeypatch.setenv(
        "FIGMA_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/figma/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    _reload_app_modules()
    import app.db as db_mod
    db_mod.init_db()
    yield


@pytest.fixture
def github_env(isolated_settings, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "gh-client-id")
    monkeypatch.setenv("GITHUB_APP_CLIENT_SECRET", "gh-client-secret")
    monkeypatch.setenv(
        "GITHUB_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/github/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    _reload_app_modules()
    import app.db as db_mod
    db_mod.init_db()
    yield


# ─────────────────────── Figma OAuth module unit tests ───────────────────────


def test_exchange_code_for_token_posts_to_api_figma_with_basic_auth(figma_env):
    """Post-Nov-2025: token URL moved from www.figma.com/api/oauth/token to
    api.figma.com/v1/oauth/token, and credentials moved from body fields
    into the HTTP Basic auth header. Both shifts are breaking; pin them
    in a test so they can't silently regress."""
    import base64
    from unittest.mock import MagicMock
    from app.connectors import figma_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "access_token": "fig-access",
        "refresh_token": "fig-refresh",
        "expires_in": 7776000,
    }
    with patch(
        "app.connectors.figma_oauth.requests.post", return_value=mock_resp
    ) as mock_post:
        out = figma_oauth.exchange_code_for_token("auth-code-x")

    assert out["access_token"] == "fig-access"
    call_args = mock_post.call_args
    assert call_args.args[0] == "https://api.figma.com/v1/oauth/token"

    # Credentials ride in Authorization: Basic, not in the body
    expected = base64.b64encode(
        b"figma-client-id:figma-client-secret"
    ).decode()
    assert call_args.kwargs["headers"]["Authorization"] == f"Basic {expected}"

    # Body is form-urlencoded with only the grant_type/code/redirect_uri trio
    body = call_args.kwargs["data"]
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "auth-code-x"
    assert body["redirect_uri"] == "http://testserver/v1/connectors/figma/callback"
    assert "client_id" not in body
    assert "client_secret" not in body


def test_refresh_access_token_posts_to_api_figma_refresh(figma_env):
    """Same migration applies to the refresh endpoint."""
    import base64
    from unittest.mock import MagicMock
    from app.connectors import figma_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"access_token": "new-access", "expires_in": 7776000}
    with patch(
        "app.connectors.figma_oauth.requests.post", return_value=mock_resp
    ) as mock_post:
        out = figma_oauth.refresh_access_token("old-refresh")

    assert out["access_token"] == "new-access"
    call_args = mock_post.call_args
    assert call_args.args[0] == "https://api.figma.com/v1/oauth/refresh"
    expected = base64.b64encode(
        b"figma-client-id:figma-client-secret"
    ).decode()
    assert call_args.kwargs["headers"]["Authorization"] == f"Basic {expected}"
    body = call_args.kwargs["data"]
    assert body["refresh_token"] == "old-refresh"
    assert "client_id" not in body
    assert "client_secret" not in body


# ─────────────────────── Figma ───────────────────────


def test_figma_authorize_redirects_to_figma(figma_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get(
        "/v1/connectors/figma/authorize",
        follow_redirects=False,
    )
    assert r.status_code == 307
    loc = r.headers["location"]
    assert loc.startswith("https://www.figma.com/oauth?")
    assert "client_id=figma-client-id" in loc
    assert "state=" in loc
    assert "scope=" in loc


def test_figma_callback_stores_token(figma_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import figma_oauth
    state = figma_oauth.sign_oauth_state(company_id=ctx.company_id)

    fake_token = {
        "access_token": "fig-access",
        "refresh_token": "fig-refresh",
        "expires_in": 7776000,
        "user_id": "user-123",
    }
    fake_me = {"id": "user-123", "email": "alice@co.com", "handle": "alice"}

    with patch("app.routes.connectors.figma_oauth.exchange_code_for_token", return_value=fake_token), \
         patch("app.routes.connectors.figma_oauth.fetch_me", return_value=fake_me):
        r = ctx.client.get(
            "/v1/connectors/figma/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )

    assert r.status_code == 307
    assert r.headers["location"].startswith(
        "http://localhost:3000/settings?section=connectors"
    )
    assert "connected=figma" in r.headers["location"]

    listed = ctx.client.get(
        "/v1/connectors"
    ).json()["connections"]
    figma = next(c for c in listed if c["provider"] == "figma")
    assert figma["account_label"] == "alice@co.com"
    assert figma["status"] == "active"
    # Post-Nov-2025: `files:read` was replaced by granular file_* scopes.
    assert "file_content:read" in figma["scopes"]
    assert "file_metadata:read" in figma["scopes"]


def test_figma_callback_rejects_bad_state(figma_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get(
        "/v1/connectors/figma/callback",
        params={"code": "abc", "state": "not.a.jwt"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_figma_disconnect(figma_env, monkeypatch):
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob={"access_token": "x"},
        label="alice@co.com",
    )
    r = ctx.client.delete(
        "/v1/connectors/figma"
    )
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert ctx.client.get(
        "/v1/connectors"
    ).json()["connections"] == []


# ─────────────────────── GitHub ───────────────────────


def test_github_authorize_redirects_to_github(github_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get(
        "/v1/connectors/github/authorize",
        follow_redirects=False,
    )
    assert r.status_code == 307
    loc = r.headers["location"]
    assert loc.startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=gh-client-id" in loc
    assert "state=" in loc


def _seed_github_install(*, account_login: str, installation_id: int = 12345) -> None:
    """Seed a github_installations row for the given login. Mimics what the
    'installation' webhook would have created when the user installed the
    Sprntly App on a repo. Used to test the OAuth-callback branching:
    if an install exists for the OAuth'd user → redirect to /settings;
    if not → redirect to the App install URL."""
    from app.db.client import require_client

    require_client().table("github_installations").upsert(
        {
            "installation_id": installation_id,
            "account_id": 42,
            "account_login": account_login,
            "account_type": "User",
            "repository_selection": "all",
        }
    ).execute()


def test_github_callback_with_existing_install_redirects_to_settings(
    github_env, monkeypatch
):
    """If the OAuth'd user already has the Sprntly App installed on their
    account, send them to the connectors page (current happy-path UX)."""
    ctx = company_client(monkeypatch)
    from app.connectors import github_app

    fake_token = {
        "access_token": "gho_xxx",
        "token_type": "bearer",
        "scope": "read:user,user:email",
    }
    fake_user = {"login": "octocat", "id": 1, "email": "octo@cat.dev"}
    _seed_github_install(account_login="octocat")

    state = github_app.sign_oauth_state(company_id=ctx.company_id)
    with patch("app.routes.connectors.github_app.exchange_code_for_token", return_value=fake_token), \
         patch("app.routes.connectors.github_app.fetch_authenticated_user", return_value=fake_user):
        r = ctx.client.get(
            "/v1/connectors/github/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )

    assert r.status_code == 307
    assert "connected=github" in r.headers["location"]
    # The connection row is still written either way.
    listed = ctx.client.get("/v1/connectors").json()["connections"]
    gh = next(c for c in listed if c["provider"] == "github")
    assert gh["account_label"] == "@octocat"


def test_github_callback_post_install_redirect_no_state(github_env, monkeypatch):
    """GitHub reuses our OAuth callback URL for the post-install redirect
    (when the App is configured with 'Request OAuth during install', or
    when no Setup URL is set). That redirect carries setup_action +
    installation_id but NO state. We must not 422 on it — instead bounce
    back to /settings, carrying the setup_action forward so the UI can
    show 'approval pending' vs 'install complete'."""
    ctx = company_client(monkeypatch)
    r = ctx.client.get(
        "/v1/connectors/github/callback",
        params={"code": "ignored", "setup_action": "request"},
        follow_redirects=False,
    )
    assert r.status_code == 307
    loc = r.headers["location"]
    assert "section=connectors" in loc
    assert "connected=github" in loc
    assert "setup_action=request" in loc


def test_github_callback_post_install_with_installation_id(github_env, monkeypatch):
    """The setup_action=install variant also carries installation_id."""
    ctx = company_client(monkeypatch)
    r = ctx.client.get(
        "/v1/connectors/github/callback",
        params={"setup_action": "install", "installation_id": 12345},
        follow_redirects=False,
    )
    assert r.status_code == 307
    loc = r.headers["location"]
    assert "setup_action=install" in loc
    assert "installation_id=12345" in loc


def test_github_callback_with_no_install_redirects_to_app_install_url(
    github_env, monkeypatch
):
    """If the OAuth'd user has no Sprntly App install yet, the callback
    must redirect them to github.com/apps/<slug>/installations/new so
    they can pick which repos to grant access to. This closes the gap
    where users finished OAuth but had no installation_id → empty
    installation picker on /lab/code-chat."""
    ctx = company_client(monkeypatch)
    from app.connectors import github_app

    fake_token = {"access_token": "gho_yyy", "token_type": "bearer"}
    fake_user = {"login": "newuser", "id": 99, "email": "new@user.dev"}

    state = github_app.sign_oauth_state(company_id=ctx.company_id)
    with patch("app.routes.connectors.github_app.exchange_code_for_token", return_value=fake_token), \
         patch("app.routes.connectors.github_app.fetch_authenticated_user", return_value=fake_user):
        r = ctx.client.get(
            "/v1/connectors/github/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )

    assert r.status_code == 307
    loc = r.headers["location"]
    assert loc.startswith("https://github.com/apps/")
    assert "/installations/new" in loc
    # The connection row was still written — OAuth succeeded, we just
    # need them to install the App on a repo before they can use it.
    listed = ctx.client.get("/v1/connectors").json()["connections"]
    assert any(c["provider"] == "github" for c in listed)


def test_github_callback_rejects_error_payload(github_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import github_app
    state = github_app.sign_oauth_state(company_id=ctx.company_id)
    with patch(
        "app.routes.connectors.github_app.exchange_code_for_token",
        side_effect=lambda code: (_ for _ in ()).throw(__import__("fastapi").HTTPException(400, "GitHub token exchange error: bad_verification_code")),
    ):
        r = ctx.client.get(
            "/v1/connectors/github/callback",
            params={"code": "bad", "state": state},
            follow_redirects=False,
        )
    assert r.status_code == 400


def test_github_disconnect(github_env, monkeypatch):
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="github",
        token_blob={"access_token": "x"},
        label="@octocat",
    )
    r = ctx.client.delete(
        "/v1/connectors/github"
    )
    assert r.status_code == 200
    assert r.json()["deleted"] is True


# ─────────────────────── Figma data endpoints (Design Agent input) ───────────────────────


def test_figma_get_file_requires_connection(figma_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get(
        "/v1/connectors/figma/files/abc123",
    )
    assert r.status_code == 404


def test_figma_get_file_returns_figma_payload(figma_env, monkeypatch):
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob={"access_token": "fig-access"},
    )
    fake_doc = {"name": "Design System", "document": {"id": "0:1", "children": []}}
    with patch(
        "app.routes.connectors.figma_oauth.fetch_file", return_value=fake_doc
    ) as mock_fetch:
        r = ctx.client.get(
            "/v1/connectors/figma/files/abc123",
            params={"depth": 3},
        )
    assert r.status_code == 200
    assert r.json() == fake_doc
    mock_fetch.assert_called_once_with("fig-access", "abc123", depth=3)


def test_figma_get_file_styles_returns_figma_payload(figma_env, monkeypatch):
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="figma",
        token_blob={"access_token": "fig-access"},
    )
    fake_styles = {"meta": {"styles": [{"key": "S:1", "name": "Brand/Primary"}]}}
    with patch(
        "app.routes.connectors.figma_oauth.fetch_file_styles", return_value=fake_styles
    ) as mock_fetch:
        r = ctx.client.get(
            "/v1/connectors/figma/files/abc123/styles",
        )
    assert r.status_code == 200
    assert r.json() == fake_styles
    mock_fetch.assert_called_once_with("fig-access", "abc123")


# ─────────────────────── GitHub data endpoints (Engineer Agent input) ───────────────────────


def test_github_repos_requires_connection(github_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get(
        "/v1/connectors/github/repos",
    )
    assert r.status_code == 404


def test_github_repos_returns_trimmed_list(github_env, monkeypatch):
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="github",
        token_blob={"access_token": "gho_xxx"},
        label="@octocat",
    )
    fake_repos = [
        {"full_name": "octocat/hello", "name": "hello", "private": False,
         "html_url": "https://github.com/octocat/hello", "default_branch": "main",
         "description": "Hi", "updated_at": "2026-05-20T00:00:00Z", "stargazers_count": 3},
    ]
    with patch(
        "app.routes.connectors.github_app.fetch_user_repos", return_value=fake_repos
    ) as mock_fetch:
        r = ctx.client.get(
            "/v1/connectors/github/repos",
            params={"per_page": 10},
        )
    assert r.status_code == 200
    assert r.json() == {"repositories": fake_repos}
    mock_fetch.assert_called_once_with("gho_xxx", per_page=10)


def test_github_app_jwt_signs_with_rs256(monkeypatch, isolated_settings):
    """Smoke-test the app-as-app JWT helper end-to-end against a generated key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    monkeypatch.setenv("GITHUB_APP_ID", "999")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", pem)
    _reload_app_modules()
    import jwt as _jwt
    from app.connectors import github_app
    token = github_app.make_app_jwt()
    # Verify with the public counterpart.
    pub_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    decoded = _jwt.decode(token, pub_pem, algorithms=["RS256"])
    assert decoded["iss"] == "999"
