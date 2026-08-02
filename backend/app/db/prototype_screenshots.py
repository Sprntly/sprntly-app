"""DB helpers for `prototype_screenshots` — the multi-screenshot design-source
join table.

Replaces the exactly-one `prototypes.screenshot_key` column for any prototype
generated after this ticket's migration: a single column can't hold N items
with a stable order, so `position` (0-indexed, upload/prompt order) lives on
this table instead.

These helpers are *synchronous* and use `require_client()`, mirroring
`db/prototype_comments.py`'s module conventions exactly — supabase-py is a
synchronous client, and the async-task routes call these sync helpers
directly from their async handlers.

Workspace isolation (Architecture Rules #20-#23):
- INSERTs populate `workspace_id` from the caller (the route's already
  ownership-checked request; the resolved company_id from require_company) —
  NEVER hardcoded here.
- All reads filter by `workspace_id`.

Legacy fallback: `resolve_screenshot_keys` is the join-table-first,
legacy-column-second helper reused by every downstream consumer (the LLM
context builder, the cost estimate, and the export markdown) so a prototype
generated BEFORE this ticket (whose single reference screenshot still lives
in the old `prototypes.screenshot_key` column) keeps resolving identically
everywhere.

Observability: no log line here carries a full storage_key (which embeds the
workspace id and a storage-internal path) — never logged.
"""
from __future__ import annotations

from app.db.client import require_client

_TABLE = "prototype_screenshots"


def insert_screenshots(
    *, prototype_id: int, workspace_id: str, storage_keys: list[str]
) -> None:
    """Persist N staged upload keys for one prototype, position = list index.

    No-op on an empty list (never called with one from the route, but
    defensive — matches `_screenshot_reference_blocks`'s own empty-list
    no-op contract).
    """
    if not storage_keys:
        return
    c = require_client()
    payload = [
        {
            "prototype_id": prototype_id,
            "workspace_id": workspace_id,
            "storage_key": key,
            "position": i,
        }
        for i, key in enumerate(storage_keys)
    ]
    c.table(_TABLE).insert(payload).execute()


def list_screenshot_keys(*, prototype_id: int, workspace_id: str) -> list[str]:
    """Ordered by `position`. Workspace-filtered. [] when the prototype has
    no rows."""
    c = require_client()
    resp = (
        c.table(_TABLE)
        .select("storage_key, position")
        .eq("prototype_id", prototype_id)
        .eq("workspace_id", workspace_id)
        .order("position", desc=False)
        .execute()
    )
    rows = resp.data or []
    return [r["storage_key"] for r in rows]


def resolve_screenshot_keys(
    *, prototype_id: int, workspace_id: str, legacy_screenshot_key: str | None
) -> list[str]:
    """join-table-first, legacy-fallback-second.

    Rows in `prototype_screenshots` are authoritative for any prototype this
    ticket's code path generated. `legacy_screenshot_key` (the caller's
    already-fetched `prototype.screenshot_key` column value) is used ONLY when
    the join table has ZERO rows for this prototype_id — a pre-ticket row whose
    single reference screenshot still lives in the old column. A prototype with
    join-table rows never falls back, even if the legacy column also happens to
    be (harmlessly) non-null.
    """
    keys = list_screenshot_keys(prototype_id=prototype_id, workspace_id=workspace_id)
    if keys:
        return keys
    return [legacy_screenshot_key] if legacy_screenshot_key else []
