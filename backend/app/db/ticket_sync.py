"""Per-artifact ticket-tracker sync state (the `prd_ticket_sync` table).

One row per (company, PRD) OR (company, standalone ticket set) — a
`TicketScope`, see app/stories/scope.py. The row records which tracker that
artifact's tickets sync to (a ClickUp list, Jira project or Asana project — one
tool at a time), the last sync outcome, and the pulled per-ticket tracker state.
It is created by the first manual push (the user picks the destination); the
scheduler's ticket_sync job then two-way syncs every row with auto_sync=true,
and the web's sync button / MCP reads resolve state from here without a live
tracker call.

The table keeps its `prd_ticket_sync` name — renaming a live table binding real
customers' trackers buys nothing — but it is no longer PRD-only: standalone
ticket sets own rows here too, via the mutually-exclusive `ticket_set_id`
column added in 20260806120000_ticket_sets.sql. Every function below takes a
scope rather than a bare `prd_id`, deliberately: a `prd_id`-shaped wrapper left
lying around is exactly the thing that later gets handed a set id and writes one
artifact's destination over another's.
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.client import require_client, retry_on_disconnect, utc_now
from app.stories.scope import TicketScope

logger = logging.getLogger(__name__)

# A sync stuck in 'syncing' longer than this is treated as dead (crashed
# process) — the next trigger/tick may take over.
STALE_SYNC_MINUTES = 10


@retry_on_disconnect
def get_sync_config(company_id: str, scope: TicketScope) -> dict[str, Any] | None:
    """This artifact's sync row, or None when its tickets were never pushed."""
    resp = (
        scope.filter_sync(
            require_client().table("prd_ticket_sync")
            .select("*")
            .eq("company_id", company_id)
        )
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None


@retry_on_disconnect
def list_sync_configs(company_id: str) -> list[dict[str, Any]]:
    """All of one company's sync rows, PRD- and set-owned alike (the MCP list
    view joins these)."""
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


def scope_of_config(cfg: dict[str, Any]) -> TicketScope | None:
    """The scope a sync row belongs to, or None when it names neither owner.

    The scheduler reads rows it did not write, so it has to be able to ask a row
    what it is. None is fail-closed: a row with no owner (impossible under the
    `prd_ticket_sync_one_owner` check, but the scheduler must not crash on data
    it did not validate) is skipped rather than guessed at.
    """
    if cfg.get("prd_id") is not None:
        return TicketScope("prd", int(cfg["prd_id"]))
    if cfg.get("ticket_set_id") is not None:
        return TicketScope("set", int(cfg["ticket_set_id"]))
    return None


@retry_on_disconnect
def upsert_sync_config(
    company_id: str,
    scope: TicketScope,
    *,
    provider: str,
    destination_id: str,
    destination_name: str | None = None,
) -> None:
    """Set (or replace) the artifact's sync destination. Switching tools or
    destinations overwrites the row — one active tracker per artifact at a time.

    The conflict target is the scope's OWN unique index (`company_id,prd_id` or
    `company_id,ticket_set_id`). Both are non-partial so Postgres can infer
    them; a partial index here would not be nameable through PostgREST and the
    upsert would silently insert a duplicate destination row per push.
    """
    require_client().table("prd_ticket_sync").upsert(
        {
            "company_id": company_id,
            **scope.sync_keys(),
            "provider": provider,
            "destination_id": destination_id,
            "destination_name": destination_name,
            "auto_sync": True,
            "updated_at": utc_now(),
        },
        on_conflict=f"company_id,{scope.column}",
    ).execute()


@retry_on_disconnect
def mark_syncing(company_id: str, scope: TicketScope) -> None:
    """Stamp a sync run as started (the UI shows 'Syncing…' off this)."""
    scope.filter_sync(
        require_client().table("prd_ticket_sync").update(
            {
                "sync_status": "syncing",
                "sync_started_at": utc_now(),
                "updated_at": utc_now(),
            }
        ).eq("company_id", company_id)
    ).execute()


@retry_on_disconnect
def save_sync_result(
    company_id: str,
    scope: TicketScope,
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
    scope.filter_sync(
        require_client().table("prd_ticket_sync").update(patch)
        .eq("company_id", company_id)
    ).execute()
