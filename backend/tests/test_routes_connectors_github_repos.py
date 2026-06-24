"""Tests for GitHub installation-repos management routes.

  GET    /v1/connectors/github/installations/{id}/repositories
  PUT    /v1/connectors/github/installations/{id}/repositories/{repo_id}
  DELETE /v1/connectors/github/installations/{id}/repositories/{repo_id}

These wrap GitHub's `/user/installations/{id}/repositories` family —
which is gated on the user's OAuth token, not the App JWT. That's why
each call decrypts the company's stored GitHub access_token (the one
captured during the OAuth callback) and passes it as a Bearer.

Note: PUT/DELETE only work for installations with
`repository_selection: "selected"`. For `all`, GitHub returns 422 and
the UI should disable the per-repo controls.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import patch, MagicMock

import pytest
from cryptography.fernet import Fernet

import app.auth  # noqa: F401

from tests._company_helpers import company_client, seed_connection


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.github_app",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def github_env(isolated_settings, monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "gh-client-id")
    monkeypatch.setenv("GITHUB_APP_CLIENT_SECRET", "gh-client-secret")
    monkeypatch.setenv(
        "GITHUB_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/github/callback",
    )
    _reload_app_modules()
    yield


def _seed_github_oauth(*, company_id: str) -> None:
    """Seed a github connection row with an encrypted user OAuth token, plus
    the installation rows these tests address (12345, 99) bound to the company
    so the per-installation routes' ownership check passes."""
    seed_connection(
        company_id=company_id,
        provider="github",
        token_blob={"access_token": "gho_USER_TOKEN", "token_type": "bearer"},
        label="@octocat",
    )
    from app import db
    for install_id in (12345, 99):
        db.upsert_github_installation(
            installation_id=install_id,
            account_id=1,
            account_login="octocat",
            account_type="User",
            company_id=company_id,
        )


# ─────────────────────── GET repositories ───────────────────────


def test_list_install_repos_returns_repo_summaries(github_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_github_oauth(company_id=ctx.company_id)

    # Repo-list now goes via the App INSTALLATION token (github_app.
    # fetch_installation_repos), not the connecting member's personal OAuth
    # token — so the picker keeps working past the ~8h OAuth-token lifetime.
    repos = [
        {
            "id": 101,
            "name": "widgets",
            "full_name": "octocat/widgets",
            "private": False,
            "html_url": "https://github.com/octocat/widgets",
            "default_branch": "main",
            "description": "things",
        },
        {
            "id": 102,
            "name": "internal",
            "full_name": "octocat/internal",
            "private": True,
            "html_url": "https://github.com/octocat/internal",
            "default_branch": "main",
            "description": None,
        },
    ]
    with patch(
        "app.routes.connectors.github_app.fetch_installation_repos",
        return_value=repos,
    ):
        r = ctx.client.get(
            "/v1/connectors/github/installations/12345/repositories"
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert len(body["repositories"]) == 2
    assert body["repositories"][0]["full_name"] == "octocat/widgets"
    assert body["repositories"][0]["id"] == 101
    assert body["repositories"][1]["private"] is True


def test_list_install_repos_uses_installation_token_not_personal(
    github_env, monkeypatch
):
    """Repo-list resolves via the App installation token, never the personal
    OAuth path — so it survives the personal token aging out. Prove the
    personal-token path is not touched."""
    ctx = company_client(monkeypatch)
    _seed_github_oauth(company_id=ctx.company_id)

    def _boom(*_a, **_k):
        raise AssertionError("personal OAuth token path must not be used")

    with patch(
        "app.routes.connectors.github_app.fetch_installation_repos",
        return_value=[],
    ) as mfetch, patch(
        "app.routes.connectors._github_access_token", side_effect=_boom
    ), patch("app.routes.connectors.requests.get", side_effect=_boom):
        r = ctx.client.get(
            "/v1/connectors/github/installations/99/repositories"
        )

    assert r.status_code == 200, r.text
    mfetch.assert_called_once_with(99)


def test_list_install_repos_requires_github_connection(github_env, monkeypatch):
    ctx = company_client(monkeypatch)
    # NO connection seeded — should 404.
    r = ctx.client.get(
        "/v1/connectors/github/installations/12345/repositories"
    )
    assert r.status_code == 404


# ─────────────────────── PUT (add repo to installation) ───────────────────────


def test_put_install_repo_adds_to_installation(github_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_github_oauth(company_id=ctx.company_id)

    mock_resp = MagicMock(ok=True, status_code=204)
    with patch(
        "app.routes.connectors.requests.put", return_value=mock_resp
    ) as mput:
        r = ctx.client.put(
            "/v1/connectors/github/installations/12345/repositories/777"
        )
    assert r.status_code == 200
    assert r.json()["added"] is True
    mput.assert_called_once()
    args, kwargs = mput.call_args
    assert (
        args[0]
        == "https://api.github.com/user/installations/12345/repositories/777"
    )
    assert kwargs["headers"]["Authorization"] == "Bearer gho_USER_TOKEN"


def test_put_install_repo_surfaces_422_for_all_repos_installs(
    github_env, monkeypatch
):
    """GitHub returns 422 when the installation is in 'all repositories'
    mode — per-repo adds aren't allowed there. Surface as 422 to the UI
    so it can show the right message."""
    ctx = company_client(monkeypatch)
    _seed_github_oauth(company_id=ctx.company_id)

    mock_resp = MagicMock(
        ok=False,
        status_code=422,
        text='{"message": "installation has all repositories"}',
    )
    mock_resp.json.return_value = {
        "message": "installation has all repositories",
    }
    with patch("app.routes.connectors.requests.put", return_value=mock_resp):
        r = ctx.client.put(
            "/v1/connectors/github/installations/12345/repositories/777"
        )
    assert r.status_code == 422
    detail = r.json()["detail"].lower()
    assert "all repositories" in detail or "selected" in detail


# ─────────────────────── DELETE (remove repo from installation) ───────────────────────


def test_delete_install_repo_removes(github_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_github_oauth(company_id=ctx.company_id)

    mock_resp = MagicMock(ok=True, status_code=204)
    with patch(
        "app.routes.connectors.requests.delete", return_value=mock_resp
    ) as mdel:
        r = ctx.client.delete(
            "/v1/connectors/github/installations/12345/repositories/777"
        )
    assert r.status_code == 200
    assert r.json()["removed"] is True
    mdel.assert_called_once()
    args, kwargs = mdel.call_args
    assert (
        args[0]
        == "https://api.github.com/user/installations/12345/repositories/777"
    )


def test_delete_install_repo_404_when_repo_not_in_install(github_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_github_oauth(company_id=ctx.company_id)

    mock_resp = MagicMock(ok=False, status_code=404, text="Not Found")
    mock_resp.json.return_value = {"message": "Not Found"}
    with patch("app.routes.connectors.requests.delete", return_value=mock_resp):
        r = ctx.client.delete(
            "/v1/connectors/github/installations/12345/repositories/999"
        )
    assert r.status_code == 404


# ─────────────────────── auth gates ───────────────────────


def test_list_install_repos_requires_auth(github_env, monkeypatch):
    company_client(monkeypatch)
    from fastapi.testclient import TestClient
    import app.main as main_mod

    unauth = TestClient(main_mod.app)
    r = unauth.get(
        "/v1/connectors/github/installations/12345/repositories"
    )
    assert r.status_code == 401
