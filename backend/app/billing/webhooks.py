"""What each Stripe event does to our records.

Deliberately free of HTTP: `handle_event` takes an already-verified event dict
and returns a short string describing what it did. Signature verification and
status codes live in `routes/billing.py`, so every rule below is testable by
calling one function with a payload.

TWO INVARIANTS RUN THROUGH ALL OF IT.

1. The tenant is resolved from the Stripe customer id, or failing that from
   `metadata.company_id` which we stamp ourselves at customer creation. It is
   NEVER taken from anything else in the payload. A webhook body is
   attacker-shaped input until proven otherwise, and the one thing it must not
   be allowed to choose is which company gets the credits.

2. Subscription state is RE-FETCHED from the API rather than read out of the
   payload. Stripe does not guarantee delivery order, so a stale
   `customer.subscription.updated` can arrive after a newer one and write a
   status that is no longer true. One extra API call removes the whole class of
   ordering bug, which is far cheaper than reasoning about event timestamps.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.billing import credits, plans, referrals, stripe_client
from app.db import billing as billing_db

logger = logging.getLogger(__name__)

# Events we act on. Anything else is acknowledged and ignored — Stripe sends a
# great deal we do not care about, and 400-ing on those would make the endpoint
# look broken in the dashboard's delivery log.
HANDLED_EVENTS = frozenset(
    {
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.paid",
        "invoice.payment_failed",
    }
)


def _iso(epoch: int | None) -> str | None:
    if not epoch:
        return None
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()


def _company_for(obj: dict) -> str | None:
    """Resolve the tenant from a Stripe object. See invariant 1."""
    customer_id = obj.get("customer")
    if isinstance(customer_id, dict):
        customer_id = customer_id.get("id")
    if customer_id:
        company_id = billing_db.company_id_for_stripe_customer(customer_id)
        if company_id:
            return company_id
    # Fallback for the window between Checkout completing and our own write of
    # stripe_customer_id landing. Our metadata, set by us at create time.
    meta = obj.get("metadata") or {}
    return meta.get("company_id") or None


def _plan_from_subscription(sub: dict) -> str:
    """The plan a subscription represents.

    Metadata first (we set it at checkout), price id second. A subscription
    created by hand in the dashboard has no metadata, and reading the price
    keeps that case working instead of silently resolving to the launch
    default.
    """
    meta_plan = (sub.get("metadata") or {}).get("plan")
    if meta_plan and plans.resolve_plan(meta_plan) == meta_plan:
        return meta_plan

    items = ((sub.get("items") or {}).get("data")) or []
    price = (items[0].get("price") or {}).get("id") if items else None
    if price:
        for plan in plans.SELF_SERVE_PLANS:
            for interval in (stripe_client.MONTHLY, stripe_client.ANNUAL):
                if stripe_client.price_id(plan, interval) == price:
                    return plan
    return plans.resolve_plan(meta_plan)


def _sync_subscription(company_id: str, subscription_id: str) -> str:
    """Write a subscription's current truth onto the company. See invariant 2."""
    sub = stripe_client.get_subscription(subscription_id)
    plan = _plan_from_subscription(sub)
    status = sub.get("status")

    # `current_period_end` moved onto the item in recent API versions; read
    # both so this keeps working across the pin.
    period_end = sub.get("current_period_end")
    if not period_end:
        items = ((sub.get("items") or {}).get("data")) or []
        period_end = items[0].get("current_period_end") if items else None

    billing_db.set_billing(
        company_id,
        {
            "plan": plan,
            "stripe_subscription_id": subscription_id,
            "subscription_status": status,
            "current_period_end": _iso(period_end),
        },
    )
    logger.info(
        "billing_subscription_synced company=%s plan=%s status=%s",
        company_id,
        plan,
        status,
    )
    return f"subscription {status} on {plan}"


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _on_checkout_completed(session: dict) -> str:
    company_id = _company_for(session)
    if not company_id:
        logger.warning("billing_webhook_no_company event=checkout.session.completed")
        return "ignored: no company"

    customer_id = session.get("customer")
    if customer_id:
        billing_db.set_billing(company_id, {"stripe_customer_id": customer_id})

    # A one-off credit purchase. Subscriptions are handled by the subscription
    # events instead, which carry the authoritative status.
    if (session.get("metadata") or {}).get("purpose") == "topup":
        # Trust the credits recorded ON THE SESSION, not today's rate: the user
        # bought a specific number of credits at the rate in force when they
        # paid, and a rate change between checkout and webhook must not alter
        # what they receive.
        purchased = int((session.get("metadata") or {}).get("credits") or 0)
        if purchased <= 0:
            logger.warning("billing_topup_zero_credits session=%s", session.get("id"))
            return "ignored: topup with no credits"
        credits.grant(
            company_id,
            purchased,
            reason="topup",
            # The session id, so a redelivered event grants once.
            ref_id=session.get("id"),
        )
        return f"granted {purchased} top-up credits"

    subscription_id = session.get("subscription")
    if subscription_id:
        return _sync_subscription(company_id, subscription_id)
    return "checkout recorded"


