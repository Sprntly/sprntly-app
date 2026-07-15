"""Team management routes — Settings → Team & roles.

Spec: Sprntly_Onboarding_Flow_Spec_v1 § Settings → Team [Only for Admins].

Endpoints:
  GET    /v1/team/members                 (C1) — list company_members
  GET    /v1/team/invites                 (C1) — list pending workspace_invites
  POST   /v1/team/invites                 (C2) — create an invite
  DELETE /v1/team/invites/{invite_id}     (C2) — revoke a pending invite
  POST   /v1/team/invites/{invite_id}/resend
                                          (C2) — bump created_at (placeholder
                                                 for real email re-send)
  PATCH  /v1/team/members/{user_id}       (C3) — change a member's role
  DELETE /v1/team/members/{user_id}       (C3) — remove a member

  POST   /v1/invites/accept               (C3) — invitee auto-accepts their
                                                 pending invite. NOT on the
                                                 /v1/team prefix because the
                                                 caller has no membership yet.

Tenancy: `require_company` resolves the active company from the JWT.
Reads are open to any member (typical SaaS "see who's on the team"
UX); team writes are gated to admin/owner. The accept endpoint uses
`require_session` instead (the invitee is *not* yet a company member).
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.auth import CompanyContext, require_company, require_session
from app import team_email as team_email_mod
from app.team_email import send_invite_email
from app.db.companies import get_seat_limit
from app.db.team import (
    accept_invite_for_user,
    count_owners,
    create_invite,
    delete_invite,
    delete_member,
    get_invite,
    get_member,
    get_pending_invite_by_email,
    list_company_members,
    list_pending_invites,
    member_exists_for_email,
    touch_invite,
    update_member_role,
)


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _require_admin(company: CompanyContext) -> None:
    """Gate for mutating routes. Members cannot mutate the team."""
    if company.role not in ("owner", "admin"):
        raise HTTPException(403, "Team management is restricted to admins")


def _seats_in_use(company_id: str) -> int:
    """Occupied seats = current members + pending invites (each pending
    invite reserves a seat so accepts can't blow past the limit)."""
    return len(list_company_members(company_id)) + len(
        list_pending_invites(company_id)
    )


def _require_free_seat(company_id: str) -> None:
    """403 when the company is at its staff-configured seat limit
    (companies.seat_limit; NULL = unlimited)."""
    limit = get_seat_limit(company_id)
    if limit is None:
        return
    if _seats_in_use(company_id) >= limit:
        raise HTTPException(
            403,
            f"Your plan allows {limit} member{'s' if limit != 1 else ''} "
            "(including pending invites). Contact Sprntly to add seats.",
        )


class InviteIn(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    role: str = "member"

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        normalised = v.strip().lower()
        if not _EMAIL_RE.match(normalised):
            raise ValueError("invalid email")
        return normalised

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        # 'owner' is reserved for the creator and rejected by the DB CHECK
        # constraint on workspace_invites.role anyway. 'viewer' was added
        # in the team-settings-style slice (SC1, 2026-06-07) — read-only
        # access, can comment but not edit.
        if v not in ("admin", "member", "viewer"):
            raise ValueError("role must be 'admin', 'member', or 'viewer'")
        return v

router = APIRouter(prefix="/v1/team", tags=["team"])


@router.get("/members")
def get_team_members(company: CompanyContext = Depends(require_company)):
    rows = list_company_members(company.company_id)
    return {
        "members": [
            {
                "id": r.get("id"),
                "user_id": r.get("user_id"),
                "role": r.get("role"),
                "created_at": r.get("created_at"),
                # SC2 (2026-06-07): profile join enrichment for the
                # Settings → Team & roles page (mockup needs name +
                # email + avatar per row).
                "display_name": r.get("display_name"),
                "email": r.get("email"),
                "avatar_url": r.get("avatar_url"),
            }
            for r in rows
        ]
    }


@router.get("/invites")
def get_team_invites(company: CompanyContext = Depends(require_company)):
    rows = list_pending_invites(company.company_id)
    return {
        "invites": [
            {
                "id": r.get("id"),
                "email": r.get("email"),
                "role": r.get("role"),
                "invited_by": r.get("invited_by"),
                "created_at": r.get("created_at"),
            }
            for r in rows
        ]
    }


def _public_invite(
    row: dict,
    *,
    email_sent: bool | None = None,
    existing_user: bool = False,
) -> dict:
    out = {
        "id": row.get("id"),
        "email": row.get("email"),
        "role": row.get("role"),
        "invited_by": row.get("invited_by"),
        "created_at": row.get("created_at"),
    }
    if email_sent is not None:
        out["email_sent"] = email_sent
    # True when the invitee already had a Sprntly account, so we emailed a
    # magic-link sign-in instead of a new-user invite. Lets the UI say
    # "they already have an account — they'll join on next sign-in".
    if existing_user:
        out["existing_user"] = True
    return out


def _invite_result(row: dict, send_status: str) -> dict:
    """Map a send_invite_email() status onto the invite response. Both SENT
    and SENT_EXISTING mean an email went out (email_sent=True); only FAILED
    surfaces the "email didn't send" warning in the UI."""
    return _public_invite(
        row,
        email_sent=send_status != team_email_mod.FAILED,
        existing_user=send_status == team_email_mod.SENT_EXISTING,
    )


@router.post("/invites", status_code=status.HTTP_201_CREATED)
def post_team_invite(
    body: InviteIn,
    company: CompanyContext = Depends(require_company),
):
    _require_admin(company)

    # 4-A: block at invite time if the invitee is already on this team
    # (one-user-one-company invariant means they can't accept anyway, and
    # the error message is clearer here than at accept time).
    if member_exists_for_email(company_id=company.company_id, email=body.email):
        raise HTTPException(409, "That email is already a member of this team")

    # Duplicate pending invite → unique(company_id, email).
    if get_pending_invite_by_email(
        company_id=company.company_id, email=body.email
    ):
        raise HTTPException(409, "An invite for that email is already pending")

    # Staff-configured seat limit (admin panel). Checked after the duplicate
    # guards so "already invited/member" wins as the clearer error.
    _require_free_seat(company.company_id)

    row = create_invite(
        company_id=company.company_id,
        email=body.email,
        role=body.role,
        invited_by=company.user_id,
    )
    # Fire the invite email. Best-effort: if it fails we still return 201 so
    # the workspace_invites row stays visible in the UI, but `email_sent:
    # false` lets the frontend nudge the inviter to resend or share the link
    # manually. Already-registered invitees get a magic-link sign-in instead
    # (email_sent stays true; `existing_user` flags the different path).
    return _invite_result(row, send_invite_email(body.email))


@router.delete(
    "/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT
)
def revoke_team_invite(
    invite_id: str,
    company: CompanyContext = Depends(require_company),
):
    _require_admin(company)
    invite = get_invite(invite_id)
    # Cross-tenant or missing → 404 (don't leak existence).
    if not invite or invite.get("company_id") != company.company_id:
        raise HTTPException(404, "Invite not found")
    delete_invite(invite_id)
    return None


@router.post("/invites/{invite_id}/resend")
def resend_team_invite(
    invite_id: str,
    company: CompanyContext = Depends(require_company),
):
    _require_admin(company)
    invite = get_invite(invite_id)
    if not invite or invite.get("company_id") != company.company_id:
        raise HTTPException(404, "Invite not found")
    updated = touch_invite(invite_id)
    # Resend = bump created_at + actually fire a new email (invite for new
    # users, magic-link sign-in for already-registered ones).
    return _invite_result(updated or invite, send_invite_email(invite["email"]))


# ─────────────────────── Member edit / remove ───────────────────────


class MemberRolePatch(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def _validate(cls, v: str) -> str:
        # 'viewer' was added in the team-settings-style slice (SC1).
        if v not in ("owner", "admin", "member", "viewer"):
            raise ValueError(
                "role must be 'owner', 'admin', 'member', or 'viewer'"
            )
        return v


def _public_member(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "user_id": row.get("user_id"),
        "role": row.get("role"),
    }


@router.patch("/members/{user_id}")
def patch_team_member(
    user_id: str,
    body: MemberRolePatch,
    company: CompanyContext = Depends(require_company),
):
    _require_admin(company)
    member = get_member(company_id=company.company_id, user_id=user_id)
    if not member:
        raise HTTPException(404, "Member not found")

    if (
        member.get("role") == "owner"
        and body.role != "owner"
        and count_owners(company.company_id) <= 1
    ):
        raise HTTPException(
            409, "Cannot demote the last owner — promote another member first"
        )

    updated = update_member_role(
        company_id=company.company_id, user_id=user_id, role=body.role
    )
    return _public_member(updated or {"user_id": user_id, "role": body.role})


@router.delete(
    "/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_team_member(
    user_id: str,
    company: CompanyContext = Depends(require_company),
):
    _require_admin(company)
    member = get_member(company_id=company.company_id, user_id=user_id)
    if not member:
        raise HTTPException(404, "Member not found")

    if member.get("role") == "owner" and count_owners(company.company_id) <= 1:
        raise HTTPException(
            409, "Cannot remove the last owner — promote another member first"
        )

    delete_member(company_id=company.company_id, user_id=user_id)
    return None


# ─────────────────────── Invite acceptance (/v1/invites) ───────────────────────
#
# Lives on a separate router because the caller has no company yet —
# `require_company` would 403 them out before they could accept their
# invite. Path is fixed to /v1/invites/accept and uses `require_session`.

accept_router = APIRouter(prefix="/v1/invites", tags=["team"])


@accept_router.post("/accept")
def post_accept_invite(
    session: dict = Depends(require_session),
):
    user_id = session.get("sub")
    user_email = (session.get("email") or "").strip().lower()

    # `require_session` accepts legacy demo cookies too, but those have no
    # user identity — there's nothing to bind a membership to.
    if not user_id or session.get("aud") != "supabase":
        raise HTTPException(403, "Invite accept requires a signed-in user")

    # Fall back to the profile's stored email if the JWT did not carry one
    # (Supabase sometimes omits `email` from the user-context token).
    if not user_email:
        from app.db.client import require_client

        prof = (
            require_client()
            .table("profiles")
            .select("email")
            .eq("id", user_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        user_email = ((prof[0] if prof else {}).get("email") or "").strip().lower()

    if not user_email:
        raise HTTPException(400, "No email on session — cannot match invite")

    try:
        result = accept_invite_for_user(user_id=user_id, email=user_email)
    except ValueError as exc:
        if str(exc) == "already_in_company":
            raise HTTPException(
                409,
                "You're already a member of another company — leave it first to accept this invite",
            )
        raise

    if result is None:
        raise HTTPException(404, "No pending invite for your email")
    return result
