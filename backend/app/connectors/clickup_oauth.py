"""ClickUp OAuth 2.0 helpers (commit H).

Flow:
    1. Frontend hits POST /v1/connectors/clickup/start-oauth (commit F)
    2. We build a state JWT + return ClickUp's authorize URL
    3. Browser navigates to ClickUp's consent screen
    4. ClickUp redirects back to /v1/connectors/clickup/callback?code=...&state=...
    5. We exchange the code for {access_token} and store an encrypted JSON
       blob under provider="clickup"

ClickUp specifics worth knowing:
    - Token exchange returns ONLY access_token (no refresh token, no expiry)
    - ClickUp access tokens don't expire under normal use; if they do, the
      user re-authorizes via Connect again
    - The user API requires the token in `Authorization: <token>` — RAW,
      no `Bearer ` prefix. Easy to get wrong.
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

CLICKUP_PROVIDER = "clickup"
CLICKUP_AUTH_URL = "https://app.clickup.com/api"
CLICKUP_TOKEN_URL = "https://api.clickup.com/api/v2/oauth/token"
CLICKUP_USER_URL = "https://api.clickup.com/api/v2/user"
JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600


def clickup_configured() -> bool:
    return bool(
        settings.clickup_client_id
        and settings.clickup_client_secret
        and settings.clickup_oauth_redirect_uri
    )


def authorize_url(state: str) -> str:
    """Build the URL the user gets redirected to for the ClickUp consent screen."""
    if not clickup_configured():
        raise HTTPException(500, "ClickUp OAuth is not configured on the server")
    from urllib.parse import urlencode

    params = {
        "client_id": settings.clickup_client_id,
        "redirect_uri": settings.clickup_oauth_redirect_uri,
        "state": state,
    }
    return f"{CLICKUP_AUTH_URL}?{urlencode(params)}"


def sign_oauth_state(*, company_id: str) -> str:
    """Mint a signed state JWT that binds the OAuth round-trip to a
    specific company. The callback (which has no user session) trusts
    only this signature to know which company gets the new token."""
    now = int(time.time())
    payload = {
        "provider": CLICKUP_PROVIDER,
        "company_id": company_id,
        "nonce": uuid.uuid4().hex,
        "iat": now,
        "exp": now + STATE_TTL_SECONDS,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALG)


def verify_oauth_state(state: str) -> dict:
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[JWT_ALG])
    except jwt.PyJWTError as e:
        raise HTTPException(400, "Invalid or expired OAuth state") from e
    if payload.get("provider") != CLICKUP_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    if not payload.get("company_id"):
        raise HTTPException(400, "OAuth state missing company_id")
    return payload


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Trade an authorization code for an access token. Returns the parsed JSON."""
    if not clickup_configured():
        raise HTTPException(500, "ClickUp OAuth is not configured on the server")
    resp = requests.post(
        CLICKUP_TOKEN_URL,
        json={
            "client_id": settings.clickup_client_id,
            "client_secret": settings.clickup_client_secret,
            "code": code,
        },
        timeout=15,
    )
    if not resp.ok:
        logger.warning(
            "ClickUp token exchange failed: %s %s", resp.status_code, resp.text[:300]
        )
        raise HTTPException(400, "ClickUp token exchange failed")
    return resp.json()


def fetch_authenticated_user(access_token: str) -> dict[str, Any]:
    """Returns ClickUp's /user payload — {id, username, email, ...}.

    Important: ClickUp expects the access token RAW in the Authorization
    header, NOT prefixed with "Bearer ". Other APIs are different.
    """
    resp = requests.get(
        CLICKUP_USER_URL,
        headers={"Authorization": access_token},
        timeout=10,
    )
    if not resp.ok:
        logger.warning(
            "ClickUp /user failed: %s %s", resp.status_code, resp.text[:200]
        )
        return {}
    body = resp.json() or {}
    return body.get("user") or {}


def token_payload_to_store(token_json: dict[str, Any]) -> str:
    """Wrap ClickUp's token response with an obtained_at stamp before encryption."""
    payload = dict(token_json)
    payload["obtained_at"] = int(time.time())
    return json.dumps(payload)
