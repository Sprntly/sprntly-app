"""DB helpers for the `design_systems` cache.

One cached, normalized design system per (company_id, source_provider,
source_ref). That triple is the cache key: a company's Figma file, code
repository, and website each get their own row, and re-extracting the same
source overwrites the existing row in place rather than piling up duplicates.

Mirrors the synchronous helper shape used across `app.db.*` (supabase-py is a
synchronous client): get the client via `require_client()`, filter every
user-driven query by `company_id`, and log identifiers only.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from app.db.client import require_client, utc_now

logger = logging.getLogger(__name__)

_TABLE = "design_systems"


def lookup_design_system(
    company_id: str,
    source_provider: str,
    source_ref: str,
) -> dict[str, Any] | None:
    """Return the cached design system for the (company, provider, source)
    triple, or None if nothing is cached yet. Company-scoped."""
    c = require_client()
    resp = (
        c.table(_TABLE)
        .select("*")
        .eq("company_id", company_id)
        .eq("source_provider", source_provider)
        .eq("source_ref", source_ref)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def upsert_design_system(
    *,
    company_id: str,
    source_category: str,
    source_provider: str,
    source_ref: str,
    source_version: str | None,
    data: dict[str, Any],
    has_explicit_system: bool | None,
    confidence: str | None,
    extracted_at: str | None,
) -> dict[str, Any]:
    """Insert or refresh the cached design system for a source.

    Upserts on the (company_id, source_provider, source_ref) unique key, so a
    fresh extraction of an already-cached source overwrites it in place. `data`
    is the normalized DesignSystem object, stored as jsonb. Keyword-only args
    (the `*`) prevent positional confusion between the several text columns.
    Returns the stored row.
    """
    c = require_client()
    now = utc_now()
    existing = lookup_design_system(company_id, source_provider, source_ref)
    payload: dict[str, Any] = {
        "company_id": company_id,
        "source_category": source_category,
        "source_provider": source_provider,
        "source_ref": source_ref,
        "source_version": source_version,
        "data": data,
        "has_explicit_system": has_explicit_system,
        "confidence": confidence,
        "extracted_at": extracted_at,
        "updated_at": now,
    }
    if not existing:
        payload["id"] = uuid.uuid4().hex
        payload["created_at"] = now
    c.table(_TABLE).upsert(
        payload, on_conflict="company_id,source_provider,source_ref"
    ).execute()
    row = lookup_design_system(company_id, source_provider, source_ref)
    assert row is not None
    logger.info(
        "design_system_cached company_id=%s source_provider=%s confidence=%s",
        company_id, source_provider, confidence,
    )
    return row


def mark_github_design_systems_stale(repo_full_name: str) -> int:
    """Mark every cached GitHub-sourced design system for a repository stale.

    Called from the GitHub push webhook: a push to a connected repo means any
    design system extracted from that repo's code may now be out of date, so the
    next generation re-extracts instead of serving the cached row. Matches by
    source_ref across companies — the webhook is installation-scoped, not
    company-scoped, and source_ref already encodes the repo as "owner/repo" or
    "owner/repo@branch". Returns the number of rows marked stale.
    """
    cleaned = (repo_full_name or "").strip()
    if not cleaned:
        return 0
    c = require_client()
    rows = (
        c.table(_TABLE)
        .select("id,source_ref")
        .eq("source_provider", "github")
        .execute()
    ).data or []
    branch_prefix = cleaned + "@"
    stale_ids = [
        r["id"]
        for r in rows
        if r.get("source_ref") == cleaned
        or str(r.get("source_ref") or "").startswith(branch_prefix)
    ]
    if stale_ids:
        c.table(_TABLE).update(
            {"status": "stale", "updated_at": utc_now()}
        ).in_("id", stale_ids).execute()
    logger.info(
        "design_system_marked_stale repo=%s count=%d", cleaned, len(stale_ids)
    )
    return len(stale_ids)
