"""Standalone ticket sets — tickets generated from a chat with NO PRD behind them.

  GET  /v1/ticket-sets/{set_id}                -> the set + its tickets
  GET  /v1/ticket-sets/by-conversation/{cid}   -> the sets born in one chat
  GET  /v1/ticket-sets/{set_id}/sync           -> tracker-sync state
  POST /v1/ticket-sets/{set_id}/sync           -> bind a destination / re-sync
  GET  /v1/ticket-sets/{set_id}/tracker-meta   -> the destination's vocabulary

Generation itself is NOT here: a set is produced by POST /v1/stories/generate's
insight path, which creates the row and returns its `ticket_set_id`. These
routes are the read + tracker surface over the result.

Tenant gate on every route: `require_workspace` resolves the caller's company
from the JWT, and `db.ticket_sets.get_set` filters `company_id` IN THE QUERY, so
a set belonging to another company reads as absent. Both cases raise 404, never
403 — a foreign tenant must not be able to tell "exists but not yours" from
"doesn't exist". RLS is bypassed (service-role key), so this is the ONLY tenant
boundary these routes have.

The sync routes mirror /v1/stories/sync/{prd_id} exactly and share its
implementation (app/stories/sync_control.py) rather than reimplementing it —
they drive the code that creates and deletes issues in customers' live trackers,
and two copies of that logic would drift.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import WorkspaceContext, require_workspace  # noqa: F401 — re-exported for tests' dependency_overrides
from app.db.ticket_sets import get_set, list_sets_for_conversation
from app.stories.scope import set_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ticket-sets", tags=["ticket-sets"])


def _require_owned_set(set_id: int, company_id: str) -> dict:
    """The set, or 404. The company filter lives in the query (see get_set)."""
    row = get_set(company_id, set_id)
    if row is None:
        raise HTTPException(404, "Ticket set not found")
    return row


def _public_set(row: dict) -> dict:
    """One set as the web reads it.

    `title` and `source_text` are returned even when empty — the panel renders
    every field and decides its own fallback copy, rather than the API deciding
    a blank line should disappear.
    """
    stories = [s for s in (row.get("stories") or []) if isinstance(s, dict)]
    return {
        "id": row["id"],
        "title": row.get("title") or "",
        "status": row.get("status") or "ready",
        "stories": stories,
        "ticket_count": len(stories),
        "conversation_id": row.get("conversation_id"),
        "source_text": row.get("source_text") or "",
        "created_at": row.get("created_at"),
    }


@router.get("/by-conversation/{conversation_id}")
def sets_for_conversation(
    conversation_id: int,
    company: WorkspaceContext = Depends(require_workspace),
):
    """The ticket sets born in one chat, newest first (no `stories` payload).

    The thread-resume read: reopening a chat asks this so it can render the
    "View Tickets" affordance and — for a set still `generating` — put the panel
    straight back on the live progress instead of a blank tab or a stale
    "Writing tickets…" for a run that finished an hour ago.

    Company-scoped in the query, so a guessed conversation id belonging to
    another tenant returns an empty list rather than their sets.
    """
    return {
        "ticket_sets": [
            {
                "id": r["id"],
                "title": r.get("title") or "",
                "status": r.get("status") or "ready",
                "created_at": r.get("created_at"),
            }
            for r in list_sets_for_conversation(company.company_id, conversation_id)
        ]
    }


@router.get("/{set_id}")
def get_ticket_set(
    set_id: int,
    company: WorkspaceContext = Depends(require_workspace),
):
    """One ticket set with its tickets. 404 for an unknown id or a foreign
    tenant's — deliberately indistinguishable.

    Tickets are returned with DELETED ones dropped and EXCLUDED ones tagged,
    exactly as GET /v1/stories/for-prd/{prd_id} does, so the panel renders the
    same lifecycle semantics on both paths.
    """
    row = _require_owned_set(set_id, company.company_id)
    out = _public_set(row)
    out["stories"] = _apply_set_lifecycle(
        company.company_id, set_id, out["stories"]
    )
    out["ticket_count"] = len(out["stories"])
    return out


def _apply_set_lifecycle(
    company_id: str, set_id: int, stories: list[dict]
) -> list[dict]:
    """Drop DELETED tickets from a set and tag EXCLUDED ones.

    Mirrors routes/stories._apply_lifecycle. Lifecycle lives on `ticket_edits`
    (keyed by the composed ticket key), never inside the stories array, so it is
    applied on the way out here too. A lookup failure returns the stories
    untouched — a lifecycle read must never blank the panel.
    """
    from app.db.ticket_lifecycle import DELETED, lifecycle_by_key

    scope = set_scope(set_id)
    try:
        states = lifecycle_by_key(company_id)
    except Exception:  # noqa: BLE001
        logger.warning("lifecycle lookup failed for ticket set %s", set_id)
        return stories
    if not states:
        return stories
    out: list[dict] = []
    for s in stories:
        if not isinstance(s, dict):
            continue
        state = states.get(scope.ticket_key(s))
        if state == DELETED:
            continue
        out.append({**s, "lifecycle": state} if state else s)
    return out


# ── Two-way tracker sync ─────────────────────────────────────────────────────
#
# A standalone set syncs with Jira / ClickUp / Asana on exactly the same terms
# as a PRD's tickets: the first push registers a destination, the scheduler
# auto-syncs it on an interval, and edits on either side reconcile
# last-writer-wins. Nothing about the engine is PRD-specific — see
# app/stories/scope.py for what "the artifact" means to it.


class SyncTriggerIn(BaseModel):
    """Optional destination for the FIRST push (or a tool/destination switch).
    Omit all fields to re-sync the already-configured destination."""

    provider: str | None = Field(default=None)
    destination_id: str | None = Field(default=None, min_length=1)
    destination_name: str | None = None


@router.get("/{set_id}/sync")
def set_sync_state(
    set_id: int,
    company: WorkspaceContext = Depends(require_workspace),
):
    """This set's tracker-sync state: destination, whether a sync is running,
    when it last completed, and the pulled per-ticket tracker statuses.
    `configured: false` means the tickets were never pushed anywhere."""
    from app.db.ticket_sync import get_sync_config
    from app.stories.sync_control import public_sync_state

    _require_owned_set(set_id, company.company_id)
    return public_sync_state(
        get_sync_config(company.company_id, set_scope(set_id))
    )


@router.post("/{set_id}/sync")
async def trigger_set_sync(
    set_id: int,
    body: SyncTriggerIn,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Run a two-way sync pass for this set's tickets, in the background.

    First push: pass `provider` + `destination_id` to register the destination;
    the same call switches an already-bound set to a different tool. With no
    body fields the configured destination re-syncs. Poll GET
    /v1/ticket-sets/{set_id}/sync for completion.
    """
    from app.stories.sync_control import trigger_scope_sync

    _require_owned_set(set_id, company.company_id)
    return await trigger_scope_sync(
        company.company_id, set_scope(set_id),
        provider=body.provider,
        destination_id=body.destination_id,
        destination_name=body.destination_name,
        unconfigured_message=(
            "These tickets were never pushed — pick a destination first"
        ),
    )


