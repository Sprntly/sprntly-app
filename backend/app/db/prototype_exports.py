"""DB helpers for `prototype_exports` (P2-09).

Schema: one row per (prototype_id, checkpoint_id) pair (unique constraint).
Idempotency: insert_prototype_export checks for existence first; a second call
with the same pair is a no-op (returns the existing row's id) rather than
raising a unique-violation. Resume Iteration → next Mark Complete creates a
NEW checkpoint_id → NEW export row.

Workspace isolation (Rule #22): user-facing reads filter by workspace_id.
The INSERT path takes workspace_id from the caller (the /complete route
threads session.aud through; never hardcoded).
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.client import require_client, utc_now

logger = logging.getLogger(__name__)
_TABLE = "prototype_exports"


def insert_prototype_export(
    *,
    prototype_id: int,
    checkpoint_id: int,
    workspace_id: str,
    markdown_content: str,
) -> int:
    """Insert (or no-op-return existing) export row. Idempotent on
    (prototype_id, checkpoint_id). Returns the row id."""
    c = require_client()
    existing = (c.table(_TABLE)
                .select("id")
                .eq("prototype_id", prototype_id)
                .eq("checkpoint_id", checkpoint_id)
                .limit(1).execute().data)
    if existing:
        return existing[0]["id"]
    resp = c.table(_TABLE).insert({
        "prototype_id": prototype_id,
        "checkpoint_id": checkpoint_id,
        "workspace_id": workspace_id,
        "markdown_content": markdown_content,
        "generated_at": utc_now(),
    }).execute()
    row_id = resp.data[0]["id"]
    # Identifiers only — never log markdown body (it embeds PRD content).
    logger.info(
        "prototype_exported prototype_id=%s checkpoint_id=%s export_id=%s markdown_bytes=%s",
        prototype_id, checkpoint_id, row_id, len(markdown_content),
    )
    return row_id


def find_prototype_export(
    *,
    prototype_id: int,
    workspace_id: str,
) -> dict[str, Any] | None:
    """Return the most-recent export row for a prototype, filtered by workspace.
    Used by GET /v1/design-agent/{id}/export.
    Returns None when no export exists (prototype never marked complete, or
    Resume → no re-complete yet)."""
    c = require_client()
    resp = (c.table(_TABLE).select("*")
            .eq("prototype_id", prototype_id)
            .eq("workspace_id", workspace_id)
            .order("id", desc=True).limit(1).execute())
    return resp.data[0] if resp.data else None


def delete_prototype_export_by_prototype(
    *,
    prototype_id: int,
    workspace_id: str,
) -> int:
    """Delete all exports for a prototype. Used by test cleanup + rollback paths.
    Returns count deleted. Workspace-filtered (Rule #22).
    """
    c = require_client()
    resp = (c.table(_TABLE).delete()
            .eq("prototype_id", prototype_id)
            .eq("workspace_id", workspace_id)
            .execute())
    # PostgREST returns the deleted rows in `.data`; the in-memory test fake
    # returns the affected-row count in `.count` with an empty `.data`. Honour
    # whichever the active client populated so the count is correct in both.
    if resp.data:
        return len(resp.data)
    return getattr(resp, "count", 0) or 0
