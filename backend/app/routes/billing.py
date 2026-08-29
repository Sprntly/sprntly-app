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
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.billing import credits, plans, referrals, stripe_client
from app.billing.stripe_client import BillingNotConfigured
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


def _stripe_call(what: str, fn):
    """Run a Stripe SDK call, turning its errors into a readable response.

    Without this every Stripe-side problem — a bad price id, a disabled
    customer portal, an expired key — surfaces as a bare 500 with a stack trace
    in the logs and nothing useful on screen, which reads to the user as "the
    button did nothing". Stripe's own message is almost always the actionable
    part, so it is passed through rather than replaced with a generic one.

    502 rather than 500: the failure is upstream, not in this handler.
    """
    try:
        return fn()
    except BillingNotConfigured as exc:
        raise HTTPException(503, str(exc)) from None
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — the SDK raises its own hierarchy
        message = getattr(exc, "user_message", None) or str(exc)
        logger.exception("stripe_call_failed op=%s", what)
        raise HTTPException(
            502, detail={"error": "stripe_error", "op": what, "message": message}
        ) from None


def _iso_or_none(epoch) -> str | None:
    if not epoch:
        return None
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()


def _safe_return_path(path: str | None) -> str | None:
    """Validate a caller-supplied post-Checkout landing path.

    THIS IS AN OPEN-REDIRECT BOUNDARY. The value ends up as Stripe's
    `success_url`, so an unchecked one sends a user who just typed their card
    number to whatever host the request asked for — on a link that legitimately
    came from us, right after a payment, which is the most credible phishing
    moment this product has.

    So: a path, and only a path. Must start with a single "/", must not start
    with "//" (protocol-relative, "//evil.com" is an absolute URL to a browser),
    and must carry no scheme, no host, no backslash (some parsers fold "\" to
    "/"), and no control characters. Anything else is not rejected loudly — it
    falls back to the configured default, because a bad return path is a bug in
    our own caller, not something to fail a purchase over.
    """
    if not path:
        return None
    if not path.startswith("/") or path.startswith("//"):
        return None
    if "\\" in path or ":" in path.split("?", 1)[0]:
        return None
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in path):
        return None
    return path


def _return_url(status: str, return_path: str | None = None) -> str:
    """Where Stripe sends the browser back to, with the outcome appended.

    `return_path` lets a caller land somewhere other than Settings → Billing —
    the onboarding gate needs the user returned to the step they left, not
    dropped into settings mid-signup. It is joined to the SAME origin the
    configured default uses; the caller supplies a path, never a URL.
    """
    base = settings.billing_return_url
    safe = _safe_return_path(return_path)
    if safe:
        origin = base.split("://", 1)
        host = origin[1].split("/", 1)[0] if len(origin) == 2 else base.split("/", 1)[0]
        scheme = origin[0] if len(origin) == 2 else "https"
        base = f"{scheme}://{host}{safe}"
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}checkout={status}"


def _referral_url(code: str) -> str:
    """The link a customer actually shares.

    Built from `billing_return_url`'s origin rather than a second setting: that
    value already names where this app lives, and two settings that must agree
    about the same host is one of them being wrong after the next deploy.
    """
    base = settings.billing_return_url
    parts = base.split("://", 1)
    if len(parts) == 2:
        origin = f"{parts[0]}://{parts[1].split('/', 1)[0]}"
    else:
        origin = base.split("/", 1)[0]
    return f"{origin}/sign-up?ref={code}"


def _trial_days(company: CompanyContext) -> int | None:
    """The trial this checkout gets, or None to bill immediately.

    Keyed on whether the company has EVER paid, not on which screen started the
    checkout: a client flag saying "this is onboarding" would be a free trial
    for anyone who could spell it. A company that cancelled and came back has
    already seen the product, so it pays on day one.
    """
    row = billing_db.get_billing(company.company_id) or {}
    # A cancel-and-resubscribe has already seen the product; no second trial.
    if row.get("first_paid_at"):
        return None
    # THE TRIAL IS AN ONBOARDING OFFER, and only that (owner decision
    # 2026-08-29). It exists so a stranger is not asked for money at step one
    # of signup, before they have seen a single brief. Someone buying from
    # Settings has already used the product and is not taking anything on
    # faith, so they pay on the day they buy.
    #
    # Keyed on a SERVER fact rather than on which screen started the checkout:
    # a flag in the body, or trusting `return_path` to point at the onboarding
    # gate, would hand a free week to anyone who could spell the field name.
    if row.get("onboarding_completed_at"):
        return None
    return plans.TRIAL_DAYS


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

    # UNSETTLED: we know this company as a Stripe CUSTOMER but hold no
    # subscription for them. That is what a paid checkout looks like before its
    # webhook lands — and what it looks like forever if webhooks are not
    # configured, are firewalled, or cannot reach this host at all.
    #
    # So ask Stripe rather than wait to be told. Narrow on purpose: it costs one
    # API call only in the window between paying and being recorded, and never
    # once a subscription is on file. The onboarding gate polls this endpoint,
    # which is how the gate's answer comes from Stripe's records instead of from
    # a `?checkout=success` query parameter anyone could type.
    if row.get("stripe_customer_id") and not row.get("stripe_subscription_id"):
        if billing_webhooks.reconcile_from_stripe(company.company_id):
            row = billing_db.get_billing(company.company_id) or row

    plan = plans.resolve_plan(row.get("plan"))
    allowance = plans.monthly_credits(plan)
    unlimited = allowance == plans.UNLIMITED

    invites = referrals.list_for_company(company.company_id)
    # Minted on first read — a company that has never opened this screen
    # does not need a code for a link nobody has asked for.
    referral_code = referrals.code_for_company(company.company_id)

    # Pending cancellation is read LIVE rather than stored. It changes rarely
    # and only from this screen, so a column plus a sync path would be three
    # moving parts to keep in step with Stripe when one call cannot drift at
    # all. Fail-soft: a Stripe hiccup must not blank the whole Billing pane, so
    # an unreadable subscription reports "not cancelling" and the rest of the
    # screen still renders.
    cancel_at_period_end = False
    cancels_at = None
    if row.get("stripe_subscription_id") and stripe_client.configured():
        try:
            sub = stripe_client.get_subscription(row["stripe_subscription_id"])
            cancel_at_period_end = bool(sub.get("cancel_at_period_end"))
            cancels_at = _iso_or_none(sub.get("cancel_at")) or row.get(
                "current_period_end"
            )
        except Exception:  # noqa: BLE001 — informational only
            logger.warning(
                "billing_summary_subscription_unreadable company=%s",
                company.company_id,
            )

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
        "cancel_at_period_end": cancel_at_period_end,
        # When access actually stops. The paid period's end, which is the whole
        # point: they keep what they bought.
        "cancels_at": cancels_at if cancel_at_period_end else None,
        "first_paid_at": row.get("first_paid_at"),
        "refund_window_days": plans.REFUND_WINDOW_DAYS,
        "billing_configured": stripe_client.configured(),
        "has_subscription": bool(row.get("stripe_subscription_id")),
        # `action_costs` (the per-action price list) was removed with the
        # "What things cost" view, owner decision 2026-08-28: a customer is
        # shown what they HAVE used, in Credit history, and not what any
        # action would cost before they take it. Dropped from the payload
        # rather than merely unrendered — a price list sitting in the
        # network tab is still a price list we published.
        "topup_presets": list(plans.TOPUP_PRESET_USD),
        "topup_min_usd": plans.TOPUP_MIN_USD,
        "topup_max_usd": plans.TOPUP_MAX_USD,
        "credits_per_topup_usd": plans.CREDITS_PER_TOPUP_USD,
        "history": credits.history(company.company_id),
        # THE MONEY RECORD: one row per payment, with the amount in currency.
        # This is what a customer opens billing to see — what they have been
        # charged, when, and for what — and what a generated invoice document
        # will read from rather than re-deriving amounts from prices that may
        # since have changed.
        "invoices": billing_db.list_invoices(company.company_id, limit=50),
        # Plan changes. Kept on the API because it answers a real support
        # question ("when did they move to this tier"), but NOT rendered: the
        # customer-facing history is payments and credits, not tier moves.
        "subscription_history": billing_db.list_subscription_events(
            company.company_id, limit=25
        ),
        # ONE PERMANENT LINK, and the people who have arrived through it.
        # `invitee_email` is gone: nobody types an address any more, so a
        # referral has no email to show until the invitee's own company exists.
        "referrals": [
            {
                "id": r.get("id"),
                "status": r.get("status"),
                "reward_credits": r.get("reward_credits"),
                "created_at": r.get("created_at"),
                "signed_up_at": r.get("signed_up_at"),
                "rewarded_at": r.get("rewarded_at"),
            }
            for r in invites
        ],
        "referral_code": referral_code,
        "referral_url": _referral_url(referral_code),
        "referral_invites_remaining": referrals.remaining_invites(company.company_id),
        "referral_reward_credits": plans.REFERRAL_REWARD_CREDITS,
    }


