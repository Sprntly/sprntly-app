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

# A full sweep is ~4 staged web-search passes plus extraction. Observed p50 on
# staging is ~5 minutes (a real run measured 4m53s), so 15 gives 3x headroom
# over the longest plausible run while bounding how long an orphan can sit.
#
# Why it matters that this is not generous: while a 'running' row is younger
# than this window it is INDISTINGUISHABLE from a live run, so the in-flight
# guard reports "already researching" and that company cannot start a new sweep.
# A deploy that restarts the service mid-run therefore locks the company out for
# the whole window. 30 minutes (the original value) made a routine deploy look
# like a stuck feature — observed on staging 2026-07-30, run id 3.
#
# Under-shooting is cheap by construction: if this fires while a run really is
# alive, the live run still completes and writes its signals, and the racing
# trigger is caught by the partial unique index rather than double-spending a
# sweep. Over-shooting is what actually costs the user.
#
# NOTE — the sweep cadence differs per environment, so this window is the real
# bound on staging:
#   * prod has SCHEDULER_ENABLED=true, so scheduler.py's 5-minute heal job runs
#     and an orphan is cleared within ~this window + 5 minutes;
#   * STAGING has SCHEDULER_ENABLED=false — start_scheduler() never runs, so
#     the ONLY sweeps there are the one in main.py's startup lifespan and the
#     on-demand heal on the insert-conflict path
#     (start_company_research_run → _fail_stale_running_rows). Which means on
#     staging nothing clears an orphan until either a restart or the next
#     trigger arrives past this window — so keeping the window short IS the fix
#     for the stuck-row experience there.
# Keep in sync with the "~15 minutes" the chat copy promises
# (company_research._plain_payload for the already_running branch).
ORPHAN_RUN_AFTER_MINUTES = 15

#: error recorded on runs whose owning process died (server restart mid-run).
INTERRUPTED_RUN_ERROR = (
    "Interrupted — the server restarted while this run was in flight. "
    "Ask again to retry."
)


@retry_on_disconnect
def start_company_research_run(
    company_id: str, *, url: str | None, trigger: str
) -> int | None:
    """Persist a `running` row and return its id, or None when a run is already
    live for this company.

    The None case is the ATOMIC guard. `company_research_runs_one_live_idx` is a
    partial unique index on (company_id) where status='running', so the second
    of two racing inserts — a double POST, or the onboarding kick overlapping a
    chat ask — is rejected by the DATABASE. A check-then-insert in Python cannot
    close that window; this can.

    Any insert failure is re-checked against the table before being reported as
    "already running", so a genuine error (bad payload, outage) still raises
    rather than being silently swallowed as a duplicate.
    """
    c = require_client()
    row = {
        "company_id": company_id,
        "url": url or "",
        "trigger": trigger,
        "status": "running",
        "stages": {},
        # Written explicitly (rather than left to the column default) because
        # both age comparisons below are string/timestamp comparisons against
        # an ISO-8601 UTC cutoff — the two must be the same format.
        "created_at": _now(),
    }
    try:
        resp = c.table(_TABLE).insert(row).execute()
    except Exception:
        # Distinguish three cases. The unique index has no age condition, so it
        # rejects an insert whenever ANY 'running' row exists:
        #   1. a genuinely live run → we lost the race, report it;
        #   2. an ORPHAN 'running' row (owner died mid-run) → heal it and retry,
        #      otherwise a dead row would wedge this company until the periodic
        #      sweep aged it out;
        #   3. anything else → a real error, re-raise.
        if _live_run_id(company_id) is not None:
            logger.info(
                "company_research: insert lost the one-live-run race for %s "
                "(%s trigger)", company_id, trigger,
            )
            return None
        if not _fail_stale_running_rows(company_id):
            raise
        try:
            resp = c.table(_TABLE).insert(row).execute()
        except Exception:
            # Raced with someone else's heal + insert.
            logger.info(
                "company_research: insert lost the race after healing a stale "
                "row for %s", company_id,
            )
            return None
    return resp.data[0]["id"]


