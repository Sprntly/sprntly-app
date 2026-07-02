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
    back to 'backlog'. `created_at` is intentionally omitted from the payload so
    the DB default sets it once on first insert and a re-upsert can't overwrite
    it; `id` is only used for the first insert (ON CONFLICT keeps the existing).
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
        },
        on_conflict="enterprise_id,theme_id",
    ).execute()


def create_manual_backlog_item(
    enterprise_id: str,
    *,
    title: str,
    tag: Optional[str] = None,
    client=None,
) -> dict:
    """Create a USER-ADDED backlog item (the "+ Add idea" flow).

    Unlike synthesis-produced rows, a manual item has no KG theme behind it, so
    its `theme_id` is a synthetic ``manual:<uuid>`` (kept unique so it never
    collides with a real theme or another manual row, and so the (enterprise_id,
    theme_id) unique key holds). It lands at the end of the ranking (max rank + 1)
    with a zero score. Returns the created row.
    """
    cli = client or require_client()
    now = utc_now()
    existing = list_backlog_items(enterprise_id, client=cli)
    next_rank = max((r.get("rank", 0) for r in existing), default=0) + 1
    item_id = str(uuid.uuid4())
    cli.table("backlog_items").insert(
        {
            "id": item_id,
            "enterprise_id": enterprise_id,
            "theme_id": f"manual:{uuid.uuid4()}",
            "title": title,
            "tag": tag,
            "rank": next_rank,
            "score": 0.0,
            "status": STATUS_BACKLOG,
            "updated_at": now,
        }
    ).execute()
    return get_backlog_item(enterprise_id, item_id, client=cli)


def reorder_backlog_items(
    enterprise_id: str, ordered_ids: list[str], *, client=None
) -> list[dict]:
    """Persist a new manual rank order (drag-to-rerank / Re-sequence).

    `ordered_ids` is the full desired order; each listed item gets
    ``rank = position + 1``. Ids that don't belong to this enterprise are ignored
    (tenant isolation). Items not in the list keep their current rank — the
    frontend always sends the complete visible order, so this is a full rewrite
    in practice. Returns the reordered list (rank-ascending)."""
    cli = client or require_client()
    owned = {r["id"] for r in list_backlog_items(enterprise_id, client=cli)}
    now = utc_now()
    for idx, item_id in enumerate(ordered_ids):
        if item_id not in owned:
            continue
        (
            cli.table("backlog_items")
            .update({"rank": idx + 1, "updated_at": now})
            .eq("enterprise_id", enterprise_id).eq("id", item_id)
            .execute()
        )
    return list_backlog_items(enterprise_id, client=cli)


def prune_stale_backlog(
    enterprise_id: str, keep_theme_ids, *, client=None
) -> int:
    """Delete auto-generated backlog rows whose theme is NOT in the current
    converged set, so a re-sequence REPLACES the auto backlog instead of
    APPENDING. Returns the number of rows removed.

    Only ``status='backlog'`` rows are pruned — user-managed items
    (``in_progress``/``done``/``dismissed``) are always preserved. Themes still
    converging keep their row (the caller upserts them in place, idempotent on
    (enterprise_id, theme_id)); everything else auto-generated is removed. This
    is what stops the backlog growing without bound when a theme drops out of
    convergence or the KG re-extraction gives it a fresh id across runs.

    Filtered in Python (fetch → diff → delete-by-id) so it behaves identically
    against real Supabase and the in-memory test fake; per-enterprise volumes are
    tiny (one row per non-brief theme).
    """
    cli = client or require_client()
    keep = {str(t) for t in keep_theme_ids if t}
    rows = (
        cli.table("backlog_items").select("id,theme_id,status")
        .eq("enterprise_id", enterprise_id).eq("status", "backlog")
        .execute().data or []
    )
    stale = [r["id"] for r in rows if str(r.get("theme_id")) not in keep]
    if not stale:
        return 0
    (
        cli.table("backlog_items").delete()
        .eq("enterprise_id", enterprise_id).in_("id", stale)
        .execute()
    )
    return len(stale)


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
