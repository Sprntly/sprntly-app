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

from app import db
from app.config import settings
from app.db.companies import list_companies
from app.kg_ingest.auto_sync import kickoff_sync
from app.kg_ingest.runner import PULLERS

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _refresh_all_company_connectors() -> None:
    """Periodic refresh: kick the KG ingest puller for every active
    (company × KG-puller-provider) pair.

    Without this, the KG (and corpus) only refreshes:
      - once at OAuth-connect time via app.connectors.* → kickoff_sync
      - when a user manually clicks Sync in Settings
    Briefs would be generated off whatever data was current at install
    time + manual syncs. This job closes that gap by re-running the
    pullers every `pipeline_interval_hours` so the home chat / brief /
    KG synthesis always read recent connector data.

    Per-company isolated: a db.list_connections raise for one tenant is
    logged and the loop moves on. kickoff_sync itself is fire-and-forget
    (spawns a daemon thread; see auto_sync.py) and never raises — so this
    function returns quickly without waiting on any provider's HTTP call."""
    try:
        companies = list_companies() or []
    except Exception:
        logger.exception("refresh-connectors: failed to list companies")
        return

    if not companies:
        return

    for company in companies:
        company_id = company.get("id")
        if not company_id:
            continue
        try:
            connections = db.list_connections(company_id) or []
        except Exception:
            logger.exception(
                "refresh-connectors: list_connections failed for company %s",
                company_id,
            )
            continue
        for conn in connections:
            if conn.get("status") != "active":
                continue
            provider = (conn.get("provider") or "").strip()
            # Only fire for providers with a registered KG puller. Others
            # (figma / slack / google_drive) have their own corpus paths,
            # are per-user, or aren't wired for periodic refresh.
            if not provider or provider not in PULLERS:
                continue
            try:
                kickoff_sync(company_id, provider)
            except Exception:
                # kickoff_sync is designed not to raise, but be defensive
                # so a regression there can't kill the cycle.
                logger.exception(
                    "refresh-connectors: kickoff_sync raised for %s/%s",
                    company_id, provider,
                )


async def _run_synthesis_for_all_companies() -> None:
    """KG-synthesis cycle: seed-if-empty + run_synthesis per company.

    Iterates every tenant and runs the SAME engine the UI write endpoints use
    (app.synthesis_brief.generate_brief_for). Error-isolated per company — one
    company raising (unknown slug, empty KG, LLM/gateway hiccup) is logged and
    skipped so the rest of the cycle still runs. run_synthesis save_brief()s
    each result into the `briefs` table the UI reads.
    """
    from app.brief_runner import warm_synthesis_drilldowns
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
            # Parity with the legacy path: warm evidence/PRD/Ask drill-downs so
            # the first user click is instant. Error-isolated in the helper.
            warm_synthesis_drilldowns(slug)
        except Exception as exc:  # noqa: BLE001 — per-company isolation
            logger.error("Scheduler: synthesis failed for %s: %s", slug, exc)


async def _run_scheduled_cycle() -> None:
    """Run the scheduled KG-synthesis cycle: seed + run_synthesis per company."""
    await _run_synthesis_for_all_companies()


def start_scheduler() -> None:
    """Initialize and start the APScheduler. Call from FastAPI lifespan."""
    global _scheduler

    if not settings.scheduler_enabled:
        logger.info("Scheduler disabled (SCHEDULER_ENABLED=false)")
        return

    interval_hours = getattr(settings, "pipeline_interval_hours", 6)
    job_name = f"KG synthesis cycle (every {interval_hours}h)"

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _run_scheduled_cycle,
        trigger=IntervalTrigger(hours=interval_hours),
        id="pipeline_full_cycle",
        name=job_name,
        replace_existing=True,
    )
    # Second job: refresh the KG from upstream connectors so the corpus stays
    # fresh. Same cadence as the brief cycle.
    _scheduler.add_job(
        _refresh_all_company_connectors,
        trigger=IntervalTrigger(hours=interval_hours),
        id="refresh_connectors",
        name=f"Refresh connector data (every {interval_hours}h)",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "Scheduler started: KG synthesis cycle + connector refresh, every %d hours",
        interval_hours,
    )


def shutdown_scheduler() -> None:
    """Gracefully shut down the scheduler. Call from FastAPI lifespan teardown."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler shut down")
