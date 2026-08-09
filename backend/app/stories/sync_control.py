"""Shared read/trigger logic behind the tracker-sync routes.

Two surfaces expose ticket sync — `/v1/stories/sync/{prd_id}` for a PRD and
`/v1/ticket-sets/{set_id}/sync` for a standalone set — and they must behave
IDENTICALLY: the same eligibility check, the same destination upsert, the same
metadata warm, the same single-flight guard, the same public state shape. Two
copies of that would drift, and the thing they drive writes to and deletes from
customers' live Jira/ClickUp/Asana projects.

So the routes are thin argument-shufflers over the functions here, differing
only in how they resolve their `TicketScope` and tenant-gate it.
"""
from __future__ import annotations

import asyncio
import logging

from app.stories.scope import TicketScope

logger = logging.getLogger(__name__)

# Strong refs to background tasks. asyncio only holds weak references, so a
# task that isn't retained can be garbage-collected mid-flight — the same
# pattern (and reason) as routes/stories.py's own set.
_inflight_tasks: set[asyncio.Task] = set()


def _track(task: asyncio.Task) -> asyncio.Task:
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
    return task


def public_sync_state(cfg: dict | None) -> dict:
    """The sync row as the web/MCP read it. A 'syncing' older than the stale
    window reports as idle so a crashed run never wedges the button."""
    from app.stories.sync import sync_in_flight

    if cfg is None:
        return {"configured": False}
    return {
        "configured": True,
        "provider": cfg.get("provider"),
        "destination_id": cfg.get("destination_id"),
        "destination_name": cfg.get("destination_name"),
        "auto_sync": bool(cfg.get("auto_sync")),
        "sync_status": "syncing" if sync_in_flight(cfg) else "idle",
        "last_synced_at": cfg.get("last_synced_at"),
        "last_error": cfg.get("last_error"),
        "statuses": cfg.get("statuses") or {},
    }


async def trigger_scope_sync(
    company_id: str,
    scope: TicketScope,
    *,
    provider: str | None,
    destination_id: str | None,
    destination_name: str | None,
    unconfigured_message: str,
) -> dict:
    """Run a two-way sync pass for one artifact's tickets, in the background.

    First push: pass `provider` + `destination_id` to register the destination —
    the same call switches an already-bound artifact to a different tool. With
    neither, the configured destination re-syncs. Raises HTTPException(400) for
    a half-specified or ineligible destination and (404) when nothing is
    configured and none was given.
    """
    from fastapi import HTTPException

    from app.db.ticket_sync import get_sync_config, mark_syncing, upsert_sync_config
    from app.stories.sync import run_ticket_sync, sync_in_flight, ticket_sync_providers

    if (provider is None) != (destination_id is None):
        raise HTTPException(400, "provider and destination_id go together")
    if provider is not None:
        # Eligibility is type-driven: the provider must be a task-management
        # connector (app/connectors/catalog.py) the sync engine implements.
        if provider not in ticket_sync_providers():
            raise HTTPException(
                400,
                f"{provider!r} is not a task-management connector tickets can sync with",
            )
        upsert_sync_config(
            company_id, scope,
            provider=provider,
            destination_id=destination_id,
            destination_name=destination_name,
        )

    cfg = get_sync_config(company_id, scope)
    if cfg is None:
        raise HTTPException(404, unconfigured_message)

    # EVERY sync trigger (first bind AND the ad-hoc Sync button) refreshes the
    # destination's vocabulary cache — statuses / priorities / custom fields /
    # built-ins re-pulled from the tracker so tracker_meta reflects workspace
    # changes now, not at the 6h TTL. Best-effort in the background — a
    # metadata failure must never block the sync itself.
    async def _warm_meta(p: str = cfg["provider"], d: str = cfg["destination_id"]) -> None:
        from app.db.tracker_meta import get_or_fetch_meta

        try:
            await asyncio.to_thread(
                get_or_fetch_meta, company_id, p, d, refresh=True
            )
        except Exception:  # noqa: BLE001 — cache warming is best-effort
            logger.warning("tracker-meta warm failed for %s %s", scope.kind, scope.id)

    _track(asyncio.create_task(_warm_meta()))

    if sync_in_flight(cfg):
        return {"status": "syncing"}

    # Mark before spawning so a GET between this response and the thread
    # starting already reads "syncing" (no idle flash in the UI).
    mark_syncing(company_id, scope)

    async def _run() -> None:
        try:
            await asyncio.to_thread(run_ticket_sync, company_id, scope)
        except Exception:  # noqa: BLE001 — recorded on the row by run_ticket_sync
            logger.exception("ad-hoc ticket sync failed for %s %s",
                             scope.kind, scope.id)

    _track(asyncio.create_task(_run()))
    return {"status": "syncing"}
