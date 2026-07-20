"""Sprinklr OAuth 2.0 helpers.

Sprinklr (CX / social-listening platform) is a `customer-voice` connector:
the OAuth token here feeds the KG puller (app/kg_ingest/pullers/sprinklr.py)
with cases + inbound social messages — voice-of-customer evidence.

Auth model (dev.sprinklr.com):
  - An app registered on the Sprinklr developer portal yields an API KEY +
    SECRET (settings.sprinklr_api_key / sprinklr_api_secret). The key plays
    the OAuth client_id role AND must be sent as a `key` header on every
    data API call alongside the Bearer token.
  - Standard authorization-code flow. Access tokens live ~30 days
    (expires_in ≈ 2591999) and come with a refresh token; the connector
    probe refreshes near expiry (see app/connector_probe.py).
  - Everything is ENVIRONMENT-SPECIFIC: keys/tokens minted for one Sprinklr
    environment are invalid in others, and non-production environments
    carry a path segment ("prod0", "prod2", "sandbox") in every URL.
    settings.sprinklr_environment holds that segment ("" = production).

Flow (mirrors hubspot_oauth):
    1. Frontend hits POST /v1/connectors/sprinklr/start-oauth
    2. We build a state JWT + return Sprinklr's authorize URL
    3. Browser navigates to Sprinklr's consent screen
    4. Sprinklr redirects back to /v1/connectors/sprinklr/callback?code=...&state=...
    5. We exchange the code (valid 10 minutes) for {access_token,
       refresh_token, expires_in, token_type} and store an encrypted JSON
       blob under provider="sprinklr"
    6. Look up the authorizing user via GET /api/v2/me for account_label
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

SPRINKLR_PROVIDER = "sprinklr"

# OAuth lives on api3, the data APIs on api2 (current dev-portal docs).
_AUTH_HOST = "https://api3.sprinklr.com"
_API_HOST = "https://api2.sprinklr.com"

JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600


def _env_segment() -> str:
    """The environment path segment with a trailing slash ("" for prod)."""
    env = (settings.sprinklr_environment or "").strip().strip("/")
    return f"{env}/" if env else ""


def api_base() -> str:
    """Data-API base, e.g. https://api2.sprinklr.com/prod2/ (env-aware).
    Shared with the puller and the probe so URL composition lives once."""
    return f"{_API_HOST}/{_env_segment()}"


def sprinklr_configured() -> bool:
    return bool(
        settings.sprinklr_api_key
        and settings.sprinklr_api_secret
        and settings.sprinklr_oauth_redirect_url
    )


def authorize_url(state: str) -> str:
    if not sprinklr_configured():
        raise HTTPException(500, "Sprinklr OAuth is not configured on the server")
    from urllib.parse import urlencode

    params = {
        "client_id": settings.sprinklr_api_key,
        "response_type": "code",
        "redirect_uri": settings.sprinklr_oauth_redirect_url,
        "state": state,
    }
    return f"{_AUTH_HOST}/{_env_segment()}oauth/authorize?{urlencode(params)}"


def sign_oauth_state(*, company_id: str, return_to: str | None = None) -> str:
    """Mint a signed state JWT binding the OAuth round-trip to a company —
    the callback (no user session) trusts only this signature."""
    now = int(time.time())
    payload = {
        "provider": SPRINKLR_PROVIDER,
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
    if payload.get("provider") != SPRINKLR_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    if not payload.get("company_id"):
        raise HTTPException(400, "OAuth state missing company_id")
    return payload


def _token_request(grant_params: dict[str, str]) -> dict[str, Any]:
    """POST to the token endpoint. Sprinklr documents ALL parameters in the
    query string of the POST (not the body) — mirror that exactly."""
    if not sprinklr_configured():
        raise HTTPException(500, "Sprinklr OAuth is not configured on the server")
    resp = requests.post(
        f"{_AUTH_HOST}/{_env_segment()}oauth/token",
        params={
            "client_id": settings.sprinklr_api_key,
            "client_secret": settings.sprinklr_api_secret,
            "redirect_uri": settings.sprinklr_oauth_redirect_url,
            **grant_params,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    if not resp.ok:
        logger.warning(
            "Sprinklr token request (%s) failed: %s %s",
            grant_params.get("grant_type"), resp.status_code, resp.text[:300],
        )
        raise HTTPException(400, "Sprinklr token exchange failed")
    return resp.json()


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Trade an authorization code (valid only 10 minutes) for tokens."""
    return _token_request({"grant_type": "authorization_code", "code": code})


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """Trade a refresh token for a fresh ~30-day access token."""
    return _token_request(
        {"grant_type": "refresh_token", "refresh_token": refresh_token}
    )


def auth_headers(access_token: str) -> dict[str, str]:
    """Headers every Sprinklr data-API call needs: the Bearer token AND the
    developer-portal API key as a `key` header."""
    return {
        "Authorization": f"Bearer {access_token}",
        "key": settings.sprinklr_api_key,
    }


def fetch_authenticated_user(access_token: str) -> dict[str, Any]:
    """Identity of the user who authorized the token (GET /api/v2/me).
    Returns {} on any non-2xx so callers can fall back to other labels."""
    resp = requests.get(
        f"{api_base()}api/v2/me",
        headers=auth_headers(access_token),
        timeout=10,
    )
    if not resp.ok:
        logger.warning(
            "Sprinklr me lookup failed: %s %s", resp.status_code, resp.text[:200]
        )
        return {}
    payload = resp.json() or {}
    # v2 responses wrap the object in "data"; tolerate a bare object too.
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else (payload if isinstance(payload, dict) else {})


def token_payload_to_store(token_json: dict[str, Any]) -> str:
    """Wrap Sprinklr's token response with an obtained_at stamp (the probe's
    refresh-near-expiry check reads obtained_at + expires_in)."""
    payload = dict(token_json)
    payload["obtained_at"] = int(time.time())
    return json.dumps(payload)
