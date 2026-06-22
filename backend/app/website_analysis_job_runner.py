"""Background worker for the blur-safe onboarding website-analysis flow.

`POST /v1/onboarding/analyze-website` persists a `generating` row in
`website_analysis_jobs` and schedules `run_analysis_job` as a fire-and-forget
task; this module runs the SAME `analyze_website(...)` pipeline the old
synchronous endpoint ran and writes the full analysis dict onto the job row
(status → `ready`). A backgrounded / remounted onboarding tab keeps the
analysis running server-side and re-attaches by polling
GET /v1/onboarding/analyze-website/{id}.

Mirrors ask_job_runner: a worker-thread call wrapped so a failure marks the row
`error` and never crashes the task (the asyncio loop holds a strong ref via
routes/onboarding.py's `_inflight_tasks`).

`analyze_website` is itself resilient (never raises — a blocked / unreachable /
empty site returns `ok: False`), so the `error` status here is a backstop for
truly unexpected infra failures only.
"""
import asyncio
import logging

from app.db import complete_analysis_job, fail_analysis_job
from app.onboarding.website_analysis import analyze_website

logger = logging.getLogger(__name__)


def _run_sync(job_id: int, company_id: str, url: str) -> None:
    result = analyze_website(company_id, url)
    complete_analysis_job(job_id, result)


async def run_analysis_job(job_id: int, company_id: str, url: str) -> None:
    """Run the website-analysis pipeline in a worker thread; update the job row
    with the result. A failure marks the row `error` and is swallowed — the
    worker never crashes the event loop."""
    logger.info("Website-analysis job starting job_id=%s url=%s", job_id, url)
    try:
        await asyncio.to_thread(_run_sync, job_id, company_id, url)
        logger.info("Website-analysis job succeeded job_id=%s", job_id)
    except Exception as exc:  # noqa: BLE001 — best-effort; never crash the worker
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("Website-analysis job failed job_id=%s", job_id)
        try:
            fail_analysis_job(job_id, msg)
        except Exception:  # noqa: BLE001 — even the fail-marking is best-effort
            logger.exception("fail_analysis_job failed job_id=%s", job_id)
