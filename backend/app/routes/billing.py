"""Billing — plans, credits, top-ups, referrals, and the Stripe webhook.

  GET  /v1/billing/summary        everything the Billing screen renders
  POST /v1/billing/checkout       {plan, interval} -> hosted Checkout URL
  POST /v1/billing/portal         -> hosted customer-portal URL
  POST /v1/billing/topup          {amount_usd} -> hosted Checkout URL
  POST /v1/billing/referrals      {email} -> a new invite
  POST /v1/billing/webhook        Stripe -> us. NO SESSION. See below.

Grain is the COMPANY. A Team plan's credits are described as pooled, and a
company-level balance is exactly that, so nothing here is per-user or
per-workspace.

MONEY IS AN OWNER/ADMIN CONCERN. Every authed route below is gated on
owner-or-admin, matching the Claude-key and usage pages: a viewer must not be
able to start a subscription, spend the company's money on a top-up, or read
what the company pays.

THE WEBHOOK IS THE ONE ROUTE WITH NO TENANT DEPENDENCY, and that is not an
oversight. Stripe sends no JWT, no cookie and no Origin header, so:

  * it authenticates by HMAC signature over the RAW body — hence
    `await request.body()` rather than a parsed model, since re-serialising
    JSON changes the bytes and breaks the signature;
  * it deliberately does NOT take `require_same_origin`. That dependency
    fail-closes on a missing Origin, which every legitimate Stripe delivery
    lacks. The exemption is by construction — the route simply does not list
    it — matching how the public `/by-token/*` share routes are exempted;
  * the tenant comes from the Stripe customer id, never from the request body.

It also returns 200 on a handler error, on purpose. Stripe retries non-2xx for
three days and disables endpoints that keep failing; a bug in one handler must
not stall every later event. Failures are logged loudly instead, and the
handlers are idempotent so a manual replay from the dashboard is safe.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.billing import credits, plans, referrals, stripe_client
from app.billing import webhooks as billing_webhooks
from app.config import settings
from app.db import billing as billing_db
from app.design_agent.csrf import require_same_origin  # server-side CSRF/Origin gate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/billing", tags=["billing"])


def _require_admin(company: CompanyContext) -> None:
    if company.role not in ("owner", "admin"):
        raise HTTPException(403, "Billing is limited to owners and admins")


def _require_stripe() -> None:
    if not stripe_client.configured():
        raise HTTPException(503, "Billing is not configured in this environment")


def _customer_id(company: CompanyContext) -> str:
    """This company's Stripe customer, created on first use and persisted."""
    row = billing_db.get_billing(company.company_id) or {}
    customer_id = stripe_client.ensure_customer(
        company_id=company.company_id,
        existing_customer_id=row.get("stripe_customer_id"),
        email=company.user_email,
        name=row.get("display_name"),
    )
    if customer_id != row.get("stripe_customer_id"):
        billing_db.set_billing(company.company_id, {"stripe_customer_id": customer_id})
    return customer_id


def _return_url(status: str) -> str:
    sep = "&" if "?" in settings.billing_return_url else "?"
    return f"{settings.billing_return_url}{sep}checkout={status}"


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


@router.get("/summary")
def get_summary(company: CompanyContext = Depends(require_company)) -> dict:
    """Everything the Billing screen needs, in one call.

    Deliberately one endpoint rather than five: the screen renders plan, credits,
    history and referrals together, and splitting them would make the pane
    render in four stages for no benefit.
    """
    _require_admin(company)
    row = billing_db.get_billing(company.company_id) or {}
    plan = plans.resolve_plan(row.get("plan"))
    allowance = plans.monthly_credits(plan)
    unlimited = allowance == plans.UNLIMITED

    invites = referrals.list_for_company(company.company_id)

    return {
        "plan": plan,
        "plan_label": plans.plan_label(plan),
        "unlimited": unlimited,
        # Null rather than -1 on an uncapped plan: the sentinel is an internal
        # convention and rendering it as a number would put "-1 credits" on a
        # screen.
        "credit_balance": None if unlimited else int(row.get("credit_balance") or 0),
        "monthly_credits": None if unlimited else allowance,
        "subscription_status": row.get("subscription_status"),
        "has_access": plans.subscription_grants_access(
            plan, row.get("subscription_status")
        ),
        "current_period_end": row.get("current_period_end"),
        "first_paid_at": row.get("first_paid_at"),
        "refund_window_days": plans.REFUND_WINDOW_DAYS,
        "billing_configured": stripe_client.configured(),
        "has_subscription": bool(row.get("stripe_subscription_id")),
        "action_costs": dict(plans.CREDIT_COSTS),
        "topup_presets": list(plans.TOPUP_PRESET_USD),
        "topup_min_usd": plans.TOPUP_MIN_USD,
        "topup_max_usd": plans.TOPUP_MAX_USD,
        "credits_per_topup_usd": plans.CREDITS_PER_TOPUP_USD,
        "history": credits.history(company.company_id),
        "referrals": [
            {
                "id": r.get("id"),
                "invitee_email": r.get("invitee_email"),
                "status": r.get("status"),
                "code": r.get("code"),
                "reward_credits": r.get("reward_credits"),
                "created_at": r.get("created_at"),
            }
            for r in invites
        ],
        "referral_invites_remaining": referrals.remaining_invites(company.company_id),
        "referral_reward_credits": plans.REFERRAL_REWARD_CREDITS,
    }


# ---------------------------------------------------------------------------
# Checkout / portal
# ---------------------------------------------------------------------------


class CheckoutRequest(BaseModel):
    plan: str
    interval: str = stripe_client.MONTHLY


@router.post("/checkout", dependencies=[Depends(require_same_origin)])
def start_checkout(
    body: CheckoutRequest, company: CompanyContext = Depends(require_company)
) -> dict:
    _require_admin(company)
    _require_stripe()

    if body.plan not in plans.SELF_SERVE_PLANS:
        # Team is invoiced and Enterprise goes through sales. Refusing loudly
        # beats silently selling someone a Starter plan they did not pick.
        raise HTTPException(400, f"{body.plan} is not available for self-serve checkout")
    if body.interval not in (stripe_client.MONTHLY, stripe_client.ANNUAL):
        raise HTTPException(400, "interval must be 'monthly' or 'annual'")
    if not stripe_client.price_id(body.plan, body.interval):
        raise HTTPException(
            503, f"No price is configured for {body.plan}/{body.interval}"
        )

    url = stripe_client.create_subscription_checkout(
        customer_id=_customer_id(company),
        company_id=company.company_id,
        plan=body.plan,
        interval=body.interval,
        success_url=_return_url("success"),
        cancel_url=_return_url("cancelled"),
    )
    return {"url": url}


@router.post("/portal", dependencies=[Depends(require_same_origin)])
def open_portal(company: CompanyContext = Depends(require_company)) -> dict:
    """The hosted portal: card updates, invoices, receipts, and cancellation.

    Cancelling here does not refund. A refund inside the window is a staff
    decision made after seeing consumption — see the staff admin panel.
    """
    _require_admin(company)
    _require_stripe()

    row = billing_db.get_billing(company.company_id) or {}
    if not row.get("stripe_customer_id"):
        raise HTTPException(400, "No billing account yet — start a subscription first")

    url = stripe_client.create_portal_session(
        customer_id=row["stripe_customer_id"], return_url=settings.billing_return_url
    )
    return {"url": url}


class TopupRequest(BaseModel):
    # Bounds enforced here as well as in plans.py so a malformed request is
    # rejected before it reaches Stripe.
    amount_usd: int = Field(ge=plans.TOPUP_MIN_USD, le=plans.TOPUP_MAX_USD)


@router.post("/topup", dependencies=[Depends(require_same_origin)])
def start_topup(
    body: TopupRequest, company: CompanyContext = Depends(require_company)
) -> dict:
    """Buy credits on top of the plan allowance.

    No monthly cap on how often. The original spec said "only 1 time a month",
    which contradicted "users can always buy more credit" in the same
    paragraph; a cap here is code whose only job is to refuse revenue, so the
    permissive reading was taken. Say so if the cap was the intent — it is a
    counter and a check, not a redesign.
    """
    _require_admin(company)
    _require_stripe()

    purchased = plans.topup_credits_for_usd(body.amount_usd)
    url = stripe_client.create_topup_checkout(
        customer_id=_customer_id(company),
        company_id=company.company_id,
        amount_usd=body.amount_usd,
        credits_purchased=purchased,
        success_url=_return_url("topup"),
        cancel_url=_return_url("cancelled"),
    )
    return {"url": url, "credits": purchased}


