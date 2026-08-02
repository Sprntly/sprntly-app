"""Background worker for the async Business Context refresh.

`POST /v1/company/business-context/refresh` moves the company's refresh
status to 'generating' and schedules `run_business_context_refresh_job` as a
fire-and-forget task; this module runs the SAME `run_business_context(...)`
pipeline the old synchronous endpoint ran, and writes the result onto the
company's business_context_refresh_status/error columns.

No change to run_business_context() itself — this module only changes HOW
and WHEN it's invoked and awaited. Mirrors app/ask_job_runner.py: a worker
wrapped so a failure marks the row 'error' and never crashes the event loop
(the caller holds a strong ref via routes/business_context.py's
`_inflight_tasks`, the same reason app/routes/ask.py holds one)."""
from __future__ import annotations

import asyncio
import logging

from app.db.business_context_refresh import (
    ORPHAN_BUSINESS_CONTEXT_REFRESH_HEARTBEAT_SECONDS,
    complete_business_context_refresh,
    fail_business_context_refresh,
    touch_business_context_refresh,
)
from app.graph.facade import GraphFacade
from app.research.business_context_agent import run_business_context

logger = logging.getLogger(__name__)


async def _heartbeat(company_id: str) -> None:
    """Bump the row's heartbeat while this worker is alive — see the module
    docstring on app.db.business_context_refresh for why this exists (the
    ask_jobs incident this ticket is designed around: an age-only orphan
    check can reap a healthy-but-slow job out from under itself)."""
    try:
        while True:
            await asyncio.sleep(ORPHAN_BUSINESS_CONTEXT_REFRESH_HEARTBEAT_SECONDS)
            if not await asyncio.to_thread(touch_business_context_refresh, company_id):
                return  # no longer generating — nothing left to keep alive
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a heartbeat failure must never fail the refresh
        logger.exception(
            "business-context refresh heartbeat loop failed company_id=%s", company_id
        )


def _run_sync(company_id: str) -> None:
    """The blocking pipeline call, run on a worker thread. A fresh
    GraphFacade per run, same as the old synchronous route handler."""
    facade = GraphFacade()
    run_business_context(facade, company_id)


async def run_business_context_refresh_job(company_id: str) -> None:
    """Run the Business Context agent in a worker thread; update the
    company's refresh-status columns. A failure marks the row 'error' and is
    swallowed — the worker never crashes the event loop."""
    logger.info("Business context refresh job starting company_id=%s", company_id)
    beat = asyncio.create_task(_heartbeat(company_id))
    try:
        await asyncio.to_thread(_run_sync, company_id)
        complete_business_context_refresh(company_id)
        logger.info(
            "Business context refresh job succeeded company_id=%s", company_id
        )
    except Exception as exc:  # noqa: BLE001 — best-effort; never crash the worker
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception(
            "Business context refresh job failed company_id=%s", company_id
        )
        try:
            fail_business_context_refresh(company_id, msg)
        except Exception:  # noqa: BLE001 — even the fail-marking is best-effort
            logger.exception(
                "fail_business_context_refresh failed company_id=%s", company_id
            )
    finally:
        beat.cancel()
