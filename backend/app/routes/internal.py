"""Internal service-to-service API.

These endpoints are called by the DS Agent (and potentially other
internal services) to read corpus data, query enterprise input sources,
and push analysis results back into the knowledge base.

Auth: ``X-Internal-Key`` header must match ``settings.internal_api_key``.
No session cookies or JWTs — purely machine-to-machine.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app import db
from app.config import settings
from app.corpus import load_corpus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])


# ───── auth dependency ─────


def _require_internal_key(
    x_internal_key: str | None = Header(None),
) -> None:
    """Reject requests that don't carry a valid internal API key."""
    if not settings.internal_api_key:
        raise HTTPException(503, "internal API key not configured on this server")
    if not x_internal_key or x_internal_key != settings.internal_api_key:
        raise HTTPException(401, "invalid or missing X-Internal-Key")


# ───── corpus ─────


@router.get("/corpus/{dataset_slug}", dependencies=[Depends(_require_internal_key)])
def get_corpus(dataset_slug: str) -> dict[str, Any]:
    """Return the full markdown corpus for a dataset."""
    try:
        corpus = load_corpus(dataset_slug)
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "dataset": corpus.dataset,
        "docs": [{"name": d.name, "text": d.text} for d in corpus.docs],
        "total_chars": corpus.total_chars(),
        "joined": corpus.joined(),
    }


# ───── datasets ─────


@router.get("/datasets", dependencies=[Depends(_require_internal_key)])
def list_datasets() -> dict[str, Any]:
    """List all registered datasets."""
    return {"datasets": db.list_datasets()}


# ───── enterprise input sources ─────


@router.get(
    "/datasets/{slug}/input-sources",
    dependencies=[Depends(_require_internal_key)],
)
def get_input_sources(slug: str) -> dict[str, Any]:
    """Return configured input sources for a dataset/company."""
    sources = db.list_input_sources(slug)
    return {"dataset": slug, "input_sources": sources}


# ───── ingest analysis ─────

_SAFE_FILENAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,120}\.md$")


class IngestAnalysisBody(BaseModel):
    source: str = "ds-agent"
    filename: str
    markdown: str


@router.post(
    "/datasets/{slug}/ingest-analysis",
    dependencies=[Depends(_require_internal_key)],
)
def ingest_analysis(slug: str, body: IngestAnalysisBody) -> dict[str, Any]:
    """Write an analysis markdown file into the dataset corpus directory.

    The file becomes part of the corpus on the next brief-generation run.
    """
    if not db.dataset_exists(slug):
        raise HTTPException(404, f"dataset {slug!r} not found")

    filename = body.filename
    if not _SAFE_FILENAME.match(filename):
        raise HTTPException(400, f"invalid filename: {filename!r}")

    target = settings.data_path / slug / filename
    target.write_text(body.markdown, encoding="utf-8")
    logger.info(
        "Ingested analysis from %s into %s (%d chars)",
        body.source,
        target,
        len(body.markdown),
    )
    return {
        "ok": True,
        "path": str(target),
        "chars": len(body.markdown),
    }
