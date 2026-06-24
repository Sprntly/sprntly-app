"""GitHub connector runs on the App INSTALLATION token, not a member's
personal OAuth token.

The connectors screen is a company-shared connector. Previously the repo
picker and the "Test connection" probe both ran on the connecting member's
personal OAuth token (~8h lifetime), so they 401'd ("Bad credentials") for
everyone once the token aged out. These tests pin the fix:

  - repo-list resolves via github_app.fetch_installation_repos (install token);
  - /github/test probes the company install token, falling back to the
    personal token only when no install exists;
  - the post-install bind backfills real account details from GitHub's App
    API when the row is missing/thin, so we never persist an empty skeleton,
    and heals an existing full orphan row without a redundant backfill.

GitHub HTTP and the App-token mint are mocked; the in-memory fake Supabase
from conftest backs the DB (no real network).
"""
from __future__ import annotations

import importlib
import sys
import uuid
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

import app.auth  # noqa: F401

from tests._company_helpers import company_client, seed_connection


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.github_app",
        "app.connector_probe",
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


def _seed_github_oauth(*, company_id: str, with_install: bool = True) -> None:
    seed_connection(
        company_id=company_id,
        provider="github",
        token_blob={"access_token": "gho_USER_TOKEN", "token_type": "bearer"},
        label="@octocat",
    )
    if with_install:
        from app import db
        db.upsert_github_installation(
            installation_id=12345,
            account_id=1,
            account_login="octocat",
            account_type="User",
            suspended=False,
            company_id=company_id,
        )


# ─────────────────────── repo-list uses the install token ───────────────────────


def test_repo_list_uses_installation_token(github_env, monkeypatch):
    """GET .../repositories returns the install-token repo list (with `id`)
    and never touches the personal-token path."""
    ctx = company_client(monkeypatch)
    _seed_github_oauth(company_id=ctx.company_id)

    repos = [
        {
            "id": 501,
            "name": "web",
            "full_name": "octocat/web",
            "private": False,
            "html_url": "https://github.com/octocat/web",
            "default_branch": "main",
            "description": "the web app",
        },
    ]

    def _boom(*_a, **_k):
        raise AssertionError("personal OAuth token path must not be used")

    with patch(
        "app.routes.connectors.github_app.fetch_installation_repos",
        return_value=repos,
    ) as mfetch, patch(
        "app.routes.connectors.github_app.fetch_authenticated_user",
        side_effect=_boom,
    ), patch(
        "app.routes.connectors._github_access_token", side_effect=_boom
    ):
        r = ctx.client.get(
            "/v1/connectors/github/installations/12345/repositories"
        )

    assert r.status_code == 200, r.text
    body = r.json()
    mfetch.assert_called_once_with(12345)
    assert [x["id"] for x in body["repositories"]] == [501]
    assert body["repositories"][0]["full_name"] == "octocat/web"


# ─────────────────────── /github/test probes the install ───────────────────────


def test_test_endpoint_probes_installation(github_env, monkeypatch):
    """A non-suspended install + a mintable install token = healthy, even
    though the stored personal token is dead."""
    ctx = company_client(monkeypatch)
    _seed_github_oauth(company_id=ctx.company_id)

    with patch(
        "app.connectors.github_app.get_installation_token", return_value="ghs_x"
    ), patch(
        "app.connector_probe.github_app.fetch_authenticated_user",
        return_value={},  # personal token is dead
    ):
        r = ctx.client.post("/v1/connectors/github/test")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert "octocat" in body["account_label"]


def test_test_endpoint_falls_back_to_personal_when_no_install(
    github_env, monkeypatch
):
    """No install at all → fall back to the personal token. A dead personal
    token then yields the unchanged 400 reject."""
    ctx = company_client(monkeypatch)
    _seed_github_oauth(company_id=ctx.company_id, with_install=False)

    with patch(
        "app.connector_probe.github_app.fetch_authenticated_user",
        return_value={},  # personal token rejected
    ):
        r = ctx.client.post("/v1/connectors/github/test")

    assert r.status_code == 400, r.text


# ─────────────────────── bind backfills / heals ───────────────────────


def test_bind_backfills_thin_row_no_skeleton(github_env):
    """No existing row → backfill real account details from GitHub's App API
    so we never persist an empty skeleton (account_login=''/account_id=0)."""
    from app import db
    import app.routes.connectors as conn_route

    company_id = uuid.uuid4().hex
    install_id = 880001

    detail = {
        "account": {"id": 4242, "login": "acme-org", "type": "Organization"},
        "repository_selection": "all",
        "permissions": {"contents": "read"},
        "events": ["push"],
    }
    with patch(
        "app.routes.connectors.github_app.fetch_app_installation",
        return_value=detail,
    ) as mdetail:
        conn_route._bind_installation_company(install_id, company_id)

    mdetail.assert_called_once_with(install_id)
    row = db.get_github_installation(install_id)
    assert row is not None
    assert row["account_login"] == "acme-org"
    assert int(row["account_id"]) == 4242
    assert row["account_type"] == "Organization"
    assert row["repository_selection"] == "all"
    assert row["company_id"] == company_id


def test_bind_heals_existing_orphan(github_env):
    """An existing FULL row with company_id=None gets company_id set while its
    account details are preserved — and no redundant App-API backfill fires."""
    from app import db
    import app.routes.connectors as conn_route

    company_id = uuid.uuid4().hex
    install_id = 880002

    db.upsert_github_installation(
        installation_id=install_id,
        account_id=909,
        account_login="globex",
        account_type="Organization",
        suspended=False,
        company_id=None,
    )

    with patch(
        "app.routes.connectors.github_app.fetch_app_installation",
    ) as mdetail:
        conn_route._bind_installation_company(install_id, company_id)

    mdetail.assert_not_called()  # existing full row → no backfill
    row = db.get_github_installation(install_id)
    assert row["account_login"] == "globex"
    assert int(row["account_id"]) == 909
    assert row["company_id"] == company_id
