import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

# Guarantee the oauthlib scope-relax behavior in-process, regardless of whether
# the deploy .env sets it. Google's OAuth client (shared with sign-in) auto-adds
# openid / userinfo.email / userinfo.profile and may reorder/normalize openid,
# which would otherwise trip oauthlib's "Scope has changed" guard at token
# exchange. We request the full scope set explicitly (see connectors.google_oauth
# .DRIVE_SCOPES); this is the belt-and-suspenders for the reordering case. MUST
# run before any oauthlib import below (the connectors router pulls in
# google_auth_oauthlib transitively), so it sits at the very top of startup.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import auth, db, datasets as datasets_service
from app.config import settings
from app.sentry import init_sentry

# Initialise error tracking as early as possible (no-op unless SENTRY_DSN is
# set) so exceptions raised while importing routes / during startup are caught.
init_sentry()

from app.db.prototypes import (
    invalidate_orphan_generating_prototypes,
    invalidate_stale_prototypes,
)
from app.db.prototype_pending_iterations import invalidate_orphan_running_iterations
from app.db.design_agent_jobs import requeue_orphan_claimed_jobs  # Tier 2
from app.design_agent.prompts import DESIGN_AGENT_TEMPLATE_VERSION
from app.prompts import (
    ASK_CACHE_VERSION,
    BRIEF_SCHEMA_VERSION,
    EVIDENCE_TEMPLATE_VERSION,
    EVIDENCE_VARIANT,
    PRD_TEMPLATE_VERSION,
    PRD_VARIANT,
)
from app.routes import (
    admin,
    agent_chat,
    artifact_share,
    artifact_templates as artifact_templates_routes,
    artifacts,
    ask,
    brief,
    business_context as business_context_routes,
    chat,
    company,
    connectors,
    conversations,
    custom_skills as custom_skills_routes,
    datasets as datasets_routes,
    design_agent,
    design_agent_bundle,
    design_agent_comments,
    documents,
    feedback,
    ideation,
    ingest,
    internal_mcp,
    jira_write,
    metrics,
    mcp_tokens,
    multi_agent,
    onboarding,
    oncall,
    reports,
    reports_public,
    research,
    staff_admin,
    stories,
    synthesis,
    team,
    ticket_sets,
    tickets,
    transcripts,
    usage as usage_routes,
    workspaces as workspaces_routes,
    evidence,
    health,
    internal,
    pipeline,
    prd,
    prd_access,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def _startup_generate_briefs() -> None:
    """Generate startup briefs via the KG synthesis path per company.

    Runs off the event loop (it makes blocking LLM/Supabase calls).
    Error-isolated: a failure here is logged and never blocks or breaks startup.
    """
    try:
        from app.synthesis_brief import generate_all_synthesis_briefs
        await asyncio.to_thread(generate_all_synthesis_briefs)
    except Exception:  # noqa: BLE001 — startup must never break on brief gen
        logger.exception("Startup brief generation failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # Register any on-disk datasets that don't have a DB row yet — covers the
    # pre-existing `asurion` corpus and any sibling dirs added manually.
    seeded = datasets_service.seed_filesystem_datasets()
    if seeded:
        logger.info(
            "Seeded %d on-disk dataset(s) into the datasets table", seeded)
    # Demote any cached brief whose payload schema doesn't match the current
    # code. Startup brief generation will then treat affected datasets as empty
    # and regenerate them under the new schema on the next tick.
    invalidated = db.invalidate_stale_briefs(BRIEF_SCHEMA_VERSION)
    if invalidated:
        logger.info("Invalidated %d stale brief(s) (schema bump → v%d)",
                    invalidated, BRIEF_SCHEMA_VERSION)
    # Same for cached evidence docs — mismatched template_version → status
    # 'invalidated' so the next view regenerates under the current prompt.
    # Variant-scoped to the current EVIDENCE_VARIANT (v3, the HTML brief);
    # historical v1/v2 `:::block` rows are read-only and left untouched.
    ev_invalidated = db.invalidate_stale_evidences(
        EVIDENCE_TEMPLATE_VERSION, variant=EVIDENCE_VARIANT
    )
    if ev_invalidated:
        logger.info(
            "Invalidated %d stale evidence doc(s) (template bump → v%d)",
            ev_invalidated,
            EVIDENCE_TEMPLATE_VERSION,
        )
    # And the same for PRDs. Variant-scoped to the current PRD_VARIANT (v3, the
    # HTML PRD page); historical v1/v2 rows are read-only and left untouched so
    # they keep rendering under the legacy markdown path.
    prd_invalidated = db.invalidate_stale_prds(
        PRD_TEMPLATE_VERSION, variant=PRD_VARIANT
    )
    if prd_invalidated:
        logger.info(
            "Invalidated %d stale PRD(s) (template bump → v%d)",
            prd_invalidated,
            PRD_TEMPLATE_VERSION,
        )
    # Same for cached Ask responses (the predefined-prompt warm cache).
    ask_invalidated = db.invalidate_stale_cached_asks(ASK_CACHE_VERSION)
    if ask_invalidated:
        logger.info(
            "Invalidated %d stale cached Ask response(s) (cache bump → v%d)",
            ask_invalidated,
            ASK_CACHE_VERSION,
        )
    # Demote any orphaned 'generating' rows. The worker thread that owned
    # them died with the previous process; without this, find_existing_*
    # returns them and user clicks dedupe to a row that will never finish.
    ev_orphans = db.invalidate_orphan_generating_evidences()
    prd_orphans = db.invalidate_orphan_generating_prds()
    ask_orphans = db.invalidate_orphan_generating_cached_asks()
    if ev_orphans or prd_orphans or ask_orphans:
        logger.info(
            "Invalidated %d orphan generating evidence(s), %d PRD(s), %d cached Ask(s)",
            ev_orphans,
            prd_orphans,
            ask_orphans,
        )
    # Same for `ask_jobs` (the chat Ask flow the client polls). Age-gated rather
    # than "everything generating", because staging and prod share one Supabase
    # project — see fail_orphan_generating_ask_jobs. The scheduler repeats this
    # every 5m so an interrupted ask heals without waiting for a restart.
    job_ask_orphans = db.fail_orphan_generating_ask_jobs()
    if job_ask_orphans:
        logger.info(
            "Failed %d orphan generating Ask job(s)",
            job_ask_orphans,
        )
    # Same for pipeline_runs (the regenerate / regenerate-all durable run rows):
    # a restart mid-run leaves the row 'running' forever with no owner. Age-
    # gated for the same shared-Supabase reason as ask_jobs above; the
    # scheduler's heal job repeats this every 5m, and a user retry supersedes
    # the stale row immediately (routes/brief._start_durable_run).
    try:
        run_orphans = db.fail_orphan_running_runs()
        if run_orphans:
            logger.info(
                "Failed %d orphan running pipeline run(s) (restart interrupt)",
                run_orphans,
            )
    except Exception:  # noqa: BLE001 — startup must never break on bookkeeping
        logger.exception("Orphan pipeline-run sweep failed at startup")
    # Same for company_research_runs (the deep web-research sweep): a restart
    # mid-run leaves the row 'running' forever, which would also wedge the
    # in-flight guard so the company could never be researched again. Age-gated
    # for the same shared-Supabase reason; repeated by the scheduler every 5m.
    try:
        research_orphans = db.fail_orphan_company_research_runs()
        if research_orphans:
            logger.info(
                "Failed %d orphan running company-research run(s) "
                "(restart interrupt)",
                research_orphans,
            )
    except Exception:  # noqa: BLE001 — startup must never break on bookkeeping
        logger.exception("Orphan company-research sweep failed at startup")
    # Same for business-context refreshes (companies.business_context_refresh_
    # status): a restart mid-refresh leaves the row 'generating' forever, which
    # would wedge the "Save Company Shape" trigger's start-guard so the company
    # could never refresh again until this sweep or the scheduler's 5m heal
    # catches it. Age-gated on the heartbeat column, not raw age — see
    # app/db/business_context_refresh.py for why (the exact ask_jobs incident
    # this ticket is designed around: an age-only check can reap a
    # healthy-but-slow refresh out from under itself).
    try:
        bc_refresh_orphans = db.fail_orphan_business_context_refreshes()
        if bc_refresh_orphans:
            logger.info(
                "Failed %d orphan business-context refresh(es) stuck in "
                "generating (restart interrupt)",
                bc_refresh_orphans,
            )
    except Exception:  # noqa: BLE001 — startup must never break on bookkeeping
        logger.exception("Orphan business-context refresh sweep failed at startup")
    # Design Agent startup invalidation (prototypes + iterations).
    #
    # Guarded (prod-hotfix 2026-05-30): the design-agent tables are provisioned
    # out-of-band via Supabase migrations (db.init_db is a no-op) that may not yet
    # be applied in a given environment — e.g. prod before the feature flag-flip.
    # A missing design-agent table must NOT crash API startup; the Design Agent
    # surface stays dark behind NEXT_PUBLIC_DESIGN_AGENT_ENABLED regardless. Without
    # this guard an unapplied migration takes the entire API down (prod was 502 from
    # the rollup until this landed). Both unguarded calls — the prototypes
    # block AND the iterations call — live inside this ONE try/except so a
    # regression that un-guards either one is caught by the lifespan-guard test.
    try:
        # Design Agent: demote orphaned 'generating' prototypes (the worker
        # task died with the previous process) + stale 'ready' prototypes (template
        # bump). Sync helpers, across ALL workspaces — system-wide cleanup, not a
        # user-driven query (Rule #23) — mirroring the prd/evidence invalidation above.
        proto_orphans = invalidate_orphan_generating_prototypes()
        if settings.design_agent_invalidate_prototypes_on_template_bump:
            proto_stale = invalidate_stale_prototypes(
                DESIGN_AGENT_TEMPLATE_VERSION, variant="v1")
        else:
            proto_stale = 0
            logger.info(
                "prototype template-demote skipped (gate off) — existing ready prototypes preserved across the version bump")
        if proto_orphans or proto_stale:
            logger.info(
                "Invalidated %d orphan generating prototype(s), %d stale prototype(s)",
                proto_orphans,
                proto_stale,
            )
        # Design Agent: demote orphaned 'running' iterations (the worker
        # task died with the previous process) so a restart recovers the iterate queue.
        # Sync helper, across ALL workspaces — system-wide cleanup, not user-driven
        # (Rule #23) — mirroring the prototype orphan-clear above.
        iter_orphans = invalidate_orphan_running_iterations()
        if iter_orphans:
            logger.info(
                "Invalidated %d orphan running iteration(s)", iter_orphans)
        # Design Agent (Tier 2): re-queue jobs left 'claimed' by a worker
        # process that died (its claim is now orphaned) so a fresh worker picks
        # them up. Sync helper, across ALL workspaces — system-wide cleanup, not
        # user-driven. requeue_orphan_claimed_jobs is itself fail-soft
        # (missing table => 0), and it sits inside this guarded block so an
        # environment without the Tier 2 migration applied never breaks startup.
        job_orphans = requeue_orphan_claimed_jobs()
        if job_orphans:
            logger.info(
                "Re-queued %d orphan claimed design-agent job(s)", job_orphans)
    except Exception:
        logger.warning(
            "Design Agent startup invalidation skipped — table(s) unavailable "
            "(migrations not yet applied in this environment); API startup continues, "
            "feature stays dark behind flag",
            exc_info=True,
        )
    # Kick off brief generation in the background so the service starts fast.
    # Runs the KG synthesis path per company — the SAME path as /regenerate +
    # the scheduler — so a fresh deploy/restart produces an identical brief
    # (idempotent: skips datasets that already have a cached brief at the
    # current schema version). Error-isolated: startup must never block on it.
    asyncio.create_task(_startup_generate_briefs())

    # Start the pipeline scheduler if enabled (opt-in via SCHEDULER_ENABLED=true).
    if settings.scheduler_enabled:
        try:
            from app.scheduler import start_scheduler
            start_scheduler()
        except Exception:
            logger.warning("Scheduler startup failed", exc_info=True)

    yield

    # Slack report runs still in flight at SHUTDOWN: the user was acknowledged
    # ("~5-10 minutes") and the run is about to die with the process, so tell
    # them to ask again rather than leaving the thread silent forever.
    #
    # This runs on SHUTDOWN, not startup, and that is the whole point: the
    # markers live in-process, so by the time a fresh process boots its registry
    # is empty by construction and a startup sweep could never fire. Here the
    # event loop is still alive and the bot token is still readable, so the
    # notice actually goes out. Runs BEFORE the Design Agent drain (which can
    # take ~200s) so the message lands promptly.
    try:
        from app.routes.connectors import sweep_interrupted_slack_reports

        swept = await asyncio.to_thread(sweep_interrupted_slack_reports)
        if swept:
            logger.info(
                "Notified %d interrupted Slack report run(s) at shutdown",
                len(swept),
            )
    except Exception:  # noqa: BLE001 — shutdown must never break on bookkeeping
        logger.exception("Interrupted Slack report sweep failed at shutdown")

    # ── Tier 0: graceful drain of in-flight Design Agent generation ────
    # On a deploy/restart SIGTERM, stop admitting new /generate work, then wait
    # for any in-flight generation to finish (up to a tunable deadline) so the
    # process is not SIGKILLed mid-build (the deploy-time 502 class). The
    # deadline EXCEEDS the vite-build subprocess timeout
    # (settings.design_agent_vite_build_timeout_seconds, default 180s) — default
    # 200s — so a build in flight at shutdown is given room to complete. On
    # deadline-elapse we do NOT cancel (the vite thread is uncancellable); the
    # startup invalidate_orphan_generating_prototypes() sweep recovers any
    # left-behind 'generating' row on the next boot. Wrapped so a drain error
    # never blocks shutdown.
    #
    # SYSTEMD IMPLICATION (load-bearing): the unit running this process MUST set
    # TimeoutStopSec greater than design_agent_drain_deadline_seconds (>=220s for
    # the 200s default) — otherwise systemd sends SIGKILL before the drain
    # finishes and this fix is inert.
    design_agent.request_shutdown()
    try:
        await design_agent.drain_inflight(
            settings.design_agent_drain_deadline_seconds
        )
    except Exception:
        logger.warning("Design Agent drain failed during shutdown", exc_info=True)

    # Teardown: shut down scheduler if it was started.
    if settings.scheduler_enabled:
        try:
            from app.scheduler import shutdown_scheduler
            shutdown_scheduler()
        except Exception:
            pass


print("Sprntly API server starting...")
app = FastAPI(title="Sprntly API", version="0.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # RESPONSE headers JS is allowed to read cross-origin. Without this the
    # browser hides Content-Disposition from fetch(), so the report PDF download
    # cannot recover the server's filename and every file saves as the generic
    # fallback. `allow_headers` above governs REQUEST headers and does not help.
    expose_headers=["Content-Disposition"],
)

# Bind the acting company for per-request Claude-key resolution/enforcement.
# Pure-ASGI (propagates contextvars to sync/async endpoints + spawned tasks).
from app.middleware_llm_key import CompanyLLMKeyMiddleware  # noqa: E402

app.add_middleware(CompanyLLMKeyMiddleware)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(connectors.router)
app.include_router(datasets_routes.router)
app.include_router(brief.router)
app.include_router(artifacts.router)
app.include_router(reports.router)
# Renders a client-assembled document (PRD, Evidence, or the two combined) to PDF
# through the same Chromium renderer the report download uses — see
# routes/documents.py for why the HTML comes from the client.
app.include_router(documents.router)
# No-auth share viewer for reports (`/r/<token>`). Registered separately from
# reports.router so the unauthenticated surface stays visible in this list.
app.include_router(reports_public.router)
app.include_router(ideation.router)
app.include_router(ask.router)
app.include_router(chat.router)
app.include_router(agent_chat.router)
app.include_router(prd.router)
app.include_router(stories.router)
# Standalone ticket sets (chat-born tickets with no PRD). Registered before
# tickets.router only for readability — the prefixes (/v1/ticket-sets vs
# /v1/tickets) are disjoint, so order carries no routing consequence here.
app.include_router(ticket_sets.router)
app.include_router(jira_write.router)
app.include_router(evidence.router)
app.include_router(internal.router)
# Bundle proxy (Option B) registered BEFORE design_agent.router (plan fix-item #2)
# so its more-specific /{prototype_id}/bundle/{asset_path:path} +
# /by-token/{token}/bundle/{asset_path:path} routes resolve before the
# single-segment GET /{prototype_id} catch-all in design_agent.router.
app.include_router(design_agent_bundle.router)
# Comments router — same prefix as design_agent.router, registered before it to
# mirror the bundle router's placement convention above. Not load-bearing for
# these routes (all are 2-or-3-segment paths, safe in either registration order
# — see design_agent_comments.py's module docstring), but kept consistent with
# the established pattern rather than introducing a second convention.
app.include_router(design_agent_comments.router)
app.include_router(design_agent.router)
app.include_router(multi_agent.router)
app.include_router(pipeline.router)
app.include_router(synthesis.router)
app.include_router(ingest.router)
app.include_router(metrics.router)
app.include_router(research.router)
app.include_router(oncall.router)
app.include_router(company.router)
app.include_router(business_context_routes.router)
app.include_router(onboarding.router)
app.include_router(tickets.router)
app.include_router(conversations.router)
app.include_router(custom_skills_routes.router)
# Beside custom skills, its structural twin: a skill is the METHOD a document is
# reasoned with, an artifact template is the FORM it is written in. Both are
# company-scoped libraries of untrusted customer-uploaded text.
app.include_router(artifact_templates_routes.router)
app.include_router(team.router)
app.include_router(team.accept_router)
app.include_router(workspaces_routes.router)
app.include_router(admin.router)
app.include_router(usage_routes.router)
app.include_router(staff_admin.router)
app.include_router(staff_admin.claim_router)
app.include_router(transcripts.router)
app.include_router(feedback.router)
app.include_router(mcp_tokens.router)
app.include_router(internal_mcp.resolve_router)
app.include_router(internal_mcp.data_router)
app.include_router(artifact_share.router)
app.include_router(prd_access.router)

# Serve prototype bundles in dev (filesystem fallback when no Supabase Storage bucket).
_proto_dir = Path(settings.storage_dir)
if _proto_dir.exists() or settings.env == "development":
    _proto_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static/prototypes", StaticFiles(directory=str(_proto_dir)), name="prototypes")
