"""Tests for the Settings → Team write endpoints (C2 of the team-roles slice).

Endpoints under test:
  POST   /v1/team/invites               — create a workspace_invites row
  DELETE /v1/team/invites/{invite_id}   — revoke a pending invite
  POST   /v1/team/invites/{invite_id}/resend
                                        — bump created_at (placeholder for
                                          real email re-send once email infra
                                          exists)

Permission model (per CEO 2026-06-06, decision 1-A "spec it later"):
  - Members can NOT mutate the team (403). Reads are still open to members.
  - Admin and owner can do all three operations.

Validation:
  - 422 on malformed email.
  - 409 if the email already maps to a current company_member of this company.
  - 409 if a pending invite for that email already exists (unique constraint).
  - 422 on invalid role (only `admin` and `member` accepted — `owner` is
    reserved for the creator path and DB CHECK constraint rejects it anyway).

Cross-tenant: revoking / resending an invite owned by another company
returns 404 (NOT 403 — we don't tell the caller the row exists).
"""
from __future__ import annotations

import uuid

import app.auth  # noqa: F401 — ensure app.config/app.auth in sys.modules

from tests._company_helpers import company_client, seed_company


# ─────────────────────── helpers ───────────────────────


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


def _seed_profile(*, user_id: str, email: str) -> None:
    """Seed a profiles row so the 'already a member' duplicate-email check
    can match by joining `profiles` (the email-of-record live in profiles, not
    in company_members)."""
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": user_id, "email": email}
    ).execute()


def _set_role(*, company_id: str, user_id: str, role: str) -> None:
    """Promote/demote the seeded owner to a different role for permission tests."""
    from app.db.client import require_client

    require_client().table("company_members").update(
        {"role": role}
    ).eq("company_id", company_id).eq("user_id", user_id).execute()
    # The app now caches memberships/roles; the real role-change route
    # invalidates on write, so this direct-DB simulation of it must too.
    from app.db.authcache import invalidate_user

    invalidate_user(user_id)


def _list_invites(company_id: str) -> list[dict]:
    from app.db.client import require_client

    return (
        require_client()
        .table("workspace_invites")
        .select("id, email, role, created_at")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )


# ─────────────────────── POST /v1/team/invites ───────────────────────


