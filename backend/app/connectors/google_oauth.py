"""Google OAuth helpers for Drive (and future Google connectors)."""
from __future__ import annotations

import json
import logging
import time
import uuid

import jwt
import requests
from fastapi import HTTPException
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.config import settings

logger = logging.getLogger(__name__)

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GOOGLE_DRIVE_PROVIDER = "google_drive"
JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600


def google_oauth_configured() -> bool:
    return bool(
        settings.google_client_id
        and settings.google_client_secret
        and settings.google_oauth_redirect_uri
    )


def _client_config() -> dict:
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_oauth_redirect_uri],
        }
    }


def build_flow() -> Flow:
    if not google_oauth_configured():
        raise HTTPException(500, "Google OAuth is not configured on the server")
    return Flow.from_client_config(
        _client_config(),
        scopes=[DRIVE_READONLY_SCOPE],
        redirect_uri=settings.google_oauth_redirect_uri,
    )


def sign_oauth_state(*, dataset: str | None, return_to: str | None = None) -> str:
    now = int(time.time())
    payload = {
        "provider": GOOGLE_DRIVE_PROVIDER,
        "dataset": dataset,
        "nonce": uuid.uuid4().hex,
        "iat": now,
        "exp": now + STATE_TTL_SECONDS,
    }
    # Base URL of the surface (app vs demo) that started the flow, echoed back
    # so the callback redirects there instead of a single global FRONTEND_URL.
    if return_to:
        payload["return_to"] = return_to
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALG)


def verify_oauth_state(state: str) -> dict:
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        raise HTTPException(400, "Invalid or expired OAuth state")
    if payload.get("provider") != GOOGLE_DRIVE_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    return payload


def credentials_from_token_json(token_json: str) -> Credentials:
    return Credentials.from_authorized_user_info(
        json.loads(token_json),
        scopes=[DRIVE_READONLY_SCOPE],
    )


def fetch_google_account_email(credentials: Credentials) -> str | None:
    try:
        service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        about = service.about().get(fields="user").execute()
        user = about.get("user") or {}
        return user.get("emailAddress")
    except Exception:
        return None


def try_revoke_credentials(credentials: Credentials) -> None:
    """Best-effort revoke at Google; failures are logged and ignored."""
    token = credentials.token
    if not token:
        return
    try:
        requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": token},
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=5,
        )
    except Exception:
        logger.warning("Google token revoke failed", exc_info=True)
