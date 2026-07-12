"""Cached per-destination tracker vocabulary (the `tracker_meta` table).

One row per (company, provider, destination): the normalized TrackerMeta
snapshot (app/connectors/tracker_meta.py) of a ClickUp list's / Jira
project's statuses, priorities, issue types, and custom-field definitions.
Written on destination bind and refreshed when older than the TTL, so the
ticket UI and the sync engine read a customer's real vocabulary without a
live tracker round-trip on every request.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.client import require_client, retry_on_disconnect, utc_now

logger = logging.getLogger(__name__)

#: How long a cached snapshot serves before a sync pass / meta read re-fetches.
#: Cheap staleness window: workflow/field edits in the tracker are rare, and a
#: rename self-heals at the next TTL expiry (or a push 400 → forced refresh).
META_TTL_HOURS = 6


@retry_on_disconnect
def get_cached_meta(
    company_id: str, provider: str, destination_id: str
) -> dict[str, Any] | None:
    """The cached TrackerMeta payload (with its `fetched_at`), or None when
    the destination was never fetched. Never triggers a live tracker call —
    the sync engine uses this so a metadata gap degrades to the legacy
    heuristics instead of blocking a pass."""
    resp = (
        require_client().table("tracker_meta")
        .select("meta, fetched_at")
        .eq("company_id", company_id)
        .eq("provider", provider)
        .eq("destination_id", destination_id)
        .limit(1).execute()
    )
    rows = resp.data or []
    if not rows:
        return None
    meta = rows[0].get("meta") or {}
    # The row column is the authoritative fetch stamp (save_meta writes it);
    # any fetched_at inside the payload is just the connector's fetch-time
    # echo and must not shadow it for TTL checks.
    if rows[0].get("fetched_at"):
        meta["fetched_at"] = rows[0]["fetched_at"]
    return meta or None


@retry_on_disconnect
def get_newest_cached_meta(
    company_id: str, provider: str
) -> dict[str, Any] | None:
    """The most recently fetched cached meta for ANY of the provider's
    destinations — the unbound-PRD fallback: someone just connected the
    tracker (connect-time warm populated the cache) but hasn't pushed yet,
    and the ticket detail should already speak their vocabulary. The payload
    carries its own destination_id."""
    resp = (
        require_client().table("tracker_meta")
        .select("meta, fetched_at")
        .eq("company_id", company_id)
        .eq("provider", provider)
        .order("fetched_at", desc=True)
        .limit(1).execute()
    )
    rows = resp.data or []
    if not rows:
        return None
    meta = rows[0].get("meta") or {}
    if rows[0].get("fetched_at"):
        meta["fetched_at"] = rows[0]["fetched_at"]
    return meta or None


@retry_on_disconnect
def save_meta(
    company_id: str, provider: str, destination_id: str, meta: dict[str, Any]
) -> None:
    """Upsert a destination's snapshot (one row per triple)."""
    require_client().table("tracker_meta").upsert(
        {
            "company_id": company_id,
            "provider": provider,
            "destination_id": destination_id,
            "meta": meta,
            "fetched_at": utc_now(),
        },
        on_conflict="company_id,provider,destination_id",
    ).execute()


def _is_fresh(meta: dict[str, Any], max_age_hours: float) -> bool:
    raw = meta.get("fetched_at")
    if not raw:
        return False
    try:
        fetched = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched < timedelta(hours=max_age_hours)


def get_or_fetch_meta(
    company_id: str,
    provider: str,
    destination_id: str,
    *,
    max_age_hours: float = META_TTL_HOURS,
    refresh: bool = False,
) -> dict[str, Any] | None:
    """The destination's TrackerMeta: cache when fresh, else a live fetch
    (persisted for next time). `refresh=True` forces the live fetch (used on
    destination bind and the routes' ?refresh=1 escape hatch).

    A failed live fetch (tracker down, token expired, …) falls back to the
    stale cache when one exists, else returns None — metadata must degrade,
    never block the caller."""
    cached = None
    try:
        cached = get_cached_meta(company_id, provider, destination_id)
    except Exception:  # noqa: BLE001 — cache read failure ≠ no metadata
        logger.warning(
            "tracker_meta cache read failed for %s/%s/%s",
            company_id, provider, destination_id,
        )
    if cached and not refresh and _is_fresh(cached, max_age_hours):
        return cached

    from app.connectors.tracker_meta import fetch_tracker_meta

    try:
        meta = fetch_tracker_meta(company_id, provider, destination_id)
    except Exception:  # noqa: BLE001 — stale meta beats no meta
        logger.warning(
            "tracker_meta fetch failed for %s/%s/%s — %s",
            company_id, provider, destination_id,
            "serving stale cache" if cached else "no cache to fall back to",
        )
        return cached
    try:
        save_meta(company_id, provider, destination_id, meta)
    except Exception:  # noqa: BLE001 — persisting is best-effort
        logger.warning(
            "tracker_meta save failed for %s/%s/%s",
            company_id, provider, destination_id,
        )
    return meta
