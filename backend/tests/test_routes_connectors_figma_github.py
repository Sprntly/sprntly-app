"""Tests for the Figma + GitHub connector OAuth routes.

All outbound HTTP (token exchange, user lookup) is mocked. We assert:
  - authorize returns a 307 to the right consent URL with a state JWT
  - callback exchanges code, decodes state, stores an encrypted token,
    redirects to the frontend with ?connected=<provider>
  - list_connections shows the new row with account_label populated
  - delete removes the row
"""
from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


# ─────────────────────── fixtures ───────────────────────


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


def _logged_in_client():
    import app.main as main_mod
    client = TestClient(main_mod.app)
    r = client.post("/v1/auth/login", json={"password": "test-pw"})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture
def figma_client(figma_env):
    return _logged_in_client()


@pytest.fixture
def github_client(github_env):
    return _logged_in_client()


# ─────────────────────── Figma ───────────────────────


def test_figma_authorize_redirects_to_figma(figma_client):
    r = figma_client.get("/v1/connectors/figma/authorize", follow_redirects=False)
    assert r.status_code == 307
    loc = r.headers["location"]
    assert loc.startswith("https://www.figma.com/oauth?")
    assert "client_id=figma-client-id" in loc
    assert "state=" in loc
    assert "scope=" in loc


