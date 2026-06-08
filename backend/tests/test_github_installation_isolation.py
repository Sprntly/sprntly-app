"""GitHub installation tenant-isolation tests.

Why this file exists: `github_installations` is a flat global table (PK =
installation_id only, no company_id column). Prior to this slice every
endpoint that accepted an installation_id was either:
  - listing the entire pool to any signed-in user (info disclosure), or
  - dispatching App-token calls to GitHub for an attacker-supplied id
    (lateral access).

Until the proper schema fix lands (adding company_id to
github_installations and binding it at the Setup-URL callback), every
endpoint that takes installation_id MUST go through
require_installation_for_company, which enforces the conservative
binding: the install's account_login must equal the requesting
company's stored GitHub connection account_label.

These tests assert that contract for every entry point.
"""
from __future__ import annotations

import importlib
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests._company_helpers import (
    company_client,
    seed_company,
    seed_connection,
    supabase_bearer,
)


@pytest.fixture
def github_env(monkeypatch, isolated_settings):
    """Minimal env for the GitHub routes to import cleanly (mirrors the
    `github_env` fixture in test_routes_connectors_figma_github.py)."""
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "Iv1.client")
    monkeypatch.setenv("GITHUB_APP_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GITHUB_APP_ID", "999")
    monkeypatch.setenv("GITHUB_APP_SLUG", "sprntly-test")
    monkeypatch.setenv("GITHUB_APP_WEBHOOK_SECRET", "wh-secret")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k" * 44)
    importlib.reload(sys.modules["app.config"])


def _seed_install(account_login: str, installation_id: int) -> None:
    from app.db.client import require_client

    require_client().table("github_installations").upsert(
        {
            "installation_id": installation_id,
            "account_id": installation_id * 10,
            "account_login": account_login,
            "account_type": "User",
            "repository_selection": "selected",
        }
    ).execute()


def _seed_pr(installation_id: int, repo: str, number: int) -> None:
    from app.db.client import require_client

    require_client().table("github_pull_requests").upsert(
        {
            "installation_id": installation_id,
            "repo_full_name": repo,
            "pr_number": number,
            "title": f"PR {number}",
            "state": "open",
            "is_draft": False,
        },
        on_conflict="repo_full_name,pr_number",
    ).execute()


def _two_tenants(monkeypatch) -> tuple[SimpleNamespace, SimpleNamespace, SimpleNamespace]:
    """Tenant A is the default seeded company; tenant B is a second
    user+company added on top. Returns (client, A, B) where the shared
    TestClient takes headers per call."""
    ctx_a = company_client(monkeypatch)
    user_b = "user-b-" + uuid.uuid4().hex[:8]
    company_b = seed_company(user_id=user_b, slug="acme-b-" + uuid.uuid4().hex[:6])
    headers_b = supabase_bearer(user_b)
    b_ctx = SimpleNamespace(
        company_id=company_b, user_id=user_b, headers=headers_b
    )
    a_ctx = SimpleNamespace(
        company_id=ctx_a.company_id,
        user_id=ctx_a.user_id,
        headers=ctx_a.headers,
    )
    return ctx_a.client, a_ctx, b_ctx


# ─────────────────────── /github/installations LIST ───────────────────────


def test_github_installations_list_filters_to_caller_company(github_env, monkeypatch):
    """The original bug: A connected @gonzalj3, B connected @colleague,
    and B's call returned both installs (leaking @gonzalj3 to B). After
    the fix B sees only their own install; A sees only theirs."""
    client, A, B = _two_tenants(monkeypatch)
    seed_connection(
        company_id=A.company_id,
        provider="github",
        token_blob={"access_token": "tok-a"},
        label="@gonzalj3",
    )
    seed_connection(
        company_id=B.company_id,
        provider="github",
        token_blob={"access_token": "tok-b"},
        label="@colleague",
    )
    _seed_install("gonzalj3", 11111)
    _seed_install("colleague", 22222)

    rb = client.get("/v1/connectors/github/installations", headers=B.headers)
    assert rb.status_code == 200, rb.text
    ids_b = sorted(i["installation_id"] for i in rb.json()["installations"])
    assert ids_b == [22222]

    ra = client.get("/v1/connectors/github/installations", headers=A.headers)
    assert ra.status_code == 200, ra.text
    ids_a = sorted(i["installation_id"] for i in ra.json()["installations"])
    assert ids_a == [11111]


def test_github_installations_list_empty_when_company_has_no_connection(
    github_env, monkeypatch
):
    """If the requesting company hasn't connected GitHub at all, the
    list must be empty even if installs exist for some login. (Pre-fix
    it returned everything.)"""
    ctx = company_client(monkeypatch)
    _seed_install("gonzalj3", 11111)
    r = ctx.client.get("/v1/connectors/github/installations")
    assert r.status_code == 200
    assert r.json()["installations"] == []


