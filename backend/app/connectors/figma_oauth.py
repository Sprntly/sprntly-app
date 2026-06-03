"""Figma OAuth 2.0 helpers.

Flow:
    1. Frontend hits /v1/connectors/figma/authorize
    2. We build a state JWT + redirect the user to Figma's consent screen
    3. Figma redirects back to /v1/connectors/figma/callback?code=...&state=...
    4. We exchange the code for {access_token, refresh_token, expires_in,
       user_id} and store an encrypted JSON blob under provider="figma"

The stored token JSON is the literal Figma response, plus an `obtained_at`
epoch so refresh logic can decide whether to refresh proactively.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import jwt
import requests
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

FIGMA_PROVIDER = "figma"
FIGMA_AUTH_URL = "https://www.figma.com/oauth"
FIGMA_TOKEN_URL = "https://www.figma.com/api/oauth/token"
FIGMA_REFRESH_URL = "https://www.figma.com/api/oauth/refresh"
FIGMA_ME_URL = "https://api.figma.com/v1/me"
# Default scopes when nothing is configured. Comma-separated per Figma docs.
DEFAULT_SCOPES = "files:read,file_variables:read,file_dev_resources:read,current_user:read"
JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600


def figma_configured() -> bool:
    return bool(
        settings.figma_client_id
        and settings.figma_client_secret
        and settings.figma_oauth_redirect_uri
    )


def authorize_url(state: str, scopes: str | None = None) -> str:
    """Build the URL the user gets redirected to."""
    if not figma_configured():
        raise HTTPException(500, "Figma OAuth is not configured on the server")
    from urllib.parse import urlencode
    params = {
        "client_id": settings.figma_client_id,
        "redirect_uri": settings.figma_oauth_redirect_uri,
        "scope": scopes or DEFAULT_SCOPES,
        "state": state,
        "response_type": "code",
    }
    return f"{FIGMA_AUTH_URL}?{urlencode(params)}"


def sign_oauth_state(return_to: str | None = None) -> str:
    now = int(time.time())
    payload = {
        "provider": FIGMA_PROVIDER,
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
    except jwt.PyJWTError as e:
        raise HTTPException(400, "Invalid or expired OAuth state") from e
    if payload.get("provider") != FIGMA_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    return payload


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Trade an authorization code for tokens. Returns the parsed JSON."""
    if not figma_configured():
        raise HTTPException(500, "Figma OAuth is not configured on the server")
    resp = requests.post(
        FIGMA_TOKEN_URL,
        data={
            "client_id": settings.figma_client_id,
            "client_secret": settings.figma_client_secret,
            "redirect_uri": settings.figma_oauth_redirect_uri,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if not resp.ok:
        logger.warning("Figma token exchange failed: %s %s", resp.status_code, resp.text[:300])
        raise HTTPException(400, "Figma token exchange failed")
    return resp.json()


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    if not figma_configured():
        raise HTTPException(500, "Figma OAuth is not configured on the server")
    resp = requests.post(
        FIGMA_REFRESH_URL,
        data={
            "client_id": settings.figma_client_id,
            "client_secret": settings.figma_client_secret,
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    if not resp.ok:
        logger.warning("Figma refresh failed: %s %s", resp.status_code, resp.text[:300])
        raise HTTPException(400, "Figma token refresh failed")
    return resp.json()


def fetch_me(access_token: str) -> dict[str, Any]:
    """Returns the Figma /v1/me payload (id, email, handle, img_url, ...)."""
    resp = requests.get(
        FIGMA_ME_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if not resp.ok:
        logger.warning("Figma /me failed: %s %s", resp.status_code, resp.text[:200])
        return {}
    return resp.json()


def token_payload_to_store(token_json: dict[str, Any]) -> str:
    """Wrap Figma's response with an obtained_at stamp before encryption."""
    payload = dict(token_json)
    payload["obtained_at"] = int(time.time())
    return json.dumps(payload)


# ─────────────────────── data API helpers (Design Agent input) ───────────────────────

FIGMA_API_BASE = "https://api.figma.com/v1"


def fetch_file(access_token: str, file_key: str, depth: int = 2) -> dict[str, Any]:
    """Fetch a Figma file's top-level structure for the Design Agent.

    Returns the JSON payload from GET /v1/files/{key} with ?depth=N to limit
    tree traversal. depth=2 surfaces pages + their direct child frames without
    pulling every vector node. Caller is responsible for token refresh.
    """
    resp = requests.get(
        f"{FIGMA_API_BASE}/files/{file_key}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"depth": depth},
        timeout=20,
    )
    if not resp.ok:
        logger.warning(
            "Figma /files/%s failed: %s %s", file_key, resp.status_code, resp.text[:200]
        )
        raise HTTPException(resp.status_code, "Figma file fetch failed")
    return resp.json()


def fetch_file_styles(access_token: str, file_key: str) -> dict[str, Any]:
    """Fetch published styles (colors, fonts, effects) for a Figma file.

    Powers design-token extraction for the Design Agent. Returns the raw
    /v1/files/{key}/styles JSON.
    """
    resp = requests.get(
        f"{FIGMA_API_BASE}/files/{file_key}/styles",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    if not resp.ok:
        logger.warning(
            "Figma /files/%s/styles failed: %s %s",
            file_key,
            resp.status_code,
            resp.text[:200],
        )
        raise HTTPException(resp.status_code, "Figma styles fetch failed")
    return resp.json()
