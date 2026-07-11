"""Per-PRD ticket-tracker sync state (the `prd_ticket_sync` table).

One row per (company, prd): which tracker the PRD's tickets sync to (a ClickUp
list or Jira project — one tool at a time), the last sync outcome, and the
pulled per-ticket tracker state. The row is created by the first manual push
(the user picks the destination); the scheduler's ticket_sync job then two-way
syncs every row with auto_sync=true, and the web's sync button / MCP reads
resolve state from here without a live tracker call.
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.client import require_client, retry_on_disconnect, utc_now

logger = logging.getLogger(__name__)

# A sync stuck in 'syncing' longer than this is treated as dead (crashed
# process) — the next trigger/tick may take over.
STALE_SYNC_MINUTES = 10


@retry_on_disconnect
def get_sync_config(company_id: str, prd_id: int) -> dict[str, Any] | None:
    """This PRD's sync row, or None when tickets were never pushed."""
    resp = (
        require_client().table("prd_ticket_sync")
        .select("*")
        .eq("company_id", company_id).eq("prd_id", prd_id)
        .limit(1).execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None


@retry_on_disconnect
def list_sync_configs(company_id: str) -> list[dict[str, Any]]:
    """All of one company's sync rows (the MCP list view joins these)."""
    resp = (
        require_client().table("prd_ticket_sync")
        .select("*")
        .eq("company_id", company_id)
        .execute()
    )
    return resp.data or []


@retry_on_disconnect
def list_auto_sync_configs() -> list[dict[str, Any]]:
    """Every auto_sync row across ALL companies — the scheduler's work list."""
    resp = (
        require_client().table("prd_ticket_sync")
        .select("*")
        .eq("auto_sync", True)
        .execute()
    )
    return resp.data or []


@retry_on_disconnect
def upsert_sync_config(
    company_id: str,
    prd_id: int,
    *,
    provider: str,
    destination_id: str,
    destination_name: str | None = None,
) -> None:
    """Set (or replace) the PRD's sync destination. Switching tools/destinations
    overwrites the row — one active tracker per PRD at a time."""
    require_client().table("prd_ticket_sync").upsert(
        {
            "company_id": company_id,
            "prd_id": prd_id,
            "provider": provider,
            "destination_id": destination_id,
            "destination_name": destination_name,
            "auto_sync": True,
            "updated_at": utc_now(),
        },
        on_conflict="company_id,prd_id",
    ).execute()


@retry_on_disconnect
def mark_syncing(company_id: str, prd_id: int) -> None:
    """Stamp a sync run as started (the UI shows 'Syncing…' off this)."""
    require_client().table("prd_ticket_sync").update(
        {
            "sync_status": "syncing",
            "sync_started_at": utc_now(),
            "updated_at": utc_now(),
        }
    ).eq("company_id", company_id).eq("prd_id", prd_id).execute()


@retry_on_disconnect
def save_sync_result(
    company_id: str,
    prd_id: int,
    *,
    statuses: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Record a finished sync run: back to idle, last_synced_at stamped on
    success, last_error kept (or cleared) either way. `statuses` replaces the
    stored per-ticket tracker state only when the pull produced one."""
    patch: dict[str, Any] = {
        "sync_status": "idle",
        "last_error": error,
        "updated_at": utc_now(),
    }
    if error is None:
        patch["last_synced_at"] = utc_now()
    if statuses is not None:
        patch["statuses"] = statuses
    require_client().table("prd_ticket_sync").update(patch).eq(
        "company_id", company_id
    ).eq("prd_id", prd_id).execute()
