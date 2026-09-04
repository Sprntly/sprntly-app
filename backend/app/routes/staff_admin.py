"""Sprntly staff admin panel — org invites + per-company entitlements.

The panel is for the company owner only and does NOT use normal Sprntly
(Supabase) login: POST /v1/staff/login checks the dedicated credential pair
from env (STAFF_ADMIN_ID + STAFF_ADMIN_PASSWORD_HASH, argon2id) and mints a
short-lived staff JWT (aud=sprntly-staff — see app.auth.require_staff). With
it, staff invite customer organizations and configure their deal terms:

  * which modules they can access (companies.feature_flags),
  * default (platform) Claude key vs bring-your-own (companies.use_platform_key
    — the BYOK key itself is set by the company's own admin in Settings),
  * how many members they can invite (companies.seat_limit),
  * whether the prototype (design-agent) feature is enabled
    (companies.prototype_enabled).

Routes (login is open when the surface is enabled; everything else is gated
on require_staff — no/invalid/user tokens get 404, the surface is invisible;
unset env disables the whole surface, login included, with 404):
  POST   /v1/staff/login                    → dedicated-credential login → JWT
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

import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app import llm_keys
from app.billing import credits, plans, stripe_client
from app.db import billing as billing_db
from app import team_email as team_email_mod
from app.db import authcache
from app.auth import (
    STAFF_TOKEN_TTL_HOURS,
    CompanyContext,
    make_staff_token,
    require_company,
    require_staff,
    session_email,
    staff_surface_enabled,
    verify_staff_credentials,
)
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

logger = logging.getLogger(__name__)

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
    # Prototype is a default-ON module for every organization (matching the
    # companies.prototype_enabled column default) — the toggle is an opt-OUT.
    prototype_enabled: bool = True
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


class StaffLoginIn(BaseModel):
    id: str = Field(..., min_length=1, max_length=200)
    password: str = Field(..., min_length=1, max_length=1000)


@router.post("/login")
def staff_login(body: StaffLoginIn):
    """Dedicated staff login — completely separate from Supabase user auth.

    404 (not 401) when the surface is disabled so an unconfigured box shows
    nothing; 401 with a single generic message for ANY bad credential (wrong
    id and wrong password are indistinguishable — no enumeration).
    """
    if not staff_surface_enabled():
        raise HTTPException(404, "Not found")
    if not verify_staff_credentials(body.id, body.password):
        raise HTTPException(401, "Invalid credentials")
    return {
        "token": make_staff_token(),
        "token_type": "bearer",
        "expires_in": STAFF_TOKEN_TTL_HOURS * 3600,
    }


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
        if "feature_flags" in patch:
            # Same reason, for the module gates: the flags are TTL-cached in
            # authcache, so without this an operator turning a module off would
            # watch it stay on for the rest of the TTL.
            authcache.invalidate_feature_flags(company_id)
    return get_company_entitlements(company_id)


# ---------------------------------------------------------------------------
# Billing — refunds and manual credit adjustments
# ---------------------------------------------------------------------------
#
# Refunds are STAFF-APPROVED, never automatic (owner decision, 2026-08-21).
# Cancelling is self-serve in the Stripe portal; refunding is not, because an
# automatic refund on cancel is trivially farmed — spend the month's credits on
# day one, cancel on day six, keep both the output and the money. A person
# looks at `credits_used` below and decides.


class StaffRefundIn(BaseModel):
    """`cancel` also ends the subscription. Refunding the money while leaving
    the service running is almost never what is meant, so it defaults on."""

    cancel: bool = True


@router.get("/companies/{company_id}/billing")
def staff_get_billing(company_id: str, _: dict = Depends(require_staff)):
    """Billing state plus the one number a refund decision turns on: how much
    of the plan's allowance this company has actually consumed."""
    row = billing_db.get_billing(company_id)
    if not row:
        raise HTTPException(404, "Company not found")

    plan = plans.resolve_plan(row.get("plan"))
    allowance = plans.monthly_credits(plan)
    balance = int(row.get("credit_balance") or 0)
    within_window = _within_refund_window(row.get("first_paid_at"))

    return {
        **row,
        "plan": plan,
        "plan_label": plans.plan_label(plan),
        "monthly_credits": None if allowance == plans.UNLIMITED else allowance,
        # READ OFF THE LEDGER, not inferred from the balance. `allowance -
        # balance` reported a full allowance as consumed whenever a grant had
        # not landed, reported zero for anyone who had topped up, and moved
        # under every past customer whenever a plan was repriced. A refund
        # decision turns on this number, so it has to be the real one.
        "credits_used": (
            None
            if allowance == plans.UNLIMITED
            else credits.spent_since(company_id, row.get("credits_granted_for"))
        ),
        "within_refund_window": within_window,
        "refund_window_days": plans.REFUND_WINDOW_DAYS,
        "ledger": credits.history(company_id, limit=100),
    }