# ─────────────────────── /github/installations/{id}/repositories ──────────


def test_github_install_repos_get_403_for_other_companies_install(
    github_env, monkeypatch
):
    client, A, B = _two_tenants(monkeypatch)
    seed_connection(
        company_id=A.company_id,
        provider="github",
        token_blob={"access_token": "tok-a"},
        label="@gonzalj3",
    )
    seed_connection(
        company_id=B.company_id,
        provider="github",
        token_blob={"access_token": "tok-b"},
        label="@colleague",
    )
    _seed_install("gonzalj3", 11111)

    r = client.get(
        "/v1/connectors/github/installations/11111/repositories",
        headers=B.headers,
    )
    assert r.status_code == 403


def test_github_install_repos_put_403_for_other_companies_install(
    github_env, monkeypatch
):
    client, A, B = _two_tenants(monkeypatch)
    seed_connection(
        company_id=A.company_id,
        provider="github",
        token_blob={"access_token": "tok-a"},
        label="@gonzalj3",
    )
    seed_connection(
        company_id=B.company_id,
        provider="github",
        token_blob={"access_token": "tok-b"},
        label="@colleague",
    )
    _seed_install("gonzalj3", 11111)

    r = client.put(
        "/v1/connectors/github/installations/11111/repositories/77",
        headers=B.headers,
    )
    assert r.status_code == 403


def test_github_install_repos_delete_403_for_other_companies_install(
    github_env, monkeypatch
):
    client, A, B = _two_tenants(monkeypatch)
    seed_connection(
        company_id=A.company_id,
        provider="github",
        token_blob={"access_token": "tok-a"},
        label="@gonzalj3",
    )
    seed_connection(
        company_id=B.company_id,
        provider="github",
        token_blob={"access_token": "tok-b"},
        label="@colleague",
    )
    _seed_install("gonzalj3", 11111)

    r = client.delete(
        "/v1/connectors/github/installations/11111/repositories/77",
        headers=B.headers,
    )
    assert r.status_code == 403


# ─────────────────────── org-install invisibility ────────────────────────


def test_org_install_is_invisible_to_oauth_user_failclose(github_env, monkeypatch):
    """Documented fail-closed regression: until github_installations gains
    a real company_id column, an install on an org account (e.g. 'MyCorp')
    cannot be matched to any individual user's OAuth login. The conservative
    guard treats it as not-owned. This test pins that trade-off so the
    proper schema fix is the only way to lift it."""
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="github",
        token_blob={"access_token": "tok"},
        label="@alice",
    )
    # Org install. account_login is the org name, not the user.
    _seed_install("MyCorp", 33333)

    # LIST: org install is invisible.
    rl = ctx.client.get("/v1/connectors/github/installations")
    assert rl.status_code == 200
    assert rl.json()["installations"] == []

    # ACTION: trying to use the org install id is 403, NOT 404. Same
    # error shape regardless of cause — doesn't tell the caller whether
    # the install exists.
    rr = ctx.client.get(
        "/v1/connectors/github/installations/33333/repositories"
    )
    assert rr.status_code == 403


# ─────────────────────── /github/pull-requests ────────────────────────────


def test_github_pull_requests_rejects_unauth(github_env, monkeypatch):
    """Pre-fix: require_session (any logged-in user). Post-fix:
    require_company. No auth → 401/403."""
    ctx = company_client(monkeypatch)
    r = ctx.client.get(
        "/v1/connectors/github/pull-requests",
        headers={"Authorization": ""},
    )
    assert r.status_code in (401, 403)


def test_github_pull_requests_403_for_other_companies_install(
    github_env, monkeypatch
):
    client, A, B = _two_tenants(monkeypatch)
    seed_connection(
        company_id=A.company_id,
        provider="github",
        token_blob={"access_token": "tok-a"},
        label="@gonzalj3",
    )
    seed_connection(
        company_id=B.company_id,
        provider="github",
        token_blob={"access_token": "tok-b"},
        label="@colleague",
    )
    _seed_install("gonzalj3", 11111)
    _seed_pr(11111, "gonzalj3/private-repo", 1)

    r = client.get(
        "/v1/connectors/github/pull-requests?installation_id=11111",
        headers=B.headers,
    )
    assert r.status_code == 403


