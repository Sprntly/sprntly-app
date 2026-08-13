"""Delegation events — immutable inputs/facts, no derived state.

`project_delegations` records the first cross-user hand-off: who assigned
what task to whom, and where the assignee was told about it
(`delivered_conversation_id`/`delivered_turn_id`, the individual project
chat turn that delivered the brief, via the durable individual-chat
get-or-create helper). Every column is an immutable input of the
delegation event; there is deliberately NO `status` column (AD-P17).
The future ledger (out of v1, a separate re-quote) appends its own
append-only `delegation_events` log off a delegation's `id` and derives
current status at read time — this module never repaints a row.

`record_delegation` is the only writer. The three `list_delegations_for_*`
readers are thin, each hitting one of the three indexes the migration
ships (`idx_project_delegations_{project,assignee,assigner}`) so the
ledger's "handed to me / by me / in this project" queries are indexed
lookups rather than a cross-conversation turn-scan.

Mirrors `db/project_memory_entries.py`'s client/`@retry_on_disconnect`
style. Helper failures propagate to the caller (the delivery route wraps
them best-effort) — this module does not swallow errors."""
from __future__ import annotations

from app.db.client import require_client, retry_on_disconnect


@retry_on_disconnect
def record_delegation(
    *,
    project_id: int,
    assigner_user_id: str,
    assignee_user_id: str,
    task_summary: str,
    source_conversation_id: int | None,
    source_turn_id: int | None,
    delivered_conversation_id: int,
    delivered_turn_id: int,
) -> dict:
    """Insert one immutable delegation fact. No status/derived state
    (AD-P17). `id`/`created_at` are server-assigned."""
    client = require_client()
    return (
        client.table("project_delegations")
        .insert(
            {
                "project_id": project_id,
                "assigner_user_id": assigner_user_id,
                "assignee_user_id": assignee_user_id,
                "task_summary": task_summary,
                "source_conversation_id": source_conversation_id,
                "source_turn_id": source_turn_id,
                "delivered_conversation_id": delivered_conversation_id,
                "delivered_turn_id": delivered_turn_id,
            }
        )
        .execute()
        .data[0]
    )


@retry_on_disconnect
def list_delegations_for_project(project_id: int) -> list[dict]:
    """Delegation facts for this project, newest-first. Read-only, no
    derivation — hits `idx_project_delegations_project`."""
    client = require_client()
    return (
        client.table("project_delegations")
        .select("*")
        .eq("project_id", project_id)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )


@retry_on_disconnect
def list_delegations_for_assignee(assignee_user_id: str) -> list[dict]:
    """Delegation facts handed TO this user, newest-first — hits
    `idx_project_delegations_assignee`."""
    client = require_client()
    return (
        client.table("project_delegations")
        .select("*")
        .eq("assignee_user_id", assignee_user_id)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )


@retry_on_disconnect
def list_delegations_for_assigner(assigner_user_id: str) -> list[dict]:
    """Delegation facts handed OUT by this user, newest-first — hits
    `idx_project_delegations_assigner`."""
    client = require_client()
    return (
        client.table("project_delegations")
        .select("*")
        .eq("assigner_user_id", assigner_user_id)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )
