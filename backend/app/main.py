import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import auth, db
from app.brief_runner import auto_generate_all
from app.config import settings
from app.routes import ask, brief, health, prd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # Kick off brief generation in the background so the service starts fast.
    # auto_generate_all is idempotent: it skips datasets that already have a
    # cached brief in SQLite.
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
