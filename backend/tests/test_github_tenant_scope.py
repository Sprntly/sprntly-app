"""Tenant-scoping for the GitHub App connector.

Regression suite for the cross-tenant leak: github_installations and
github_pull_requests were keyed by installation_id only, with no company
column, so `GET /v1/connectors/github/installations` (and the PR list) under
`require_session` returned EVERY company's rows to any signed-in user.

The model: connectors are company-scoped and shared among the company's
invited members (mirrors the `connections` table's company_id +
unique(company_id, provider)). Access = `require_company`; any member of the
caller's company can use the company's installation. Company identity is the
company UUID, never the name.

Coverage:
  - db-layer scoping: list_github_installations / list_open_pull_requests /
    find_github_installation_for_repo / get_github_installation_for_company all
    filter by company and exclude legacy NULL-company rows.
  - cross-tenant denial: company A never sees company B's installs/PRs.
  - member-shared: a SECOND member of company A sees company A's install.
  - callback persists company_id from the signed state onto the installation.
  - route gating: the list/PR/per-install routes require_company and 404 on a
    foreign or unbound installation_id.

GitHub HTTP and Supabase are both mocked (the in-memory fake Supabase from
conftest; no real network).
"""
from __future__ import annotations

import importlib
import sys
import uuid

import pytest
from cryptography.fernet import Fernet

import app.auth  # noqa: F401

from tests._company_helpers import (
    company_client,
    seed_company,
    seed_connection,
    setup_supabase_auth,
    supabase_bearer,
)


# ─────────────────────── fixtures / helpers ───────────────────────


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


def _seed_install(*, installation_id: int, company_id: str | None, login: str):
    from app import db
    db.upsert_github_installation(
        installation_id=installation_id,
        account_id=installation_id,
        account_login=login,
        account_type="Organization",
        company_id=company_id,
    )


def _seed_pr(*, installation_id: int, company_id: str | None, repo: str, number: int):
    from app import db
    db.upsert_github_pull_request(
        installation_id=installation_id,
        repo_full_name=repo,
        pr_number=number,
        title=f"PR {number}",
        state="open",
        company_id=company_id,
    )


# ─────────────────────── db-layer: installations ───────────────────────


def test_list_installations_scoped_to_company(github_env):
    company_a = uuid.uuid4().hex
    company_b = uuid.uuid4().hex
    _seed_install(installation_id=1, company_id=company_a, login="acme")
    _seed_install(installation_id=2, company_id=company_b, login="globex")

    from app import db
    rows_a = db.list_github_installations(company_a)
    assert [r["installation_id"] for r in rows_a] == [1]
    rows_b = db.list_github_installations(company_b)
    assert [r["installation_id"] for r in rows_b] == [2]


def test_list_installations_excludes_null_company_rows(github_env):
    company_a = uuid.uuid4().hex
    _seed_install(installation_id=1, company_id=company_a, login="acme")
    _seed_install(installation_id=9, company_id=None, login="legacy")  # legacy row

    from app import db
    rows = db.list_github_installations(company_a)
    assert [r["installation_id"] for r in rows] == [1]
    # And no company ever sees the unbound legacy row.
    assert db.list_github_installations(uuid.uuid4().hex) == []


def test_list_installations_empty_company_returns_nothing(github_env):
    _seed_install(installation_id=1, company_id=uuid.uuid4().hex, login="acme")
    from app import db
    assert db.list_github_installations("") == []


def test_get_installation_for_company_rejects_foreign_and_legacy(github_env):
    company_a = uuid.uuid4().hex
    company_b = uuid.uuid4().hex
    _seed_install(installation_id=1, company_id=company_a, login="acme")
    _seed_install(installation_id=9, company_id=None, login="legacy")

    from app import db
    assert db.get_github_installation_for_company(1, company_a)["installation_id"] == 1
    # Foreign company → None (no existence leak).
    assert db.get_github_installation_for_company(1, company_b) is None
    # Legacy NULL-company → None.
    assert db.get_github_installation_for_company(9, company_a) is None
    # Unknown id → None.
    assert db.get_github_installation_for_company(404, company_a) is None


# ─────────────────────── db-layer: find_for_repo ───────────────────────


def test_find_installation_for_repo_scoped_to_company(github_env):
    company_a = uuid.uuid4().hex
    company_b = uuid.uuid4().hex
    # Same account login "acme" installed under BOTH companies.
    _seed_install(installation_id=1, company_id=company_a, login="acme")
    _seed_install(installation_id=2, company_id=company_b, login="acme")

    from app import db
    found_a = db.find_github_installation_for_repo("acme/widgets", company_a)
    assert found_a["installation_id"] == 1
    found_b = db.find_github_installation_for_repo("acme/widgets", company_b)
    assert found_b["installation_id"] == 2