def _on_subscription_event(sub: dict, *, deleted: bool) -> str:
    company_id = _company_for(sub)
    if not company_id:
        logger.warning("billing_webhook_no_company event=customer.subscription")
        return "ignored: no company"

    if deleted:
        # ONLY if the dead subscription is the one this company is actually on.
        #
        # A customer can hold several subscriptions over time — cancel, then
        # resubscribe, and the old one's `deleted` event is still in flight.
        # Stripe does not order deliveries, so that event routinely lands AFTER
        # the new subscription's `created`/`invoice.paid`. Writing "canceled"
        # unconditionally then revokes access for a customer who has just paid,
        # which is exactly what happened in testing: four events for the new
        # subscription set it active, and a stale delete two seconds later
        # turned it off.
        #
        # This is the same ordering hazard the update path solves by re-fetching
        # from the API. A deleted subscription cannot be re-fetched, so the
        # guard is an identity check instead.
        row = billing_db.get_billing(company_id) or {}
        current = row.get("stripe_subscription_id")
        if current and current != sub.get("id"):
            logger.info(
                "billing_stale_cancel_ignored company=%s dead=%s current=%s",
                company_id,
                sub.get("id"),
                current,
            )
            return "ignored: cancel for a superseded subscription"

        # Plan is left alone on purpose: it is the record of what they had, and
        # access is gated on status.
        billing_db.set_billing(company_id, {"subscription_status": "canceled"})
        logger.info("billing_subscription_canceled company=%s", company_id)
        return "subscription canceled"

    return _sync_subscription(company_id, sub["id"])


def _subscription_id_from_invoice(invoice: dict) -> str | None:
    """The subscription an invoice belongs to, across API versions.

    `invoice.subscription` was flattened into `invoice.parent.
    subscription_details.subscription` in 2025-03-31.basil. Reading both keeps
    this correct if the pinned version moves either way.
    """
    parent = invoice.get("parent") or {}
    details = parent.get("subscription_details") or {}
    sub = details.get("subscription") or invoice.get("subscription")
    if isinstance(sub, dict):
        return sub.get("id")
    return sub


def _on_invoice_paid(invoice: dict) -> str:
    company_id = _company_for(invoice)
    if not company_id:
        logger.warning("billing_webhook_no_company event=invoice.paid")
        return "ignored: no company"

    row = billing_db.get_billing(company_id) or {}
    outcome = []

    # First payment ever: starts the refund-window clock, and is the moment a
    # referral converts.
    if not row.get("first_paid_at"):
        billing_db.set_billing(company_id, {"first_paid_at": _iso(invoice.get("created"))})
        if referrals.reward_for_first_payment(company_id):
            outcome.append("referral rewarded")

    subscription_id = _subscription_id_from_invoice(invoice)
    if not subscription_id:
        # A one-off invoice (a top-up buys through Checkout, which is handled
        # above). Nothing to grant on a plan basis.
        return ", ".join(outcome) or "invoice recorded"

    outcome.append(_sync_subscription(company_id, subscription_id))

    # The period grant. Keyed on the period start so a redelivered invoice does
    # not hand out a second month — `grant_monthly` is idempotent on it, and
    # storing it makes the state visible rather than implicit.
    fresh = billing_db.get_billing(company_id) or {}
    period_start = _iso(invoice.get("period_start")) or fresh.get("current_period_end")
    if period_start and fresh.get("credits_granted_for") != period_start:
        credits.grant_monthly(company_id, fresh.get("plan"), period_start=period_start)
        billing_db.set_billing(company_id, {"credits_granted_for": period_start})
        outcome.append("credits granted")

    return ", ".join(outcome)


def _on_invoice_failed(invoice: dict) -> str:
    company_id = _company_for(invoice)
    if not company_id:
        return "ignored: no company"

    subscription_id = _subscription_id_from_invoice(invoice)
    if subscription_id:
        # Re-fetch rather than assuming past_due: Stripe decides the next
        # status from the dashboard's retry settings, and it may already have
        # moved to canceled or unpaid.
        return _sync_subscription(company_id, subscription_id)

    billing_db.set_billing(company_id, {"subscription_status": "past_due"})
    return "payment failed"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def handle_event(event: dict) -> str:
    """Apply one verified Stripe event. Returns a short description.

    Assumes the caller has already verified the signature and claimed the event
    id (see `routes/billing.py`). Unknown event types are a no-op by design.
    """
    event_type = event.get("type") or ""
    obj = ((event.get("data") or {}).get("object")) or {}

    if event_type not in HANDLED_EVENTS:
        return f"ignored: {event_type}"

    if event_type == "checkout.session.completed":
        return _on_checkout_completed(obj)
    if event_type == "customer.subscription.deleted":
        return _on_subscription_event(obj, deleted=True)
    if event_type.startswith("customer.subscription."):
        return _on_subscription_event(obj, deleted=False)
    if event_type == "invoice.paid":
        return _on_invoice_paid(obj)
    if event_type == "invoice.payment_failed":
        return _on_invoice_failed(obj)
    return f"ignored: {event_type}"
