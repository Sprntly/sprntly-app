"""OAuth and status for third-party connectors.

  GET    /v1/connectors                         -> list (no secrets)

  GET    /v1/connectors/google-drive/authorize  -> redirect to Google
  GET    /v1/connectors/google-drive/callback   -> OAuth callback
  GET    /v1/connectors/google-drive/folders    -> browse folders to select
  POST   /v1/connectors/google-drive/config     -> save folder + dataset
  POST   /v1/connectors/google-drive/sync       -> pull folder into corpus
  DELETE /v1/connectors/google-drive            -> disconnect

  GET    /v1/connectors/figma/authorize         -> redirect to Figma
  GET    /v1/connectors/figma/callback          -> OAuth callback
  DELETE /v1/connectors/figma                   -> disconnect

  GET    /v1/connectors/github/authorize        -> redirect to GitHub
  GET    /v1/connectors/github/callback         -> OAuth callback
  DELETE /v1/connectors/github                  -> disconnect
"""
from __future__ import annotations

import json
import logging
from urllib.parse import urlencode

from fastapi import Depends, APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from google.auth.transport.requests import Request
from pydantic import BaseModel

from app import db
from app.auth import require_session
from app.config import settings
from app.connectors import figma_oauth, github_app, google_oauth
from app.connectors.google_drive_sync import (
    SyncConfigError,
    browse_folders,
    merge_config,
    parse_folder_id,
    sync_google_drive,
)
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
    # account_label is the generic identifier ("alice@co.com" for Figma,
    # "@octocat" for GitHub, the user's email for Google). google_email
    # is preserved for the existing Drive frontend; new providers should
    # read account_label.
    return {
        "id": row["id"],
        "provider": row["provider"],
        "status": row["status"],
        "google_email": row.get("google_email"),
        "account_label": row.get("account_label") or row.get("google_email"),
        "scopes": row.get("scopes") or "",
        "config": config,
        "last_sync_at": row.get("last_sync_at"),
        "last_sync_error": row.get("last_sync_error"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@router.get("")
def list_connections(_session: dict = Depends(require_session)):
    rows = db.list_connections()
    return {"connections": [_public_connection(r) for r in rows]}


@router.get("/google-drive/authorize")
def google_drive_authorize(
    dataset: str | None = None,
    _session: dict = Depends(require_session),
):
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


class GoogleDriveConfigIn(BaseModel):
    folder_id: str
    folder_name: str | None = None
    dataset: str | None = None


class GoogleDriveSyncIn(BaseModel):
    dataset: str | None = None
    folder_id: str | None = None


@router.get("/google-drive/folders")
def google_drive_list_folders(
    parent_id: str | None = None,
    _session: dict = Depends(require_session),
):
    try:
        return browse_folders(parent_id)
    except SyncConfigError as e:
        logger.warning("Drive folder browse failed: %s", e)
        raise HTTPException(400, str(e)) from e


@router.post("/google-drive/config")
def google_drive_config(
    body: GoogleDriveConfigIn,
    _session: dict = Depends(require_session),
):
    row = db.get_connection(google_oauth.GOOGLE_DRIVE_PROVIDER)
    if not row:
        raise HTTPException(404, "Google Drive is not connected")
    try:
        fid = parse_folder_id(body.folder_id)
    except SyncConfigError as e:
        raise HTTPException(422, str(e)) from e
    patch: dict = {"folder_id": fid}
    if body.folder_name:
        patch["folder_name"] = body.folder_name.strip()
    if body.dataset:
        patch["dataset"] = body.dataset.strip()
    updated = merge_config(row, patch)
    return {"ok": True, "config": updated}


@router.post("/google-drive/sync")
def google_drive_sync(
    body: GoogleDriveSyncIn | None = None,
    _session: dict = Depends(require_session),
):
    payload = body or GoogleDriveSyncIn()
    try:
        result = sync_google_drive(
            dataset=payload.dataset,
            folder_id=payload.folder_id,
        )
    except SyncConfigError as e:
        raise HTTPException(400, str(e)) from e
    return result.to_dict()


@router.delete("/google-drive")
def google_drive_disconnect(_session: dict = Depends(require_session)):
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


# ─────────────────────── Figma ───────────────────────


@router.get("/figma/authorize")
def figma_authorize(_session: dict = Depends(require_session)):
    if not figma_oauth.figma_configured():
        raise HTTPException(500, "Figma OAuth is not configured on the server")
    url = figma_oauth.authorize_url(state=figma_oauth.sign_oauth_state())
    return RedirectResponse(url)


@router.get("/figma/callback")
def figma_callback(code: str, state: str):
    figma_oauth.verify_oauth_state(state)
    token_json = figma_oauth.exchange_code_for_token(code)
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(400, "Figma did not return an access_token")

    me = figma_oauth.fetch_me(access_token)
    label = me.get("email") or me.get("handle") or token_json.get("user_id")

    try:
        token_encrypted = encrypt_token_json(figma_oauth.token_payload_to_store(token_json))
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    db.upsert_connection(
        provider=figma_oauth.FIGMA_PROVIDER,
        token_encrypted=token_encrypted,
        scopes=figma_oauth.DEFAULT_SCOPES,
        account_label=label,
        config_json=json.dumps({"user": me}) if me else "{}",
    )

    q = urlencode({"connected": figma_oauth.FIGMA_PROVIDER})
    return RedirectResponse(f"{settings.frontend_url.rstrip('/')}/connectors?{q}")


@router.delete("/figma")
def figma_disconnect(_session: dict = Depends(require_session)):
    row = db.get_connection(figma_oauth.FIGMA_PROVIDER)
    if not row:
        raise HTTPException(404, "Figma is not connected")
    # Figma has no documented revoke endpoint; just drop our copy of the token.
    db.delete_connection(figma_oauth.FIGMA_PROVIDER)
    return {"deleted": True, "provider": figma_oauth.FIGMA_PROVIDER}


# ─────────────────────── GitHub (App, user-OAuth half) ───────────────────────


@router.get("/github/authorize")
def github_authorize(_session: dict = Depends(require_session)):
    if not github_app.github_oauth_configured():
        raise HTTPException(500, "GitHub OAuth is not configured on the server")
    url = github_app.authorize_url(state=github_app.sign_oauth_state())
    return RedirectResponse(url)


@router.get("/github/callback")
def github_callback(code: str, state: str):
    github_app.verify_oauth_state(state)
    token_json = github_app.exchange_code_for_token(code)
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(400, "GitHub did not return an access_token")

    me = github_app.fetch_authenticated_user(access_token)
    label = me.get("login")
    if label:
        label = f"@{label}"

    try:
        token_encrypted = encrypt_token_json(github_app.token_payload_to_store(token_json))
    except TokenEncryptionError as e:
        raise HTTPException(500, str(e)) from e

    scopes = token_json.get("scope") or github_app.DEFAULT_SCOPES
    db.upsert_connection(
        provider=github_app.GITHUB_PROVIDER,
        token_encrypted=token_encrypted,
        scopes=scopes,
        account_label=label,
        config_json=json.dumps({"user": me}) if me else "{}",
    )

    q = urlencode({"connected": github_app.GITHUB_PROVIDER})
    return RedirectResponse(f"{settings.frontend_url.rstrip('/')}/connectors?{q}")


@router.delete("/github")
def github_disconnect(_session: dict = Depends(require_session)):
    row = db.get_connection(github_app.GITHUB_PROVIDER)
    if not row:
        raise HTTPException(404, "GitHub is not connected")
    db.delete_connection(github_app.GITHUB_PROVIDER)
    return {"deleted": True, "provider": github_app.GITHUB_PROVIDER}
