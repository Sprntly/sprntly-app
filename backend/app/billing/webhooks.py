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


def _sync_subscription(
    company_id: str, subscription_id: str, *, source: str | None = None
) -> str:
    """Write a subscription's current truth onto the company. See invariant 2."""
    sub = stripe_client.get_subscription(subscription_id)
    plan = _plan_from_subscription(sub)
    status = sub.get("status")

    # What it was, read BEFORE the write, so the history row can say what
    # actually changed rather than just what it now is.
    before = billing_db.get_billing(company_id) or {}
    prev_plan = before.get("plan")
    prev_status = before.get("subscription_status")

    # `current_period_end` moved onto the item in recent API versions; read
    # both so this keeps working across the pin.
    items = ((sub.get("items") or {}).get("data")) or []
    period_end = sub.get("current_period_end")
    if not period_end:
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

    # A TRIAL NEVER PAYS AN INVOICE, so the period grant cannot hang off
    # `invoice.paid` alone. Stripe's trial docs are explicit: a free trial puts
    # the subscription in `trialing` and the events it emits are
    # `customer.subscription.*` — the real invoice, and `invoice.paid` with it,
    # arrives only when the trial ENDS. Granting on the invoice alone gave a
    # trialling company a plan, a countdown, and a balance of zero: seven days
    # of a product that refuses every generation, which is worse than no trial.
    #
    # So the grant follows the SUBSCRIPTION PERIOD rather than the payment.
    # `grant_monthly` is idempotent on `period_start`, and `credits_granted_for`
    # is checked first, so this and `invoice.paid` cannot double-grant: whichever
    # arrives first for a given period does the work and the other is a no-op.
    # At trial end the period rolls over, `credits_granted_for` no longer
    # matches, and the first paid month grants again — which is correct.
    # A REFERRAL CONVERTS WHEN THE INVITEE SUBSCRIBES, not when they are first
    # charged. `trialing` counts: the card is on file and Stripe has accepted
    # it. Waiting for `invoice.paid` meant a referrer waited out the invitee's
    # whole trial with no signal that anything had worked.
    #
    # Only on the TRANSITION into a live status, so re-syncing an already-live
    # subscription does not re-run it. The reward is keyed on the referral id
    # besides, so both the status transition and the ledger index stop a repeat.
    if (
        status in plans.ACTIVE_SUBSCRIPTION_STATUSES
        and prev_status not in plans.ACTIVE_SUBSCRIPTION_STATUSES
    ):
        if referrals.reward_for_subscription(company_id):
            logger.info("referral_converted_on_subscribe company=%s", company_id)

    granted_period = False
    if status in plans.ACTIVE_SUBSCRIPTION_STATUSES:
        period_start = sub.get("current_period_start") or (
            items[0].get("current_period_start") if items else None
        )
        period = _iso(period_start) or _iso(period_end)
        fresh = billing_db.get_billing(company_id) or {}
        if period and fresh.get("credits_granted_for") != period:
            credits.grant_monthly(company_id, plan, period_start=period)
            billing_db.set_billing(company_id, {"credits_granted_for": period})
            granted_period = True
            logger.info(
                "billing_period_credits_granted company=%s plan=%s status=%s period=%s",
                company_id,
                plan,
                status,
                period,
            )

    # AN UPGRADE MID-PERIOD PAYS THE DIFFERENCE IN CREDITS TOO.
    #
    # Credits are granted per BILLING PERIOD, and a plan change does not start
    # one: same subscription, same `current_period_end`, so `credits_granted_for`
    # still matches and the grant above is skipped. The customer was charged a
    # prorated difference by Stripe and kept the smaller allowance until the
    # next renewal — paying Product Builder money to hold a Starter balance.
    #
    # So top up by the DIFFERENCE, not to the new figure: adding
    # (new allowance - old allowance) preserves whatever they have already
    # spent this month, where setting the balance to the new allowance would
    # quietly refund it.
    #
    # A DOWNGRADE takes nothing back. They have already paid for this month and
    # may have spent it; the smaller allowance applies from the next period,
    # which is what the ordinary grant does anyway. Clawing back credits
    # someone has bought is the kind of thing that ends up in a chargeback.
    if (
        # Not on a first purchase. `companies.plan` defaults to 'starter' for
        # every company, so a brand-new Product Builder subscription LOOKS like
        # an upgrade from Starter — and got the full allowance plus the uplift,
        # 1,876 instead of 1,316. A previous plan only counts if there was a
        # live subscription behind it.
        prev_status in plans.ACTIVE_SUBSCRIPTION_STATUSES
        # Not when the period grant just ran: that already handed over the new
        # plan's full allowance, and adding an uplift on top double-counts.
        and not granted_period
        and prev_plan
        and plan != prev_plan
        and status in plans.ACTIVE_SUBSCRIPTION_STATUSES
        and not plans.is_unlimited(plan)
        and not plans.is_unlimited(prev_plan)
    ):
        uplift = plans.monthly_credits(plan) - plans.monthly_credits(prev_plan)
        if uplift > 0:
            credits.grant(
                company_id,
                uplift,
                reason="plan_upgrade",
                # Idempotent on the period and the plan moved to, so a
                # redelivered event — or the webhook arriving after the in-app
                # switch already synced — cannot grant the uplift twice.
                # `before` is the pre-write read and is always in scope; `fresh` is
                # local to the period-grant branch above and may not be.
                ref_id=f"{subscription_id}:{before.get('credits_granted_for')}:{plan}",
            )
            logger.info(
                "billing_upgrade_credits_granted company=%s %s->%s uplift=%s",
                company_id,
                prev_plan,
                plan,
                uplift,
            )

    # HISTORY, written only on a real transition.
    #
    # One purchase produces three webhooks — invoice.paid,
    # customer.subscription.created, checkout.session.completed — and each
    # re-syncs the subscription. Logging every sync would bury one real change
    # under two identical rows and make the table useless for the questions it
    # exists to answer.
    if (plan, status) != (prev_plan, prev_status):
        billing_db.record_subscription_event(
            company_id,
            plan=plan,
            status=status,
            previous_plan=prev_plan,
            previous_status=prev_status,
            stripe_subscription_id=subscription_id,
            current_period_end=_iso(period_end),
            source=source,
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
        return _sync_subscription(
            company_id, subscription_id, source="checkout.session.completed"
        )
    return "checkout recorded"


def _on_subscription_event(
    sub: dict, *, deleted: bool, event_type: str = "customer.subscription"
) -> str:
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

        # HISTORY. This path returns before `_sync_subscription`, which is where
        # every other transition is recorded — so cancellations were invisible
        # in the history, and a cancellation is precisely the event a support
        # question is about. A deleted subscription cannot be re-fetched from
        # Stripe, so the row is written from what we already hold.
        prev_plan = plans.resolve_plan(row.get("plan"))
        prev_status = row.get("subscription_status")
        if prev_status != "canceled":
            billing_db.record_subscription_event(
                company_id,
                plan=prev_plan,
                status="canceled",
                previous_plan=prev_plan,
                previous_status=prev_status,
                stripe_subscription_id=sub.get("id"),
                current_period_end=row.get("current_period_end"),
                source="customer.subscription.deleted",
            )

        logger.info("billing_subscription_canceled company=%s", company_id)
        return "subscription canceled"

    return _sync_subscription(company_id, sub["id"], source=event_type)


def reconcile_from_stripe(company_id: str) -> bool:
    """Ask Stripe what this company's subscription actually is, and record it.

    The PULL half of the webhook, and the reason the payment gate does not have
    to trust a query parameter. `?checkout=success` is a string in a URL that
    anyone can type; a subscription on the customer in Stripe's own records is
    not. Reconciling makes the gate's answer come from Stripe rather than from
    the browser that claims to have come back from it.

    It also makes webhooks a latency optimisation rather than a hard dependency:
    an unconfigured, blocked, or merely slow webhook no longer strands a paying
    customer outside the product.

    Returns True when a subscription was found and written. Never raises — a
    Stripe outage must not turn into a 500 on a screen someone is waiting on.
    """
    row = billing_db.get_billing(company_id) or {}
    customer_id = row.get("stripe_customer_id")
    if not customer_id or not stripe_client.configured():
        return False
    try:
        sub = stripe_client.latest_subscription_for_customer(customer_id)
    except Exception:
        logger.warning("billing_reconcile_failed company=%s", company_id, exc_info=True)
        return False
    if not sub or not sub.get("id"):
        return False
    # Same writer as the webhook path, so a reconciled subscription and a
    # pushed one cannot end up meaning different things — and the period grant
    # (which a trial has no invoice for) happens here too.
    _sync_subscription(company_id, sub["id"], source="reconcile")
    logger.info(
        "billing_reconciled company=%s subscription=%s status=%s",
        company_id,
        sub.get("id"),
        sub.get("status"),
    )
    return True


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

    subscription_id = _subscription_id_from_invoice(invoice)
    if not subscription_id:
        # A one-off invoice (a top-up buys through Checkout, which is handled
        # above). Nothing to grant on a plan basis.
        return ", ".join(outcome) or "invoice recorded"

    # THE GRANT LIVES IN `_sync_subscription`, and only there. It used to be
    # duplicated here, keyed on the INVOICE's `period_start` while the sync
    # keyed on the SUBSCRIPTION's — two keys for one period, so an invoice
    # granted a second time over a period the sync had already granted, wiping
    # whatever the customer had spent. One key, one place: the subscription's
    # own period, which is also the only key a trial has (a trial pays no
    # invoice, so there is no invoice period to read).
    outcome.append(_sync_subscription(company_id, subscription_id, source="invoice.paid"))

    # THE MONEY RECORD. `invoice.paid` is the only event that means a payment
    # actually happened, so it is the only place this is written. Plan is read
    # AFTER the sync above so the row names the plan the invoice was for rather
    # than whatever we believed before the event arrived.
    fresh_row = billing_db.get_billing(company_id) or {}
    billing_db.record_invoice(
        company_id,
        {
            "company_id": company_id,
            "stripe_invoice_id": invoice.get("id"),
            "stripe_subscription_id": subscription_id,
            "plan": plans.resolve_plan(fresh_row.get("plan")),
            # Minor units, exactly as Stripe reports them. `amount_paid` is the
            # figure that actually cleared — `total` can differ once credits or
            # proration are applied, and a receipt must show what was taken.
            "amount_paid_cents": int(invoice.get("amount_paid") or 0),
            "currency": (invoice.get("currency") or "usd").lower(),
            "status": invoice.get("status"),
            "period_start": _iso(invoice.get("period_start")),
            "period_end": _iso(invoice.get("period_end")),
            "paid_at": _iso(invoice.get("status_transitions", {}).get("paid_at"))
            or _iso(invoice.get("created")),
            "invoice_number": invoice.get("number"),
            "hosted_invoice_url": invoice.get("hosted_invoice_url"),
            "invoice_pdf_url": invoice.get("invoice_pdf"),
        },
    )
    outcome.append("invoice recorded")

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
        return _sync_subscription(
            company_id, subscription_id, source="invoice.payment_failed"
        )

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
        return _on_subscription_event(obj, deleted=True, event_type=event_type)
    if event_type.startswith("customer.subscription."):
        return _on_subscription_event(obj, deleted=False, event_type=event_type)
    if event_type == "invoice.paid":
        return _on_invoice_paid(obj)
    if event_type == "invoice.payment_failed":
        return _on_invoice_failed(obj)
    return f"ignored: {event_type}"
