"""HubSpot OAuth 2.0 helpers (commit I, modular v1/v3 — commit I.1).

Supports both HubSpot OAuth API generations:

  - v1 (legacy): /oauth/v1/token for the code exchange.
                 Sunset-pending; only usable from older HubSpot accounts.
  - v3 (modern, default): /oauth/v3/token for the code exchange.
                 30-minute access tokens. New HubSpot accounts can only
                 create v3-compatible apps (via `hs project create` in the
                 HubSpot CLI).

Token identity for BOTH versions is fetched via
GET /oauth/v1/access-tokens/{token} — that v1 metadata endpoint accepts
v3 tokens too. (There is no working /oauth/v3/introspect endpoint; it 404s.)

Dispatch is on `settings.hubspot_oauth_version` (defaults to "v3").
Public functions — `authorize_url`, `exchange_code_for_token`,
`fetch_token_info`, `sign_oauth_state`, etc. — keep the same signatures
across versions so callers (routes/connectors.py) never branch on it.

Flow (both versions):
    1. Frontend hits POST /v1/connectors/hubspot/start-oauth (commit F)
    2. We build a state JWT + return HubSpot's authorize URL
    3. Browser navigates to HubSpot's consent screen
    4. HubSpot redirects back to /v1/connectors/hubspot/callback?code=...&state=...
    5. We exchange the code for {access_token, refresh_token, expires_in,
       token_type} and store an encrypted JSON blob under provider="hubspot"
    6. Look up portal identity (email + hub_id) for account_label

What's the same across v1/v3:
    - Authorize URL: https://app.hubspot.com/oauth/authorize
    - Token POST body format: application/x-www-form-urlencoded
    - Token POST body fields: grant_type, client_id, client_secret,
      redirect_uri, code
    - Token response: {access_token, refresh_token, expires_in, token_type}
    - Identity lookup: GET /oauth/v1/access-tokens/{token} — this v1
      metadata endpoint works for BOTH v1 and v3 tokens. (The RFC-7662
      /oauth/v3/introspect endpoint does not exist / 404s.)

What differs:
    - Token URL: hubapi.com (v1) vs hubspot.com (v3)
    - Access token TTL: ~6h (v1) vs 30min (v3)
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
HUBSPOT_AUTH_URL = "https://app.hubspot.com/oauth/authorize"  # same in both

# v1 (legacy)
HUBSPOT_TOKEN_URL_V1 = "https://api.hubapi.com/oauth/v1/token"
HUBSPOT_TOKEN_INFO_URL_V1 = "https://api.hubapi.com/oauth/v1/access-tokens"

# v3 (modern). NOTE: there is no working `/oauth/v3/introspect` endpoint
# (it 404s); token identity for v3 tokens is fetched via the v1
# access-tokens endpoint above — see fetch_token_info().
HUBSPOT_TOKEN_URL_V3 = "https://api.hubspot.com/oauth/v3/token"

JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600


def _oauth_version() -> str:
    """'v3' (default) or 'v1' based on settings.hubspot_oauth_version."""
    raw = (settings.hubspot_oauth_version or "v3").strip().lower()
    return "v1" if raw == "v1" else "v3"


def hubspot_configured() -> bool:
    return bool(
        settings.hubspot_client_id
        and settings.hubspot_client_secret
        and settings.hubspot_oauth_redirect_uri
    )


def authorize_url(state: str, scopes: str | None = None) -> str:
    """Build the URL the user gets redirected to. Same in v1 and v3."""
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


def sign_oauth_state(
    *, company_id: str, return_to: str | None = None,
) -> str:
    """Mint a signed state JWT that binds the OAuth round-trip to a
    specific company. The callback (which has no user session) trusts
    only this signature to know which company gets the new token.

    `return_to` is an optional relative path the callback redirects
    to instead of the default /settings?section=connectors."""
    now = int(time.time())
    payload = {
        "provider": HUBSPOT_PROVIDER,
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
    if payload.get("provider") != HUBSPOT_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    if not payload.get("company_id"):
        raise HTTPException(400, "OAuth state missing company_id")
    return payload


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Trade an authorization code for tokens. Returns parsed JSON.

    Body and response shapes are identical between v1 and v3 — only the
    endpoint URL changes. We always send form-urlencoded.
    """
    if not hubspot_configured():
        raise HTTPException(500, "HubSpot OAuth is not configured on the server")
    url = HUBSPOT_TOKEN_URL_V1 if _oauth_version() == "v1" else HUBSPOT_TOKEN_URL_V3
    resp = requests.post(
        url,
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
            "HubSpot token exchange (%s) failed: %s %s",
            _oauth_version(), resp.status_code, resp.text[:300],
        )
        raise HTTPException(400, "HubSpot token exchange failed")
    return resp.json()


def fetch_token_info(access_token: str) -> dict[str, Any]:
    """Look up the portal/user identity for an access token.

    Uses HubSpot's access-token metadata endpoint
    `GET /oauth/v1/access-tokens/{token}`, which works for access tokens
    minted by BOTH v1- and v3-generation OAuth apps and returns the
    HubSpot-native shape callers rely on:
      {
        "user": "<email>",                # authenticated user's email
        "hub_id": <int>,
        "hub_domain": "<str>",
        "scopes": ["...", "...", ...],    # list of granted scopes
        "user_id": <int>,
        # ... plus app_id, token_type, expires_in, etc.
      }

    NOTE: despite the module name, there is NO working RFC-7662
    `/oauth/v3/introspect` endpoint — it 404s — so v3 tokens are
    introspected through this same v1 metadata endpoint. Returns {} on any
    non-2xx so callers can fall back to other label sources (e.g. hub_domain).
    """
    resp = requests.get(
        f"{HUBSPOT_TOKEN_INFO_URL_V1}/{access_token}",
        timeout=10,
    )

    if not resp.ok:
        logger.warning(
            "HubSpot token-info failed: %s %s",
            resp.status_code, resp.text[:200],
        )
        return {}

    # Native shape: {user, hub_id, hub_domain, scopes[list], user_id, ...}
    return resp.json() or {}


def token_payload_to_store(token_json: dict[str, Any]) -> str:
    """Wrap HubSpot's token response with an obtained_at + oauth_version stamp."""
    payload = dict(token_json)
    payload["obtained_at"] = int(time.time())
    payload["oauth_version"] = _oauth_version()
    return json.dumps(payload)
