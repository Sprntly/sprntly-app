"""Onboarding routes.

POST /v1/onboarding/analyze-website — from a product website URL, infer the
company's industry / business-type / a readable business-context brief /
suggested success metrics (the onboarding redesign pre-fills these; the user can
always edit). Tenant-scoped via require_company; the analysis is persisted to
the caller's company business_context.

Fire-and-forget (blur/remount-safe), mirroring the chat Ask flow: the POST
persists a `generating` row in `website_analysis_jobs`, kicks the same
`analyze_website(...)` pipeline in a background task, and returns a `job_id`; the
client polls GET /v1/onboarding/analyze-website/{job_id}. A backgrounded or
remounted onboarding tab keeps the analysis running server-side and re-attaches
by polling, instead of orphaning the in-flight request.

Resilient by design: the analyzer NEVER raises — a blocked / unreachable / empty
site returns `ok: false` with empty fields so onboarding falls back to manual
entry. The GET's `result` carries the SAME dict `analyze_website` returns today,
so the onboarding form's setWebsiteAnalysis(result) consumes an unchanged shape.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import CompanyContext, require_company
from app.db import (
    get_analysis_job,
    start_analysis_job,
)
from app.website_analysis_job_runner import run_analysis_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/onboarding", tags=["onboarding"])


# Strong refs to in-flight background website-analysis tasks. asyncio holds only
# a weak reference to a bare create_task result, so without this the task can be
# garbage-collected mid-run and the row would be stuck 'generating'. The
# done-callback discards each task on completion (mirrors routes/ask.py).
_inflight_tasks: set[asyncio.Task] = set()


class AnalyzeWebsiteIn(BaseModel):
    url: str


@router.post("/analyze-website")
async def analyze_website_route(
    body: AnalyzeWebsiteIn,
    company: CompanyContext = Depends(require_company),
):
    """Kick off a website analysis for the caller's company, returning
    `{job_id, status}`.

    Fire-and-forget — the analysis keeps running server-side so a backgrounded
    or remounted onboarding tab re-attaches by polling
    GET /v1/onboarding/analyze-website/{job_id} instead of orphaning the
    request. Always 200; the analysis itself signals graceful degrade via the
    `ok`/`reason` fields on the GET's `result`, never an HTTP error.
    """
    company_id = company.company_id
    job_id = start_analysis_job(company_id=company_id, url=body.url)

    if "pytest" in sys.modules:
        # The TestClient does not keep the app's event loop alive between
        # requests, so a fire-and-forget create_task would never run and the
        # client's status-poll would spin forever. Run the worker inline under
        # pytest for deterministic results (mirrors routes/ask.py). Production
        # keeps the non-blocking create_task path below.
        await run_analysis_job(job_id, company_id, body.url)
        row = get_analysis_job(job_id)
        return {"job_id": job_id, "status": (row or {}).get("status", "ready")}

    task = asyncio.create_task(run_analysis_job(job_id, company_id, body.url))
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
    return {"job_id": job_id, "status": "generating"}


@router.get("/analyze-website/{job_id}")
def get_analyze_website(
    job_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Status + result for a website-analysis job.

    Returns `{status, result, error}`. Once `status == 'ready'` the `result`
    field carries the SAME analysis dict `analyze_website` returns today (ok /
    reason / industry / business_type / business_context / suggested_metrics /
    ...), so the onboarding form's setWebsiteAnalysis(result) is unchanged. 404
    if the job doesn't belong to the caller's company (no cross-tenant existence
    disclosure).
    """
    row = get_analysis_job(job_id)
    if not row or row.get("company_id") != company.company_id:
        raise HTTPException(404, "Analysis job not found")
    return {
        "status": row.get("status") or "generating",
        "result": row.get("result"),
        "error": row.get("error"),
    }
