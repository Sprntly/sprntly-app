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
