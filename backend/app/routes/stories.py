"""User-story endpoints — generate from a PRD, then push into ClickUp.

  POST /v1/stories/generate  {prd_id}            -> generated stories (no write)
  POST /v1/stories/lists                          -> ClickUp lists to pick a target
  POST /v1/stories/push      {list_id, stories}  -> create the stories in ClickUp

Generation and push are kept SEPARATE on purpose: generation never touches the
user's tracker, so the user reviews the stories before any are written. Push is
the explicit, outward-facing write. All routes require_company (tenant scoped).
"""
from __future__ import annotations

import asyncio
import itertools
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.connectors import clickup_oauth
from app.stories.generate import (
    PRDNotFoundError,
    Story,
    generate_user_stories,
)
from app.stories.push import ClickUpNotConnectedError, push_stories_to_clickup

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/stories", tags=["stories"])

# ── Async story generation jobs ──────────────────────────────────────────────
# Breaking a PRD into tickets is a multi-minute LLM call. Running it inside the
# request (the old synchronous POST) made the Tickets tab hang/spin for minutes
# and risked proxy/browser timeouts. So generation is now fire-and-forget: POST
# schedules a background task and returns a job id immediately; the client polls
# GET /jobs/{id} until status is "ready" or "failed".
#
# Stories are TRANSIENT — they were never persisted (the user reviews them, then
# pushes to ClickUp). So the job store is in-memory, matching the established
# single-uvicorn-worker pattern elsewhere (e.g. app/brief_runner.py). A backend
# restart drops in-flight jobs; the user simply re-opens the tab to regenerate.
# All dict mutations happen on the event loop (the to_thread result is applied
# back in the coroutine), so no lock is needed under the single-worker topology.
_jobs: dict[int, dict] = {}
_job_ids = itertools.count(1)
_JOBS_CAP = 200  # keep the store bounded over a long-running process
_inflight_tasks: set[asyncio.Task] = set()


def _prune_jobs() -> None:
    """Bound the in-memory store: drop the oldest finished jobs past the cap."""
    if len(_jobs) <= _JOBS_CAP:
        return
    finished = [jid for jid, j in _jobs.items() if j["status"] in ("ready", "failed")]
    for jid in sorted(finished)[: len(_jobs) - _JOBS_CAP]:
        _jobs.pop(jid, None)


class GenerateIn(BaseModel):
    prd_id: int | None = Field(default=None, ge=1)
    insight: str | None = None


class StoryIn(BaseModel):
    title: str
    body: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    priority: str | None = None
    route: str | None = None


class PushIn(BaseModel):
    list_id: str = Field(..., min_length=1)
    stories: list[StoryIn] = Field(..., min_length=1)


@router.post("/generate")
async def generate(
    body: GenerateIn,
    company: CompanyContext = Depends(require_company),
):
    """Kick off user-story generation from a PRD (or a free-form insight).

    Fire-and-forget: schedules the multi-minute generation in the background and
    returns a `job_id` immediately so the Tickets tab never blocks on a hung
    request. Poll GET /v1/stories/jobs/{job_id} until status is "ready" (carries
    `stories`) or "failed" (carries `error`). Generation never writes to ClickUp
    — call /v1/stories/push separately once the user has reviewed.
    """
    if (body.prd_id is None) == (body.insight is None):
        raise HTTPException(400, "provide exactly one of prd_id or insight")

    job_id = next(_job_ids)
    _jobs[job_id] = {
        "id": job_id,
        "company_id": company.company_id,
        "status": "generating",
        "stories": None,
        "error": None,
    }
    _prune_jobs()

    async def _run() -> None:
        try:
            stories = await asyncio.to_thread(
                generate_user_stories,
                company.company_id, prd_id=body.prd_id, insight=body.insight,
            )
            job = _jobs.get(job_id)
            if job is not None:
                job["status"] = "ready"
                job["stories"] = [s.to_dict() for s in stories]
        except PRDNotFoundError as exc:
            job = _jobs.get(job_id)
            if job is not None:
                job["status"], job["error"] = "failed", str(exc)
        except Exception as exc:  # noqa: BLE001 — surface, never hang the poll
            logger.exception("story generation failed job_id=%s", job_id)
            job = _jobs.get(job_id)
            if job is not None:
                job["status"], job["error"] = "failed", f"{type(exc).__name__}: {exc}"

    task = asyncio.create_task(_run())
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)

    return {"job_id": job_id, "status": "generating"}


@router.get("/jobs/{job_id}")
def get_job(
    job_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Poll a story-generation job. 404 for an unknown job or a foreign tenant
    (job ids are sequential integers, so bind to the caller's company)."""
    job = _jobs.get(job_id)
    if job is None or job["company_id"] != company.company_id:
        raise HTTPException(404, "Job not found")
    out: dict = {"job_id": job_id, "status": job["status"]}
    if job["status"] == "ready":
        out["stories"] = job["stories"] or []
    elif job["status"] == "failed":
        out["error"] = job["error"]
    return out


@router.post("/lists")
def clickup_lists(company: CompanyContext = Depends(require_company)):
    """List the ClickUp lists this company can push into (target picker).

    404 if ClickUp isn't connected.
    """
    from app.stories.push import _clickup_access_token

    try:
        token = _clickup_access_token(company.company_id)
    except ClickUpNotConnectedError as e:
        raise HTTPException(404, str(e)) from e
    return {"lists": clickup_oauth.list_lists(token)}


@router.post("/push")
def push(
    body: PushIn,
    company: CompanyContext = Depends(require_company),
):
    """Create the given stories as tasks in a ClickUp list (explicit write).

    404 if ClickUp isn't connected. Per-story failures are isolated and
    reported in `errors` rather than failing the whole batch.
    """
    stories = [
        Story(
            title=s.title,
            body=s.body,
            acceptance_criteria=s.acceptance_criteria,
            priority=s.priority,
            route=s.route,
        )
        for s in body.stories
    ]
    try:
        result = push_stories_to_clickup(company.company_id, body.list_id, stories)
    except ClickUpNotConnectedError as e:
        raise HTTPException(404, str(e)) from e
    return result