def _within_refund_window(first_paid_at: str | None) -> bool:
    if not first_paid_at:
        return False
    try:
        paid = datetime.fromisoformat(str(first_paid_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if paid.tzinfo is None:
        paid = paid.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - paid <= timedelta(
        days=plans.REFUND_WINDOW_DAYS
    )


@router.post("/companies/{company_id}/billing/refund")
def staff_refund(
    company_id: str, body: StaffRefundIn, _: dict = Depends(require_staff)
):
    """Refund the latest invoice, and by default cancel the subscription.

    Outside the window is allowed but reported, so a goodwill refund is
    possible without the endpoint pretending the policy was met.
    """
    row = billing_db.get_billing(company_id)
    if not row:
        raise HTTPException(404, "Company not found")
    subscription_id = row.get("stripe_subscription_id")
    if not subscription_id:
        raise HTTPException(400, "This company has no Stripe subscription")
    if not stripe_client.configured():
        raise HTTPException(503, "Billing is not configured in this environment")

    refund_id = stripe_client.refund_latest_payment(subscription_id=subscription_id)
    if body.cancel:
        stripe_client.cancel_subscription(subscription_id)
        billing_db.set_billing(company_id, {"subscription_status": "canceled"})

    logger.info(
        "staff_refund company=%s refund=%s cancelled=%s",
        company_id,
        refund_id,
        body.cancel,
    )
    return {
        "refund_id": refund_id,
        "cancelled": body.cancel,
        "within_refund_window": _within_refund_window(row.get("first_paid_at")),
    }


class StaffCreditAdjustIn(BaseModel):
    """Positive only. Taking credits away by hand is not offered: every real
    reason to reduce a balance (a spend, a plan change) already has a path that
    writes a ledger row explaining itself."""

    credits: int = Field(gt=0, le=100_000)
    note: str = ""


@router.post("/companies/{company_id}/billing/credits")
def staff_grant_credits(
    company_id: str, body: StaffCreditAdjustIn, _: dict = Depends(require_staff)
):
    """Hand a company credits — goodwill, a failed generation, a support fix.

    Lands in the same ledger as everything else, tagged `adjustment`, so the
    Billing screen explains where they came from rather than showing a balance
    that silently changed.
    """
    if not billing_db.get_billing(company_id):
        raise HTTPException(404, "Company not found")

    balance = credits.grant(
        company_id,
        body.credits,
        reason="adjustment",
        ref_id=credits.new_ref(),
    )
    logger.info(
        "staff_credit_grant company=%s credits=%s", company_id, body.credits
    )
    return {"credit_balance": balance}


@router.get("/invites")
def staff_list_invites(_: dict = Depends(require_staff)):
    return {"invites": [_public_invite(r) for r in list_org_invites()]}


@router.post("/invites", status_code=status.HTTP_201_CREATED)
def staff_post_invite(body: OrgInviteIn, _: dict = Depends(require_staff)):
    if get_pending_org_invite_by_email(body.email):
        raise HTTPException(409, "An invite for that email is already pending")

    row = create_org_invite(
        email=body.email,
        company_name=body.company_name,
        # The staff token's sub is the literal "staff" — not a Supabase user
        # uuid, which is what org_invites.invited_by (uuid) stores. There is
        # exactly one staff credential, so NULL loses nothing.
        invited_by=None,
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
    # This write sets feature_flags too, so the module gates must be flushed
    # with the key posture — an accepted invite grants modules immediately.
    authcache.invalidate_feature_flags(company.company_id)
    mark_org_invite_accepted(invite["id"], company_id=company.company_id)
    return {
        "applied": True,
        "invite_id": invite["id"],
        "entitlements": get_company_entitlements(company.company_id),
    }
