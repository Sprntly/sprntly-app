"""company_research_runs — durable run rows for the deep company-research sweep.

One row per run of `app/company_research.py` (onboarding kick or chat ask).
The row exists for three reasons:

  1. **Abandonment-proof onboarding.** The wizard never waits for the deep
     sweep, so nothing client-side owns it; the row is what says "this
     company's research ran / is running / failed".
  2. **Double-trigger guard.** `run_in_flight` makes a second trigger a no-op
     while a run is live for the company (a sweep costs real money and minutes).
  3. **Captured records.** The fact records the sweep collected are stored so a
     later read doesn't require re-running a multi-minute web sweep.

Deliberately NOT `pipeline_runs`: that table's supersede-on-start semantics are
one-pipeline-per-dataset, so reusing it would let a regenerate kill a research
run (and vice versa).

Names are prefixed (`start_company_research_run`, …) because `app.db` re-exports
everything flat and `complete_run` / `fail_run` are already taken by
`pipeline_runs`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.client import require_client, retry_on_disconnect

logger = logging.getLogger(__name__)

_TABLE = "company_research_runs"

# A full sweep is ~4 staged web-search passes plus extraction: 5-10 minutes in
# practice. Anything still 'running' past this has lost its owner (the process
# died mid-run). Tighter than pipeline_runs' 60 minutes because a research run
# is bounded work with no fan-out, and a stuck row blocks the in-flight guard.
ORPHAN_RUN_AFTER_MINUTES = 30

#: error recorded on runs whose owning process died (server restart mid-run).
INTERRUPTED_RUN_ERROR = (
    "Interrupted — the server restarted while this run was in flight. "
    "Ask again to retry."
)


@retry_on_disconnect
def start_company_research_run(
    company_id: str, *, url: str | None, trigger: str
) -> int:
    """Persist a `running` row and return its id."""
    c = require_client()
    resp = c.table(_TABLE).insert({
        "company_id": company_id,
        "url": url or "",
        "trigger": trigger,
        "status": "running",
        "stages": {},
        # Written explicitly (rather than left to the column default) because
        # both age comparisons below are string/timestamp comparisons against
        # an ISO-8601 UTC cutoff — the two must be the same format.
        "created_at": _now(),
    }).execute()
    return resp.data[0]["id"]


def complete_company_research_run(
    run_id: int,
    *,
    stages: dict[str, Any],
    records: list[dict],
    summary: str,
) -> None:
    """Store the per-stage results + captured records and mark the run done."""
    c = require_client()
    c.table(_TABLE).update({
        "status": "completed",
        "stages": stages or {},
        "records": records or [],
        "summary": summary or "",
        "error": None,
        "completed_at": _now(),
    }).eq("id", run_id).execute()


def fail_company_research_run(run_id: int, error: str) -> None:
    """Mark the run `failed` (best-effort — the worker never crashes here)."""
    c = require_client()
    c.table(_TABLE).update({
        "status": "failed",
        "error": (error or "")[:1000],
        "completed_at": _now(),
    }).eq("id", run_id).execute()


@retry_on_disconnect
def latest_company_research_run(company_id: str) -> dict | None:
    """The most recent run row for a company, or None."""
    c = require_client()
    resp = (
        c.table(_TABLE)
        .select("id, company_id, url, trigger, status, stages, records, "
                "summary, error, created_at, completed_at")
        .eq("company_id", company_id)
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def company_research_run_in_flight(company_id: str) -> bool:
    """Is a research run live for this company right now?

    True only for a `running` row YOUNGER than the orphan cutoff: an older one
    belongs to a process that died, and must not block a retry forever. Fails
    OPEN (False) on any read error — the guard is a cost optimization, and its
    failure mode should be "run it" rather than "silently never research this
    company again".
    """
    try:
        c = require_client()
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(minutes=ORPHAN_RUN_AFTER_MINUTES)
        ).isoformat()
        rows = (
            c.table(_TABLE).select("id")
            .eq("company_id", company_id)
            .eq("status", "running")
            .gt("created_at", cutoff)
            .limit(1)
            .execute()
            .data
            or []
        )
        return bool(rows)
    except Exception:  # noqa: BLE001 — advisory guard, fail open
        logger.exception(
            "company_research in-flight check failed for %s (allowing run)",
            company_id,
        )
        return False


def fail_orphan_company_research_runs(
    older_than_minutes: int = ORPHAN_RUN_AFTER_MINUTES,
) -> int:
    """Fail rows abandoned in `running` by a dead process. Returns the count.

    Age-gated, NOT "everything running": staging and prod share one Supabase
    project, so a blanket sweep at staging startup would kill a run the prod
    process is executing right then. Age is the only signal separating "owner
    is dead" from "owner is another live process" — same reasoning as
    db/pipeline_runs.fail_orphan_running_runs and
    db/asks.fail_orphan_generating_ask_jobs. Runs at startup and on the
    scheduler's 5-minute heal job.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
    ).isoformat()
    c = require_client()
    rows = (
        c.table(_TABLE).select("id")
        .eq("status", "running")
        .lt("created_at", cutoff)
        .execute()
        .data
        or []
    )
    for r in rows:
        fail_company_research_run(r["id"], INTERRUPTED_RUN_ERROR)
    return len(rows)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
