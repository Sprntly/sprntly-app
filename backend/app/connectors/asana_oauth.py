"""Asana OAuth 2.0 helpers.

Asana is a `task-management` connector (catalog.py). CURRENT SCOPE: OAuth
connect only — no KG puller and no ticket-sync engine branch yet, so a
connected Asana shows up healthy in Settings → Connectors but does not
appear on the ticket sync button (SYNC_PROVIDERS) and ingests nothing
(PULLERS). Those are deliberate later phases.

Auth model (developers.asana.com/docs/oauth):
  - App registered in the Asana developer console → client id + secret.
  - Authorization-code flow. The scope param depends on the app's
    permission MODE in the console: full-permissions apps accept only the
    special "default" scope (anything granular → `forbidden_scopes`);
    scoped-permissions apps take space-separated "<resource>:<action>"
    scopes pre-selected on the app. settings.asana_scopes defaults to
    "default"; override for scoped apps.
  - Access tokens live ~1 hour; the long-lived refresh token mints new
    ones (the connector probe refreshes near expiry). Refresh responses
    may omit the refresh_token — keep the stored one in that case.
  - The token response also embeds the authorizing user under "data"
    ({gid, name, email}), which the callback uses for account_label
    without a second call.

Flow (mirrors hubspot/sprinklr):
    1. Frontend hits POST /v1/connectors/asana/start-oauth
    2. We build a state JWT + return Asana's authorize URL
    3. Browser navigates to Asana's consent screen
    4. Asana redirects back to /v1/connectors/asana/callback?code=...&state=...
    5. We exchange the code for {access_token, refresh_token, expires_in,
       token_type, data:{user}} and store an encrypted JSON blob under
       provider="asana"
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

ASANA_PROVIDER = "asana"
ASANA_AUTH_URL = "https://app.asana.com/-/oauth_authorize"
ASANA_TOKEN_URL = "https://app.asana.com/-/oauth_token"
ASANA_API = "https://app.asana.com/api/1.0"

JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600


def asana_configured() -> bool:
    return bool(
        settings.asana_client_id
        and settings.asana_client_secret
        and settings.asana_oauth_redirect_uri
    )


def authorize_url(state: str) -> str:
    if not asana_configured():
        raise HTTPException(500, "Asana OAuth is not configured on the server")
    from urllib.parse import urlencode

    params = {
        "client_id": settings.asana_client_id,
        "redirect_uri": settings.asana_oauth_redirect_uri,
        "response_type": "code",
        "scope": settings.asana_scopes,
        "state": state,
    }
    return f"{ASANA_AUTH_URL}?{urlencode(params)}"


def sign_oauth_state(*, company_id: str, return_to: str | None = None) -> str:
    """Mint a signed state JWT binding the OAuth round-trip to a company —
    the callback (no user session) trusts only this signature."""
    now = int(time.time())
    payload = {
        "provider": ASANA_PROVIDER,
        "company_id": company_id,
        "return_to": return_to,
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
    if payload.get("provider") != ASANA_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    if not payload.get("company_id"):
        raise HTTPException(400, "OAuth state missing company_id")
    return payload


def _token_request(grant_params: dict[str, str]) -> dict[str, Any]:
    if not asana_configured():
        raise HTTPException(500, "Asana OAuth is not configured on the server")
    resp = requests.post(
        ASANA_TOKEN_URL,
        data={
            "client_id": settings.asana_client_id,
            "client_secret": settings.asana_client_secret,
            "redirect_uri": settings.asana_oauth_redirect_uri,
            **grant_params,
        },
        timeout=15,
    )
    if not resp.ok:
        logger.warning(
            "Asana token request (%s) failed: %s %s",
            grant_params.get("grant_type"), resp.status_code, resp.text[:300],
        )
        raise HTTPException(400, "Asana token exchange failed")
    return resp.json()


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Trade an authorization code for tokens. The response embeds the
    authorizing user under "data" ({gid, name, email})."""
    return _token_request({"grant_type": "authorization_code", "code": code})


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """Trade the long-lived refresh token for a fresh ~1h access token.
    NOTE: the response may omit refresh_token — callers must keep the
    stored one in that case (see token_payload_to_store's merge param)."""
    return _token_request(
        {"grant_type": "refresh_token", "refresh_token": refresh_token}
    )


def fetch_authenticated_user(access_token: str) -> dict[str, Any]:
    """Identity of the token's user (GET /users/me → {gid, name, email}).
    Returns {} on any non-2xx so callers can fall back to other labels."""
    resp = requests.get(
        f"{ASANA_API}/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if not resp.ok:
        logger.warning(
            "Asana users/me failed: %s %s", resp.status_code, resp.text[:200]
        )
        return {}
    payload = resp.json() or {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def token_payload_to_store(
    token_json: dict[str, Any], *, keep_refresh_token: str | None = None,
) -> str:
    """Wrap Asana's token response with an obtained_at stamp (the probe's
    refresh-near-expiry check reads obtained_at + expires_in). A refresh
    response that omitted refresh_token keeps `keep_refresh_token`."""
    payload = dict(token_json)
    if not payload.get("refresh_token") and keep_refresh_token:
        payload["refresh_token"] = keep_refresh_token
    payload["obtained_at"] = int(time.time())
    return json.dumps(payload)
