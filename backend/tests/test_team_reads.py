"""Tests for the Settings → Team read endpoints (C1 of the team-roles slice).

Endpoints under test:
  GET /v1/team/members  — current company's company_members rows
  GET /v1/team/invites  — current company's pending workspace_invites rows

Tenancy: both endpoints resolve `company_id` from `require_company` (the
one-user-one-company JWT path). No client-supplied company_id; routes
must NEVER leak rows from another company.

Auth contract:
  - No bearer header → 401 from require_session.
  - Bearer + no membership → 403 from require_company.
  - Bearer + membership → 200 with rows scoped to the user's company only.

The role-gate on reads is "any member can see the team" (typical SaaS UX).
The role-gate on writes lands in C2/C3.
"""
from __future__ import annotations

import uuid

# Importing anything under `app.` ensures `app.config` and `app.auth` land in
# sys.modules so `_company_helpers.setup_supabase_auth` can reload them.
import app.auth  # noqa: F401

from tests._company_helpers import (
    company_client,
    seed_company,
    setup_supabase_auth,
    supabase_bearer,
)


def _seed_invite(*, company_id: str, email: str, role: str = "member") -> str:
    """Insert a workspace_invites row directly. Returns the invite id."""
    from app.db.client import require_client

    iid = uuid.uuid4().hex
    require_client().table("workspace_invites").insert(
        {
            "id": iid,
            "company_id": company_id,
            "email": email,
            "role": role,
        }
    ).execute()
    return iid


def _add_member(*, company_id: str, user_id: str, role: str = "member") -> None:
    """Insert a company_members row directly (bypasses one-user-one-company
    invariant — only safe in tests that exercise list-membership behavior)."""
    from app.db.client import require_client

    require_client().table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": company_id,
            "user_id": user_id,
            "role": role,
        }
    ).execute()


# NOTE: every test takes `isolated_settings` as a fixture parameter — that wires
# in the in-memory fake Supabase + reloads app modules. `company_client` is then
# called as a helper inside the test body (not a fixture) to seed the company +
# return a Bearer-authed TestClient. Pattern copied from
# tests/test_routes_connectors_clickup.py.


# ─────────────────────── GET /v1/team/members ───────────────────────


def test_members_requires_auth(isolated_settings, monkeypatch):
    """No bearer → 401."""
    company_client(monkeypatch)  # sets SUPABASE_JWT_SECRET + reloads app.main
    from fastapi.testclient import TestClient
    import app.main as main_mod

    unauth = TestClient(main_mod.app)
    r = unauth.get("/v1/team/members")
    assert r.status_code == 401


def test_members_requires_company(isolated_settings, monkeypatch):
    """Bearer for a user with no membership → 403."""
    setup_supabase_auth(monkeypatch)
    import importlib
    import sys

    importlib.reload(sys.modules["app.main"])
    from fastapi.testclient import TestClient
    import app.main as main_mod

    orphan_user = "orphan-" + uuid.uuid4().hex[:8]
    client = TestClient(main_mod.app, headers=supabase_bearer(orphan_user))
    r = client.get("/v1/team/members")
    assert r.status_code == 403


def test_members_returns_seeded_owner(isolated_settings, monkeypatch):
    """The seeded owner appears in the response."""
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/team/members")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "members" in body
    rows = body["members"]
    assert len(rows) == 1
    assert rows[0]["user_id"] == ctx.user_id
    assert rows[0]["role"] == "owner"


def test_members_returns_multiple_roles(isolated_settings, monkeypatch):
    """Owner + admin + member all surface, each with their role string."""
    ctx = company_client(monkeypatch)
    _add_member(company_id=ctx.company_id, user_id="alice", role="admin")
    _add_member(company_id=ctx.company_id, user_id="bob", role="member")

    r = ctx.client.get("/v1/team/members")
    assert r.status_code == 200
    rows = r.json()["members"]
    by_user = {row["user_id"]: row["role"] for row in rows}
    assert by_user[ctx.user_id] == "owner"
    assert by_user["alice"] == "admin"
    assert by_user["bob"] == "member"


def test_members_does_not_leak_other_company(isolated_settings, monkeypatch):
    """A second company's members never appear in our response."""
    ctx = company_client(monkeypatch)
    other_company_id = seed_company(user_id="someone-else", slug="other-co")
    _add_member(company_id=other_company_id, user_id="other-member", role="member")

    r = ctx.client.get("/v1/team/members")
    assert r.status_code == 200
    user_ids = {row["user_id"] for row in r.json()["members"]}
    assert "other-member" not in user_ids
    assert user_ids == {ctx.user_id}


# ─────────────────────── GET /v1/team/invites ───────────────────────


def test_invites_requires_auth(isolated_settings, monkeypatch):
    company_client(monkeypatch)
    from fastapi.testclient import TestClient
    import app.main as main_mod

    unauth = TestClient(main_mod.app)
    r = unauth.get("/v1/team/invites")
    assert r.status_code == 401


def test_invites_empty_when_none(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/team/invites")
    assert r.status_code == 200
    assert r.json() == {"invites": []}


def test_invites_returns_pending_rows(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_invite(company_id=ctx.company_id, email="a@co.com", role="member")
    _seed_invite(company_id=ctx.company_id, email="b@co.com", role="admin")

    r = ctx.client.get("/v1/team/invites")
    assert r.status_code == 200
    rows = r.json()["invites"]
    emails = {row["email"]: row["role"] for row in rows}
    assert emails == {"a@co.com": "member", "b@co.com": "admin"}
    for row in rows:
        assert "id" in row
        assert "created_at" in row


def test_invites_does_not_leak_other_company(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    other_company_id = seed_company(user_id="someone-else", slug="other-co")
    _seed_invite(company_id=other_company_id, email="other@x.com")

    r = ctx.client.get("/v1/team/invites")
    assert r.status_code == 200
    emails = {row["email"] for row in r.json()["invites"]}
    assert "other@x.com" not in emails
