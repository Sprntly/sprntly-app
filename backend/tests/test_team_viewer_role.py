"""Tests for the new `viewer` role (Settings-style slice, SC1).

Mockup: `sprntly-pages/15-settings.html` § Team & roles introduces a 4th
role — Viewer (read-only, can comment but not edit). This file pins:

  - DB CHECK constraint extension: 'viewer' is a valid value on both
    company_members.role and workspace_invites.role.
  - Pydantic acceptance: POST /v1/team/invites + PATCH /v1/team/members
    accept role='viewer'.
  - Tenancy unchanged: viewer is a "non-admin" — like members, viewers
    cannot mutate the team (writes 403). Read access matches members.
"""
from __future__ import annotations

import uuid

import app.auth  # noqa: F401

from tests._company_helpers import company_client


def _set_role(*, company_id: str, user_id: str, role: str) -> None:
    from app.db.client import require_client

    require_client().table("company_members").update({"role": role}).eq(
        "company_id", company_id
    ).eq("user_id", user_id).execute()
    from app.db.authcache import invalidate_user

    invalidate_user(user_id)


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


def _list_invites(company_id: str) -> list[dict]:
    from app.db.client import require_client

    return (
        require_client()
        .table("workspace_invites")
        .select("id, email, role")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )


# ─────────────────────── Invite as viewer ───────────────────────


def test_invite_accepts_viewer_role(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/team/invites", json={"email": "v@co.com", "role": "viewer"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "viewer"
    rows = _list_invites(ctx.company_id)
    assert any(row["role"] == "viewer" for row in rows)


def test_invite_still_rejects_owner_role(isolated_settings, monkeypatch):
    """Sanity: adding 'viewer' to the enum must not also unlock 'owner'
    in the invite enum (owner is reserved for the creator path)."""
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/team/invites", json={"email": "x@co.com", "role": "owner"}
    )
    assert r.status_code == 422


# ─────────────────────── Patch to/from viewer ───────────────────────


def test_patch_member_role_to_viewer(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _add_member(company_id=ctx.company_id, user_id="alice", role="member")
    r = ctx.client.patch(
        "/v1/team/members/alice", json={"role": "viewer"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "viewer"


def test_patch_member_role_from_viewer_to_member(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _add_member(company_id=ctx.company_id, user_id="vix", role="viewer")
    r = ctx.client.patch(
        "/v1/team/members/vix", json={"role": "member"}
    )
    assert r.status_code == 200
    assert r.json()["role"] == "member"


# ─────────────────────── Viewer cannot mutate ───────────────────────


def test_viewer_cannot_invite(isolated_settings, monkeypatch):
    """Viewers are non-admins. Same gate as members — writes 403."""
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="viewer")
    r = ctx.client.post(
        "/v1/team/invites", json={"email": "x@co.com", "role": "member"}
    )
    assert r.status_code == 403


def test_viewer_can_read_members(isolated_settings, monkeypatch):
    """Viewers can see the roster (same as members)."""
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="viewer")
    r = ctx.client.get("/v1/team/members")
    assert r.status_code == 200
    assert len(r.json()["members"]) >= 1
