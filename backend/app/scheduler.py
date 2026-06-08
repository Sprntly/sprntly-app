"""APScheduler integration for the intelligence pipeline.

Runs inside the FastAPI process. Triggers the full pipeline
orchestrator on a configurable cron interval (default: every 6 hours).

Opt-in via SCHEDULER_ENABLED=true in .env.
"""
from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _run_synthesis_for_all_companies() -> None:
    """KG-synthesis cycle: seed-if-empty + run_synthesis per company.

    Iterates every tenant and runs the SAME engine the UI write endpoints use
    (app.synthesis_brief.generate_brief_for). Error-isolated per company — one
    company raising (unknown slug, empty KG, LLM/gateway hiccup) is logged and
    skipped so the rest of the cycle still runs. run_synthesis save_brief()s
    each result into the `briefs` table the UI reads.
    """
    from app.db.companies import list_companies
    from app.synthesis_brief import generate_brief_for

    try:
        companies = list_companies()
    except Exception as exc:
        logger.error("Scheduler: failed to list companies: %s", exc)
        return

    if not companies:
        logger.info("Scheduler: no companies found, skipping synthesis cycle")
        return

    logger.info("Scheduler: starting synthesis cycle for %d companies", len(companies))

    for company in companies:
        slug = company.get("slug") or company.get("id")
        try:
            # generate_brief_for is blocking (LLM + Supabase); keep it off the
            # event loop so one slow company can't stall the scheduler thread.
            await asyncio.to_thread(generate_brief_for, slug)
            logger.info("Scheduler: synthesis brief for %s → ok", slug)
        except Exception as exc:  # noqa: BLE001 — per-company isolation
            logger.error("Scheduler: synthesis failed for %s: %s", slug, exc)


async def _run_pipeline_for_all_datasets() -> None:
    """Run the legacy full pipeline for every registered dataset."""
    from app import db
    from app.pipeline import run_full_pipeline

    try:
        slugs = db.list_dataset_slugs()
    except Exception as exc:
        logger.error("Scheduler: failed to list datasets: %s", exc)
        return

    if not slugs:
        logger.info("Scheduler: no datasets found, skipping pipeline")
        return

    logger.info("Scheduler: starting pipeline for %d datasets: %s", len(slugs), slugs)

    for slug in slugs:
        try:
            result = await run_full_pipeline(slug, trigger="scheduled")
            status = result.get("status", "unknown")
            logger.info("Scheduler: pipeline for %s → %s", slug, status)
        except Exception as exc:
            logger.error("Scheduler: pipeline failed for %s: %s", slug, exc)


async def _run_scheduled_cycle() -> None:
    """Dispatch the scheduled cycle to the engine selected by BRIEF_ENGINE.

    "synthesis" (default) → KG seed + run_synthesis per company.
    "legacy"              → the placeholder full pipeline per dataset.
    """
    if settings.brief_engine == "synthesis":
        await _run_synthesis_for_all_companies()
    else:
        await _run_pipeline_for_all_datasets()


def start_scheduler() -> None:
    """Initialize and start the APScheduler. Call from FastAPI lifespan."""
    global _scheduler

    if not settings.scheduler_enabled:
        logger.info("Scheduler disabled (SCHEDULER_ENABLED=false)")
        return

    interval_hours = getattr(settings, "pipeline_interval_hours", 6)
    engine = settings.brief_engine
    job_name = (
        f"KG synthesis cycle (every {interval_hours}h)"
        if engine == "synthesis"
        else f"Full pipeline cycle (every {interval_hours}h)"
    )

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _run_scheduled_cycle,
        trigger=IntervalTrigger(hours=interval_hours),
        id="pipeline_full_cycle",
        name=job_name,
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "Scheduler started: %s engine runs every %d hours", engine, interval_hours,
    )


def shutdown_scheduler() -> None:
    """Gracefully shut down the scheduler. Call from FastAPI lifespan teardown."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler shut down")
