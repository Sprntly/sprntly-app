"""OAuth and status for third-party connectors.

  GET    /v1/connectors                         -> list (no secrets)
  GET    /v1/connectors/google-drive/authorize  -> redirect to Google
  GET    /v1/connectors/google-drive/callback   -> OAuth callback
  DELETE /v1/connectors/google-drive            -> disconnect
"""
from __future__ import annotations

import json
import logging
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import RedirectResponse
from google.auth.transport.requests import Request

from app import db
from app.auth import require_session
from app.config import settings
from app.connectors import google_oauth
from app.connectors.tokens import (
    TokenEncryptionError,
    decrypt_token_json,
    encrypt_token_json,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/connectors", tags=["connectors"])


def _public_connection(row: dict) -> dict:
    config = {}
    if row.get("config_json"):
        try:
            config = json.loads(row["config_json"])
        except (TypeError, ValueError):
            config = {}
    return {
        "id": row["id"],
        "provider": row["provider"],
        "status": row["status"],
        "google_email": row.get("google_email"),
        "scopes": row.get("scopes") or "",
        "config": config,
        "last_sync_at": row.get("last_sync_at"),
        "last_sync_error": row.get("last_sync_error"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@router.get("")
def list_connections(sprintly_session: Annotated[str | None, Cookie()] = None):
    require_session(sprintly_session)
    rows = db.list_connections()
    return {"connections": [_public_connection(r) for r in rows]}


@router.get("/google-drive/authorize")
def google_drive_authorize(
    dataset: str | None = None,
    sprintly_session: Annotated[str | None, Cookie()] = None,
):
    require_session(sprintly_session)
    state = google_oauth.sign_oauth_state(dataset=dataset)
    flow = google_oauth.build_flow()
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return RedirectResponse(url)


@router.get("/google-drive/callback")
def google_drive_callback(code: str, state: str):
    payload = google_oauth.verify_oauth_state(state)
    flow = google_oauth.build_flow()
    try:
        flow.fetch_token(code=code)
    except Exception as e:
        logger.exception("Google OAuth token exchange failed")
        raise HTTPException(400, f"Google authorization failed: {e}") from e

    creds = flow.credentials
    if not creds or not creds.token:
        raise HTTPException(400, "Google did not return credentials")

    try:
        token_encrypted = encrypt_token_json(creds.to_json())
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    email = google_oauth.fetch_google_account_email(creds)
    config = {}
    if payload.get("dataset"):
        config["dataset"] = payload["dataset"]

    db.upsert_connection(
        provider=google_oauth.GOOGLE_DRIVE_PROVIDER,
        token_encrypted=token_encrypted,
        scopes=google_oauth.DRIVE_READONLY_SCOPE,
        google_email=email,
        config_json=json.dumps(config),
    )

    q = urlencode({"connected": google_oauth.GOOGLE_DRIVE_PROVIDER})
    return RedirectResponse(f"{settings.frontend_url.rstrip('/')}/connectors?{q}")


@router.delete("/google-drive")
def google_drive_disconnect(sprintly_session: Annotated[str | None, Cookie()] = None):
    require_session(sprintly_session)
    row = db.get_connection(google_oauth.GOOGLE_DRIVE_PROVIDER)
    if not row:
        raise HTTPException(404, "Google Drive is not connected")

    try:
        creds = google_oauth.credentials_from_token_json(
            decrypt_token_json(row["token_json_encrypted"])
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        google_oauth.try_revoke_credentials(creds)
    except Exception:
        logger.warning("Could not revoke Google token on disconnect", exc_info=True)

    db.delete_connection(google_oauth.GOOGLE_DRIVE_PROVIDER)
    return {"deleted": True, "provider": google_oauth.GOOGLE_DRIVE_PROVIDER}
