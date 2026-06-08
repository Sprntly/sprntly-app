"""backlog_items store — the sequenced, ranked product backlog.

One row = one theme NOT in the current weekly brief, carrying its rank, score,
and a one-line triage rationale. The unique key (enterprise_id, theme_id) makes
re-sequencing idempotent: a re-run of synthesis upserts each theme in place
(refreshing its rank/score) rather than appending duplicates.
"""
from __future__ import annotations

import uuid
from typing import Optional

from app.db.client import require_client, utc_now

# Statuses a backlog item can move through. 'backlog' is the default landing
# state; the other three are user-driven transitions (PATCH route).
STATUS_BACKLOG = "backlog"
ALLOWED_STATUSES = ("backlog", "in_progress", "done", "dismissed")
# Statuses a PATCH may move an item INTO (you don't re-set 'backlog' by hand).
PATCHABLE_STATUSES = ("in_progress", "done", "dismissed")


def upsert_backlog_item(
    enterprise_id: str,
    *,
    theme_id: str,
    title: str,
    rank: int,
    score: float,
    tag: Optional[str] = None,
    hypothesis_id: Optional[str] = None,
    reasoning: Optional[str] = None,
    client=None,
) -> None:
    """Insert-or-refresh one backlog item, idempotent on (enterprise_id, theme_id).

    A re-run of the sequencer refreshes rank/score/reasoning in place and bumps
    updated_at; the user-owned `status` is intentionally NOT overwritten so a
    re-sequence never silently resets a 'done'/'dismissed'/'in_progress' item
    back to 'backlog'. `id`/`created_at` are only honored on first insert (the
    upsert's ON CONFLICT update sets the listed columns only).
    """
    cli = client or require_client()
    now = utc_now()
    cli.table("backlog_items").upsert(
        {
            "id": str(uuid.uuid4()),
            "enterprise_id": enterprise_id,
            "theme_id": theme_id,
            "hypothesis_id": hypothesis_id,
            "title": title,
            "tag": tag,
            "rank": int(rank),
            "score": float(score),
            "reasoning": reasoning,
            "updated_at": now,
            "created_at": now,
        },
        on_conflict="enterprise_id,theme_id",
    ).execute()


def list_backlog_items(
    enterprise_id: str,
    *,
    statuses: Optional[tuple[str, ...]] = None,
    client=None,
) -> list[dict]:
    """All backlog items for an enterprise, sorted ascending by rank.

    `statuses` optionally filters to a subset. Ordering is done in Python so it
    behaves identically against real Supabase and the in-memory test fake;
    per-enterprise volumes are tiny (one row per non-brief theme)."""
    cli = client or require_client()
    rows = (
        cli.table("backlog_items").select("*")
        .eq("enterprise_id", enterprise_id).execute().data or []
    )
    if statuses is not None:
        rows = [r for r in rows if r.get("status") in statuses]
    rows.sort(key=lambda r: r.get("rank", 0))
    return rows


def get_backlog_item(
    enterprise_id: str, item_id: str, *, client=None
) -> Optional[dict]:
    """One backlog item by id, scoped to the enterprise (tenant isolation)."""
    cli = client or require_client()
    rows = (
        cli.table("backlog_items").select("*")
        .eq("enterprise_id", enterprise_id).eq("id", item_id)
        .execute().data or []
    )
    return rows[0] if rows else None


def update_backlog_status(
    enterprise_id: str, item_id: str, status: str, *, client=None
) -> Optional[dict]:
    """Move one item to a new status, scoped to the enterprise. Returns the
    updated row, or None if no such item exists for this tenant."""
    cli = client or require_client()
    if get_backlog_item(enterprise_id, item_id, client=cli) is None:
        return None
    (
        cli.table("backlog_items")
        .update({"status": status, "updated_at": utc_now()})
        .eq("enterprise_id", enterprise_id).eq("id", item_id)
        .execute()
    )
    return get_backlog_item(enterprise_id, item_id, client=cli)
