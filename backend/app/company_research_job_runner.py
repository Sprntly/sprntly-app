"""Background worker for the onboarding-triggered deep company research.

`POST /v1/onboarding/analyze-website` kicks this alongside the small website
analysis. Unlike that flow there is NO client polling: the wizard proceeds
immediately and never waits, so the run is entirely server-side and the
`company_research_runs` row is the only handle on it (abandonment-proof by
construction — closing the tab changes nothing).

Mirrors website_analysis_job_runner: the blocking sweep runs in a worker thread
(`asyncio.to_thread`) so the event loop stays free, and any failure is caught
here — `execute_run` has already marked the row `failed`, so this layer only has
to keep the exception from escaping into the task. The onboarding response has
long since returned either way; a research failure must never affect it.
"""
import asyncio
import logging

from app.company_research import execute_run

logger = logging.getLogger(__name__)


def _run_sync(company_id: str, url: str, trigger: str) -> dict:
    return execute_run(company_id, url=url, trigger=trigger)


async def run_company_research_job(
    company_id: str, url: str, *, trigger: str = "onboarding"
) -> None:
    """Run the deep company-research sweep in a worker thread.

    Never raises: the run row is the record of what happened (`execute_run`
    marks it `completed` or `failed`), and a double trigger while a run is live
    is a logged no-op rather than a second sweep.
    """
    logger.info(
        "company-research job starting company=%s url=%s trigger=%s",
        company_id, url, trigger,
    )
    try:
        result = await asyncio.to_thread(_run_sync, company_id, url, trigger)
        if result.get("reason") == "already_running":
            logger.info(
                "company-research job skipped company=%s — run already live",
                company_id,
            )
        else:
            logger.info(
                "company-research job finished company=%s run_id=%s records=%s "
                "signals=%s", company_id, result.get("run_id"),
                len(result.get("records") or []), result.get("signals"),
            )
    except Exception:  # noqa: BLE001 — best-effort; never crash the worker
        logger.exception(
            "company-research job failed company=%s url=%s", company_id, url)