@retry_on_disconnect
def complete_company_research_run(
    run_id: int,
    *,
    stages: dict[str, Any],
    records: list[dict],
    summary: str,
    partial: bool = False,
) -> None:
    """Store the per-stage results + captured records and mark the run done.

    `partial=True` records `completed_partial` — some stages ran, at least one
    failed. A partial run must not read as a clean one: the summary names the
    failed stages, and the status says so.
    """
    c = require_client()
    c.table(_TABLE).update({
        "status": "completed_partial" if partial else "completed",
        "stages": stages or {},
        "records": records or [],
        "summary": summary or "",
        "error": None,
        "completed_at": _now(),
    }).eq("id", run_id).execute()


@retry_on_disconnect
def fail_company_research_run(run_id: int, error: str) -> None:
    """Mark the run `failed`.

    Retried on a transient disconnect: this is the ONLY thing that moves a row
    out of `running`, and a stranded `running` row blocks every future run for
    that company until the orphan sweep ages it out.
    """
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


def _live_run_id(company_id: str) -> int | None:
    """The id of this company's live (`running`, younger than the orphan cutoff)
    run, or None. Raises on a read error — callers decide how to degrade."""
    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(minutes=ORPHAN_RUN_AFTER_MINUTES)
    ).isoformat()
    rows = (
        require_client().table(_TABLE).select("id")
        .eq("company_id", company_id)
        .eq("status", "running")
        .gt("created_at", cutoff)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0]["id"] if rows else None


def _fail_stale_running_rows(company_id: str) -> int:
    """Fail THIS company's `running` rows older than the orphan cutoff.

    The one-live-run unique index carries no age condition, so an orphan row
    (server restarted mid-run) would otherwise block every future run for this
    company until the periodic sweep caught it — turning a transient restart into
    "this company can never be researched again for 30 minutes". Called on the
    insert-conflict path, so the heal is paid for only when it is needed.
    """
    return fail_orphan_company_research_runs(company_id=company_id)


def company_research_run_in_flight(company_id: str) -> bool:
    """Is a research run live for this company right now?

    Advisory pre-check: it exists to give the chat a good message ("already
    researching…") without an insert attempt. The real guard is the partial
    unique index — see start_company_research_run — so this failing open is safe.

    True only for a `running` row YOUNGER than the orphan cutoff: an older one
    belongs to a process that died and must not block a retry forever. Fails
    OPEN (False) on any read error: the DB still refuses a genuine double.
    """
    try:
        return _live_run_id(company_id) is not None
    except Exception:  # noqa: BLE001 — advisory guard, fail open
        logger.exception(
            "company_research in-flight check failed for %s (allowing run)",
            company_id,
        )
        return False


def fail_orphan_company_research_runs(
    older_than_minutes: int = ORPHAN_RUN_AFTER_MINUTES,
    *,
    company_id: str | None = None,
) -> int:
    """Fail rows abandoned in `running` by a dead process. Returns the count.

    Age-gated, NOT "everything running": staging and prod share one Supabase
    project, so a blanket sweep at staging startup would kill a run the prod
    process is executing right then. Age is the only signal separating "owner
    is dead" from "owner is another live process" — same reasoning as
    db/pipeline_runs.fail_orphan_running_runs and
    db/asks.fail_orphan_generating_ask_jobs. Runs at startup and on the
    scheduler's 5-minute heal job.

    `company_id` narrows the sweep to one tenant — used by the insert-conflict
    heal so a new trigger doesn't have to wait for the periodic sweep.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
    ).isoformat()
    c = require_client()
    q = c.table(_TABLE).select("id").eq("status", "running")
    if company_id is not None:
        q = q.eq("company_id", company_id)
    rows = q.lt("created_at", cutoff).execute().data or []
    for r in rows:
        fail_company_research_run(r["id"], INTERRUPTED_RUN_ERROR)
    return len(rows)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
