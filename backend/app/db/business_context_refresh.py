"""business_context_refresh — async status for the Business Context agent.

`POST /v1/company/business-context/refresh` used to block the request for
the whole research pass. It now returns immediately and the real work runs
in a background task (app/business_context_refresh_runner.py); these four
`companies` columns (business_context_refresh_status/error/started_at/
heartbeat_at) are that job's durable, singleton-per-tenant status handle —
see the migration (20260802140000_business_context_refresh_status.sql) for
why columns rather than a dedicated table.

Mirrors app/db/asks.py's ask_jobs helpers and app/db/company_research_runs.py's
one-live-run-per-tenant guard + heal-on-conflict shape, adapted for a status
column instead of a row per attempt.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.db.client import require_client, retry_on_disconnect

logger = logging.getLogger(__name__)

_TABLE = "companies"

# A business-context refresh is ONE grounded web pass (max_searches=8) —
# shorter than company_research's 4-stage sweep (p50 ~5 min), but the
# underlying call streams on the 600s long-request timeout (see
# app.llm.call_with_web_search), so a slow real run can still comfortably
# take several minutes under load. 15 minutes matches the same-shaped
# windows already tuned in this codebase (ask_jobs, company_research_runs)
# rather than inventing a new number without any observed data of our own.
ORPHAN_BUSINESS_CONTEXT_REFRESH_AFTER_MINUTES = 15

# Comfortably inside the window above, so a couple of missed beats (a slow
# DB, a blocked thread) still don't trip the sweep. Same value as
# ORPHAN_ASK_JOB_HEARTBEAT_SECONDS.
ORPHAN_BUSINESS_CONTEXT_REFRESH_HEARTBEAT_SECONDS = 60

#: error recorded on a refresh whose owning process died (server restart
#: mid-refresh).
INTERRUPTED_REFRESH_ERROR = (
    "Interrupted — the server restarted while this refresh was in flight. "
    "Save Company Shape again to retry."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@retry_on_disconnect
def _try_start(company_id: str) -> bool:
    """One attempt at the atomic start-guard. Returns True iff THIS call's
    token is the one that landed.

    The UPDATE's WHERE clause only ever admits a row whose status is not
    already 'generating', so two concurrent callers racing this can't both
    proceed — Postgres serializes the two statements against the same row;
    whichever commits first is the only one whose WHERE clause still matches.
    We do NOT trust the update call's own response to say who won (a fresh,
    independent re-read + token compare is correct under both a real
    Postgres/PostgREST RETURNING and a simpler re-SELECT-after-write fake, and
    matches this codebase's existing convention — see cancel_ask_job, which
    also re-reads rather than trusting its own conditional update's payload)."""
    c = require_client()
    token = _now()
    c.table(_TABLE).update({
        "business_context_refresh_status": "generating",
        "business_context_refresh_error": None,
        "business_context_refresh_started_at": token,
        "business_context_refresh_heartbeat_at": token,
    }).eq("id", company_id).neq(
        "business_context_refresh_status", "generating"
    ).execute()
    row = (
        c.table(_TABLE)
        .select("business_context_refresh_started_at")
        .eq("id", company_id)
        .limit(1)
        .execute()
    )
    return bool(row.data) and row.data[0].get(
        "business_context_refresh_started_at"
    ) == token


def start_business_context_refresh(company_id: str) -> bool:
    """Move this company's refresh state to 'generating'. Returns True if
    this call should proceed to start the background job, False if a refresh
    is already live for this company (the caller treats that as a no-op, not
    an error — mirrors company_research_runs' "already researching" branch).

    Self-healing on the same conflict path company_research_runs uses: if the
    first attempt loses, that might mean a refresh is genuinely live OR that
    the row is a STALE 'generating' left by a dead worker (a server restart
    mid-refresh). Heal just this company's stale row, if any, and retry once
    — otherwise a restart would lock this company out of refreshing until the
    periodic sweep (scheduler, every 5m) caught it."""
    if _try_start(company_id):
        return True
    healed = fail_orphan_business_context_refreshes(company_id=company_id)
    if not healed:
        return False  # genuinely in-flight; not our turn
    return _try_start(company_id)


