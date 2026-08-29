"""DB helpers for the billing columns on `companies`, plus the webhook replay guard.

These are `companies` columns, not their own table, so they could have lived in
`db/companies.py`. They are here instead because that module is already large
and mixes onboarding profile, LLM keys and staff entitlements; billing is a
separate concern with a separate reader (the webhook handler) and benefits from
not being read by anything that does not need it.

Every write here is FAIL-LOUD. `db/llm_usage.py` next door swallows its errors
because broken analytics must not break generation; the opposite holds here,
where a silently-dropped write means a customer paid and got nothing.
"""
from __future__ import annotations

import logging

from app.db.client import require_client, retry_on_disconnect

logger = logging.getLogger(__name__)

_BILLING_COLUMNS = (
    "id, slug, display_name, plan, credit_balance, credits_granted_for, "
    "stripe_customer_id, stripe_subscription_id, subscription_status, "
    # `onboarding_completed_at` is not a billing column, but the checkout route
    # needs it: the trial is an ONBOARDING offer, so a company that has finished
    # signup pays on the day it buys.
    "current_period_end, first_paid_at, onboarding_completed_at"
)

# Columns a caller may patch. An allow-list rather than a pass-through so a
# stray key in a webhook payload can never write to an unrelated column.
_WRITABLE = frozenset(
    {
        "plan",
        "credits_granted_for",
        "stripe_customer_id",
        "stripe_subscription_id",
        "subscription_status",
        "current_period_end",
        "first_paid_at",
    }
)


@retry_on_disconnect
def get_billing(company_id: str) -> dict | None:
    rows = (
        require_client()
        .table("companies")
        .select(_BILLING_COLUMNS)
        .eq("id", company_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


@retry_on_disconnect
def set_billing(company_id: str, patch: dict) -> None:
    """Write billing columns. Silently drops keys outside the allow-list."""
    payload = {k: v for k, v in patch.items() if k in _WRITABLE}
    if not payload:
        return
    require_client().table("companies").update(payload).eq("id", company_id).execute()


@retry_on_disconnect
@retry_on_disconnect
def record_subscription_event(
    company_id: str,
    *,
    plan: str,
    status: str | None,
    previous_plan: str | None,
    previous_status: str | None,
    stripe_subscription_id: str | None = None,
    current_period_end: str | None = None,
    source: str | None = None,
) -> None:
    """Append one row to the subscription history.

    BEST EFFORT, and deliberately so: this is a record of what happened, not
    part of making it happen. A company whose history row fails to write must
    still have its subscription synced — losing an audit line is bad, refusing
    someone's paid-for access because we could not write one is worse.
    """
    try:
        require_client().table("subscription_events").insert(
            {
                "company_id": company_id,
                "plan": plan,
                "status": status,
                "previous_plan": previous_plan,
                "previous_status": previous_status,
                "stripe_subscription_id": stripe_subscription_id,
                "current_period_end": current_period_end,
                "source": source,
            }
        ).execute()
    except Exception:
        logger.warning(
            "subscription_event_not_recorded company=%s plan=%s status=%s",
            company_id,
            plan,
            status,
            exc_info=True,
        )


def list_subscription_events(company_id: str, *, limit: int = 50) -> list[dict]:
    """One company's billing history, newest first."""
    return (
        require_client()
        .table("subscription_events")
        .select(
            "id, plan, status, previous_plan, previous_status, "
            "stripe_subscription_id, current_period_end, source, created_at"
        )
        .eq("company_id", company_id)
        # Ordered by ID, not by timestamp. `created_at` has second resolution
        # on the test fake, so two transitions in the same second tie and
        # "newest first" becomes arbitrary — which is exactly the case this
        # table exists to record (a purchase and its follow-up events land
        # together). The id is monotonic and means the same thing.
        .order("id", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )


@retry_on_disconnect
def record_invoice(company_id: str, invoice: dict) -> None:
    """Record one PAID invoice. Idempotent on the Stripe invoice id.

    Stripe retries a webhook for days, so the guard has to be the INVOICE id,
    not the event id: two different events (`invoice.paid` and a later
    `invoice.payment_succeeded`, or one redelivery) describe the same payment
    and must not produce two rows on a page a customer reads as their receipts.

    Best effort, like the history: a failed write loses a receipt line, which is
    bad; refusing to record the payment itself would be worse.
    """
    try:
        require_client().table("subscription_invoices").insert(invoice).execute()
    except Exception as exc:
        text = f"{type(exc).__name__} {exc}".lower()
        if "unique" in text or "duplicate" in text or "23505" in text:
            # The redelivery this exists to absorb. Not worth a warning.
            return
        logger.warning(
            "invoice_not_recorded company=%s invoice=%s",
            company_id,
            invoice.get("stripe_invoice_id"),
            exc_info=True,
        )


def list_invoices(company_id: str, *, limit: int = 50) -> list[dict]:
    """One company's payments, newest first — the money record."""
    return (
        require_client()
        .table("subscription_invoices")
        .select(
            "id, stripe_invoice_id, plan, amount_paid_cents, currency, status, "
            "period_start, period_end, paid_at, invoice_number, "
            "hosted_invoice_url, invoice_pdf_url"
        )
        .eq("company_id", company_id)
        .order("id", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )


def company_id_for_stripe_customer(customer_id: str) -> str | None:
    """Resolve a Stripe customer back to our tenant.

    The primary path for every webhook. When it misses — a customer created
    outside this app, or a race where the event beats our own write — the
    caller falls back to `metadata.company_id`, which we stamp on the customer
    at creation. Never resolve a tenant from anything else in the payload.
    """
    rows = (
        require_client()
        .table("companies")
        .select("id")
        .eq("stripe_customer_id", customer_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0]["id"] if rows else None


@retry_on_disconnect
def claim_stripe_event(event_id: str, event_type: str) -> bool:
    """Record a webhook event id. Returns False if we already processed it.

    Stripe retries a webhook for up to three days until it gets a 2xx and
    states plainly that delivery is at-least-once and unordered. Each handler
    is independently idempotent, so this is the cheap outer guard rather than
    the only one — belt and braces on a path where a double-apply hands out
    free credits.
    """
    try:
        require_client().table("stripe_events").insert(
            {"id": event_id, "type": event_type}
        ).execute()
        return True
    except Exception as exc:  # noqa: BLE001 — a duplicate is the expected case
        text = f"{type(exc).__name__} {exc}".lower()
        if "unique" in text or "duplicate" in text or "23505" in text:
            logger.info("stripe_event_replayed id=%s type=%s", event_id, event_type)
            return False
        raise


@retry_on_disconnect
def list_billing_for_staff() -> list[dict]:
    """Every company's billing state, for the staff admin panel."""
    return (
        require_client()
        .table("companies")
        .select(_BILLING_COLUMNS)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )
