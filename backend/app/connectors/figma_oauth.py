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
# Post-Nov-2025 platform update: token + refresh moved off www.figma.com
# onto api.figma.com, and credentials moved from body fields into the
# HTTP Basic auth header.
# https://developers.figma.com/docs/updates-to-figmas-developer-platform/
FIGMA_TOKEN_URL = "https://api.figma.com/v1/oauth/token"
FIGMA_REFRESH_URL = "https://api.figma.com/v1/oauth/refresh"
FIGMA_ME_URL = "https://api.figma.com/v1/me"
# Default scopes when nothing is configured. Comma-separated per Figma docs.
# Per Figma's Nov 17, 2025 platform update, the old `files:read` scope is
# replaced by the granular pair `file_content:read` + `file_metadata:read`.
# https://developers.figma.com/docs/updates-to-figmas-developer-platform/
DEFAULT_SCOPES = (
    "file_content:read,file_metadata:read,"
    "file_dev_resources:read,current_user:read,"
    "library_content:read"
)
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


def sign_oauth_state(
    *, company_id: str, return_to: str | None = None,
) -> str:
    """Mint a signed state JWT that binds the OAuth round-trip to a
    specific company. The callback (which has no user session) trusts
    only this signature to know which company gets the new token.

    `return_to` is an optional relative path (e.g. `/onboarding/4`)
    that the callback will redirect to instead of the default
    `/settings?section=connectors`. Validated upstream in routes."""
    now = int(time.time())
    payload = {
        "provider": FIGMA_PROVIDER,
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
    if payload.get("provider") != FIGMA_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    if not payload.get("company_id"):
        raise HTTPException(400, "OAuth state missing company_id")
    return payload


def _basic_auth_header() -> dict[str, str]:
    """`Authorization: Basic <base64(client_id:client_secret)>` — Figma's
    new token + refresh endpoints take client credentials this way, not
    in the request body."""
    import base64

    creds = f"{settings.figma_client_id}:{settings.figma_client_secret}"
    return {"Authorization": f"Basic {base64.b64encode(creds.encode()).decode()}"}


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Trade an authorization code for tokens. Returns the parsed JSON."""
    if not figma_configured():
        raise HTTPException(500, "Figma OAuth is not configured on the server")
    resp = requests.post(
        FIGMA_TOKEN_URL,
        headers=_basic_auth_header(),
        data={
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
        headers=_basic_auth_header(),
        data={"refresh_token": refresh_token},
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


def fetch_files(access_token: str, team_id: str | None = None) -> list[dict[str, str]]:
    """List the user's Figma files as a normalised ``[{"key", "name"}]`` list.

    Figma's REST API has no flat "list all my files" endpoint: listing requires
    walking ``GET /v1/teams/{team_id}/projects`` -> ``GET
    /v1/projects/{project_id}/files``, which needs both a ``team_id`` AND a
    project-listing OAuth scope. The current connection requests neither (see
    ``DEFAULT_SCOPES`` -- no project/team-listing scope) and captures no
    ``team_id`` at connect time, so without a ``team_id`` this returns ``[]`` -- an
    honest empty list the caller renders as "no designs", never fabricated files.
    Provisioning the listing scope + a stored ``team_id`` is a connectors-lane
    dependency; once it lands and a ``team_id`` is supplied, this walks the
    hierarchy and returns the real list.

    Failures are non-fatal: any upstream error is logged at WARNING (status +
    truncated body, identifiers only -- never the token or file contents) and
    yields ``[]`` so the caller degrades to an honest empty state. Uses
    ``requests.get`` with an explicit timeout, mirroring ``fetch_file`` /
    ``fetch_file_styles``; the caller is responsible for token refresh.
    """
    if not team_id:
        # No team-listing scope / team_id provisioned yet -> honest empty list.
        return []
    files: list[dict[str, str]] = []
    try:
        projects_resp = requests.get(
            f"{FIGMA_API_BASE}/teams/{team_id}/projects",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if not projects_resp.ok:
            logger.warning(
                "Figma /teams/%s/projects failed: %s %s",
                team_id,
                projects_resp.status_code,
                projects_resp.text[:200],
            )
            return []
        for project in projects_resp.json().get("projects", []):
            project_id = project.get("id")
            if not project_id:
                continue
            files_resp = requests.get(
                f"{FIGMA_API_BASE}/projects/{project_id}/files",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            if not files_resp.ok:
                logger.warning(
                    "Figma /projects/%s/files failed: %s %s",
                    project_id,
                    files_resp.status_code,
                    files_resp.text[:200],
                )
                continue
            for f in files_resp.json().get("files", []):
                key = f.get("key")
                name = f.get("name")
                if key and name:
                    files.append({"key": str(key), "name": str(name)})
    except requests.RequestException as exc:
        # Network-level failure (timeout, connection error): identifiers only.
        logger.warning("Figma file listing failed: error_class=%s", type(exc).__name__)
        return []
    return files


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


def fetch_file_variables(access_token: str, file_key: str) -> dict[str, Any]:
    """Fetch local variable definitions for a Figma file.

    Returns the raw GET /v1/files/{key}/variables/local JSON on success.
    Returns ``{}`` on ANY non-OK status or any exception — this call 403s on
    every non-Enterprise plan and whenever the OAuth scopes do not include
    ``file_variables:read`` (which is today's production default).  The
    caller must be able to degrade silently; this helper deliberately diverges
    from ``fetch_file_styles``'s ``raise HTTPException`` pattern.

    Logs non-OK at debug level with status code + file key only (no token,
    no body) to avoid leaking credentials in log aggregators.
    """
    try:
        resp = requests.get(
            f"{FIGMA_API_BASE}/files/{file_key}/variables/local",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        if not resp.ok:
            logger.debug(
                "Figma /files/%s/variables/local returned %s — degrading to empty",
                file_key,
                resp.status_code,
            )
            return {}
        return resp.json()
    except Exception:
        logger.debug(
            "Figma /files/%s/variables/local raised an exception — degrading to empty",
            file_key,
        )
        return {}
