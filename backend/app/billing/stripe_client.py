"""The only module that talks to Stripe.

Everything else in the app asks this module for a checkout URL or a portal URL
and gets a string back. Keeping the SDK behind one wall means the webhook
handler, the routes and the tests all deal in plain dicts, and swapping or
mocking Stripe is one patch point rather than a dozen.

INERT WITHOUT CONFIGURATION. `settings.stripe_secret_key` is empty in local dev
and in CI, and every entry point here checks `configured()` first. That is why
the backend test suite needs no Stripe credentials and why a developer running
the app locally sees "billing is not configured" rather than a stack trace.

HOSTED CHECKOUT AND HOSTED PORTAL, deliberately. Card fields never touch our
code, which keeps PCI scope at SAQ-A — and matters more than usual here because
`web/` is a static export with no server, no route handlers and no middleware,
so there is nowhere to put a server-side payment step even if we wanted one.
The portal also supplies card updates, cancellation, invoice history and
receipts for free; every one of those is a screen we do not build.
"""
from __future__ import annotations

import logging
from typing import Any

from app.billing import plans
from app.config import settings

logger = logging.getLogger(__name__)

# The REST API version this code is written against, pinned independently of
# the SDK version. Stripe evolves the API behind dated versions; without this,
# upgrading the library silently changes response shapes underneath us. Chosen
# to match the shapes read in `webhooks.py` — notably `invoice.parent.
# subscription_details.subscription`, which replaced the flat
# `invoice.subscription` field in 2025-03-31.basil.
STRIPE_API_VERSION = "2025-03-31.basil"


class BillingNotConfigured(RuntimeError):
    """Stripe credentials are absent in this environment."""


def configured() -> bool:
    return bool(settings.stripe_secret_key)


def _stripe():
    """The configured SDK module.

    Imported lazily, not at module scope, for two reasons: the package is
    absent from some environments entirely, and importing it eagerly would put
    a hard dependency on the import path of every test that merely touches
    `app.billing`.
    """
    if not configured():
        raise BillingNotConfigured("STRIPE_SECRET_KEY is not set")
    import stripe  # noqa: PLC0415 — deliberate lazy import, see docstring

    stripe.api_key = settings.stripe_secret_key
    stripe.api_version = STRIPE_API_VERSION
    return stripe


# ---------------------------------------------------------------------------
# Price ids
# ---------------------------------------------------------------------------

# Every Stripe Price id starts with this. Anything else in the env var is a
# configuration mistake, most often the dollar amount pasted in place of the id.
_PRICE_ID_PREFIX = "price_"

MONTHLY = "monthly"
ANNUAL = "annual"


def price_id(plan: str, interval: str) -> str:
    """The dashboard-created price for a (plan, interval), or "" if unsold.

    Prices live in the Stripe dashboard rather than in code so that changing a
    number does not need a deploy. The cost is that a missing price is a
    configuration error discovered at runtime, which is why this returns "" and
    the caller turns that into a clear 503 rather than a Stripe API error.
    """
    table = {
        (plans.STARTER, MONTHLY): settings.stripe_price_starter_monthly,
        (plans.STARTER, ANNUAL): settings.stripe_price_starter_annual,
        (plans.PRODUCT_BUILDER, MONTHLY): settings.stripe_price_product_builder_monthly,
        (plans.PRODUCT_BUILDER, ANNUAL): settings.stripe_price_product_builder_annual,
    }
    value = (table.get((plans.resolve_plan(plan), interval)) or "").strip()
    if value and not value.startswith(_PRICE_ID_PREFIX):
        # The easy mistake, and one Stripe only reports at checkout time as
        # "The `price` parameter should be the ID of a price object, rather
        # than the literal numerical price" — by which point the user has
        # clicked Choose and got a 500. Caught here instead, so the route can
        # say which variable is wrong before anyone talks to Stripe.
        logger.error(
            "stripe_price_malformed plan=%s interval=%s "
            "value_looks_like=%r expected_prefix=%s",
            plan,
            interval,
            value[:12],
            _PRICE_ID_PREFIX,
        )
        return ""
    return value


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


