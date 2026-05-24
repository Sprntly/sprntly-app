"""GitHub App helpers.

Two distinct token modes live in a GitHub App:

  - User-to-server OAuth — the user clicks "Connect GitHub",
    redirects through GitHub's consent screen, returns with a code,
    we exchange for an access_token (+ refresh_token if expiration
    is enabled on the app). This is what /v1/connectors/github/*
    handles. Stored under provider="github".

  - App-as-app JWT + installation tokens — the app proves it is
    itself by signing a short JWT with its RSA private key, then
    swaps the JWT for a 1-hour installation access_token. Used for
    repo operations that happen WITHOUT a user present (cron jobs,
    webhook handlers). `make_app_jwt()` is wired in below; the
    installation-token route is intentionally not implemented yet
    — add it when we actually need server-side repo operations.

The stored user-OAuth payload is GitHub's literal token response plus
an `obtained_at` epoch, JSON-encoded then Fernet-encrypted (same
pattern as the Google + Figma connectors).
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

GITHUB_PROVIDER = "github"
GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
DEFAULT_SCOPES = "read:user user:email"  # most repo perms come from the App install, not the OAuth scope
JWT_ALG_STATE = "HS256"
JWT_ALG_APP = "RS256"
STATE_TTL_SECONDS = 600
# GitHub max JWT lifetime is 10 minutes; we use 8 to be safe.
APP_JWT_TTL_SECONDS = 8 * 60


def github_oauth_configured() -> bool:
    return bool(
        settings.github_app_client_id
        and settings.github_app_client_secret
        and settings.github_oauth_redirect_uri
    )


def github_app_configured() -> bool:
    return bool(settings.github_app_id and settings.github_app_private_key_pem)


# ─────────────────────── user OAuth flow ───────────────────────

def authorize_url(state: str, scopes: str | None = None) -> str:
    if not github_oauth_configured():
        raise HTTPException(500, "GitHub OAuth is not configured on the server")
    from urllib.parse import urlencode
    params = {
        "client_id": settings.github_app_client_id,
        "redirect_uri": settings.github_oauth_redirect_uri,
        "scope": scopes or DEFAULT_SCOPES,
        "state": state,
        "allow_signup": "true",
    }
    return f"{GITHUB_AUTH_URL}?{urlencode(params)}"


def sign_oauth_state() -> str:
    now = int(time.time())
    payload = {
        "provider": GITHUB_PROVIDER,
        "nonce": uuid.uuid4().hex,
        "iat": now,
        "exp": now + STATE_TTL_SECONDS,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALG_STATE)


def verify_oauth_state(state: str) -> dict:
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[JWT_ALG_STATE])
    except jwt.PyJWTError as e:
        raise HTTPException(400, "Invalid or expired OAuth state") from e
    if payload.get("provider") != GITHUB_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    return payload


def exchange_code_for_token(code: str) -> dict[str, Any]:
    if not github_oauth_configured():
        raise HTTPException(500, "GitHub OAuth is not configured on the server")
    resp = requests.post(
        GITHUB_TOKEN_URL,
        headers={"Accept": "application/json"},
        data={
            "client_id": settings.github_app_client_id,
            "client_secret": settings.github_app_client_secret,
            "code": code,
            "redirect_uri": settings.github_oauth_redirect_uri,
        },
        timeout=15,
    )
    if not resp.ok:
        logger.warning("GitHub token exchange failed: %s %s", resp.status_code, resp.text[:300])
        raise HTTPException(400, "GitHub token exchange failed")
    data = resp.json()
    if "error" in data:
        # GitHub returns 200 + {"error": "..."} on application errors
        logger.warning("GitHub token exchange error payload: %s", data.get("error"))
        raise HTTPException(400, f"GitHub token exchange error: {data.get('error')}")
    if "access_token" not in data:
        raise HTTPException(400, "GitHub did not return an access_token")
    return data


def refresh_user_token(refresh_token: str) -> dict[str, Any]:
    """User tokens expire ~8h if the App was registered with token expiration on."""
    if not github_oauth_configured():
        raise HTTPException(500, "GitHub OAuth is not configured on the server")
    resp = requests.post(
        GITHUB_TOKEN_URL,
        headers={"Accept": "application/json"},
        data={
            "client_id": settings.github_app_client_id,
            "client_secret": settings.github_app_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    if not resp.ok:
        logger.warning("GitHub refresh failed: %s %s", resp.status_code, resp.text[:300])
        raise HTTPException(400, "GitHub token refresh failed")
    return resp.json()


def fetch_authenticated_user(access_token: str) -> dict[str, Any]:
    resp = requests.get(
        GITHUB_USER_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=10,
    )
    if not resp.ok:
        logger.warning("GitHub /user failed: %s %s", resp.status_code, resp.text[:200])
        return {}
    return resp.json()


def token_payload_to_store(token_json: dict[str, Any]) -> str:
    payload = dict(token_json)
    payload["obtained_at"] = int(time.time())
    return json.dumps(payload)


# ─────────────────────── app-as-app JWT ───────────────────────


def make_app_jwt() -> str:
    """Sign a short-lived JWT that proves we are the GitHub App itself.

    Use this with /app/installations and /app/installations/{id}/access_tokens
    to obtain installation tokens for server-side repo operations.
    """
    if not github_app_configured():
        raise HTTPException(500, "GitHub App private key / app_id not configured")
    now = int(time.time())
    payload = {
        "iat": now - 30,                            # clock skew tolerance
        "exp": now + APP_JWT_TTL_SECONDS,
        "iss": str(settings.github_app_id),
    }
    return jwt.encode(payload, settings.github_app_private_key_pem, algorithm=JWT_ALG_APP)
