"""Ingestion routes — pull a connected provider into the knowledge graph.

POST /v1/ingest/{provider}/sync — tenant-scoped (require_company). Reads the
stored connection, decrypts the credential, runs the provider's puller, and
routes the records through the generic extractor into the KG.

Connections are company-scoped (one row per company+provider).
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException

from app import db
from app.auth import CompanyContext, require_company
from app.connectors.tokens import TokenEncryptionError, decrypt_token_json
from app.graph.facade import GraphFacade
from app.kg_ingest.runner import PULLERS, sync_provider, token_for

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])


@router.post("/{provider}/sync")
def sync(provider: str, company: CompanyContext = Depends(require_company)):
    if provider not in PULLERS:
        raise HTTPException(404, f"No ingestion puller for provider {provider!r}")

    row = db.get_connection(company.company_id, provider)
    if not row:
        raise HTTPException(404, f"{provider!r} is not connected")
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
        token = token_for(provider, token_json)
    except (TokenEncryptionError, json.JSONDecodeError, ValueError) as e:
        raise HTTPException(500, f"Stored credential unusable: {e}") from e

    facade = GraphFacade()
    try:
        result = sync_provider(facade, company.company_id, provider, token=token)
    except Exception as e:  # noqa: BLE001 — puller-level failure (bad token, API down)
        logger.exception("sync failed for %s", provider)
        raise HTTPException(502, f"{provider} sync failed: {e}") from e

    from app.db.client import utc_now
    db.update_connection_sync(company.company_id, provider, last_sync_at=utc_now(), last_sync_error=None)
    return {"ok": True, "provider": provider, **result}
