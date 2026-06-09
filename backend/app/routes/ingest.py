"""Ingestion routes — pull a connected provider into the knowledge graph.

POST /v1/ingest/{provider}/sync — tenant-scoped (require_company). Reads the
stored connection, decrypts the credential, runs the provider's puller, and
routes the records through the generic extractor into the KG.

Connections are company-scoped (one row per company+provider).
"""
from __future__ import annotations

import json
import logging

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app import db
from app.auth import CompanyContext, require_company
from app.connectors.tokens import TokenEncryptionError, decrypt_token_json
from app.graph.facade import GraphFacade
from app.kg_ingest.runner import PULLERS, sync_provider, token_for

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])


def _decrypt_provider_token(company_id: str, provider: str) -> str:
    """Read the stored connection, decrypt it, and return the credential the
    provider's puller expects. 404 if not connected; 500 if unreadable."""
    row = db.get_connection(company_id, provider)
    if not row:
        raise HTTPException(404, f"{provider!r} is not connected")
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
        return token_for(provider, token_json)
    except (TokenEncryptionError, json.JSONDecodeError, ValueError) as e:
        raise HTTPException(500, f"Stored credential unusable: {e}") from e


@router.post("/{provider}/sync")
def sync(provider: str, company: CompanyContext = Depends(require_company)):
    if provider not in PULLERS:
        raise HTTPException(404, f"No ingestion puller for provider {provider!r}")

    token = _decrypt_provider_token(company.company_id, provider)

    facade = GraphFacade()
    try:
        result = sync_provider(facade, company.company_id, provider, token=token)
    except Exception as e:  # noqa: BLE001 — puller-level failure (bad token, API down)
        logger.exception("sync failed for %s", provider)
        from app.db.client import utc_now
        db.update_connection_sync(company.company_id, provider,
                                  last_sync_at=utc_now(), last_sync_error=str(e)[:500])
        raise HTTPException(502, f"{provider} sync failed: {e}") from e

    from app.db.client import utc_now
    db.update_connection_sync(company.company_id, provider, last_sync_at=utc_now(), last_sync_error=None)
    return {"ok": True, "provider": provider, **result}


# Whisper's per-request hard limit. Longer recordings need server-side chunking
# (split on silence, transcribe each chunk, concatenate) — tracked as a
# follow-up; for the pilot we reject oversized files with a clear message.
_MAX_AUDIO_BYTES = 25 * 1024 * 1024


@router.post("/audio")
async def ingest_audio_route(
    file: Annotated[UploadFile, File(description="Meeting audio (mp3/m4a/wav)")],
    source: Annotated[str, Form()] = "fireflies",
    company: CompanyContext = Depends(require_company),
):
    """Upload a meeting audio file → transcribe (Whisper) → distill into the KG.

    Complements the Fireflies puller (which pulls already-summarized meetings):
    this path takes a raw recording, transcribes it, and routes the transcript
    through the generic extractor. Only distilled signals are persisted, never
    the verbatim transcript (§6 data minimization)."""
    filename = file.filename or "audio"
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(400, "Empty audio file")
    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            413,
            f"Audio file is {len(audio_bytes)} bytes; the limit is "
            f"{_MAX_AUDIO_BYTES} bytes (~25 MB). Split longer recordings.",
        )

    from app.kg_ingest.audio_ingest import ingest_audio
    facade = GraphFacade()
    try:
        result = ingest_audio(
            facade, company.company_id,
            audio_bytes=audio_bytes, filename=filename, source=source,
        )
    except RuntimeError as e:  # OPENAI_API_KEY unset → config problem, not a 5xx
        raise HTTPException(503, str(e)) from e
    except ValueError as e:  # empty / oversized audio surfaced from transcription
        raise HTTPException(422, str(e)) from e
    except Exception as e:  # noqa: BLE001 — transcription / extraction failure
        logger.exception("audio ingestion failed for %s", filename)
        raise HTTPException(502, f"audio ingestion failed: {e}") from e

    return {"ok": True, "source": source, "filename": filename, **result}


class GitHubDeepReadIn(BaseModel):
    repo: str


@router.post("/github/deep-read")
def github_deep_read(
    body: GitHubDeepReadIn,
    company: CompanyContext = Depends(require_company),
):
    """On-demand deep-read of one GitHub repo → distilled system map in the KG.

    Transiently reads README + structure + languages, runs one injection-defended
    gateway analysis call, and extracts the distilled map (no raw code persisted).
    Separate from the weekly /github/sync activity pull."""
    repo = (body.repo or "").strip()
    if not repo or "/" not in repo:
        raise HTTPException(422, "repo must be in 'owner/name' form")

    token = _decrypt_provider_token(company.company_id, "github")

    from app.kg_ingest.github_deep_read import deep_read_repo
    facade = GraphFacade()
    try:
        return deep_read_repo(facade, company.company_id, repo, access_token=token)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:  # noqa: BLE001 — read/analysis failure
        logger.exception("github deep-read failed for %s", repo)
        raise HTTPException(502, f"github deep-read failed: {e}") from e
