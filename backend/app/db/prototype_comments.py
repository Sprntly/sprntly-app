"""DB helpers for `prototype_comments` (P3-01).

F8 (anchored comments) storage primitive: a comment is keyed to a
`prototype_id` and anchored to a `data-anchor-id` string (AD4/AD12). This
module lands the helpers; P3-02 mounts the routes on top, P3-03 renders the
panel, and P3-04 wires `mark_comments_orphaned` + `list_open_comment_anchor_ids`
into the regeneration path.

These helpers are *synchronous* and use `require_client()` + `utc_now()`,
mirroring `db/prototypes.py` / `db/prototype_exports.py` exactly — supabase-py is
a synchronous client, and the async-task routes call these sync helpers directly
from their async handlers.

Storage decision (store INPUTS only): `status` is a stored three-state enum
because it is a genuine INPUT (a user resolves a comment; the regeneration walk
orphans one) — NOT a derived label. There is no denormalised `is_orphaned`
boolean or `display_label` column. `author` is the identity punt — a free-text
string ('demo' for now), not an FK, so a real users table can land later without
migrating the column.

Workspace isolation (Architecture Rules #20-#23):
- INSERTs populate `workspace_id` from the caller (the route threads
  `require_session().aud` through, or the resolved prototype's workspace_id for
  public-route writes; it is NEVER hardcoded here).
- All user-driven SELECT / UPDATE filter by `workspace_id`.
- `mark_comments_orphaned` is invoked from the regeneration path with an explicit
  prototype_id + workspace_id (the prototype being regenerated is known), so it
  IS workspace-filtered — it is NOT a cross-workspace background sweep.

Observability (Rule #24): every log line carries identifiers only — never the
comment body (PII).
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.client import require_client, utc_now

logger = logging.getLogger(__name__)

_TABLE = "prototype_comments"
_LEGAL_STATUS = {"open", "resolved", "orphaned"}


def insert_comment(
    *,
    prototype_id: int,
    workspace_id: str,            # from the route (internal session.aud, or the
                                  # resolved prototype's workspace_id for public writes)
    anchor_id: str,
    body: str,
    author: str = "demo",
    pin_x_pct: float | None = None,        # viewport-relative x position (0..100), None for non-pin comments
    pin_y_pct: float | None = None,        # viewport-relative y position (0..100), None for non-pin comments
    resolved_anchor_id: str | None = None,  # stable JSX anchor at the pin point, None if unresolved
) -> dict[str, Any]:
    """Insert an open comment anchored to anchor_id. Returns the inserted row.

    Raises ValueError on empty anchor_id or empty/whitespace body — both are
    programming/validation bugs, not runtime conditions to swallow.
    """
    if not anchor_id:
        raise ValueError("insert_comment: anchor_id is empty")
    if not body.strip():
        raise ValueError("insert_comment: body is empty")
    c = require_client()
    payload: dict[str, Any] = {
        "prototype_id": prototype_id,
        "workspace_id": workspace_id,
        "anchor_id": anchor_id,
        "body": body,
        "author": author,
        "status": "open",
    }
    # Write position keys only when supplied — keeps the right-click anchor path
    # (no pin) inserting exactly the prior column set; null position is honest absence.
    if pin_x_pct is not None:
        payload["pin_x_pct"] = pin_x_pct
    if pin_y_pct is not None:
        payload["pin_y_pct"] = pin_y_pct
    if resolved_anchor_id is not None:
        payload["resolved_anchor_id"] = resolved_anchor_id
    resp = c.table(_TABLE).insert(payload).execute()
    row = resp.data[0]
    # Identifiers only -- never log comment body (PII per Rule #24).
    logger.info(
        "comment_created prototype_id=%s comment_id=%s anchor_id=%s",
        prototype_id, row["id"], anchor_id,
    )
    return row


def list_comments(
    *,
    prototype_id: int,
    workspace_id: str,
) -> list[dict[str, Any]]:
    """Return all comments for a prototype (any status), ordered by created_at
    ascending. Workspace-filtered (Rule #22)."""
    c = require_client()
    resp = (c.table(_TABLE).select("*")
            .eq("prototype_id", prototype_id)
            .eq("workspace_id", workspace_id)
            .order("created_at", desc=False).execute())
    return resp.data or []


def resolve_comment(
    *,
    comment_id: int,
    workspace_id: str,
) -> dict[str, Any] | None:
    """Flip a comment to status='resolved' + set resolved_at. Workspace-filtered.

    Returns the updated row, or None if not found in workspace. Idempotent:
    resolving an already-resolved comment is a no-op that returns the row.
    """
    c = require_client()
    (c.table(_TABLE).update({"status": "resolved", "resolved_at": utc_now()})
     .eq("id", comment_id).eq("workspace_id", workspace_id).execute())
    resp = (c.table(_TABLE).select("*")
            .eq("id", comment_id).eq("workspace_id", workspace_id).limit(1).execute())
    if not resp.data:
        return None
    logger.info("comment_resolved comment_id=%s", comment_id)
    return resp.data[0]


def list_open_comment_anchor_ids(
    *,
    prototype_id: int,
    workspace_id: str,
) -> list[str]:
    """Return the distinct set of anchor_ids referenced by OPEN comments for a
    prototype, workspace-filtered. P3-04 calls this to decide which anchors must
    survive a regeneration. Resolved/orphaned comments are excluded.

    Returns a `list[str]` (not a set) for JSON-serialisability and to match the
    `list_comments` return convention; duplicates are collapsed, first-seen order
    preserved (created_at-ascending).
    """
    c = require_client()
    resp = (c.table(_TABLE).select("anchor_id")
            .eq("prototype_id", prototype_id)
            .eq("workspace_id", workspace_id)
            .eq("status", "open")
            .order("created_at", desc=False).execute())
    distinct: list[str] = []
    for row in resp.data or []:
        anchor = row["anchor_id"]
        if anchor not in distinct:
            distinct.append(anchor)
    return distinct


def mark_comments_orphaned(
    *,
    prototype_id: int,
    workspace_id: str,
    surviving_anchor_ids: set[str],
) -> int:
    """Flip every OPEN comment whose anchor_id is NOT in surviving_anchor_ids to
    status='orphaned'. Used by P3-04 after a new checkpoint is built. Comments
    already resolved/orphaned are untouched. Returns count orphaned.

    NOTE: this is invoked from the regeneration path with an explicit
    prototype_id + workspace_id (the prototype being regenerated is known), so
    it IS workspace-filtered -- it is NOT a cross-workspace background sweep.
    """
    c = require_client()
    open_rows = (c.table(_TABLE).select("id, anchor_id")
                 .eq("prototype_id", prototype_id)
                 .eq("workspace_id", workspace_id)
                 .eq("status", "open").execute().data or [])
    orphan_ids = [r["id"] for r in open_rows if r["anchor_id"] not in surviving_anchor_ids]
    if orphan_ids:
        (c.table(_TABLE).update({"status": "orphaned"})
         .in_("id", orphan_ids).eq("workspace_id", workspace_id).execute())
        logger.info(
            "comments_orphaned prototype_id=%s count=%s", prototype_id, len(orphan_ids),
        )
    return len(orphan_ids)


def list_resolved_comments(
    *,
    prototype_id: int,
    workspace_id: str,
) -> list[dict[str, Any]]:
    """Return the prototype's status='resolved' comments, ordered deterministically
    by (anchor_id, id). Workspace-filtered (Rule #22). Used by the export serialiser
    to render the Resolved Feedback section (F16).

    Resolved ONLY: open comments are unresolved feedback (not part of the locked-state
    handoff); orphaned comments point at anchors that no longer exist (P3-04). Ordered
    by (anchor_id, id) rather than created_at for byte-determinism (created_at can
    collide at same-second resolution; id is the monotonic tiebreak), and so a thread's
    comments group visually under one anchor in the export.
    """
    c = require_client()
    resp = (c.table(_TABLE).select("*")
            .eq("prototype_id", prototype_id)
            .eq("workspace_id", workspace_id)
            .eq("status", "resolved")
            .order("anchor_id", desc=False).order("id", desc=False).execute())
    rows = resp.data or []
    # Final ordering authority is this stable Python sort, NOT the DB layer: it keeps
    # the (anchor_id, id) contract byte-deterministic regardless of the backend's
    # multi-column-order or collation semantics. The `.order()` calls above let real
    # Postgres pre-sort; this guarantees the invariant the export serialiser relies on.
    return sorted(rows, key=lambda r: (r["anchor_id"], r["id"]))
