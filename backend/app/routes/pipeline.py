"""Pipeline management routes.

  POST /v1/pipeline/{dataset}/run     → manual trigger
  GET  /v1/pipeline/{dataset}/status  → last run status
  GET  /v1/pipeline/runs              → list recent runs
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from app.auth import CompanyContext, require_company
from app.db.pipeline_runs import get_latest_run, list_runs
from app.deps.ownership import require_owned_dataset

router = APIRouter(prefix="/v1/pipeline", tags=["pipeline"])


@router.post("/{dataset}/run")
async def trigger_pipeline(
    dataset: str,
    company: CompanyContext = Depends(require_company),
):
    """Trigger a full pipeline run for a dataset.

    Returns immediately with a run_id. The pipeline runs in the background.
    """
    # Tenant guard: only run a pipeline on a dataset the caller's company owns
    # (404 otherwise — these are expensive runs and the slug is low-entropy).
    require_owned_dataset(dataset, company.company_id)
    from app.pipeline import run_full_pipeline

    # Fire-and-forget: run the pipeline in the background
    task = asyncio.create_task(run_full_pipeline(dataset, trigger="manual"))

    return {
        "started": True,
        "dataset": dataset,
        "message": "Pipeline started in background. Poll GET /v1/pipeline/{dataset}/status for progress.",
    }


@router.get("/{dataset}/status")
def pipeline_status(
    dataset: str,
    company: CompanyContext = Depends(require_company),
):
    """Get the latest pipeline run status for a dataset."""
    require_owned_dataset(dataset, company.company_id)
    try:
        run = get_latest_run(dataset)
    except Exception:
        # Table may not exist yet
        return {"dataset": dataset, "status": "no_runs"}

    if not run:
        return {"dataset": dataset, "status": "no_runs"}

    return {
        "dataset": dataset,
        "run_id": run.get("id"),
        "status": run.get("status"),
        "trigger": run.get("trigger"),
        "stages": run.get("stages", {}),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "error": run.get("error"),
    }


@router.get("/runs")
def pipeline_runs_list(
    dataset: str,
    limit: int = 20,
    company: CompanyContext = Depends(require_company),
):
    """List recent pipeline runs for a dataset."""
    require_owned_dataset(dataset, company.company_id)
    try:
        runs = list_runs(dataset, limit=limit)
    except Exception:
        return {"runs": []}

    return {"runs": runs}
