"""Writers + point-read for `delegation_followups` — the durable per-task
cadence-scheduling row (one per `project_delegations.id`). Inputs/facts
only, never a derived status (AD-P17): current status still lives in
`delegation_events`/`v_delegation_status`.

`list_due_followups` (the cheap SQL pre-filter the outbound follow-up
sweep uses) and `timezones_for_user_ids` (the assignee-timezone reader
that sweep needs) are appended below, at the bottom of this module —
they ship with the sweep, not the cadence-writer ticket that created this
file. Everything above the append marker is unchanged from that ticket.

Mirrors `db/project_delegations.py`'s client/`@retry_on_disconnect` style.
Helper failures propagate to the caller — this module does not swallow
errors; `delegation_status_ingest.py`'s callers wrap it best-effort
themselves."""
from __future__ import annotations

from datetime import datetime

from app.db.client import require_client, retry_on_disconnect, utc_now
from app.db.delegation_events import OPEN_STATES

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


# ── Appended for the outbound follow-up sweep (app/delegation_followup.py) ──


@retry_on_disconnect
def list_due_followups(now: datetime) -> list[dict]:
    """Every task DUE for evaluation right now — the cheap SQL pre-filter
    that keeps the LLM decision off every non-due task (spec §5 cost
    bound): `next_check_in <= now`, `muted = false`, and the delegation's
    DERIVED status (`v_delegation_status`, never copied here per AD-P17)
    is still in `OPEN_STATES`. `cleared`/`completed` are both
    `CLOSED_STATES`, so a cleared task (the assigner's kill switch) or a
    finished one gets NO further pings — that filter alone is what honors
    both.

    `delegation_followups` and `v_delegation_status` have no direct FK to
    each other for PostgREST to embed in one call (the view is keyed off
    `project_delegations`, not this sibling table), so this reads the
    cheap due-set first, then enriches only THOSE ids against the status
    view — never the reverse (which would derive status for every open
    delegation regardless of whether it's due).

    Returns one dict per due+open+unmuted task carrying every column the
    sweep needs: `delegation_id, project_id, assigner_user_id,
    assignee_user_id, task_summary, delivered_conversation_id,
    next_check_in, last_checked_in, expected_completion,
    pending_done_since, status`. `pending_done_since` rides along (the
    sweep's step-0 soft-done short-circuit needs it) even though a
    soft-done task's derived status is still open (`done_inferred` never
    emits a `completed` event — the soft-done design)."""
    client = require_client()
    due_rows = (
        client.table("delegation_followups")
        .select(
            "delegation_id, next_check_in, last_checked_in, "
            "expected_completion, pending_done_since"
        )
        .lte("next_check_in", _iso(now))
        .eq("muted", False)
        .execute()
        .data
        or []
    )
    if not due_rows:
        return []

    ids = [row["delegation_id"] for row in due_rows]
    status_rows = (
        client.table("v_delegation_status")
        .select(
            "delegation_id, project_id, assigner_user_id, assignee_user_id, "
            "task_summary, delivered_conversation_id, status"
        )
        .in_("delegation_id", ids)
        .execute()
        .data
        or []
    )
    status_by_id = {row["delegation_id"]: row for row in status_rows}

    out: list[dict] = []
    for row in due_rows:
        status_row = status_by_id.get(row["delegation_id"])
        if status_row is None or status_row.get("status") not in OPEN_STATES:
            continue
        out.append({**status_row, **row})
    return out


@retry_on_disconnect
def timezones_for_user_ids(user_ids: list[str | None]) -> dict[str, str]:
    """Map user_id -> IANA timezone string from `profiles`. Structured like
    `invite_reminders.first_names_for_user_ids` (dedup ids, empty input ->
    `{}` with no query, `@retry_on_disconnect`) but reading the same
    `profiles.timezone` column `db.companies._attach_owner_timezones`
    reads (`db/companies.py:100-110`). Unknown ids and profiles with no
    timezone set are simply absent — the caller (`brief_schedule.
    resolve_user_timezone`) degrades a missing entry to UTC."""
    ids = [u for u in dict.fromkeys(user_ids) if u]
    if not ids:
        return {}
    rows = (
        require_client()
        .table("profiles")
        .select("id, timezone")
        .in_("id", ids)
        .execute()
        .data
        or []
    )
    return {
        r["id"]: r["timezone"]
        for r in rows
        if r.get("id") and (r.get("timezone") or "").strip()
    }
