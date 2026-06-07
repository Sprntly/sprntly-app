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

Tenancy: `require_company` resolves the active company from the JWT.
Reads are open to any member (typical SaaS "see who's on the team"
UX); writes are gated to admin/owner.

Edit-role / remove / accept land in C3.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.auth import CompanyContext, require_company
from app.db.team import (
    create_invite,
    delete_invite,
    get_invite,
    get_pending_invite_by_email,
    list_company_members,
    list_pending_invites,
    member_exists_for_email,
    touch_invite,
)


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _require_admin(company: CompanyContext) -> None:
    """Gate for mutating routes. Members cannot mutate the team."""
    if company.role not in ("owner", "admin"):
        raise HTTPException(403, "Team management is restricted to admins")


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
        # constraint on workspace_invites.role anyway.
        if v not in ("admin", "member"):
            raise ValueError("role must be 'admin' or 'member'")
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


def _public_invite(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "email": row.get("email"),
        "role": row.get("role"),
        "invited_by": row.get("invited_by"),
        "created_at": row.get("created_at"),
    }


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

    row = create_invite(
        company_id=company.company_id,
        email=body.email,
        role=body.role,
        invited_by=company.user_id,
    )
    return _public_invite(row)


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
    return _public_invite(updated or invite)
