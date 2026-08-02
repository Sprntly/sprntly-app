"""DB helpers for pipeline_runs audit log."""
from __future__ import annotations

import json
from typing import Any

from app.db.client import require_client, utc_now


def create_run(dataset: str, trigger: str = "scheduled") -> int:
    """Create a new pipeline run in 'running' state. Returns the id."""
    c = require_client()
    resp = c.table("pipeline_runs").insert({
        "dataset": dataset,
        "trigger": trigger,
        "status": "running",
        "stages": {},
    }).execute()
    return resp.data[0]["id"]


def update_run_stage(
    run_id: int,
    stage_name: str,
    stage_status: dict[str, Any],
) -> None:
    """Merge a stage result into the run's stages JSONB."""
    c = require_client()
    # Read current stages, merge, write back
    resp = c.table("pipeline_runs").select("stages").eq("id", run_id).limit(1).execute()
    stages = (resp.data[0].get("stages") or {}) if resp.data else {}
    stages[stage_name] = stage_status
    c.table("pipeline_runs").update({"stages": stages}).eq("id", run_id).execute()


def complete_run(run_id: int) -> None:
    c = require_client()
    c.table("pipeline_runs").update({
        "status": "completed",
        "completed_at": utc_now(),
    }).eq("id", run_id).execute()


def fail_run(run_id: int, error: str) -> None:
    c = require_client()
    c.table("pipeline_runs").update({
        "status": "failed",
        "completed_at": utc_now(),
        "error": (error or "")[:1000],
    }).eq("id", run_id).execute()


# A full regenerate (ingest → brief → PRD/evidence fan-out) legitimately runs
# for many minutes; anything 'running' for longer than this has lost its owner
# (the process died mid-run). See fail_orphan_running_runs for why this is an
# age cutoff and not "everything running".
ORPHAN_RUN_AFTER_MINUTES = 60

#: error recorded on runs whose owning process died (server restart mid-run).
INTERRUPTED_RUN_ERROR = (
    "Interrupted — the server restarted while this run was in flight. "
    "Regenerate to retry."
)


def supersede_running_runs(dataset: str, *, reason: str | None = None) -> int:
    """Fail any 'running' rows for THIS dataset before a new run starts.

    A dataset has one pipeline at a time, so a prior row still 'running' when a
    new run is being created is either owned by a dead process (restart killed
    it) or about to be superseded — both are terminal for that row. Precise and
    immediate (no age gate needed: the dataset scoping means we can only be
    racing ourselves), it heals the common case where a user retries after a
    deploy restart killed their run. Returns the number of rows failed."""
    c = require_client()
    rows = (
        c.table("pipeline_runs").select("id")
        .eq("dataset", dataset).eq("status", "running")
        .execute().data or []
    )
    for r in rows:
        fail_run(r["id"], reason or INTERRUPTED_RUN_ERROR)
    return len(rows)


def fail_orphan_running_runs(
    older_than_minutes: int = ORPHAN_RUN_AFTER_MINUTES,
) -> int:
    """Fail pipeline_runs rows abandoned in 'running' by a dead process.

    When the service restarts mid-run the owning asyncio task dies with it, so
    nothing ever moves the row to a terminal state — the run reads as forever
    in-flight and the interruption is invisible (observed on staging 2026-07-27
    when the #905 deploy restart landed mid-regenerate).

    IMPORTANT — why the age cutoff rather than "fail everything running":
    staging and prod share one Supabase project, so both environments' rows
    live in this table. A blanket sweep at staging startup would kill a run the
    prod process is executing right then (and vice versa). Age is the only
    signal separating "owner is dead" from "owner is another live process",
    since rows carry no owner/heartbeat column — same reasoning as
    db/asks.fail_orphan_generating_ask_jobs. Runs on startup AND on the
    scheduler's 5-minute heal job, so an interrupted run is marked failed
    within ~an hour even if nobody retries."""
    from datetime import datetime, timedelta, timezone

    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
    ).isoformat()
    c = require_client()
    rows = (
        c.table("pipeline_runs").select("id")
        .eq("status", "running")
        .lt("started_at", cutoff)
        .execute().data or []
    )
    for r in rows:
        fail_run(r["id"], INTERRUPTED_RUN_ERROR)
    return len(rows)


def get_latest_run(dataset: str) -> dict[str, Any] | None:
    c = require_client()
    resp = (
        c.table("pipeline_runs").select("*")
        .eq("dataset", dataset)
        .order("started_at", desc=True)
        .limit(1).execute()
    )
    return resp.data[0] if resp.data else None


def list_runs(dataset: str, limit: int = 20) -> list[dict[str, Any]]:
    c = require_client()
    resp = (
        c.table("pipeline_runs").select("*")
        .eq("dataset", dataset)
        .order("started_at", desc=True)
        .limit(limit).execute()
    )
    return resp.data or []
