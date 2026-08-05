# Read-only helper for the onboarding `products` table so prototype generation
# can fall back to the company's primary product website when no Figma source is
# connected. Mirrors the supabase-py sync helper pattern in the rest of `app/db/`
# (require_client(), company filtering, identifiers-only logging).
# Schema: supabase/migrations/20260525150300_products.sql.
"""Products — onboarding's per-company product rows (read-only here).

`products` is owned by the onboarding flow (migration 20260525150300_products.sql);
each company gets a primary product carrying the brand `website`. This module
*reads* it for two purposes: sourcing a design system when generation has no
Figma file (`get_company_website`), and rendering the workspace's self-reported
name/website into the Ask answer prompt (`get_primary_product`, app.ask_runner).
"""
from __future__ import annotations

import logging

from app.db.client import require_client

logger = logging.getLogger(__name__)


def get_company_website(company_id: str) -> str | None:
    """Return the company's primary-product website URL, or None.

    Selects `website` from `products` for the given company, preferring the
    `is_primary` row (the onboarding flow writes the brand site there). Falls
    back to the most recent product with a non-empty website if no primary
    row carries one. Returns None when the company has no product, or none of
    its products has a usable website. Identifiers only in logs — never the URL
    value or any PRD content.
    """
    if not company_id:
        return None
    client = require_client()
    rows = (
        client.table("products")
        .select("website, is_primary, created_at")
        .eq("company_id", company_id)
        .order("is_primary", desc=True)   # primary row first
        .order("created_at", desc=True)   # then most-recent
        .execute()
        .data
    ) or []
    for row in rows:
        website = (row.get("website") or "").strip()
        if website:
            return website
    logger.info("design_agent_no_company_website company_id=%s", company_id)
    return None


def get_primary_product(company_id: str) -> dict | None:
    """Return `{"name": str, "website": str}` for the company's ONE product
    row — the `is_primary` row when one exists, else the most-recently
    created (same ordering as `get_company_website`; `LIMIT 1` so this never
    fans out across a company's other products). `website` is `""` when the
    row's website column is empty/NULL — the caller decides what to do with a
    missing website. Returns None when the company has no product row at all.
    """
    if not company_id:
        return None
    rows = (
        require_client()
        .table("products")
        .select("name, website, is_primary, created_at")
        .eq("company_id", company_id)
        .order("is_primary", desc=True)   # primary row first
        .order("created_at", desc=True)   # then most-recent
        .limit(1)
        .execute()
        .data
    ) or []
    if not rows:
        return None
    row = rows[0]
    return {
        "name": (row.get("name") or "").strip(),
        "website": (row.get("website") or "").strip(),
    }
