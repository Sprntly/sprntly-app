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


async def _run_pipeline_for_all_datasets() -> None:
    """Run the full pipeline for every registered dataset."""
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


def start_scheduler() -> None:
    """Initialize and start the APScheduler. Call from FastAPI lifespan."""
    global _scheduler

    if not settings.scheduler_enabled:
        logger.info("Scheduler disabled (SCHEDULER_ENABLED=false)")
        return

    interval_hours = getattr(settings, "pipeline_interval_hours", 6)

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _run_pipeline_for_all_datasets,
        trigger=IntervalTrigger(hours=interval_hours),
        id="pipeline_full_cycle",
        name=f"Full pipeline cycle (every {interval_hours}h)",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "Scheduler started: pipeline runs every %d hours", interval_hours,
    )


def shutdown_scheduler() -> None:
    """Gracefully shut down the scheduler. Call from FastAPI lifespan teardown."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler shut down")