def ensure_customer(
    *, company_id: str, existing_customer_id: str | None, email: str | None, name: str | None
) -> str:
    """The company's Stripe customer id, creating one on first use.

    `metadata.company_id` is the link back to our tenant and is the ONLY way a
    webhook resolves which company an event belongs to when the customer id
    lookup misses. Never derive the company from anything in a request body.
    """
    if existing_customer_id:
        return existing_customer_id

    stripe = _stripe()
    customer = stripe.Customer.create(
        email=email or None,
        name=name or None,
        metadata={"company_id": company_id},
        # Retrying a create without this makes a second customer, and a company
        # with two Stripe customers bills twice and reconciles to neither.
        idempotency_key=f"customer:{company_id}",
    )
    logger.info("stripe_customer_created company=%s", company_id)
    return customer["id"]


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


def create_subscription_checkout(
    *,
    customer_id: str,
    company_id: str,
    plan: str,
    interval: str,
    success_url: str,
    cancel_url: str,
) -> str:
    """A hosted Checkout session for a subscription. Returns its URL.

    `allow_promotion_codes` is what makes the discount codes work. The pricing
    table's "monthly w/ code" column ($35 against $59, $59 against $99) is one
    Stripe Coupon with promotion codes generated off it in the dashboard — no
    code generator here, no codes table, and staff can mint them without a
    deploy.
    """
    price = price_id(plan, interval)
    if not price:
        raise BillingNotConfigured(f"no Stripe price configured for {plan}/{interval}")

    stripe = _stripe()
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        allow_promotion_codes=True,
        # Echoed back on every event for this session and its subscription, so
        # the webhook can resolve the tenant without a database round trip.
        metadata={"company_id": company_id, "plan": plan, "interval": interval},
        subscription_data={"metadata": {"company_id": company_id, "plan": plan}},
    )
    return session["url"]


def create_topup_checkout(
    *,
    customer_id: str,
    company_id: str,
    amount_usd: int,
    credits_purchased: int,
    success_url: str,
    cancel_url: str,
) -> str:
    """A one-off Checkout session buying `credits_purchased` credits.

    The price is built inline rather than pulled from the dashboard because the
    amount is chosen by the user (presets plus a custom field); there is no
    fixed price object to point at. Validation of `amount_usd` happens in the
    route, before this is reached.
    """
    stripe = _stripe()
    session = stripe.checkout.Session.create(
        mode="payment",
        customer=customer_id,
        line_items=[
            {
                "price_data": {
                    "currency": "usd",
                    "unit_amount": amount_usd * 100,
                    "product_data": {
                        "name": f"{credits_purchased:,} Sprntly credits",
                    },
                },
                "quantity": 1,
            }
        ],
        success_url=success_url,
        cancel_url=cancel_url,
        # `credits` rides on the session so the webhook grants exactly what was
        # bought, even if the rate in plans.py changes between purchase and
        # delivery.
        metadata={
            "company_id": company_id,
            "purpose": "topup",
            "credits": str(credits_purchased),
        },
    )
    return session["url"]


# ---------------------------------------------------------------------------
# Portal
# ---------------------------------------------------------------------------


def create_portal_session(*, customer_id: str, return_url: str) -> str:
    """A hosted customer-portal session. Returns its URL.

    This is where cancellation happens. Cancelling does NOT refund — a refund
    is a staff decision made after seeing how many credits were consumed (see
    plans.REFUND_WINDOW_DAYS).
    """
    stripe = _stripe()
    session = stripe.billing_portal.Session.create(
        customer=customer_id, return_url=return_url
    )
    return session["url"]


# ---------------------------------------------------------------------------
# Reads + refunds
# ---------------------------------------------------------------------------


def _as_dict(obj: Any) -> dict[str, Any]:
    """A Stripe SDK object as a plain nested dict.

    `dict(obj)` LOOKS right and raises TypeError: a StripeObject is not a
    Mapping, it only mimics subscript access. That mistake shipped once already
    and cost every webhook delivery — verification appeared to fail with
    "TypeError" while the signature was perfectly valid, because the exception
    came from the line AFTER the check.

    `to_dict()` converts nested objects to plain dicts too, so the handlers can
    use ordinary `.get()` chains all the way down. It is the only supported
    public converter in stripe 15.x — `to_dict_recursive` is private there.
    """
    to_dict = getattr(obj, "to_dict", None)
    return to_dict() if callable(to_dict) else dict(obj)