def test_find_installation_for_repo_excludes_other_company_and_legacy(github_env):
    company_a = uuid.uuid4().hex
    company_b = uuid.uuid4().hex
    _seed_install(installation_id=2, company_id=company_b, login="acme")
    _seed_install(installation_id=9, company_id=None, login="acme")  # legacy

    from app import db
    # Company A has NO acme install → must not resolve B's or the legacy one.
    assert db.find_github_installation_for_repo("acme/widgets", company_a) is None


# ─────────────────────── db-layer: pull requests ───────────────────────


def test_list_open_prs_scoped_to_company(github_env):
    company_a = uuid.uuid4().hex
    company_b = uuid.uuid4().hex
    _seed_pr(installation_id=1, company_id=company_a, repo="acme/x", number=1)
    _seed_pr(installation_id=2, company_id=company_b, repo="globex/y", number=2)

    from app import db
    prs_a = db.list_open_pull_requests(company_a)
    assert [p["pr_number"] for p in prs_a] == [1]
    prs_b = db.list_open_pull_requests(company_b)
    assert [p["pr_number"] for p in prs_b] == [2]


def test_list_open_prs_excludes_null_company_and_empty(github_env):
    company_a = uuid.uuid4().hex
    _seed_pr(installation_id=1, company_id=company_a, repo="acme/x", number=1)
    _seed_pr(installation_id=9, company_id=None, repo="legacy/z", number=3)

    from app import db
    prs = db.list_open_pull_requests(company_a)
    assert [p["pr_number"] for p in prs] == [1]
    # Empty company → nothing (never a global list).
    assert db.list_open_pull_requests("") == []
    assert db.list_open_pull_requests(uuid.uuid4().hex) == []


# ─────────────────────── route gating ───────────────────────


def test_installations_route_requires_company(github_env):
    """Unauthenticated → 401."""
    import app.main as main_mod
    from fastapi.testclient import TestClient
    c = TestClient(main_mod.app)
    assert c.get("/v1/connectors/github/installations").status_code == 401
    assert c.get("/v1/connectors/github/pull-requests").status_code == 401


def test_installations_route_cross_tenant_denial(github_env, monkeypatch):
    """Company A's session must NOT see company B's installation."""
    ctx = company_client(monkeypatch)
    company_b = seed_company(user_id="user-b-" + uuid.uuid4().hex[:6], slug="globex")
    _seed_install(installation_id=1, company_id=ctx.company_id, login="acme")
    _seed_install(installation_id=2, company_id=company_b, login="globex")

    r = ctx.client.get("/v1/connectors/github/installations")
    assert r.status_code == 200
    ids = [i["installation_id"] for i in r.json()["installations"]]
    assert ids == [1]  # only company A's install — never B's


def test_installations_route_member_shared(github_env, monkeypatch):
    """A SECOND member of company A sees company A's installation (connectors
    are shared among the company's invited members)."""
    ctx = company_client(monkeypatch)
    _seed_install(installation_id=1, company_id=ctx.company_id, login="acme")

    # Add a second user to the SAME company, then hit the route as them.
    from app.db.client import require_client
    member_b = "user-a2-" + uuid.uuid4().hex[:6]
    require_client().table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": ctx.company_id,
            "user_id": member_b,
            "role": "member",
        }
    ).execute()

    import app.main as main_mod
    from fastapi.testclient import TestClient
    member_client = TestClient(main_mod.app, headers=supabase_bearer(member_b))
    r = member_client.get("/v1/connectors/github/installations")
    assert r.status_code == 200
    assert [i["installation_id"] for i in r.json()["installations"]] == [1]


def test_pull_requests_route_cross_tenant_denial(github_env, monkeypatch):
    ctx = company_client(monkeypatch)
    company_b = seed_company(user_id="user-b-" + uuid.uuid4().hex[:6], slug="globex")
    _seed_pr(installation_id=1, company_id=ctx.company_id, repo="acme/x", number=1)
    _seed_pr(installation_id=2, company_id=company_b, repo="globex/y", number=2)

    r = ctx.client.get("/v1/connectors/github/pull-requests")
    assert r.status_code == 200
    nums = [p["pr_number"] for p in r.json()["pull_requests"]]
    assert nums == [1]  # never company B's PR


