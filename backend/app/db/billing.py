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
    "current_period_end, first_paid_at"
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