# ---------------------------------------------------------------------------
# Referrals
# ---------------------------------------------------------------------------


class ReferralRequest(BaseModel):
    email: str


@router.post("/referrals", dependencies=[Depends(require_same_origin)])
def invite_friend(
    body: ReferralRequest, company: CompanyContext = Depends(require_company)
) -> dict:
    _require_admin(company)
    try:
        referral = referrals.create_invite(
            referrer_company_id=company.company_id,
            referrer_user_id=company.user_id,
            invitee_email=body.email,
        )
    except referrals.ReferralLimitReached:
        raise HTTPException(
            400, f"You have used all {plans.MAX_REFERRAL_INVITES} invites"
        ) from None
    except referrals.AlreadyInvited:
        raise HTTPException(400, "You have already invited that address") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    return {
        "id": referral["id"],
        "invitee_email": referral["invitee_email"],
        "code": referral["code"],
        "status": referral["status"],
        "reward_credits": plans.REFERRAL_REWARD_CREDITS,
        "invites_remaining": referrals.remaining_invites(company.company_id),
    }


class ReferralClaim(BaseModel):
    code: str


@router.post("/referrals/claim", dependencies=[Depends(require_same_origin)])
def claim_referral(
    body: ReferralClaim, company: CompanyContext = Depends(require_company)
) -> dict:
    """Attach a newly created company to the referral that brought it.

    Called once by the onboarding client straight after it creates the company
    row, mirroring `/v1/org-invites/claim` next door — companies are created
    client-side through Supabase, so this is the only moment the backend learns
    a new tenant exists.

    NO CREDIT IS GRANTED HERE. This only records who to pay; the reward fires
    on this company's first paid invoice, in the `invoice.paid` webhook. That
    ordering is the anti-abuse story: signing up is free and infinitely
    repeatable, paying is not.

    Owner-only, and a bad or spent code is a quiet `{claimed: false}` rather
    than an error — the caller runs this best-effort inside onboarding, and a
    stale link must never block someone creating their workspace.
    """
    if company.role != "owner":
        raise HTTPException(403, "Only the workspace owner can claim a referral")

    claimed = referrals.claim_on_signup(
        code=body.code.strip(), invitee_company_id=company.company_id
    )
    return {"claimed": bool(claimed)}


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


@router.post("/webhook")
async def stripe_webhook(request: Request) -> dict:
    """Stripe -> us. Signature-verified; no session, no Origin, no tenant dep.

    See the module docstring for why each of those is absent.
    """
    if not stripe_client.configured():
        # Nothing can be verified without a key, and pretending otherwise would
        # accept unauthenticated credit grants.
        raise HTTPException(503, "Billing is not configured in this environment")

    payload = await request.body()
    try:
        event = stripe_client.verify_webhook(
            payload, request.headers.get("stripe-signature")
        )
    except ValueError as exc:
        logger.warning("stripe_webhook_rejected reason=%s", type(exc).__name__)
        raise HTTPException(400, "Invalid webhook signature") from None
    except Exception as exc:  # noqa: BLE001 — SDK raises its own signature error
        logger.warning("stripe_webhook_rejected reason=%s", type(exc).__name__)
        raise HTTPException(400, "Invalid webhook signature") from None

    event_id = event.get("id") or ""
    event_type = event.get("type") or ""
    if event_id and not billing_db.claim_stripe_event(event_id, event_type):
        return {"received": True, "result": "duplicate"}

    try:
        result = billing_webhooks.handle_event(event)
    except Exception:
        # 200 anyway — see the module docstring. Stripe disables endpoints that
        # keep returning errors, and one broken handler must not stall the
        # whole event stream.
        logger.exception("stripe_webhook_handler_failed type=%s id=%s", event_type, event_id)
        return {"received": True, "result": "error"}

    logger.info("stripe_webhook_handled type=%s result=%s", event_type, result)
    return {"received": True, "result": result}