def test_pull_requests_route_foreign_installation_id_404s(github_env, monkeypatch):
    """Passing another company's installation_id must 404, not leak its PRs."""
    ctx = company_client(monkeypatch)
    company_b = seed_company(user_id="user-b-" + uuid.uuid4().hex[:6], slug="globex")
    _seed_install(installation_id=2, company_id=company_b, login="globex")
    _seed_pr(installation_id=2, company_id=company_b, repo="globex/y", number=2)

    r = ctx.client.get(
        "/v1/connectors/github/pull-requests", params={"installation_id": 2}
    )
    assert r.status_code == 404


def test_install_repos_route_foreign_installation_id_404s(github_env, monkeypatch):
    """A member of company A can't manage company B's installation by id."""
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="github",
        token_blob={"access_token": "gho_A", "token_type": "bearer"},
        label="@acme",
    )
    company_b = seed_company(user_id="user-b-" + uuid.uuid4().hex[:6], slug="globex")
    _seed_install(installation_id=2, company_id=company_b, login="globex")

    # No HTTP mock needed — ownership check 404s before any GitHub call.
    r = ctx.client.get("/v1/connectors/github/installations/2/repositories")
    assert r.status_code == 404


# ─────────────────────── callback binds company_id ───────────────────────


def test_callback_post_install_binds_company_id(github_env, monkeypatch):
    """The post-install callback carries the signed state (company_id) and the
    installation_id from GitHub's Setup-URL redirect. It must bind company_id
    onto the installation so the company can then see it. Verifies the leak's
    root cause is closed at the write path."""
    from app import db
    import app.routes.connectors as conn_route
    from app.connectors import github_app

    company_id = uuid.uuid4().hex
    # Webhook created the install first with NO company (the leak shape).
    _seed_install(installation_id=555, company_id=None, login="acme")
    assert db.get_github_installation(555)["company_id"] in (None, "")

    state = github_app.sign_oauth_state(company_id=company_id)

    import app.main as main_mod
    from fastapi.testclient import TestClient
    client = TestClient(main_mod.app, follow_redirects=False)
    # Post-install branch: setup_action + installation_id + state, no code.
    r = client.get(
        "/v1/connectors/github/callback",
        params={
            "setup_action": "install",
            "installation_id": 555,
            "state": state,
        },
    )
    assert r.status_code in (302, 307)
    # Installation is now bound to the company and visible to it.
    bound = db.get_github_installation(555)
    assert bound["company_id"] == company_id
    assert [i["installation_id"] for i in db.list_github_installations(company_id)] == [555]


def test_callback_binds_only_caller_company_not_others(github_env):
    """Binding via the callback must attach the EXACT company from the signed
    state — never a different tenant."""
    from app import db
    from app.connectors import github_app

    company_a = uuid.uuid4().hex
    company_b = uuid.uuid4().hex
    _seed_install(installation_id=777, company_id=None, login="acme")

    state = github_app.sign_oauth_state(company_id=company_a)
    import app.main as main_mod
    from fastapi.testclient import TestClient
    client = TestClient(main_mod.app, follow_redirects=False)
    client.get(
        "/v1/connectors/github/callback",
        params={"setup_action": "install", "installation_id": 777, "state": state},
    )
    assert db.get_github_installation_for_company(777, company_a)["installation_id"] == 777
    assert db.get_github_installation_for_company(777, company_b) is None


def test_webhook_pr_inherits_company_from_installation(github_env):
    """A PR webhook for a company-bound installation tags the PR with that
    company, so the company-scoped PR list surfaces it; an unbound install's
    PR stays NULL-company and invisible."""
    from app import db
    import app.routes.connectors as conn_route

    company_a = uuid.uuid4().hex
    _seed_install(installation_id=42, company_id=company_a, login="acme")
    _seed_install(installation_id=43, company_id=None, login="legacy")

    conn_route._handle_pull_request_event({
        "action": "opened",
        "installation": {"id": 42},
        "repository": {"full_name": "acme/x"},
        "pull_request": {"number": 1, "title": "t", "state": "open"},
    })
    conn_route._handle_pull_request_event({
        "action": "opened",
        "installation": {"id": 43},
        "repository": {"full_name": "legacy/z"},
        "pull_request": {"number": 2, "title": "t", "state": "open"},
    })

    prs = db.list_open_pull_requests(company_a)
    assert [p["pr_number"] for p in prs] == [1]  # legacy PR #2 excluded


# ─────────────────────── /github/accessible-repos ────────────────────────
#
# New endpoint that lists repos the Sprntly App can read, aggregated
# across all installations owned by the caller's company. Uses the
# App installation TOKEN (not the user OAuth token) so it works for any
# company member, not just the original installer. This is what the
# Generate Prototype modal needs — the old /github/repos endpoint went
# via the OAuth user-token + `read:user user:email` scopes, which
# returned empty for users without public repos under that login.


def test_accessible_repos_empty_when_no_installation(github_env, monkeypatch):
    """Company has no GitHub install at all → empty list, not 500."""
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/connectors/github/accessible-repos")
    assert r.status_code == 200, r.text
    assert r.json() == {"repositories": []}


def test_accessible_repos_aggregates_across_company_installs(
    github_env, monkeypatch
):
    """One install, two repos visible to the App → both surface."""
    from unittest.mock import MagicMock, patch

    ctx = company_client(monkeypatch)
    _seed_install(installation_id=11, company_id=ctx.company_id, login="acme")

    fake = MagicMock(ok=True, status_code=200)
    fake.json.return_value = {
        "total_count": 2,
        "repositories": [
            {
                "full_name": "acme/web",
                "name": "web",
                "private": False,
                "html_url": "https://github.com/acme/web",
                "default_branch": "main",
                "description": "the web app",
            },
            {
                "full_name": "acme/api",
                "name": "api",
                "private": True,
                "html_url": "https://github.com/acme/api",
                "default_branch": "main",
                "description": None,
            },
        ],
    }
    # Patch BOTH the App-token mint (so we don't sign a real JWT in test) and
    # the HTTP call to GitHub.
    with patch(
        "app.connectors.github_app.get_installation_token", return_value="ghs_x"
    ), patch(
        "app.connectors.github_app.requests.get", return_value=fake
    ):
        r = ctx.client.get("/v1/connectors/github/accessible-repos")
    assert r.status_code == 200, r.text
    full_names = sorted(x["full_name"] for x in r.json()["repositories"])
    assert full_names == ["acme/api", "acme/web"]


def test_accessible_repos_cross_tenant_returns_empty(github_env, monkeypatch):
    """Caller's company has no install. Another company's install exists
    and has repos. The caller must see NOTHING — not 'install exists for
    someone else', not a generic error, just an empty list."""
    ctx = company_client(monkeypatch)
    other_company = seed_company(user_id="other-" + uuid.uuid4().hex[:6], slug="globex")
    _seed_install(installation_id=99, company_id=other_company, login="globex")

    # No mock for the GitHub call — if the route tried to fetch globex's repos
    # it would hit the real network and fail. The route must not try.
    r = ctx.client.get("/v1/connectors/github/accessible-repos")
    assert r.status_code == 200, r.text
    assert r.json() == {"repositories": []}


def test_accessible_repos_visible_to_invited_member(github_env, monkeypatch):
    """Company connector model: any member of the company that owns the
    install sees the install's repos — not just the original installer."""
    from unittest.mock import MagicMock, patch

    # Set up the first member (this is the installer).
    ctx_a = company_client(monkeypatch)
    _seed_install(installation_id=33, company_id=ctx_a.company_id, login="acme")

    # Add a SECOND user to the same company (the invited teammate).
    from app.db.client import require_client

    teammate_id = "teammate-" + uuid.uuid4().hex[:6]
    require_client().table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": ctx_a.company_id,
            "user_id": teammate_id,
            "role": "member",
        }
    ).execute()
    teammate_headers = supabase_bearer(teammate_id)

    fake = MagicMock(ok=True, status_code=200)
    fake.json.return_value = {
        "total_count": 1,
        "repositories": [
            {
                "full_name": "acme/web",
                "name": "web",
                "private": False,
                "html_url": "https://github.com/acme/web",
                "default_branch": "main",
                "description": "",
            },
        ],
    }
    with patch(
        "app.connectors.github_app.get_installation_token", return_value="ghs_x"
    ), patch(
        "app.connectors.github_app.requests.get", return_value=fake
    ):
        r = ctx_a.client.get(
            "/v1/connectors/github/accessible-repos", headers=teammate_headers
        )
    assert r.status_code == 200, r.text
    assert [x["full_name"] for x in r.json()["repositories"]] == ["acme/web"]


def test_accessible_repos_requires_auth(github_env, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main_mod

    setup_supabase_auth(monkeypatch)
    company_client(monkeypatch)  # ensures reload happened
    anon = TestClient(main_mod.app)
    r = anon.get("/v1/connectors/github/accessible-repos")
    assert r.status_code == 401