@router.get("/{set_id}/tracker-meta")
def set_tracker_meta(
    set_id: int,
    refresh: bool = False,
    company: WorkspaceContext = Depends(require_workspace),
):
    """The tracker vocabulary (statuses / priorities / issue types / custom
    fields) this set's ticket detail renders instead of Sprntly's canned lists.

    Bound set → the bound destination's meta (cache; `?refresh=1` forces a live
    re-fetch). UNBOUND set with a connected tracker → the freshest meta the
    connect-time warm cached for that tracker, so the detail speaks the
    customer's vocabulary before any push. No tracker → all-null and the web
    keeps its defaults. Same three-way contract as the PRD route.
    """
    from app import db
    from app.db.ticket_sync import get_sync_config
    from app.db.tracker_meta import get_newest_cached_meta, get_or_fetch_meta
    from app.stories.sync import ticket_sync_providers

    _require_owned_set(set_id, company.company_id)
    cfg = get_sync_config(company.company_id, set_scope(set_id))
    if cfg is not None:
        return {
            "configured": True,
            "provider": cfg.get("provider"),
            "destination_id": cfg.get("destination_id"),
            "meta": get_or_fetch_meta(
                company.company_id, cfg["provider"], cfg["destination_id"],
                refresh=refresh,
            ),
        }

    for provider in ticket_sync_providers():
        try:
            if not db.get_connection(company.company_id, provider):
                continue
            meta = get_newest_cached_meta(company.company_id, provider)
        except Exception:  # noqa: BLE001 — fallback vocabulary is best-effort
            continue
        if meta:
            return {
                "configured": False,
                "provider": provider,
                "destination_id": meta.get("destination_id"),
                "meta": meta,
            }
        from app.connectors.tracker_meta import kick_company_meta_warm

        kick_company_meta_warm(company.company_id, provider)
    return {"configured": False, "provider": None,
            "destination_id": None, "meta": None}