def test_invite_creates_pending_row(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/team/invites", json={"email": "new@co.com", "role": "member"}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "new@co.com"
    assert body["role"] == "member"
    assert "id" in body and body["id"]
    rows = _list_invites(ctx.company_id)
    assert len(rows) == 1
    assert rows[0]["email"] == "new@co.com"


def test_invite_defaults_role_to_member(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/team/invites", json={"email": "x@co.com"})
    assert r.status_code == 201
    assert r.json()["role"] == "member"


def test_invite_rejects_malformed_email(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/team/invites", json={"email": "not-an-email", "role": "member"}
    )
    assert r.status_code == 422


def test_invite_rejects_invalid_role(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/team/invites", json={"email": "ok@co.com", "role": "owner"}
    )
    assert r.status_code == 422
    r = ctx.client.post(
        "/v1/team/invites", json={"email": "ok@co.com", "role": "superadmin"}
    )
    assert r.status_code == 422


def test_invite_409_if_email_already_a_member(isolated_settings, monkeypatch):
    """4-A: block at invite time when the email already belongs to a member of
    this company (one-user-one-company invariant)."""
    ctx = company_client(monkeypatch)
    _add_member(company_id=ctx.company_id, user_id="existing-user-1", role="member")
    _seed_profile(user_id="existing-user-1", email="alice@co.com")

    r = ctx.client.post(
        "/v1/team/invites", json={"email": "alice@co.com", "role": "member"}
    )
    assert r.status_code == 409
    assert "already" in r.json()["detail"].lower()


def test_invite_409_if_email_belongs_to_another_company(isolated_settings, monkeypatch):
    """Send-time guard (2026-07): an email already on a DIFFERENT company can
    never accept (one-user-one-company — its /v1/invites/accept would 409), so
    the invite is refused immediately with the reason instead of dangling."""
    ctx = company_client(monkeypatch)
    other_company = seed_company(user_id="outsider-1", slug="rival")
    _seed_profile(user_id="outsider-1", email="bob@rival.com")

    r = ctx.client.post(
        "/v1/team/invites", json={"email": "bob@rival.com", "role": "member"}
    )
    assert r.status_code == 409
    assert "another company" in r.json()["detail"].lower()
    # No pending row was created for the refused invite.
    assert _list_invites(ctx.company_id) == []
    # Sanity: the outsider really is on the other company, untouched.
    assert other_company != ctx.company_id


def test_invite_201_when_email_has_profile_but_no_company(isolated_settings, monkeypatch):
    """A profile without any company membership (e.g. signed up, never
    onboarded) is NOT 'another company' — the invite must go through."""
    ctx = company_client(monkeypatch)
    _seed_profile(user_id="floating-user-1", email="free@agent.com")

    r = ctx.client.post(
        "/v1/team/invites", json={"email": "free@agent.com", "role": "member"}
    )
    assert r.status_code == 201, r.text


def test_invite_409_if_duplicate_pending(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r1 = ctx.client.post(
        "/v1/team/invites", json={"email": "dup@co.com", "role": "member"}
    )
    assert r1.status_code == 201
    r2 = ctx.client.post(
        "/v1/team/invites", json={"email": "dup@co.com", "role": "admin"}
    )
    assert r2.status_code == 409


def test_invite_normalises_email_lowercase(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/team/invites", json={"email": "Mixed@CO.com", "role": "member"}
    )
    assert r.status_code == 201
    assert r.json()["email"] == "mixed@co.com"


def test_invite_403_when_member_role(isolated_settings, monkeypatch):
    """Members can't invite — only admin/owner."""
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="member")
    r = ctx.client.post(
        "/v1/team/invites", json={"email": "x@co.com", "role": "member"}
    )
    assert r.status_code == 403


def test_invite_201_when_admin_role(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="admin")
    r = ctx.client.post(
        "/v1/team/invites", json={"email": "x@co.com", "role": "member"}
    )
    assert r.status_code == 201


# ─────────────────────── DELETE /v1/team/invites/{id} ───────────────────────


def test_revoke_invite_deletes_row(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    created = ctx.client.post(
        "/v1/team/invites", json={"email": "del@co.com", "role": "member"}
    ).json()
    iid = created["id"]

    r = ctx.client.delete(f"/v1/team/invites/{iid}")
    assert r.status_code == 204
    assert _list_invites(ctx.company_id) == []


def test_revoke_404_when_unknown_id(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.delete(f"/v1/team/invites/{uuid.uuid4().hex}")
    assert r.status_code == 404


def test_revoke_404_cross_tenant(isolated_settings, monkeypatch):
    """Revoking another company's invite must return 404 (not 403 — don't
    leak that the row exists)."""
    ctx = company_client(monkeypatch)
    other_cid = seed_company(user_id="other-user", slug="other-co")
    # Seed an invite directly into other company.
    from app.db.client import require_client

    iid = uuid.uuid4().hex
    require_client().table("workspace_invites").insert(
        {
            "id": iid,
            "company_id": other_cid,
            "email": "stranger@x.com",
            "role": "member",
        }
    ).execute()

    r = ctx.client.delete(f"/v1/team/invites/{iid}")
    assert r.status_code == 404


def test_revoke_403_when_member_role(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    # Create the invite first as owner.
    iid = ctx.client.post(
        "/v1/team/invites", json={"email": "x@co.com", "role": "member"}
    ).json()["id"]
    # Demote self to member.
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="member")

    r = ctx.client.delete(f"/v1/team/invites/{iid}")
    assert r.status_code == 403


# ─────────────────────── POST /v1/team/invites/{id}/resend ───────────────────────


def test_resend_bumps_created_at(isolated_settings, monkeypatch):
    """Resend is a placeholder for real email re-send once email infra ships.
    Today it just updates the row's timestamp so the UI can show 'resent'."""
    import time

    ctx = company_client(monkeypatch)
    iid = ctx.client.post(
        "/v1/team/invites", json={"email": "r@co.com", "role": "member"}
    ).json()["id"]
    before = _list_invites(ctx.company_id)[0]["created_at"]
    time.sleep(1.1)  # ensure datetime('now') tick (seconds resolution)

    r = ctx.client.post(f"/v1/team/invites/{iid}/resend")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == iid
    assert body["created_at"] != before


def test_resend_404_when_unknown(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(f"/v1/team/invites/{uuid.uuid4().hex}/resend")
    assert r.status_code == 404


def test_resend_403_when_member_role(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    iid = ctx.client.post(
        "/v1/team/invites", json={"email": "r@co.com", "role": "member"}
    ).json()["id"]
    _set_role(company_id=ctx.company_id, user_id=ctx.user_id, role="member")
    r = ctx.client.post(f"/v1/team/invites/{iid}/resend")
    assert r.status_code == 403