# ---------------------------------------------------------------------------
# Checkout / portal
# ---------------------------------------------------------------------------


class CheckoutRequest(BaseModel):
    plan: str
    interval: str = stripe_client.MONTHLY
    # A PATH on this app, not a URL — see `_safe_return_path`. Onboarding sends
    # its own step so a user mid-signup is returned to where they left off
    # rather than dropped into Settings.
    return_path: str | None = None


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

    # ALREADY PAYING? THEN THIS IS A SWITCH, NOT A PURCHASE.
    #
    # Checkout always creates a NEW subscription — it cannot replace one — so
    # letting an active customer through here leaves them paying for two plans
    # at once, and nothing downstream would have noticed. `/change-plan`
    # modifies the subscription they already have.
    #
    # Enforced at the server rather than trusted to the button: the screen that
    # calls this today is correct, and the fourth screen that calls it will not
    # be.
    existing = billing_db.get_billing(company.company_id) or {}
    if existing.get("stripe_subscription_id") and plans.subscription_grants_access(
        plans.resolve_plan(existing.get("plan")), existing.get("subscription_status")
    ):
        raise HTTPException(
            409,
            detail={
                "error": "already_subscribed",
                "message": (
                    "This company already has an active subscription. "
                    "Change the plan instead of buying a second one."
                ),
                "plan": plans.resolve_plan(existing.get("plan")),
            },
        )
    if body.interval not in (stripe_client.MONTHLY, stripe_client.ANNUAL):
        raise HTTPException(400, "interval must be 'monthly' or 'annual'")
    if not stripe_client.price_id(body.plan, body.interval):
        env_var = f"STRIPE_PRICE_{body.plan}_{body.interval}".upper()
        raise HTTPException(
            503,
            detail={
                "error": "price_not_configured",
                "message": (
                    f"No usable Stripe price for {body.plan}/{body.interval}. "
                    f"Set {env_var} to a price id (price_…), not an amount."
                ),
            },
        )

    url = _stripe_call(
        "checkout",
        lambda: stripe_client.create_subscription_checkout(
            customer_id=_customer_id(company),
            company_id=company.company_id,
            plan=body.plan,
            interval=body.interval,
            success_url=_return_url("success", body.return_path),
            cancel_url=_return_url("cancelled", body.return_path),
            trial_days=_trial_days(company),
        ),
    )
    return {"url": url}


class ChangePlanRequest(BaseModel):
    plan: str
    interval: str = stripe_client.MONTHLY


@router.post("/change-plan", dependencies=[Depends(require_same_origin)])
def change_plan(
    body: ChangePlanRequest, company: CompanyContext = Depends(require_company)
) -> dict:
    """Move a LIVE subscription onto a different plan.

    One subscription, one price swapped — the old plan stops billing the moment
    the new one starts, because they are the same subscription. The alternative,
    sending an existing customer back through Checkout, creates a second
    subscription and bills them twice.
    """
    _require_admin(company)
    _require_stripe()

    if body.plan not in plans.SELF_SERVE_PLANS:
        raise HTTPException(400, f"{body.plan} is not available for self-serve checkout")
    if body.interval not in (stripe_client.MONTHLY, stripe_client.ANNUAL):
        raise HTTPException(400, "interval must be 'monthly' or 'annual'")

    row = billing_db.get_billing(company.company_id) or {}
    subscription_id = row.get("stripe_subscription_id")
    if not subscription_id:
        # Nothing to change. Say which door to use rather than failing vaguely.
        raise HTTPException(
            409,
            detail={
                "error": "no_subscription",
                "message": "There is no subscription to change. Choose a plan to start one.",
            },
        )
    if plans.resolve_plan(row.get("plan")) == body.plan:
        raise HTTPException(
            400,
            detail={"error": "same_plan", "message": "You are already on that plan."},
        )

    _stripe_call(
        "change_plan",
        lambda: stripe_client.change_subscription_plan(
            subscription_id=subscription_id, plan=body.plan, interval=body.interval
        ),
    )
    # Re-read from Stripe rather than assuming the write landed as asked — the
    # same reason every webhook re-fetches instead of trusting its payload.
    #
    # Syncing the subscription we ALREADY KNOW rather than reconciling by
    # listing the customer's subscriptions: we are holding its id, so asking
    # "what does this customer have?" would be one more API call to rediscover
    # a fact we were just given. `_sync_subscription` is the same writer the
    # webhook path uses, so a switch and a pushed event cannot disagree.
    billing_webhooks._sync_subscription(
        company.company_id, subscription_id, source="change_plan"
    )
    fresh = billing_db.get_billing(company.company_id) or {}
    return {
        "plan": plans.resolve_plan(fresh.get("plan")),
        "subscription_status": fresh.get("subscription_status"),
    }


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

    url = _stripe_call(
        "portal",
        lambda: stripe_client.create_portal_session(
            customer_id=row["stripe_customer_id"],
            return_url=settings.billing_return_url,
        ),
    )
    return {"url": url}


@router.post("/cancel", dependencies=[Depends(require_same_origin)])
def cancel_subscription(company: CompanyContext = Depends(require_company)) -> dict:
    """Cancel at the end of the paid period.

    Not immediately: the customer has already paid for this month or year, so
    they keep the plan, the credits and the access until it runs out. Taking
    that away the moment they click Cancel would be removing something they
    already bought, and would turn every cancellation into a refund request.

    Reversible via /resume right up to the period boundary.
    """
    _require_admin(company)
    _require_stripe()

    row = billing_db.get_billing(company.company_id) or {}
    subscription_id = row.get("stripe_subscription_id")
    if not subscription_id:
        raise HTTPException(400, "There is no subscription to cancel")

    sub = _stripe_call(
        "cancel", lambda: stripe_client.schedule_cancellation(subscription_id)
    )
    logger.info("billing_cancellation_scheduled company=%s", company.company_id)
    return {
        "cancel_at_period_end": True,
        "cancels_at": _iso_or_none(sub.get("cancel_at"))
        or row.get("current_period_end"),
    }


@router.post("/resume", dependencies=[Depends(require_same_origin)])
def resume_subscription(company: CompanyContext = Depends(require_company)) -> dict:
    """Undo a pending cancellation, before the period ends.

    A subscription Stripe has already cancelled cannot be reactivated, so that
    case is reported as its own message rather than appearing to work — the
    customer needs to know they must choose a plan again, not wonder why
    nothing happened.
    """
    _require_admin(company)
    _require_stripe()

    row = billing_db.get_billing(company.company_id) or {}
    subscription_id = row.get("stripe_subscription_id")
    if not subscription_id:
        raise HTTPException(400, "There is no subscription to resume")
    if row.get("subscription_status") == "canceled":
        raise HTTPException(
            400,
            detail={
                "error": "subscription_ended",
                "message": (
                    "This subscription has already ended and cannot be "
                    "resumed. Choose a plan to start again."
                ),
            },
        )

    _stripe_call("resume", lambda: stripe_client.resume_subscription(subscription_id))
    logger.info("billing_cancellation_reversed company=%s", company.company_id)
    return {"cancel_at_period_end": False}


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
    url = _stripe_call(
        "topup",
        lambda: stripe_client.create_topup_checkout(
            customer_id=_customer_id(company),
            company_id=company.company_id,
            amount_usd=body.amount_usd,
            credits_purchased=purchased,
            success_url=_return_url("topup"),
            cancel_url=_return_url("cancelled"),
        ),
    )
    return {"url": url, "credits": purchased}


# ---------------------------------------------------------------------------
# Referrals
# ---------------------------------------------------------------------------


class ReferralRequest(BaseModel):
    email: str


# THE EMAIL INVITE ENDPOINT IS GONE (owner decision, 2026-08-29).
#
# It took an address, minted a code for that one person, and created a referral
# row before anybody had done anything. A company now has ONE permanent code —
# served on the billing summary as `referral_code` / `referral_url` — and the
# referral row is created when somebody actually ARRIVES through the link
# (`referrals.claim_on_signup`), not when somebody types an address.
#
# Nothing replaces it: there is no call to make. The link already exists.


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
    when THIS company subscribes — the transition into `active` or `trialing`,
    handled in `_sync_subscription`. That ordering is the anti-abuse story:
    signing up is free and infinitely repeatable, putting a real card on file
    is not.

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