def test_figma_callback_stores_token(figma_client):
    # Sign a real state so the callback validates.
    from app.connectors import figma_oauth
    state = figma_oauth.sign_oauth_state()

    fake_token = {
        "access_token": "fig-access",
        "refresh_token": "fig-refresh",
        "expires_in": 7776000,
        "user_id": "user-123",
    }
    fake_me = {"id": "user-123", "email": "alice@co.com", "handle": "alice"}

    with patch("app.routes.connectors.figma_oauth.exchange_code_for_token", return_value=fake_token), \
         patch("app.routes.connectors.figma_oauth.fetch_me", return_value=fake_me):
        r = figma_client.get(
            "/v1/connectors/figma/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )

    assert r.status_code == 307
    assert r.headers["location"].startswith("http://localhost:3000/connectors")
    assert "connected=figma" in r.headers["location"]

    # Row landed in db
    listed = figma_client.get("/v1/connectors").json()["connections"]
    figma = next(c for c in listed if c["provider"] == "figma")
    assert figma["account_label"] == "alice@co.com"
    assert figma["status"] == "active"
    assert "files:read" in figma["scopes"]


def test_figma_callback_honors_return_to(figma_client):
    """A return_to from a trusted origin redirects back to that surface
    (carrying its /demo base path) instead of the global FRONTEND_URL."""
    from app.connectors import figma_oauth
    # http://localhost:3000 is the test FRONTEND_URL, so its origin is trusted.
    state = figma_oauth.sign_oauth_state(return_to="http://localhost:3000/demo")

    with patch("app.routes.connectors.figma_oauth.exchange_code_for_token",
               return_value={"access_token": "fig-access", "user_id": "u"}), \
         patch("app.routes.connectors.figma_oauth.fetch_me", return_value={}):
        r = figma_client.get(
            "/v1/connectors/figma/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )

    assert r.status_code == 307
    assert r.headers["location"].startswith("http://localhost:3000/demo/connectors")
    assert "connected=figma" in r.headers["location"]


def test_figma_callback_rejects_foreign_return_to(figma_client):
    """An untrusted return_to is ignored — no open redirect. Falls back to
    FRONTEND_URL."""
    from app.connectors import figma_oauth
    state = figma_oauth.sign_oauth_state(return_to="https://evil.example.com/phish")

    with patch("app.routes.connectors.figma_oauth.exchange_code_for_token",
               return_value={"access_token": "fig-access", "user_id": "u"}), \
         patch("app.routes.connectors.figma_oauth.fetch_me", return_value={}):
        r = figma_client.get(
            "/v1/connectors/figma/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )

    assert r.status_code == 307
    loc = r.headers["location"]
    assert loc.startswith("http://localhost:3000/connectors")
    assert "evil.example.com" not in loc


def test_figma_callback_rejects_bad_state(figma_client):
    r = figma_client.get(
        "/v1/connectors/figma/callback",
        params={"code": "abc", "state": "not.a.jwt"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_figma_disconnect(figma_client):
    # Insert a row directly via the db API.
    from app.connectors import figma_oauth
    from app.connectors.tokens import encrypt_token_json
    import app.db as db_mod
    db_mod.upsert_connection(
        provider=figma_oauth.FIGMA_PROVIDER,
        token_encrypted=encrypt_token_json(json.dumps({"access_token": "x"})),
        scopes=figma_oauth.DEFAULT_SCOPES,
        account_label="alice@co.com",
    )
    r = figma_client.delete("/v1/connectors/figma")
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    assert figma_client.get("/v1/connectors").json()["connections"] == []


# ─────────────────────── GitHub ───────────────────────


def test_github_authorize_redirects_to_github(github_client):
    r = github_client.get("/v1/connectors/github/authorize", follow_redirects=False)
    assert r.status_code == 307
    loc = r.headers["location"]
    assert loc.startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=gh-client-id" in loc
    assert "state=" in loc


def test_github_callback_stores_token(github_client):
    from app.connectors import github_app
    state = github_app.sign_oauth_state()

    fake_token = {
        "access_token": "gho_xxx",
        "token_type": "bearer",
        "scope": "read:user,user:email",
        "refresh_token": "ghr_xxx",
        "expires_in": 28800,
    }
    fake_user = {"login": "octocat", "id": 1, "email": "octo@cat.dev"}

    with patch("app.routes.connectors.github_app.exchange_code_for_token", return_value=fake_token), \
         patch("app.routes.connectors.github_app.fetch_authenticated_user", return_value=fake_user):
        r = github_client.get(
            "/v1/connectors/github/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )

    assert r.status_code == 307
    assert "connected=github" in r.headers["location"]

    listed = github_client.get("/v1/connectors").json()["connections"]
    gh = next(c for c in listed if c["provider"] == "github")
    assert gh["account_label"] == "@octocat"
    assert gh["scopes"] == "read:user,user:email"


def test_github_callback_rejects_error_payload(github_client):
    from app.connectors import github_app
    state = github_app.sign_oauth_state()
    # GitHub returns 200 + {error: ...} on app errors — make sure that 400s.
    with patch(
        "app.routes.connectors.github_app.exchange_code_for_token",
        side_effect=lambda code: (_ for _ in ()).throw(__import__("fastapi").HTTPException(400, "GitHub token exchange error: bad_verification_code")),
    ):
        r = github_client.get(
            "/v1/connectors/github/callback",
            params={"code": "bad", "state": state},
            follow_redirects=False,
        )
    assert r.status_code == 400


def test_github_disconnect(github_client):
    from app.connectors import github_app
    from app.connectors.tokens import encrypt_token_json
    import app.db as db_mod
    db_mod.upsert_connection(
        provider=github_app.GITHUB_PROVIDER,
        token_encrypted=encrypt_token_json(json.dumps({"access_token": "x"})),
        scopes="read:user",
        account_label="@octocat",
    )
    r = github_client.delete("/v1/connectors/github")
    assert r.status_code == 200
    assert r.json()["deleted"] is True


# ─────────────────────── Figma data endpoints (Design Agent input) ───────────────────────


def _seed_figma_token(token: str = "fig-access") -> None:
    from app.connectors import figma_oauth
    from app.connectors.tokens import encrypt_token_json
    import app.db as db_mod
    db_mod.upsert_connection(
        provider=figma_oauth.FIGMA_PROVIDER,
        token_encrypted=encrypt_token_json(json.dumps({"access_token": token})),
        scopes=figma_oauth.DEFAULT_SCOPES,
        account_label="alice@co.com",
    )


def test_figma_get_file_requires_connection(figma_client):
    r = figma_client.get("/v1/connectors/figma/files/abc123")
    assert r.status_code == 404


def test_figma_get_file_returns_figma_payload(figma_client):
    _seed_figma_token()
    fake_doc = {"name": "Design System", "document": {"id": "0:1", "children": []}}
    with patch(
        "app.routes.connectors.figma_oauth.fetch_file", return_value=fake_doc
    ) as mock_fetch:
        r = figma_client.get("/v1/connectors/figma/files/abc123?depth=3")
    assert r.status_code == 200
    assert r.json() == fake_doc
    mock_fetch.assert_called_once_with("fig-access", "abc123", depth=3)


def test_figma_get_file_styles_returns_figma_payload(figma_client):
    _seed_figma_token()
    fake_styles = {"meta": {"styles": [{"key": "S:1", "name": "Brand/Primary"}]}}
    with patch(
        "app.routes.connectors.figma_oauth.fetch_file_styles", return_value=fake_styles
    ) as mock_fetch:
        r = figma_client.get("/v1/connectors/figma/files/abc123/styles")
    assert r.status_code == 200
    assert r.json() == fake_styles
    mock_fetch.assert_called_once_with("fig-access", "abc123")


# ─────────────────────── GitHub data endpoints (Engineer Agent input) ───────────────────────


def _seed_github_token(token: str = "gho_xxx") -> None:
    from app.connectors import github_app
    from app.connectors.tokens import encrypt_token_json
    import app.db as db_mod
    db_mod.upsert_connection(
        provider=github_app.GITHUB_PROVIDER,
        token_encrypted=encrypt_token_json(json.dumps({"access_token": token})),
        scopes="read:user user:email",
        account_label="@octocat",
    )


def test_github_repos_requires_connection(github_client):
    r = github_client.get("/v1/connectors/github/repos")
    assert r.status_code == 404


def test_github_repos_returns_trimmed_list(github_client):
    _seed_github_token()
    fake_repos = [
        {"full_name": "octocat/hello", "name": "hello", "private": False,
         "html_url": "https://github.com/octocat/hello", "default_branch": "main",
         "description": "Hi", "updated_at": "2026-05-20T00:00:00Z", "stargazers_count": 3},
    ]
    with patch(
        "app.routes.connectors.github_app.fetch_user_repos", return_value=fake_repos
    ) as mock_fetch:
        r = github_client.get("/v1/connectors/github/repos?per_page=10")
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
