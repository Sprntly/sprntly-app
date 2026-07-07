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
    agent_chat,
    artifacts,
    ask,
    backlog,
    brief,
    business_context as business_context_routes,
    company,
    connectors,
    conversations,
    datasets as datasets_routes,
    design_agent,
    design_agent_bundle,
    feedback,
    ingest,
    internal_mcp,
    metrics,
    mcp_tokens,
    multi_agent,
    onboarding,
    oncall,
    research,
    stories,
    synthesis,
    team,
    tickets,
    evidence,
    health,
    internal,
    pipeline,
    prd,
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
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(connectors.router)
app.include_router(datasets_routes.router)
app.include_router(brief.router)
app.include_router(artifacts.router)
app.include_router(backlog.router)
app.include_router(ask.router)
app.include_router(agent_chat.router)
app.include_router(prd.router)
app.include_router(stories.router)
app.include_router(evidence.router)
app.include_router(internal.router)
# Bundle proxy (Option B) registered BEFORE design_agent.router (plan fix-item #2)
# so its more-specific /{prototype_id}/bundle/{asset_path:path} +
# /by-token/{token}/bundle/{asset_path:path} routes resolve before the
# single-segment GET /{prototype_id} catch-all in design_agent.router.
app.include_router(design_agent_bundle.router)
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
app.include_router(team.router)
app.include_router(team.accept_router)
app.include_router(feedback.router)
app.include_router(mcp_tokens.router)
app.include_router(internal_mcp.resolve_router)
app.include_router(internal_mcp.data_router)

# Serve prototype bundles in dev (filesystem fallback when no Supabase Storage bucket).
_proto_dir = Path(settings.storage_dir)
if _proto_dir.exists() or settings.env == "development":
    _proto_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static/prototypes", StaticFiles(directory=str(_proto_dir)), name="prototypes")
