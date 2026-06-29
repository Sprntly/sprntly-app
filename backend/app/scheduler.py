"""APScheduler integration for the intelligence pipeline.

Runs inside the FastAPI process. Two jobs (opt-in via SCHEDULER_ENABLED=true):

  weekly_brief_tick  — fires every WEEKLY_BRIEF_TICK_MINUTES and, for each
                       company, generates the weekly brief iff the company's
                       local Monday-06:00 firing window is open (v0 checklist
                       2.4). Timezone comes from the company owner's
                       profiles.timezone (default UTC). All day/time/tz/DST logic
                       lives in the pure, unit-testable app.brief_schedule
                       module; this job is the thin shell that ticks a clock and
                       drives it per company.
  refresh_connectors — re-pulls connector data into the KG every
                       PIPELINE_INTERVAL_HOURS so the brief reads fresh data.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app import db
from app.brief_schedule import resolve_user_timezone, should_run_weekly_brief
from app.config import settings
from app.db.companies import list_companies
from app.kg_ingest.auto_sync import kickoff_sync
from app.kg_ingest.runner import PULLERS

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

# Per-company "last weekly brief fired at" ledger (company_id → aware UTC dt),
# the once-per-week guard handed to should_run_weekly_brief. In-memory only: a
# process restart re-evaluates from scratch, which is safe — should_run_weekly_brief
# keys off the local Monday-09:00 firing WINDOW (default 1h), so a restart can at
# most regenerate one brief inside an open window, and brief generation is itself
# idempotent/refresh-gated (synthesis_brief.generate_brief_for skips when the KG
# is unchanged). Bounded by the company count.
_last_brief_run: dict[str, datetime] = {}


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


async def _run_weekly_brief_tick(now: datetime | None = None) -> None:
    """Generate the weekly brief for every company whose local Monday-06:00
    firing window is open right now (v0 checklist 2.4).

    Ticks every WEEKLY_BRIEF_TICK_MINUTES. For each company it:
      1. resolves the timezone from the company owner's profiles.timezone
         (default UTC),
      2. asks the PURE app.brief_schedule.should_run_weekly_brief whether the
         company's local Monday-06:00 window is open and not yet fired this week,
      3. if due, generates that company's brief from current KG state and records
         the run in the in-memory once-per-week ledger.

    All scheduling intelligence is in the pure function — this shell only owns
    the clock, the per-company iteration, the ledger, and error isolation. One
    company raising (unknown slug, empty KG, LLM hiccup) is logged and skipped so
    the rest of the tick still runs. ``now`` is injectable for tests; defaults to
    real UTC now.
    """
    now = now or datetime.now(timezone.utc)

    try:
        companies = list_companies()
    except Exception as exc:  # noqa: BLE001
        logger.error("Weekly brief tick: failed to list companies: %s", exc)
        return

    if not companies:
        return

    for company in companies:
        company_id = company.get("id")
        slug = company.get("slug") or company_id
        if not slug:
            continue
        tz = resolve_user_timezone(company.get("owner_timezone"))
        last_run = _last_brief_run.get(company_id) if company_id else None
        if not should_run_weekly_brief(now, tz, last_run):
            continue

        logger.info(
            "Weekly brief tick: company=%s (slug=%s, tz=%s) is due — generating",
            company_id, slug, tz.key,
        )
        try:
            await _generate_weekly_brief_for_company(slug)
            if company_id:
                _last_brief_run[company_id] = now.astimezone(timezone.utc)
            logger.info("Weekly brief tick: brief for %s → ok", slug)
        except Exception as exc:  # noqa: BLE001 — per-company isolation
            logger.error("Weekly brief tick: brief failed for %s: %s", slug, exc)


async def _generate_weekly_brief_for_company(slug: str) -> None:
    """Generate one company's weekly brief via the KG synthesis engine, off the
    event loop (LLM + Supabase are blocking). Synthesis is the only path since
    the legacy brief/KG engine was retired (main #321)."""
    from app.brief_runner import warm_synthesis_drilldowns
    from app.synthesis_brief import generate_brief_for

    await asyncio.to_thread(generate_brief_for, slug)
    # Warm evidence/PRD/Ask drill-downs so the first user click is instant.
    # Error-isolated in the helper.
    warm_synthesis_drilldowns(slug)


async def _run_drip_email_cycle() -> None:
    """Onboarding drip / nudge email cycle (v0 checklist 2.1).

    Runs the per-company drip sender for every tenant: members who have
    crossed a cadence step's day_offset and haven't yet received it get the
    email, tracked in drip_email_sends so steps never double-send. The whole
    pass is error-isolated inside run_drip_cycle; the blocking Supabase +
    Resend HTTP work is pushed off the event loop so it can't stall the
    scheduler thread. Independent of BRIEF_ENGINE."""
    from app.drip_email import run_drip_cycle

    try:
        summary = await asyncio.to_thread(run_drip_cycle)
        logger.info("Scheduler: drip cycle → %s", summary)
    except Exception as exc:  # noqa: BLE001 — never let one cycle kill the job
        logger.error("Scheduler: drip cycle failed: %s", exc)


async def _run_brief_nudge_cycle() -> None:
    """Brief-nudge reminder cycle: send the due Day 1/2/3 reminder for each
    company's current brief while it's still unopened (Day 0 is sent inline at
    generation). Mirrors the drip job: error-isolated inside run_nudge_cycle,
    blocking Supabase + Slack HTTP pushed off the event loop. Opt-in via
    BRIEF_NUDGE_ENABLED."""
    from app.brief_nudge import run_nudge_cycle

    try:
        summary = await asyncio.to_thread(run_nudge_cycle)
        logger.info("Scheduler: brief nudge cycle → %s", summary)
    except Exception as exc:  # noqa: BLE001 — never let one cycle kill the job
        logger.error("Scheduler: brief nudge cycle failed: %s", exc)


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
    tick_minutes = getattr(settings, "weekly_brief_tick_minutes", 15)

    _scheduler = AsyncIOScheduler()
    # Weekly brief: tick frequently, generate per company only when that company's
    # local Monday-09:00 firing window is open (v0 checklist 2.4). The day/time/tz
    # decision is the pure app.brief_schedule.should_run_weekly_brief, so the
    # cadence here just has to be finer than the firing window — it does NOT set
    # the send time. Replaces the old fire-every-N-hours brief cycle.
    _scheduler.add_job(
        _run_weekly_brief_tick,
        trigger=IntervalTrigger(minutes=tick_minutes),
        id="weekly_brief_tick",
        name=(
            f"Weekly brief — Monday 09:00 per company tz "
            f"(tick every {tick_minutes}m)"
        ),
        replace_existing=True,
    )
    # Second job: refresh the KG from upstream connectors so the corpus stays
    # fresh. Keeps the existing ~6h cadence — connector freshness is decoupled
    # from the once-a-week brief send time.
    _scheduler.add_job(
        _refresh_all_company_connectors,
        trigger=IntervalTrigger(hours=interval_hours),
        id="refresh_connectors",
        name=f"Refresh connector data (every {interval_hours}h)",
        replace_existing=True,
    )
    # Third job: onboarding drip / nudge emails (v0 checklist 2.1). Opt-in via
    # DRIP_EMAILS_ENABLED, on its own cadence (DRIP_INTERVAL_HOURS) since the
    # drip pass is cheap and benefits from finer granularity than the brief
    # cycle. Independent of BRIEF_ENGINE.
    if settings.drip_emails_enabled:
        drip_hours = getattr(settings, "drip_interval_hours", 6) or 6
        _scheduler.add_job(
            _run_drip_email_cycle,
            trigger=IntervalTrigger(hours=drip_hours),
            id="drip_emails",
            name=f"Onboarding drip emails (every {drip_hours}h)",
            replace_existing=True,
        )

    # Brief nudges: Day 1/2/3 reminders that drive users to open their weekly
    # brief (Day 0 fires inline at generation). Opt-in via BRIEF_NUDGE_ENABLED;
    # idempotent + open-state-gated so extra ticks are cheap no-ops.
    if settings.brief_nudge_enabled:
        nudge_hours = getattr(settings, "brief_nudge_interval_hours", 6) or 6
        _scheduler.add_job(
            _run_brief_nudge_cycle,
            trigger=IntervalTrigger(hours=nudge_hours),
            id="brief_nudges",
            name=f"Brief nudge reminders (every {nudge_hours}h)",
            replace_existing=True,
        )

    # Synthetic sign-in monitor: authenticate the Google OAuth client against
    # Google's token endpoint on an interval; alert if the secret is rejected
    # (the 2026-06-22 silent "Sign in with Google" break). Only registered when
    # enabled and a Google client is configured.
    if (
        settings.signin_monitor_enabled
        and settings.google_client_id
        and settings.google_client_secret
    ):
        from app.signin_monitor import run_google_signin_health_check

        signin_mins = getattr(settings, "signin_monitor_interval_minutes", 15) or 15
        _scheduler.add_job(
            run_google_signin_health_check,
            trigger=IntervalTrigger(minutes=signin_mins),
            id="signin_health_monitor",
            name=f"Synthetic Google sign-in monitor (every {signin_mins}m)",
            replace_existing=True,
        )

    # Connector health monitor: re-validate every active connector's stored
    # OAuth/API token on an interval and persist the result, so a dead connector
    # surfaces in the UI proactively (not just on-open) and we email a
    # healthy→disconnected transition alert. Opt-in via CONNECTOR_HEALTH_ENABLED.
    if settings.connector_health_enabled:
        from app.connector_health import run_connector_health_check

        ch_mins = (
            getattr(settings, "connector_health_interval_minutes", 60) or 60
        )
        _scheduler.add_job(
            run_connector_health_check,
            trigger=IntervalTrigger(minutes=ch_mins),
            id="connector_health_monitor",
            name=f"Connector token health monitor (every {ch_mins}m)",
            replace_existing=True,
        )

    _scheduler.start()
    logger.info(
        "Scheduler started: weekly brief tick every %dm "
        "(Monday 09:00 per-company tz) + connector refresh every %dh%s",
        tick_minutes, interval_hours,
        " + drip emails" if settings.drip_emails_enabled else "",
    )


def shutdown_scheduler() -> None:
    """Gracefully shut down the scheduler. Call from FastAPI lifespan teardown."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler shut down")
