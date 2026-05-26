"""Comprehensive Brief cache.

Spec source: Master PRD §4.2 — "Comprehensive output is cached until
the next Monday morning run." Key is `(workspace_id, week_start_iso)`
where `week_start_iso` is the ISO-8601 date of the most recent Monday
00:00 UTC. The next Monday's scheduled run produces a fresh row; the
old rows stay for history.

This mirrors the `cached_asks` pattern (status / response / generated_at)
but keys by week, not question. Live deployments add a Supabase
migration that creates `cached_briefs`; for tests we add the same DDL
to `_FAKE_SCHEMA` in conftest.py.

The on-disk shape (jsonb in Postgres, TEXT in the fake) holds the
serialized `Brief.model_dump(mode="json")`. Round-tripping is the
caller's job (`Brief.model_validate(...)`) so this module stays a
thin store wrapper.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.db.client import require_client

logger = logging.getLogger(__name__)


def week_start_iso(now: Optional[datetime] = None) -> str:
    """ISO date string for the Monday-of-this-week in UTC.

    Why Monday-of-this-week rather than "next Monday":
        the scheduler runs ON Monday morning. Before it fires, the
        cache for *this week* doesn't yet exist; after it fires, the
        cache for this week is hot until next Monday's run overwrites
        it. Manual triggers between Monday runs hit the cache.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    # Monday=0, Sunday=6. Anchor to UTC date.
    today = now.astimezone(timezone.utc).date()
    monday: date = today - timedelta(days=today.weekday())
    return monday.isoformat()


def get_cached_brief(
    workspace_id: str, week_start: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """Return the cached Brief payload (JSON dict) for the given week,
    or None on miss. `status='ready'` rows only — partial rows from
    in-flight generations are treated as misses.
    """
    if week_start is None:
        week_start = week_start_iso()
    c = require_client()
    resp = (
        c.table("cached_briefs")
        .select("*")
        .eq("workspace_id", workspace_id)
        .eq("week_start", week_start)
        .eq("status", "ready")
        .order("generated_at", desc=True)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    row = resp.data[0]
    payload = row.get("payload")
    # Supabase jsonb returns a dict; the fake stores TEXT — handle both.
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning(
                "cached_briefs row %s has malformed payload; treating as miss",
                row.get("id"),
            )
            return None
    return payload


def save_cached_brief(
    workspace_id: str,
    week_start: str,
    brief_payload: dict[str, Any],
    *,
    dataset_slug: Optional[str] = None,
) -> int:
    """Upsert the cache row for (workspace_id, week_start).

    "Upsert" because a manual /comprehensive/regenerate that follows a
    failed scheduled run should overwrite the prior failed row, and a
    Monday run that follows last week's must obviously not collide on
    the unique key. We delete-then-insert to keep this transparent (no
    DB-specific UPSERT syntax in the fake).
    """
    c = require_client()
    c.table("cached_briefs").delete().eq("workspace_id", workspace_id).eq(
        "week_start", week_start
    ).execute()
    # Postgres can store dicts directly via jsonb; the fake stores TEXT,
    # so we JSON-encode here for portability. Supabase-py serializes the
    # value transparently either way.
    resp = c.table("cached_briefs").insert({
        "workspace_id": workspace_id,
        "week_start": week_start,
        "dataset_slug": dataset_slug,
        "payload": json.dumps(brief_payload, default=str),
        "status": "ready",
    }).execute()
    return resp.data[0]["id"]


__all__ = [
    "get_cached_brief",
    "save_cached_brief",
    "week_start_iso",
]
