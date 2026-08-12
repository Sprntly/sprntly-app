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


# ── State machine + party map (AD-P27, spec §3 decision 2) ────────────────
#
# Pure logic, no DB access — the emit route's gates 3/4 evaluate against
# these directly. `assigned` is deliberately absent from `EVENT_PARTY`: it
# is the server-only genesis event (the hand-off hook in `project_delegation.py`)
# and is never client-emittable over this endpoint.

OPEN_STATES: frozenset[str] = frozenset({"assigned", "accepted", "in_progress", "reopened"})
CLOSED_STATES: frozenset[str] = frozenset({"completed", "declined", "cancelled"})

#: Which PARTY may emit each client-emittable event. Assignee owns the
#: forward-progress events (accept/start/finish/decline); assigner owns the
#: two overrides (cancel, reopen) — no assigner-override completion.
EVENT_PARTY: dict[str, str] = {
    "accepted": "assignee",
    "in_progress": "assignee",
    "completed": "assignee",
    "declined": "assignee",
    "cancelled": "assigner",
    "reopened": "assigner",
}

#: Legal edges: current derived status -> the set of events that may follow
#: it. A `reopened` delegation behaves like a freshly-`assigned` one — same
#: outgoing edges (spec-silent; adopted per spec §3 decision 2).
TRANSITIONS: dict[str, frozenset[str]] = {
    "assigned": frozenset({"accepted", "in_progress", "declined", "cancelled"}),
    "accepted": frozenset({"in_progress", "completed", "declined", "cancelled"}),
    "in_progress": frozenset({"completed", "declined", "cancelled"}),
    "completed": frozenset({"reopened"}),
    "declined": frozenset({"reopened", "cancelled"}),
    "cancelled": frozenset({"reopened"}),
    "reopened": frozenset({"accepted", "in_progress", "declined", "cancelled"}),
}


def is_legal_transition(current: str, event: str) -> bool:
    """Whether `event` may legally follow a delegation currently at
    `current` (its latest/derived status). Unknown `current` values (should
    never happen — `current_status` only ever returns a value from the
    fixed `delegation_events.event` CHECK vocabulary, or the `assigned`
    fallback) legally transition to nothing."""
    return event in TRANSITIONS.get(current, frozenset())


@retry_on_disconnect
def load_delegation_for_authz(delegation_id: int) -> dict | None:
    """The four authz-relevant columns for one delegation, read straight off
    `project_delegations` (the fact table, not the derived view) — mirrors
    `db.projects.is_project_member`'s client/`.limit(1)` style. `None` only
    if the id does not exist at all; the route's gate 2 uses that (plus a
    `project_id` mismatch) to 404 opaquely."""
    rows = (
        require_client()
        .table("project_delegations")
        .select("id, project_id, assigner_user_id, assignee_user_id")
        .eq("id", delegation_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None
