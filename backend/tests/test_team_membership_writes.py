"""Tests for C3 of the Settings → Team & roles slice.

Endpoints under test:
  PATCH  /v1/team/members/{user_id}   — change a member's role
  DELETE /v1/team/members/{user_id}   — remove a member
  POST   /v1/invites/accept           — invitee auto-accepts a pending invite

Permission model (per CEO 2026-06-06, decision 1-A):
  - Members CANNOT mutate the team (403).
  - Admins and owners can edit/remove members.
  - The "last owner" cannot be removed or demoted (409) — at least one
    owner must remain so the company stays administratable.
  - An admin can promote a member to admin or owner, demote an admin to
    member, but cannot demote/remove an existing owner. Owners can do
    everything.

Accept-invite contract:
  - Caller's email must match the pending invite (case-insensitive).
  - Caller must NOT already belong to a company (one-user-one-company
    invariant — accept would otherwise violate the unique(user_id) index).
  - On success: company_members row created with the invite's role;
    invite row deleted; response carries the new {company_id, role}.
  - Idempotency: accepting a nonexistent invite → 404. Accepting a
    matching invite when already a member of the *same* company → 200
    (no-op, invite deleted).
"""
from __future__ import annotations

import uuid

import app.auth  # noqa: F401 — load app.config + app.auth into sys.modules

from tests._company_helpers import (
    company_client,
    seed_company,
    setup_supabase_auth,
    supabase_bearer,
)


def _add_member(*, company_id: str, user_id: str, role: str = "member") -> None:
    from app.db.client import require_client

    require_client().table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": company_id,
            "user_id": user_id,
            "role": role,
        }
    ).execute()


def _set_role(*, company_id: str, user_id: str, role: str) -> None:
    from app.db.client import require_client

    require_client().table("company_members").update({"role": role}).eq(
        "company_id", company_id
    ).eq("user_id", user_id).execute()
    from app.db.authcache import invalidate_user

    invalidate_user(user_id)


def _seed_invite(*, company_id: str, email: str, role: str = "member") -> str:
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


def _list_members(company_id: str) -> list[dict]:
    from app.db.client import require_client

    return (
        require_client()
        .table("company_members")
        .select("user_id, role")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )


def _list_invites(company_id: str) -> list[dict]:
    from app.db.client import require_client

    return (
        require_client()
        .table("workspace_invites")
        .select("id, email")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )


# ─────────────────────── PATCH /v1/team/members/{user_id} ───────────────────────


