"""Team management routes — Settings → Team & roles.

Spec: Sprntly_Onboarding_Flow_Spec_v1 § Settings → Team [Only for Admins].

Read endpoints (this module, C1 of the slice):
  GET  /v1/team/members   — company_members rows for the resolved tenant
  GET  /v1/team/invites   — pending workspace_invites rows

Write endpoints (invite / revoke / edit-role / remove) land in C2 + C3.

Tenancy: `require_company` resolves the active company from the JWT.
Reads are open to any member (typical SaaS "see who's on the team"
UX); writes will be gated to admin/owner at the route level.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import CompanyContext, require_company
from app.db.team import list_company_members, list_pending_invites

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
