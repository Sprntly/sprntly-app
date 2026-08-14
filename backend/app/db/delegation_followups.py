"""Writers + point-read for `delegation_followups` — the durable per-task
cadence-scheduling row (one per `project_delegations.id`). Inputs/facts
only, never a derived status (AD-P17): current status still lives in
`delegation_events`/`v_delegation_status`.

The due-list reader (`list_due_followups`, consumed by the outbound
follow-up sweep) is intentionally NOT in this module — it ships with that
sweep, not this ticket.

Mirrors `db/project_delegations.py`'s client/`@retry_on_disconnect` style.
Helper failures propagate to the caller — this module does not swallow
errors; `delegation_status_ingest.py`'s callers wrap it best-effort
themselves."""
from __future__ import annotations

from datetime import datetime

from app.db.client import require_client, retry_on_disconnect, utc_now

# Sentinel distinguishing "caller did not pass this kwarg" from an explicit
# `None` (which means "clear this column to NULL"). A `datetime` value is
# never a valid sentinel, so identity comparison against this module-level
# object is unambiguous.
_UNSET = object()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@retry_on_disconnect
def upsert_followup(
    delegation_id: int,
    *,
    expected_completion: datetime | None = _UNSET,  # type: ignore[assignment]
    next_check_in: datetime | None = _UNSET,  # type: ignore[assignment]
    last_checked_in: datetime | None = _UNSET,  # type: ignore[assignment]
    muted: bool = _UNSET,  # type: ignore[assignment]
    pending_done_since: datetime | None = _UNSET,  # type: ignore[assignment]
) -> dict:
    """Partial upsert (`on_conflict=delegation_id`). Only the EXPLICITLY
    passed fields are written — a kwarg left at the `_UNSET` sentinel is
    omitted from the payload entirely, so PostgREST's merge-duplicates
    upsert leaves that column's existing value untouched. Passing a field
    as `None` (e.g. `pending_done_since=None`) is a deliberate write —
    clears that column to NULL. Always bumps `updated_at`. Uses
    `require_client()` (service role)."""
    payload: dict = {"delegation_id": delegation_id, "updated_at": utc_now()}
    if expected_completion is not _UNSET:
        payload["expected_completion"] = _iso(expected_completion)
    if next_check_in is not _UNSET:
        payload["next_check_in"] = _iso(next_check_in)
    if last_checked_in is not _UNSET:
        payload["last_checked_in"] = _iso(last_checked_in)
    if muted is not _UNSET:
        payload["muted"] = muted
    if pending_done_since is not _UNSET:
        payload["pending_done_since"] = _iso(pending_done_since)

    return (
        require_client()
        .table("delegation_followups")
        .upsert(payload, on_conflict="delegation_id")
        .execute()
        .data[0]
    )


@retry_on_disconnect
def get_followup(delegation_id: int) -> dict | None:
    """The cadence row for one delegation, or `None` if it has never been
    upserted."""
    rows = (
        require_client()
        .table("delegation_followups")
        .select("*")
        .eq("delegation_id", delegation_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None
