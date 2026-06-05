# UX-EXPLORE (throwaway — REVERT): onboarding-website-as-design-source fallback.
# New db helper module reading the onboarding `products` table so prototype
# generation can fall back to the company's primary product website when no
# Figma source is connected. Mirrors the supabase-py sync helper pattern in
# the rest of `app/db/` (require_client(), company filtering, identifiers-only
# logging). Schema: supabase/migrations/20260525150300_products.sql.
"""Products — onboarding's per-company product rows (read-only here).

`products` is owned by the onboarding flow (migration 20260525150300_products.sql);
each company gets a primary product carrying the brand `website`. This module only
*reads* it, to source a design system when generation has no Figma file.
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
