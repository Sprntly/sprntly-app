"""Project memory — discrete, provenance-tagged entries (the source of
truth) plus the read-only cached synthesized summary on top of them.

Layering (build spec AD-P3): `project_memory_entries` rows are either
user-authored (`author_user_id` set, `promoted_by` NULL — "Added by
David") or agent-promoted (`author_user_id` NULL, `promoted_by='agent'` —
"Promoted by Sprntly"); the schema's XOR check enforces exactly one is
set. This ticket only ever writes the user-authored shape — agent
promotion is a Phase 2 writer (`project_memory.py`, not built here).

`project_memory_summary` is a cached materialization, never written by
this module beyond flipping its `stale` flag on entry mutation (AD-P7):
regenerating it is a bounded LLM call, out of scope here. `get_summary`
serves the cached/seeded row read-only, or a computed fallback when none
exists yet — it never calls an LLM.
"""
from __future__ import annotations

from app.db.client import require_client, retry_on_disconnect


@retry_on_disconnect
def list_entries(project_id: int) -> list[dict]:
    """Memory entries for this project, most-recently-updated first."""
    return (
        require_client()
        .table("project_memory_entries")
        .select("*")
        .eq("project_id", project_id)
        .order("updated_at", desc=True)
        .execute()
        .data
        or []
    )


@retry_on_disconnect
def add_entry(project_id: int, *, body: str, author_user_id: str) -> dict:
    """Insert a user-authored entry (`author_user_id` set, `promoted_by`
    left NULL by omission) and flip an existing summary's `stale` flag —
    the entries changed, so the cached synthesis is out of date until the
    Phase-2 writer regenerates it. Never calls an LLM."""
    client = require_client()
    row = (
        client.table("project_memory_entries")
        .insert(
            {
                "project_id": project_id,
                "body": body,
                "author_user_id": author_user_id,
            }
        )
        .execute()
        .data[0]
    )
    _flip_summary_stale(client, project_id)
    return row


@retry_on_disconnect
def update_entry(project_id: int, entry_id: int, *, body: str) -> dict | None:
    """Edit an entry's body — scoped to `(id, project_id)` so an entry_id
    from another project can never be edited through this project (404 at
    the route layer when this returns None). Flips summary `stale`."""
    client = require_client()
    rows = (
        client.table("project_memory_entries")
        .update({"body": body})
        .eq("id", entry_id)
        .eq("project_id", project_id)
        .execute()
        .data
        or []
    )
    if not rows:
        return None
    _flip_summary_stale(client, project_id)
    return rows[0]


@retry_on_disconnect
def delete_entry(project_id: int, entry_id: int) -> bool:
    """Remove an entry — scoped to `(id, project_id)`, same isolation as
    `update_entry`. True iff a row was actually deleted. Flips summary
    `stale` only when a row existed to remove."""
    client = require_client()
    resp = (
        client.table("project_memory_entries")
        .delete()
        .eq("id", entry_id)
        .eq("project_id", project_id)
        .execute()
    )
    deleted = bool(resp.count) if resp.count is not None else bool(resp.data)
    if deleted:
        _flip_summary_stale(client, project_id)
    return deleted


@retry_on_disconnect
def get_summary(project_id: int) -> dict:
    """The cached `project_memory_summary` row, read-only, when one
    exists; otherwise a computed fallback `{summary_md: None, entry_count,
    stale: False}`. Never issues an LLM call in either branch — synthesis
    is a Phase 2 writer, this endpoint only reads (AD-P7/§5.4)."""
    client = require_client()
    rows = (
        client.table("project_memory_summary")
        .select("*")
        .eq("project_id", project_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if rows:
        return rows[0]

    count = (
        client.table("project_memory_entries")
        .select("id")
        .eq("project_id", project_id)
        .execute()
        .data
        or []
    )
    return {"summary_md": None, "entry_count": len(count), "stale": False}


def _flip_summary_stale(client, project_id: int) -> None:
    """Set `stale=true` on the project's summary row if one exists. A
    plain `UPDATE ... WHERE project_id = ?` is a no-op when no row is
    present, so this needs no existence check first."""
    client.table("project_memory_summary").update({"stale": True}).eq(
        "project_id", project_id
    ).execute()
