import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import auth, db
from app.brief_runner import auto_generate_all
from app.config import settings
from app.prompts import BRIEF_SCHEMA_VERSION, EVIDENCE_TEMPLATE_VERSION
from app.routes import ask, brief, evidence, health, prd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
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
app.include_router(brief.router)
app.include_router(ask.router)
app.include_router(prd.router)
app.include_router(evidence.router)
