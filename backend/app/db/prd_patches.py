"""DB helpers for `prd_patches` (P3-09, F11).

F11: the Design Agent proposes PRD edits as SIBLING rows here — `prds.payload_md`
is NEVER altered (no `UPDATE`/`ALTER` against `prds` exists in this module). The
PRD is rendered by applying outstanding `applied` patches on READ via
`apply_patches_to_prd_md` (append-model under a "## Design Agent updates" section;
P3 MVP — a structural 3-way merge is deferred, and BUILD-PHASES P3 risk-mitigation
allows only one pending patch at a time as the cheap escape hatch). This module
persists proposals (`status='pending'`); P3-10 flips them to `applied`/`rejected`
and consumes `apply_patches_to_prd_md` in its render path.

These helpers are *synchronous* and use `require_client()` + `utc_now()`, mirroring
`db/prototype_comments.py` / `db/prototypes.py` exactly — supabase-py is a
synchronous client, and the async-task routes call these sync helpers directly from
their async handlers.

Storage decision (store INPUTS only): `status` is a genuine three-state INPUT (the
agent proposes 'pending'; the user accepts→'applied' / rejects→'rejected') — NOT a
derived label, so it is a real column (per [[prefer-inference-over-stored-derived-state]]).
`patch_md`/`rationale` are the agent's LLM output — regenerating them is neither
free nor deterministic, so snapshotting them is justified (per
[[storage-decisions-name-cost-model]]). There is NO denormalised "applied PRD text"
column: the rendered PRD is DERIVED at read time (a cheap, deterministic string
fold), never persisted.

Workspace isolation (Architecture Rules #20-#23):
- INSERTs populate `workspace_id` from the caller (the route threads
  `require_session().aud` through; it is NEVER hardcoded here).
- All user-driven SELECT / UPDATE filter by `workspace_id`.

Observability (Rule #24): every log line carries identifiers only — never
`patch_md` or `rationale` (they can embed PRD body / product detail).
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.client import require_client, utc_now

logger = logging.getLogger(__name__)

_TABLE = "prd_patches"
_LEGAL_STATUS = {"pending", "applied", "rejected"}
_UPDATES_HEADING = "## Design Agent updates"


def insert_patch(
    *,
    prd_id: int,
    prototype_id: int,
    workspace_id: str,            # from the caller (session.aud); NEVER hardcoded
    rationale: str,
    patch_md: str,
) -> dict[str, Any]:
    """Insert a PENDING patch proposal. Returns the inserted row.

    Raises ValueError on empty rationale or empty/whitespace patch_md — both are
    validation bugs (the sentinel input_schema marks them required), not runtime
    conditions to swallow.
    """
    if not rationale.strip():
        raise ValueError("insert_patch: rationale is empty")
    if not patch_md.strip():
        raise ValueError("insert_patch: patch_md is empty")
    c = require_client()
    resp = c.table(_TABLE).insert({
        "prd_id": prd_id,
        "prototype_id": prototype_id,
        "workspace_id": workspace_id,
        "rationale": rationale,
        "patch_md": patch_md,
        "status": "pending",
    }).execute()
    row = resp.data[0]
    # Identifiers only -- never patch_md / rationale (PRD body, Rule #24).
    logger.info(
        "prd_patch_proposed prototype_id=%s prd_id=%s patch_id=%s",
        prototype_id, prd_id, row["id"],
    )
    return row


def list_pending_patches(
    *,
    prd_id: int,
    workspace_id: str,
) -> list[dict[str, Any]]:
    """Return the PENDING patches for a PRD, created_at-ascending. Workspace-filtered.

    P3-10's banner calls this to show the user what is awaiting accept/reject. Only
    'pending' rows are returned — 'applied'/'rejected' are resolved and excluded.
    """
    c = require_client()
    resp = (c.table(_TABLE).select("*")
            .eq("prd_id", prd_id)
            .eq("workspace_id", workspace_id)
            .eq("status", "pending")
            .order("created_at", desc=False).execute())
    return resp.data or []


def mark_patch_applied(
    *,
    patch_id: int,
    workspace_id: str,
) -> dict[str, Any] | None:
    """Flip a patch to status='applied' + set resolved_at. Workspace-filtered.

    Returns the updated row, or None if not found in workspace. Idempotent:
    re-applying an already-applied patch is a no-op flip that returns the row.
    P3-10's accept path calls this.
    """
    c = require_client()
    (c.table(_TABLE).update({"status": "applied", "resolved_at": utc_now()})
     .eq("id", patch_id).eq("workspace_id", workspace_id).execute())
    resp = (c.table(_TABLE).select("*")
            .eq("id", patch_id).eq("workspace_id", workspace_id).limit(1).execute())
    if not resp.data:
        return None
    logger.info("prd_patch_applied patch_id=%s", patch_id)
    return resp.data[0]


def mark_patch_rejected(
    *,
    patch_id: int,
    workspace_id: str,
) -> dict[str, Any] | None:
    """Flip a patch to status='rejected' + set resolved_at. Workspace-filtered.

    Returns the updated row, or None if not found in workspace. Idempotent.
    P3-10's reject path calls this.
    """
    c = require_client()
    (c.table(_TABLE).update({"status": "rejected", "resolved_at": utc_now()})
     .eq("id", patch_id).eq("workspace_id", workspace_id).execute())
    resp = (c.table(_TABLE).select("*")
            .eq("id", patch_id).eq("workspace_id", workspace_id).limit(1).execute())
    if not resp.data:
        return None
    logger.info("prd_patch_rejected patch_id=%s", patch_id)
    return resp.data[0]


def apply_patches_to_prd_md(prd_md: str, patches: list[dict[str, Any]]) -> str:
    """Render the PRD with all `applied` patches folded in. Pure + deterministic.

    P3 MVP append-model (BUILD-PHASES P3 risk-mitigation — a structural 3-way merge
    is deferred): every `status='applied'` patch's `patch_md` is appended, in
    `created_at` order, under a single "## Design Agent updates" section at the end
    of the PRD. Non-'applied' patches (pending/rejected) are ignored. No DB hit — a
    string fold over the passed-in rows, so storing the result would violate
    [[prefer-inference-over-stored-derived-state]]; the rendered PRD is derived on
    read, never persisted.

    Deterministic ordering: `created_at` ascending, with each row's monotonic `id`
    as the tie-break (same-second inserts still order stably). Same inputs →
    byte-identical output. The input list is NOT mutated (a copy is sorted)."""
    applied = [p for p in patches if p.get("status") == "applied"]
    if not applied:
        return prd_md
    applied = sorted(applied, key=lambda p: (p.get("created_at") or "", p.get("id") or 0))
    body = "\n\n".join((p.get("patch_md") or "").strip() for p in applied)
    return f"{prd_md.rstrip()}\n\n{_UPDATES_HEADING}\n\n{body}\n"