def test_github_pull_requests_no_install_id_returns_only_owned_prs(
    github_env, monkeypatch
):
    """When the caller doesn't pass installation_id, the response must
    still be filtered to PRs whose install is owned by the caller. Pre-fix
    the route returned every PR across every tenant."""
    client, A, B = _two_tenants(monkeypatch)
    seed_connection(
        company_id=A.company_id,
        provider="github",
        token_blob={"access_token": "tok-a"},
        label="@gonzalj3",
    )
    seed_connection(
        company_id=B.company_id,
        provider="github",
        token_blob={"access_token": "tok-b"},
        label="@colleague",
    )
    _seed_install("gonzalj3", 11111)
    _seed_install("colleague", 22222)
    _seed_pr(11111, "gonzalj3/private-repo", 1)
    _seed_pr(22222, "colleague/their-repo", 9)

    r = client.get(
        "/v1/connectors/github/pull-requests",
        headers=B.headers,
    )
    assert r.status_code == 200, r.text
    nums = sorted(p["pr_number"] for p in r.json()["pull_requests"])
    assert nums == [9]


def test_github_pull_requests_no_connection_returns_empty_not_global(
    github_env, monkeypatch
):
    """If the caller's company has no GitHub connection, the PR list
    must be empty — NOT a global PR dump."""
    ctx = company_client(monkeypatch)
    # Seed a PR for SOME install — there's no connection for this company,
    # so it must not appear in the response.
    _seed_install("someone-else", 99999)
    _seed_pr(99999, "someone-else/repo", 7)

    r = ctx.client.get("/v1/connectors/github/pull-requests")
    assert r.status_code == 200
    assert r.json()["pull_requests"] == []


# ─────────────────────── /github/sync-to-corpus ───────────────────────────


def test_github_sync_to_corpus_rejects_unauth(github_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/github/sync-to-corpus",
        json={"dataset": "acme", "installation_id": 11111},
        headers={"Authorization": ""},
    )
    assert r.status_code in (401, 403)


def test_github_sync_to_corpus_403_for_other_companies_install(
    github_env, monkeypatch
):
    client, A, B = _two_tenants(monkeypatch)
    seed_connection(
        company_id=A.company_id,
        provider="github",
        token_blob={"access_token": "tok-a"},
        label="@gonzalj3",
    )
    seed_connection(
        company_id=B.company_id,
        provider="github",
        token_blob={"access_token": "tok-b"},
        label="@colleague",
    )
    _seed_install("gonzalj3", 11111)

    r = client.post(
        "/v1/connectors/github/sync-to-corpus",
        json={"dataset": "acme-b", "installation_id": 11111},
        headers=B.headers,
    )
    assert r.status_code == 403


def test_github_sync_to_corpus_no_install_id_syncs_only_owned(
    github_env, monkeypatch
):
    """No installation_id means 'sync all owned installs' — never global.
    With no GitHub connection at all the corpus write is a no-op (empty
    PR set), not a global dump of every tenant's PRs."""
    ctx = company_client(monkeypatch)
    # Seed a PR owned by a different login. No connection for this company.
    _seed_install("someone-else", 99999)
    _seed_pr(99999, "someone-else/repo", 42)

    r = ctx.client.post(
        "/v1/connectors/github/sync-to-corpus",
        json={"dataset": "acme"},
    )
    assert r.status_code == 200, r.text
    # Confirm we did not pull the foreign PR into our corpus.
    body = r.json()
    # Route returns a status payload; the markdown body lives on disk. The
    # contract that matters here is that the route didn't 500 or expose
    # foreign data — sync_count should be 0.
    assert body.get("pr_count", 0) == 0


# ─────────────────────── /sync-status removed ────────────────────────────


def test_sync_status_returns_404(github_env, monkeypatch):
    """Regression guard: /v1/connectors/sync-status was a global,
    cross-tenant leak (returned every tenant's connections + dataset
    stats) AND was broken-on-call (db.list_connections() requires
    company_id). It had no consumer in the frontend. Removed in the
    security hotfix; this test prevents accidental reintroduction."""
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/connectors/sync-status")
    assert r.status_code == 404


# ─────────────────────── /v1/agent/chat-with-tools ────────────────────────


def test_agent_chat_with_tools_403_for_other_companies_install(
    github_env, monkeypatch
):
    """The most dangerous path: agent tools dispatch against an App
    installation token. A stolen installation_id from another tenant
    would let the LLM read that tenant's repos."""
    client, A, B = _two_tenants(monkeypatch)
    seed_connection(
        company_id=A.company_id,
        provider="github",
        token_blob={"access_token": "tok-a"},
        label="@gonzalj3",
    )
    seed_connection(
        company_id=B.company_id,
        provider="github",
        token_blob={"access_token": "tok-b"},
        label="@colleague",
    )
    _seed_install("gonzalj3", 11111)

    # Patch the Anthropic client so the test never reaches it — the
    # guard must reject before tool dispatch.
    with patch("app.routes.agent_chat.get_llm_client") as mock_client:
        r = client.post(
            "/v1/agent/chat-with-tools",
            json={"message": "show me their repo", "installation_id": 11111},
            headers=B.headers,
        )

    assert r.status_code == 403
    # Guard fires BEFORE the LLM is consulted — proves the gate is at the
    # edge, not buried inside a tool.
    mock_client.assert_not_called()
