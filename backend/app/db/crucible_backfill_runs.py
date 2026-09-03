"""crucible_backfill_runs — the audit trail for the deterministic commercial-
figure backfill operator tool.

TENANCY. Every read filters `company_id` in the query itself, same posture as
`db/crucible_runs.py` — the backend holds the service-role key (RLS bypassed),
so this filter IS the tenant boundary.

THE ROW IS THE AUDIT, NOT THE QUEUE. Unlike `design_agent_jobs`/
`prototype_pending_iterations`, there is no per-item claim state here: the
unit of work is a `kg_signal` row, and that row's own `properties.amount`
presence is its completion marker (already-enriched rows are skipped, never
re-derived — see `app/crucible/backfill.py`). A crashed run recovers by
simply being re-invoked; this table exists purely so an operator can see what
a run reported without diffing `kg_signal` by hand.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.db.client import require_client, utc_now

logger = logging.getLogger(__name__)

TABLE = "crucible_backfill_runs"


def start(
    *, company_id: str, mode: str, pattern_version: str
) -> dict[str, Any]:
    """Create the 'running' row before any work happens. Returns it."""
    row = {
        "company_id": company_id,
        "phase": "deterministic_sweep",
        "mode": mode,
        "pattern_version": pattern_version,
        "status": "running",
        "updated_at": utc_now(),
    }
    res = require_client().table(TABLE).insert(row).execute()
    out = (res.data or [{}])[0]
    logger.info(
        "crucible_backfill_run_started run_id=%s company_id=%s mode=%s pattern_version=%s",
        out.get("id"), company_id, mode, pattern_version,
    )
    return out


def finish(
    *,
    run_id: int,
    company_id: str,
    status: str,
    examined_count: int,
    enriched_count: int,
    skipped_counts: dict[str, int],
    error: Optional[str] = None,
) -> None:
    """Flip a run to its terminal state ('completed' or 'failed') with the
    final counts. Tenant-filtered on the UPDATE, same posture as every other
    write in this module."""
    update: dict[str, Any] = {
        "status": status,
        "examined_count": examined_count,
        "enriched_count": enriched_count,
        "skipped_counts": skipped_counts,
        "finished_at": utc_now(),
        "updated_at": utc_now(),
    }
    if error is not None:
        update["error"] = error[:2000]
    (
        require_client().table(TABLE)
        .update(update)
        .eq("id", run_id)
        .eq("company_id", company_id)
        .execute()
    )
    logger.info(
        "crucible_backfill_run_finished run_id=%s company_id=%s status=%s "
        "examined=%s enriched=%s",
        run_id, company_id, status, examined_count, enriched_count,
    )


def list_for_company(company_id: str, limit: int = 20) -> list[dict[str, Any]]:
    res = (
        require_client().table(TABLE).select("*")
        .eq("company_id", company_id)
        .order("started_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []
