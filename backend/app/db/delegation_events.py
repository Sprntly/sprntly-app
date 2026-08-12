"""Append-only lifecycle log for a delegation, plus the derive-at-read
current-status reader helpers.

`delegation_events` is the accountability ledger's spine: every row is an
immutable input — who did what, and when — never updated or deleted
(AD-P26). Current status is never stored; it is derived at read time by
`v_delegation_status` (the migration's own view) as the latest event per
delegation, falling back to a synthetic `assigned` when a delegation has
zero events (the empty-events belt-and-braces default). This module never
repaints a row and never computes status itself — it only appends facts
and reads the view.

Mirrors `db/project_delegations.py`'s client/`@retry_on_disconnect`
style. Helper failures propagate to the caller — this module does not
swallow errors; `record_event`'s caller (the genesis hook in
`project_delegation.py`) wraps it best-effort itself."""
from __future__ import annotations

from app.db.client import require_client, retry_on_disconnect


@retry_on_disconnect
def record_event(
    *, delegation_id: int, event: str, actor_user_id: str, note: str | None = None
) -> dict:
    """Append one immutable lifecycle event. No status/derived state
    (AD-P26). Rows are only ever inserted — never updated or deleted."""
    return (
        require_client()
        .table("delegation_events")
        .insert(
            {
                "delegation_id": delegation_id,
                "event": event,
                "actor_user_id": actor_user_id,
                "note": note,
            }
        )
        .execute()
        .data[0]
    )


@retry_on_disconnect
def current_status(delegation_id: int) -> str | None:
    """The derived status string for one delegation via `v_delegation_status`
    — `None` only if the delegation id does not exist at all (the view's
    coalesce fallback means any real delegation always yields a status)."""
    rows = (
        require_client()
        .table("v_delegation_status")
        .select("status")
        .eq("delegation_id", delegation_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0]["status"] if rows else None


@retry_on_disconnect
def list_status_for_assignee(project_id: int, user_id: str) -> list[dict]:
    """Derived-status rows handed TO this user in this project,
    newest-first by `status_at`. Read-only, no derivation of its own —
    reads `v_delegation_status`."""
    return (
        require_client()
        .table("v_delegation_status")
        .select("*")
        .eq("project_id", project_id)
        .eq("assignee_user_id", user_id)
        .order("status_at", desc=True)
        .execute()
        .data
        or []
    )


@retry_on_disconnect
def list_status_for_assigner(project_id: int, user_id: str) -> list[dict]:
    """Derived-status rows handed OUT by this user in this project,
    newest-first by `status_at`."""
    return (
        require_client()
        .table("v_delegation_status")
        .select("*")
        .eq("project_id", project_id)
        .eq("assigner_user_id", user_id)
        .order("status_at", desc=True)
        .execute()
        .data
        or []
    )


@retry_on_disconnect
def list_events(delegation_id: int) -> list[dict]:
    """The full, ordered event trail for one delegation — read-only, the
    raw log (not the derived view)."""
    return (
        require_client()
        .table("delegation_events")
        .select("*")
        .eq("delegation_id", delegation_id)
        .order("id")
        .execute()
        .data
        or []
    )