def test_patch_member_role_updates_row(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _add_member(company_id=ctx.company_id, user_id="alice", role="member")

    r = ctx.client.patch(
        "/v1/team/members/alice", json={"role": "admin"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin"
    roles = {m["user_id"]: m["role"] for m in _list_members(ctx.company_id)}
    assert roles["alice"] == "admin"


def test_patch_member_invalid_role(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _add_member(company_id=ctx.company_id, user_id="alice", role="member")
    r = ctx.client.patch(
        "/v1/team/members/alice", json={"role": "superadmin"}
    )
    assert r.status_code == 422


def test_patch_member_404_for_non_member(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.patch(
        "/v1/team/members/ghost", json={"role": "admin"}
    )
    assert r.status_code == 404


def test_patch_member_404_for_other_company(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    other_cid = seed_company(user_id="other-owner", slug="other-co")
    _add_member(company_id=other_cid, user_id="other-mem", role="member")
    r = ctx.client.patch(
        "/v1/team/members/other-mem", json={"role": "admin"}
    )
    assert r.status_code == 404


def test_patch_member_403_when_caller_is_member(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _add_member(company_id=ctx.company_id, user_id="alice", role="member")
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="member")
    r = ctx.client.patch(
        "/v1/team/members/alice", json={"role": "admin"}
    )
    assert r.status_code == 403


def test_patch_last_owner_to_non_owner_blocked(isolated_settings, monkeypatch):
    """Demoting the only owner would leave the company with zero owners."""
    ctx = company_client(monkeypatch)
    r = ctx.client.patch(
        f"/v1/team/members/{ctx.user_id}", json={"role": "admin"}
    )
    assert r.status_code == 409
    assert "owner" in r.json()["detail"].lower()


def test_patch_owner_demotion_ok_when_another_owner_exists(
    isolated_settings, monkeypatch
):
    ctx = company_client(monkeypatch)
    _add_member(company_id=ctx.company_id, user_id="co-owner", role="owner")
    r = ctx.client.patch(
        f"/v1/team/members/{ctx.user_id}", json={"role": "admin"}
    )
    assert r.status_code == 200


# ─────────────────────── DELETE /v1/team/members/{user_id} ───────────────────────


def test_delete_member_removes_row(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _add_member(company_id=ctx.company_id, user_id="alice", role="member")
    r = ctx.client.delete("/v1/team/members/alice")
    assert r.status_code == 204
    user_ids = {m["user_id"] for m in _list_members(ctx.company_id)}
    assert "alice" not in user_ids


def test_delete_member_404_for_non_member(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.delete("/v1/team/members/ghost")
    assert r.status_code == 404


def test_delete_member_404_for_other_company(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    other_cid = seed_company(user_id="other-owner", slug="other-co")
    _add_member(company_id=other_cid, user_id="other-mem", role="member")
    r = ctx.client.delete("/v1/team/members/other-mem")
    assert r.status_code == 404


def test_delete_member_403_when_caller_is_member(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _add_member(company_id=ctx.company_id, user_id="alice", role="member")
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="member")
    r = ctx.client.delete("/v1/team/members/alice")
    assert r.status_code == 403


def test_delete_last_owner_blocked(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.delete(f"/v1/team/members/{ctx.user_id}")
    assert r.status_code == 409
    assert "owner" in r.json()["detail"].lower()


def test_delete_owner_ok_when_another_owner_exists(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _add_member(company_id=ctx.company_id, user_id="co-owner", role="owner")
    r = ctx.client.delete(f"/v1/team/members/{ctx.user_id}")
    assert r.status_code == 204


# ─────────────────────── POST /v1/invites/accept ───────────────────────


def _orphan_client(monkeypatch, *, email: str):
    """Return (client, user_id) for a signed-in user with NO company membership.
    Stamps the email into profiles + the JWT so accept can match."""
    setup_supabase_auth(monkeypatch)
    import importlib
    import sys

    importlib.reload(sys.modules["app.main"])
    from fastapi.testclient import TestClient
    import app.main as main_mod
    from app.db.client import require_client

    user_id = "orphan-" + uuid.uuid4().hex[:8]
    require_client().table("profiles").insert(
        {"id": user_id, "email": email}
    ).execute()

    client = TestClient(main_mod.app, headers=supabase_bearer(user_id))
    return client, user_id


def test_accept_creates_membership_and_deletes_invite(
    isolated_settings, monkeypatch
):
    # Seed a company + invite.
    company_id = seed_company(user_id="company-owner", slug="acme")
    _seed_invite(company_id=company_id, email="invitee@co.com", role="admin")

    # Invitee signs in (no membership yet).
    client, invitee_id = _orphan_client(monkeypatch, email="invitee@co.com")

    r = client.post("/v1/invites/accept")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["company_id"] == company_id
    assert body["role"] == "admin"

    members = _list_members(company_id)
    user_ids = {m["user_id"] for m in members}
    assert invitee_id in user_ids
    assert _list_invites(company_id) == []


def test_accept_matches_email_case_insensitive(isolated_settings, monkeypatch):
    company_id = seed_company(user_id="owner", slug="acme")
    _seed_invite(company_id=company_id, email="lower@co.com", role="member")
    client, _ = _orphan_client(monkeypatch, email="LOWER@CO.com")

    r = client.post("/v1/invites/accept")
    assert r.status_code == 200


def test_accept_404_when_no_pending_invite(isolated_settings, monkeypatch):
    client, _ = _orphan_client(monkeypatch, email="nobody@x.com")
    r = client.post("/v1/invites/accept")
    assert r.status_code == 404


def test_accept_409_when_already_in_another_company(
    isolated_settings, monkeypatch
):
    """One-user-one-company invariant: invitee can't accept while still
    a member elsewhere."""
    # Set up the invitee in company A.
    ctx = company_client(monkeypatch)
    # `company_client` seeds companies + company_members but not profiles.
    # Insert the caller's profile so the route can resolve their email.
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": ctx.user_id, "email": "dual@co.com"}
    ).execute()
    # Seed a pending invite for the same email from company B.
    company_b = seed_company(user_id="other-owner", slug="other-co")
    _seed_invite(company_id=company_b, email="dual@co.com", role="member")

    r = ctx.client.post("/v1/invites/accept")
    assert r.status_code == 409
    assert (
        "company" in r.json()["detail"].lower()
        or "member" in r.json()["detail"].lower()
    )


def test_accept_requires_auth(isolated_settings, monkeypatch):
    """No bearer → 401 (not 404)."""
    company_client(monkeypatch)
    from fastapi.testclient import TestClient
    import app.main as main_mod

    unauth = TestClient(main_mod.app)
    r = unauth.post("/v1/invites/accept")
    assert r.status_code == 401
