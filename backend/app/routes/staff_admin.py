"""Sprntly staff admin panel — org invites + per-company entitlements.

Staff (STAFF_EMAILS allowlist, see app.auth.require_staff) invite customer
organizations and configure their deal terms:

  * which modules they can access (companies.feature_flags),
  * default (platform) Claude key vs bring-your-own (companies.use_platform_key
    — the BYOK key itself is set by the company's own admin in Settings),
  * how many members they can invite (companies.seat_limit),
  * whether the prototype (design-agent) feature is enabled
    (companies.prototype_enabled).

Routes (all gated on require_staff — non-staff get 404, the surface is
invisible):
  GET    /v1/staff/companies                → orgs + entitlements + counts
  PATCH  /v1/staff/companies/{company_id}   → edit entitlements
  GET    /v1/staff/invites                  → org invites (all statuses)
  POST   /v1/staff/invites                  → invite an org (sends email)
  DELETE /v1/staff/invites/{invite_id}      → revoke a pending invite
  POST   /v1/staff/invites/{invite_id}/resend

Plus the claim endpoint the onboarding flow calls after the invitee creates
their company (require_company, NOT staff):
  POST   /v1/org-invites/claim              → apply the invite's entitlements
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app import llm_keys
from app import team_email as team_email_mod
from app.auth import CompanyContext, require_company, require_staff, session_email
from app.db.companies import (
    get_company_entitlements,
    list_companies_for_staff,
    update_company_entitlements,
)
from app.db.org_invites import (
    create_org_invite,
    get_org_invite,
    get_pending_org_invite_by_email,
    list_org_invites,
    mark_org_invite_accepted,
    revoke_org_invite,
)
from app.team_email import send_invite_email

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

router = APIRouter(prefix="/v1/staff", tags=["staff"])


class EntitlementsPatch(BaseModel):
    """Partial entitlement edit. Omitted fields are untouched; an explicit
    `"seat_limit": null` clears the limit (unlimited). feature_flags is a
    partial merge — only the keys sent change."""

    seat_limit: int | None = Field(default=None, ge=1, le=100000)
    prototype_enabled: bool | None = None
    use_platform_key: bool | None = None
    feature_flags: dict[str, bool] | None = None


class OrgInviteIn(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    company_name: str = Field(..., min_length=1, max_length=200)
    seat_limit: int | None = Field(default=None, ge=1, le=100000)
    prototype_enabled: bool = False
    use_platform_key: bool = False
    feature_flags: dict[str, bool] = Field(default_factory=dict)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        normalised = v.strip().lower()
        if not _EMAIL_RE.match(normalised):
            raise ValueError("invalid email")
        return normalised

    @field_validator("company_name")
    @classmethod
    def _validate_company_name(cls, v: str) -> str:
        name = v.strip()
        if not name:
            raise ValueError("company name is required")
        return name


def _public_invite(row: dict, *, email_sent: bool | None = None) -> dict:
    out = {
        "id": row.get("id"),
        "email": row.get("email"),
        "company_name": row.get("company_name"),
        "seat_limit": row.get("seat_limit"),
        "prototype_enabled": bool(row.get("prototype_enabled")),
        "use_platform_key": bool(row.get("use_platform_key")),
        "feature_flags": row.get("feature_flags") or {},
        "status": row.get("status"),
        "company_id": row.get("company_id"),
        "created_at": row.get("created_at"),
        "accepted_at": row.get("accepted_at"),
    }
    if email_sent is not None:
        out["email_sent"] = email_sent
    return out


@router.get("/companies")
def staff_list_companies(_: dict = Depends(require_staff)):
    return {"companies": list_companies_for_staff()}


@router.patch("/companies/{company_id}")
def staff_patch_company(
    company_id: str,
    body: EntitlementsPatch,
    _: dict = Depends(require_staff),
):
    current = get_company_entitlements(company_id)
    if not current:
        raise HTTPException(404, "Company not found")

    patch: dict = {}
    if "seat_limit" in body.model_fields_set:
        patch["seat_limit"] = body.seat_limit
    if body.prototype_enabled is not None:
        patch["prototype_enabled"] = body.prototype_enabled
    if body.use_platform_key is not None:
        patch["use_platform_key"] = body.use_platform_key
    if body.feature_flags is not None:
        patch["feature_flags"] = {**current["feature_flags"], **body.feature_flags}

    if patch:
        update_company_entitlements(company_id, patch)
        if "use_platform_key" in patch:
            # The LLM-key resolver caches per-company posture (app.llm_keys);
            # flush so the key-mode change takes effect immediately.
            llm_keys.invalidate(company_id)
    return get_company_entitlements(company_id)


@router.get("/invites")
def staff_list_invites(_: dict = Depends(require_staff)):
    return {"invites": [_public_invite(r) for r in list_org_invites()]}


@router.post("/invites", status_code=status.HTTP_201_CREATED)
def staff_post_invite(body: OrgInviteIn, session: dict = Depends(require_staff)):
    if get_pending_org_invite_by_email(body.email):
        raise HTTPException(409, "An invite for that email is already pending")

    row = create_org_invite(
        email=body.email,
        company_name=body.company_name,
        invited_by=session.get("sub"),
        seat_limit=body.seat_limit,
        prototype_enabled=body.prototype_enabled,
        use_platform_key=body.use_platform_key,
        feature_flags=body.feature_flags,
    )
    # Same best-effort semantics as team invites: the row is the source of
    # truth; `email_sent: false` lets the panel offer a resend.
    send_status = send_invite_email(body.email)
    return _public_invite(row, email_sent=send_status != team_email_mod.FAILED)


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def staff_revoke_invite(invite_id: str, _: dict = Depends(require_staff)):
    invite = get_org_invite(invite_id)
    if not invite or invite.get("status") != "pending":
        raise HTTPException(404, "Invite not found")
    revoke_org_invite(invite_id)
    return None


@router.post("/invites/{invite_id}/resend")
def staff_resend_invite(invite_id: str, _: dict = Depends(require_staff)):
    invite = get_org_invite(invite_id)
    if not invite or invite.get("status") != "pending":
        raise HTTPException(404, "Invite not found")
    send_status = send_invite_email(invite["email"])
    return _public_invite(invite, email_sent=send_status != team_email_mod.FAILED)


# ─────────────────────── Invite claim (/v1/org-invites) ───────────────────────
#
# Called by the onboarding flow right after the invited admin creates their
# company. Separate router: the caller is a customer (require_company), not
# staff. Applying is idempotent-safe — the invite settles to 'accepted' and a
# second claim finds nothing.

claim_router = APIRouter(prefix="/v1/org-invites", tags=["staff"])


@claim_router.post("/claim")
def claim_org_invite(company: CompanyContext = Depends(require_company)):
    """Apply the caller's pending org invite to their newly created company.

    Matches on the signed-in user's email. Only the company owner (the person
    who just created the workspace) can claim; 404 when there is no pending
    invite for their email — callers treat that as "nothing to do"."""
    if company.role != "owner":
        raise HTTPException(403, "Only the workspace owner can claim an org invite")
    # Same email resolution as require_staff: JWT claim first, stored profile
    # as fallback (Supabase user-context tokens sometimes omit `email`).
    email = session_email({"email": company.user_email, "sub": company.user_id})
    if not email:
        raise HTTPException(404, "No pending invite")
    invite = get_pending_org_invite_by_email(email)
    if not invite:
        raise HTTPException(404, "No pending invite")

    current = get_company_entitlements(company.company_id)
    flags = {**(current or {}).get("feature_flags", {}), **(invite.get("feature_flags") or {})}
    update_company_entitlements(
        company.company_id,
        {
            "seat_limit": invite.get("seat_limit"),
            "prototype_enabled": bool(invite.get("prototype_enabled")),
            "use_platform_key": bool(invite.get("use_platform_key")),
            "feature_flags": flags,
        },
    )
    llm_keys.invalidate(company.company_id)
    mark_org_invite_accepted(invite["id"], company_id=company.company_id)
    return {
        "applied": True,
        "invite_id": invite["id"],
        "entitlements": get_company_entitlements(company.company_id),
    }