@retry_on_disconnect
def touch_business_context_refresh(company_id: str) -> bool:
    """Heartbeat: bump business_context_refresh_heartbeat_at so the orphan
    sweep can tell a LIVE long refresh from a dead worker's abandoned row.

    Guarded on status == 'generating' so a beat can never resurrect a row a
    worker already finished or failed. Returns True when the row was still
    generating (i.e. the beat landed), False otherwise — the caller uses that
    to stop beating (mirrors app.db.asks.touch_ask_job exactly).

    Best-effort by contract: a transient DB error returns True (keep beating)
    rather than aborting a healthy refresh over a blip."""
    try:
        c = require_client()
        resp = (
            c.table(_TABLE)
            .update({"business_context_refresh_heartbeat_at": _now()})
            .eq("id", company_id)
            .eq("business_context_refresh_status", "generating")
            .execute()
        )
    except Exception:  # noqa: BLE001 — a blip must not stop the heartbeat
        logger.warning(
            "business-context refresh heartbeat failed for company_id=%s",
            company_id, exc_info=True,
        )
        return True
    return bool(resp.data)


@retry_on_disconnect
def complete_business_context_refresh(company_id: str) -> None:
    """Mark the refresh 'done'. Guarded on status == 'generating' the same
    way complete_ask_job is — a trailing completion from an abandoned worker
    must not resurrect a row the orphan sweep already failed out."""
    c = require_client()
    c.table(_TABLE).update({
        "business_context_refresh_status": "done",
        "business_context_refresh_error": None,
    }).eq("id", company_id).eq(
        "business_context_refresh_status", "generating"
    ).execute()


def fail_business_context_refresh(company_id: str, error: str) -> None:
    """Mark the refresh 'error' (best-effort — the worker never crashes on
    this). Guarded on status == 'generating' for the same reason as
    complete_business_context_refresh."""
    c = require_client()
    c.table(_TABLE).update({
        "business_context_refresh_status": "error",
        "business_context_refresh_error": (error or "")[:500],
    }).eq("id", company_id).eq(
        "business_context_refresh_status", "generating"
    ).execute()


@retry_on_disconnect
def business_context_refresh_state(company_id: str) -> dict:
    """`{status, error}` for the poll route. status defaults to 'idle' (never
    None) for a company row that predates this migration or was never
    touched by a refresh."""
    c = require_client()
    resp = (
        c.table(_TABLE)
        .select("business_context_refresh_status, business_context_refresh_error")
        .eq("id", company_id)
        .limit(1)
        .execute()
    )
    row = resp.data[0] if resp.data else {}
    return {
        "status": row.get("business_context_refresh_status") or "idle",
        "error": row.get("business_context_refresh_error"),
    }


def fail_orphan_business_context_refreshes(
    older_than_minutes: int = ORPHAN_BUSINESS_CONTEXT_REFRESH_AFTER_MINUTES,
    *,
    company_id: str | None = None,
) -> int:
    """Fail 'generating' rows abandoned by a dead worker. Returns the count.

    Keys off business_context_refresh_HEARTBEAT_at, not started_at — a
    heartbeat still landing every ORPHAN_BUSINESS_CONTEXT_REFRESH_HEARTBEAT_SECONDS
    means the row is live no matter how long the refresh has been running, so
    this can never reap a healthy-but-slow job out from under itself (the
    exact ask_jobs incident this ticket exists to not repeat). Age-gated
    rather than "everything generating": staging and prod share one Supabase
    project, so a blanket sweep at startup would fail a refresh the OTHER
    environment's process is running right now — same reasoning as
    db.asks.fail_orphan_generating_ask_jobs and
    db.company_research_runs.fail_orphan_company_research_runs.

    `company_id` narrows the sweep to one tenant — used by
    start_business_context_refresh's insert-conflict-style heal so a new
    trigger doesn't have to wait for the periodic sweep. Runs at startup and
    on the scheduler's 5-minute heal job (same cadence as the two sweeps
    above)."""
    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(minutes=older_than_minutes)
    ).isoformat()
    c = require_client()
    q = c.table(_TABLE).select("id").eq("business_context_refresh_status", "generating")
    if company_id is not None:
        q = q.eq("id", company_id)
    rows = q.lt("business_context_refresh_heartbeat_at", cutoff).execute().data or []
    ids = [r["id"] for r in rows]
    if ids:
        c.table(_TABLE).update({
            "business_context_refresh_status": "error",
            "business_context_refresh_error": INTERRUPTED_REFRESH_ERROR,
        }).in_("id", ids).eq(
            "business_context_refresh_status", "generating"
        ).execute()
    return len(ids)