def get_subscription(subscription_id: str) -> dict[str, Any]:
    """Fetch a subscription's CURRENT state.

    Webhook payloads are snapshots and Stripe does not guarantee delivery
    order, so an older event can arrive after a newer one and write stale
    status. Re-fetching on every subscription event costs one API call and
    removes the entire class of ordering bugs — cheaper than reasoning about
    event timestamps.
    """
    return _as_dict(_stripe().Subscription.retrieve(subscription_id))


def schedule_cancellation(subscription_id: str) -> dict[str, Any]:
    """Cancel at the END of the paid period, not now.

    The customer has already paid for this month or year, so they keep their
    plan, their credits and their access until it runs out — cancelling
    immediately would take away something already bought and turn every
    cancellation into a refund request.

    Stripe keeps `status` at `active` with `cancel_at_period_end=true`, which
    means `plans.subscription_grants_access` needs no special case: access
    simply continues until `customer.subscription.deleted` arrives at the
    period boundary.

    Reversible until that moment — see `resume_subscription`.
    """
    return _as_dict(
        _stripe().Subscription.modify(subscription_id, cancel_at_period_end=True)
    )


def resume_subscription(subscription_id: str) -> dict[str, Any]:
    """Undo a pending cancellation.

    Only works before the period ends; once Stripe has actually cancelled it,
    a subscription cannot be reactivated and the customer must buy a new one.
    The route surfaces that difference rather than silently failing.
    """
    return _as_dict(
        _stripe().Subscription.modify(subscription_id, cancel_at_period_end=False)
    )


def refund_latest_payment(*, subscription_id: str, reason: str = "requested_by_customer") -> str:
    """Refund the most recent paid invoice on a subscription. Returns refund id.

    Staff-triggered, never automatic. Deliberately refunds only the LATEST
    invoice: a customer cancelling in the refund window has paid exactly once,
    and walking further back would refund months of legitimately consumed
    service.
    """
    stripe = _stripe()
    sub = _as_dict(stripe.Subscription.retrieve(subscription_id))
    invoice_id = sub.get("latest_invoice")
    if not invoice_id:
        raise RuntimeError(f"subscription {subscription_id} has no invoice to refund")

    invoice = _as_dict(stripe.Invoice.retrieve(invoice_id))
    payment_intent = invoice.get("payment_intent")
    if not payment_intent:
        raise RuntimeError(f"invoice {invoice_id} has no payment to refund")

    refund = stripe.Refund.create(
        payment_intent=payment_intent,
        reason=reason,
        idempotency_key=f"refund:{invoice_id}",
    )
    logger.info("stripe_refund_created subscription=%s invoice=%s", subscription_id, invoice_id)
    return refund["id"]


def cancel_subscription(subscription_id: str) -> dict[str, Any]:
    """Cancel immediately. Used by the staff refund flow, which refunds the
    money and therefore must also stop the service."""
    return _as_dict(_stripe().Subscription.delete(subscription_id))


# ---------------------------------------------------------------------------
# Webhook verification
# ---------------------------------------------------------------------------


def verify_webhook(payload: bytes, signature: str | None) -> dict[str, Any]:
    """Verify a webhook's signature and return the parsed event.

    FAILS CLOSED on a missing secret. An unverified webhook body is attacker
    input that grants credits and changes plans — accepting one because the
    environment forgot to configure a secret would be a self-serve upgrade
    endpoint. Raises `ValueError` on anything unverifiable; the route turns
    that into a 400.
    """
    secret = settings.stripe_webhook_secret
    if not secret:
        raise ValueError("STRIPE_WEBHOOK_SECRET is not set")
    if not signature:
        raise ValueError("missing Stripe-Signature header")

    stripe = _stripe()
    # Verifies the HMAC with a timing-safe compare AND enforces a timestamp
    # tolerance, which is what stops a captured payload being replayed later.
    event = stripe.Webhook.construct_event(payload, signature, secret)
    return _as_dict(event)
