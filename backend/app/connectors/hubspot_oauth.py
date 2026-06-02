"""HubSpot OAuth 2.0 helpers (commit I).

Flow:
    1. Frontend hits POST /v1/connectors/hubspot/start-oauth (commit F)
    2. We build a state JWT + return HubSpot's authorize URL
    3. Browser navigates to HubSpot's consent screen
    4. HubSpot redirects back to /v1/connectors/hubspot/callback?code=...&state=...
    5. We exchange the code for {access_token, refresh_token, expires_in,
       token_type} and store an encrypted JSON blob under provider="hubspot"

HubSpot specifics worth knowing:
    - Token exchange is form-urlencoded (NOT JSON) — use `data=` in requests
    - Access tokens expire (typically 21600s = 6 hours); refresh tokens
      persist longer. We store both but don't refresh proactively in V1.
    - The account identity (user email + portal/hub id) comes from a
      DIFFERENT endpoint than the token exchange:
        GET /oauth/v1/access-tokens/{token}
      Returns {user, hub_id, hub_domain, scopes, ...} where `user` is the
      authenticated user's email — perfect for account_label.
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

HUBSPOT_PROVIDER = "hubspot"
HUBSPOT_AUTH_URL = "https://app.hubspot.com/oauth/authorize"
HUBSPOT_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
HUBSPOT_TOKEN_INFO_URL = "https://api.hubapi.com/oauth/v1/access-tokens"
JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600


def hubspot_configured() -> bool:
    return bool(
        settings.hubspot_client_id
        and settings.hubspot_client_secret
        and settings.hubspot_oauth_redirect_uri
    )


def authorize_url(state: str, scopes: str | None = None) -> str:
    """Build the URL the user gets redirected to for HubSpot's consent screen."""
    if not hubspot_configured():
        raise HTTPException(500, "HubSpot OAuth is not configured on the server")
    from urllib.parse import urlencode

    params = {
        "client_id": settings.hubspot_client_id,
        "redirect_uri": settings.hubspot_oauth_redirect_uri,
        "scope": scopes or settings.hubspot_scopes,
        "state": state,
    }
    return f"{HUBSPOT_AUTH_URL}?{urlencode(params)}"


def sign_oauth_state() -> str:
    now = int(time.time())
    payload = {
        "provider": HUBSPOT_PROVIDER,
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
    if payload.get("provider") != HUBSPOT_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    return payload


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Trade an authorization code for tokens. Returns parsed JSON.

    HubSpot's token endpoint requires application/x-www-form-urlencoded
    (NOT JSON) — pass via `data=` so requests serialises correctly.
    """
    if not hubspot_configured():
        raise HTTPException(500, "HubSpot OAuth is not configured on the server")
    resp = requests.post(
        HUBSPOT_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": settings.hubspot_client_id,
            "client_secret": settings.hubspot_client_secret,
            "redirect_uri": settings.hubspot_oauth_redirect_uri,
            "code": code,
        },
        timeout=15,
    )
    if not resp.ok:
        logger.warning(
            "HubSpot token exchange failed: %s %s", resp.status_code, resp.text[:300]
        )
        raise HTTPException(400, "HubSpot token exchange failed")
    return resp.json()


def fetch_token_info(access_token: str) -> dict[str, Any]:
    """Look up the portal/user identity associated with a HubSpot access token.

    Returns {user (email), hub_id, hub_domain, scopes, ...} — we use this
    to populate `account_label` so the UI shows the user's email rather
    than an opaque token reference.
    """
    resp = requests.get(
        f"{HUBSPOT_TOKEN_INFO_URL}/{access_token}",
        timeout=10,
    )
    if not resp.ok:
        logger.warning(
            "HubSpot access-tokens info failed: %s %s",
            resp.status_code,
            resp.text[:200],
        )
        return {}
    return resp.json() or {}


def token_payload_to_store(token_json: dict[str, Any]) -> str:
    """Wrap HubSpot's token response with an obtained_at stamp before encryption."""
    payload = dict(token_json)
    payload["obtained_at"] = int(time.time())
    return json.dumps(payload)
