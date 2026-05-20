import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import auth, db, datasets as datasets_service
from app.brief_runner import auto_generate_all
from app.config import settings
from app.prompts import (
    ASK_CACHE_VERSION,
    BRIEF_SCHEMA_VERSION,
    EVIDENCE_TEMPLATE_VERSION,
    EVIDENCE_V2_TEMPLATE_VERSION,
    PRD_TEMPLATE_VERSION,
)
from app.routes import (
    ask,
    brief,
    datasets as datasets_routes,
    evidence,
    evidence_v2,
    health,
    prd,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # Register any on-disk datasets that don't have a DB row yet — covers the
    # pre-existing `asurion` corpus and any sibling dirs added manually.
    seeded = datasets_service.seed_filesystem_datasets()
    if seeded:
        logger.info("Seeded %d on-disk dataset(s) into the datasets table", seeded)
    # Demote any cached brief whose payload schema doesn't match the current
    # code. auto_generate_all will then treat affected datasets as empty and
    # regenerate them under the new schema on the next tick.
    invalidated = db.invalidate_stale_briefs(BRIEF_SCHEMA_VERSION)
    if invalidated:
        logger.info("Invalidated %d stale brief(s) (schema bump → v%d)", invalidated, BRIEF_SCHEMA_VERSION)
    # Same for cached evidence docs — mismatched template_version → status
    # 'invalidated' so the next view regenerates under the current prompt.
    ev_invalidated = db.invalidate_stale_evidences(EVIDENCE_TEMPLATE_VERSION)
    if ev_invalidated:
        logger.info(
            "Invalidated %d stale evidence doc(s) (template bump → v%d)",
            ev_invalidated,
            EVIDENCE_TEMPLATE_VERSION,
        )
    # Variant-scoped: a v1 bump doesn't touch v2 rows and vice versa.
    ev_v2_invalidated = db.invalidate_stale_evidences(
        EVIDENCE_V2_TEMPLATE_VERSION, variant="v2"
    )
    if ev_v2_invalidated:
        logger.info(
            "Invalidated %d stale evidence-v2 doc(s) (template bump → v%d)",
            ev_v2_invalidated,
            EVIDENCE_V2_TEMPLATE_VERSION,
        )
    # And the same for PRDs.
    prd_invalidated = db.invalidate_stale_prds(PRD_TEMPLATE_VERSION)
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
    # Kick off brief generation in the background so the service starts fast.
    # auto_generate_all is idempotent: it skips datasets that already have a
    # cached brief in SQLite at the current schema version.
    asyncio.create_task(auto_generate_all())
    yield


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
app.include_router(datasets_routes.router)
app.include_router(brief.router)
app.include_router(ask.router)
app.include_router(prd.router)
app.include_router(evidence.router)
app.include_router(evidence_v2.router)
