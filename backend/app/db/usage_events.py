"""DB helpers for `design_agent_usage_events` — the per-action usage ledger.

One row per user-action LLM run (a full prototype generation or a comment-driven
iteration): `start_usage_event` inserts a `started` row at the action boundary,
`finalize_usage_event` transitions it to `succeeded` / `failed` with the rolled-up
token totals + estimated cost at the bg-runner terminal.

Mirrors `db/prototypes.py` exactly: synchronous functions over the supabase-py
service-role client (`require_client()` + `utc_now()`), a module-level `_TABLE`
const, `c.table(_TABLE).insert({...}).execute()` → `resp.data[0]["id"]`, and
workspace-filtered UPDATE chains.

Fail-open contract: these helpers do NOT swallow their own errors — the
fail-open `try/except` lives at the CALL SITES (the route bg-runners), so a
ledger-write failure can never change generation/iteration control flow. The
helpers themselves only guarantee they do not raise on a `None` usage and that an
unpriced model degrades to a null cost rather than an exception.

Workspace isolation (the same rules as the rest of `app.db.*`): the INSERT
populates `workspace_id` from the caller; `finalize_usage_event` filters by
`.eq("workspace_id", ...)` so a finalize under the wrong workspace is a no-op.
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.client import require_client, utc_now
from app.llm_telemetry import RunUsage, UnknownModelError

logger = logging.getLogger(__name__)

_TABLE = "design_agent_usage_events"


def start_usage_event(
    *,
    workspace_id: str,
    kind: str,
    prd_id: int | None = None,
    prototype_id: int | None = None,
    trigger_comment_id: int | None = None,
) -> int:
    """Insert a `status='started'` ledger row, return its id.

    `kind` is 'full_generation' or 'iteration'. `trigger_comment_id` is the
    applied comment id for an iteration (None for a generation). `workspace_id`
    is the caller's company id — never hardcoded here.
    """
    c = require_client()
    resp = c.table(_TABLE).insert({
        "workspace_id": workspace_id,
        "kind": kind,
        "status": "started",
        "prd_id": prd_id,
        "prototype_id": prototype_id,
        "trigger_comment_id": trigger_comment_id,
    }).execute()
    row_id = resp.data[0]["id"]
    logger.info(
        "usage_event_started event_id=%s kind=%s prototype_id=%s",
        row_id, kind, prototype_id,
    )
    return row_id


def finalize_usage_event(
    *,
    event_id: int,
    workspace_id: str,
    status: str,
    usage: RunUsage | None = None,
    model: str | None = None,
    prototype_id: int | None = None,
    error_class: str | None = None,
) -> None:
    """Transition a ledger row to its terminal status with optional token totals.

    UPDATE by `(id, workspace_id)` — workspace-filtered so a finalize under the
    wrong workspace touches no row. Sets `status` + `completed_at`. When `usage`
    is present, writes the four token columns; when BOTH `usage` and `model` are
    present, computes `est_cost_usd = usage.est_cost_usd(model)`. An unpriced
    model (`UnknownModelError`) leaves `est_cost_usd` null but still persists the
    tokens — cost is best-effort, the token counts are the billing ground truth.
    `prototype_id` is patched only when passed (non-null).
    """
    patch: dict[str, Any] = {
        "status": status,
        "completed_at": utc_now(),
    }
    if error_class is not None:
        patch["error_class"] = error_class
    if prototype_id is not None:
        patch["prototype_id"] = prototype_id
    if usage is not None:
        patch["input_tokens"] = usage.input_tokens
        patch["output_tokens"] = usage.output_tokens
        patch["cache_creation_input_tokens"] = usage.cache_creation_input_tokens
        patch["cache_read_input_tokens"] = usage.cache_read_input_tokens
        if model is not None:
            try:
                patch["est_cost_usd"] = usage.est_cost_usd(model)
                patch["model"] = model
            except UnknownModelError:
                # An unpriced model is not an error to propagate: keep the tokens
                # (the billing ground truth) and leave est_cost_usd null so spend
                # can be re-derived later when the model is priced. The model is
                # still recorded so the gap is visible.
                patch["model"] = model
                logger.warning(
                    "usage_event_unknown_model event_id=%s model=%s",
                    event_id, model,
                )
    c = require_client()
    (
        c.table(_TABLE)
        .update(patch)
        .eq("id", event_id)
        .eq("workspace_id", workspace_id)  # explicit workspace filter
        .execute()
    )
    logger.info(
        "usage_event_finalized event_id=%s status=%s prototype_id=%s",
        event_id, status, prototype_id,
    )
