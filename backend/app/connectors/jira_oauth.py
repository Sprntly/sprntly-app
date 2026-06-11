"""Jira Cloud OAuth 2.0 (3LO) helpers.

Flow:
    1. Frontend hits POST /v1/connectors/jira/start-oauth
    2. We build a state JWT + return Atlassian's authorize URL
    3. Browser navigates to Atlassian's consent screen
    4. Atlassian redirects back to /v1/connectors/jira/callback?code=...&state=...
    5. We exchange the code for {access_token, refresh_token, expires_in},
       resolve the Jira Cloud site (cloud_id) via accessible-resources, and
       store an encrypted JSON blob under provider="jira"

Jira specifics worth knowing:
    - Access tokens expire in ~1 hour; `offline_access` scope grants a
      rotating refresh token. Refresh-at-use mirrors the HubSpot pattern.
    - All API calls go through the gateway host with an explicit cloud id:
      https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/...
      The cloud id comes from GET /oauth/token/accessible-resources and is
      stored alongside the token at connect time.
    - API v3 requires issue descriptions in Atlassian Document Format (ADF),
      not markdown — see `adf_document`.
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

JIRA_PROVIDER = "jira"
ATLASSIAN_AUTH_URL = "https://auth.atlassian.com/authorize"
ATLASSIAN_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
ATLASSIAN_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
ATLASSIAN_API_BASE = "https://api.atlassian.com/ex/jira"
# read for the picker/status checks, write to create issues, offline_access
# for the refresh token (without it the connection dies after an hour).
JIRA_SCOPES = "read:jira-work write:jira-work read:me offline_access"
JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600
_READ_TIMEOUT = 15
_WRITE_TIMEOUT = 20
# Refresh this many seconds before nominal expiry to avoid mid-call death.
_REFRESH_SKEW_SECONDS = 120


def jira_configured() -> bool:
    return bool(
        settings.jira_client_id
        and settings.jira_client_secret
        and settings.jira_oauth_redirect_uri
    )


def authorize_url(state: str) -> str:
    """Build the Atlassian consent-screen URL for the Jira connector."""
    if not jira_configured():
        raise HTTPException(500, "Jira OAuth is not configured on the server")
    from urllib.parse import urlencode

    params = {
        "audience": "api.atlassian.com",
        "client_id": settings.jira_client_id,
        "scope": JIRA_SCOPES,
        "redirect_uri": settings.jira_oauth_redirect_uri,
        "state": state,
        "response_type": "code",
        "prompt": "consent",
    }
    return f"{ATLASSIAN_AUTH_URL}?{urlencode(params)}"


def sign_oauth_state(*, company_id: str, return_to: str | None = None) -> str:
    """Mint a signed state JWT binding the OAuth round-trip to a company.
    The callback (which has no user session) trusts only this signature to
    know which company gets the new token."""
    now = int(time.time())
    payload = {
        "provider": JIRA_PROVIDER,
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
    if payload.get("provider") != JIRA_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    if not payload.get("company_id"):
        raise HTTPException(400, "OAuth state missing company_id")
    return payload


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Trade an authorization code for tokens. Returns the parsed JSON
    ({access_token, refresh_token, expires_in, scope})."""
    if not jira_configured():
        raise HTTPException(500, "Jira OAuth is not configured on the server")
    resp = requests.post(
        ATLASSIAN_TOKEN_URL,
        json={
            "grant_type": "authorization_code",
            "client_id": settings.jira_client_id,
            "client_secret": settings.jira_client_secret,
            "code": code,
            "redirect_uri": settings.jira_oauth_redirect_uri,
        },
        timeout=_READ_TIMEOUT,
    )
    if not resp.ok:
        logger.warning(
            "Jira token exchange failed: %s %s", resp.status_code, resp.text[:300]
        )
        raise HTTPException(400, "Jira token exchange failed")
    return resp.json()


def refresh_access_token(token_json: dict[str, Any]) -> dict[str, Any]:
    """Refresh an expired Jira access token IN PLACE (rotating refresh
    token: Atlassian may issue a new one on every refresh — always store
    the returned value, never reuse the old one)."""
    refresh_token = token_json.get("refresh_token")
    if not refresh_token:
        raise HTTPException(
            400, "No Jira refresh_token available — user must re-authorize"
        )
    resp = requests.post(
        ATLASSIAN_TOKEN_URL,
        json={
            "grant_type": "refresh_token",
            "client_id": settings.jira_client_id,
            "client_secret": settings.jira_client_secret,
            "refresh_token": refresh_token,
        },
        timeout=_READ_TIMEOUT,
    )
    if not resp.ok:
        logger.warning(
            "Jira token refresh failed: %s %s", resp.status_code, resp.text[:300]
        )
        raise HTTPException(400, "Jira token refresh failed — reconnect Jira")
    new_tokens = resp.json()
    token_json["access_token"] = new_tokens["access_token"]
    token_json["refresh_token"] = new_tokens.get("refresh_token", refresh_token)
    token_json["expires_in"] = new_tokens.get("expires_in", 3600)
    token_json["obtained_at"] = int(time.time())
    return token_json


def fetch_accessible_resources(access_token: str) -> list[dict[str, Any]]:
    """List the Jira Cloud sites this token can reach
    ([{id, url, name, scopes}]). The `id` is the cloud_id used in API URLs."""
    resp = requests.get(
        ATLASSIAN_RESOURCES_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=_READ_TIMEOUT,
    )
    if not resp.ok:
        logger.warning(
            "Jira accessible-resources failed: %s %s",
            resp.status_code, resp.text[:200],
        )
        return []
    body = resp.json()
    return body if isinstance(body, list) else []


def fetch_myself(access_token: str, cloud_id: str) -> dict[str, Any]:
    """Returns Jira's /myself payload ({accountId, emailAddress, displayName,
    ...}) — the canonical token-validity check for test-connection."""
    resp = requests.get(
        f"{ATLASSIAN_API_BASE}/{cloud_id}/rest/api/3/myself",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=_READ_TIMEOUT,
    )
    if not resp.ok:
        logger.warning(
            "Jira /myself failed: %s %s", resp.status_code, resp.text[:200]
        )
        return {}
    return resp.json() or {}


def token_payload_to_store(
    token_json: dict[str, Any], *, resources: list[dict[str, Any]] | None = None,
) -> str:
    """Wrap Atlassian's token response with an obtained_at stamp and the
    resolved cloud site(s) before encryption. The first accessible site is
    the active one; the full list is kept for a future site picker."""
    payload = dict(token_json)
    payload["obtained_at"] = int(time.time())
    if resources:
        payload["cloud_id"] = resources[0].get("id")
        payload["site_url"] = resources[0].get("url")
        payload["site_name"] = resources[0].get("name")
        payload["resources"] = [
            {"id": r.get("id"), "url": r.get("url"), "name": r.get("name")}
            for r in resources
        ]
    return json.dumps(payload)


def get_valid_access_token(company_id: str) -> tuple[str, dict[str, Any]]:
    """Decrypt the company's stored Jira token, refresh if (nearly) expired,
    persist the rotated token, and return (access_token, token_json).
    token_json carries cloud_id/site_url resolved at connect time.

    Raises HTTPException(404) when Jira isn't connected — callers surface
    that as 'connect Jira first'."""
    from app import db
    from app.connectors.tokens import (
        TokenEncryptionError,
        decrypt_token_json,
        encrypt_token_json,
    )

    row = db.get_connection(company_id, JIRA_PROVIDER)
    if not row or not row.get("token_json_encrypted"):
        raise HTTPException(404, "Jira is not connected for this company")
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, ValueError) as e:
        raise HTTPException(500, "Jira token unreadable") from e

    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(500, "Jira connection has no access_token")

    obtained_at = token_json.get("obtained_at", 0)
    expires_in = token_json.get("expires_in", 3600)
    if time.time() > obtained_at + expires_in - _REFRESH_SKEW_SECONDS:
        token_json = refresh_access_token(token_json)
        try:
            encrypted = encrypt_token_json(json.dumps(token_json))
            db.update_connection_tokens(company_id, JIRA_PROVIDER, encrypted)
        except Exception:  # noqa: BLE001 — a persist failure must not block the call
            logger.warning("Failed to persist refreshed Jira token", exc_info=True)
        access_token = token_json["access_token"]

    return access_token, token_json


# ── Read side (target pickers) ────────────────────────────────────────────────


def list_projects(access_token: str, cloud_id: str) -> list[dict[str, Any]]:
    """All projects the token can see, as {id, key, name} dicts (paginated
    project search). Used to pick the push target."""
    out: list[dict[str, Any]] = []
    start_at = 0
    while True:
        data = _get(
            access_token, cloud_id, "/project/search",
            params={"startAt": start_at, "maxResults": 50},
        )
        values = data.get("values", [])
        for p in values:
            out.append({"id": p.get("id"), "key": p.get("key"),
                        "name": p.get("name")})
        if data.get("isLast", True) or not values:
            break
        start_at += len(values)
    return [p for p in out if p.get("id")]


def list_issue_types(
    access_token: str, cloud_id: str, project_id: str,
) -> list[dict[str, Any]]:
    """Create-meta issue types for one project, as {id, name, subtask}.
    Subtask types are excluded — stories push as standalone issues."""
    data = _get(
        access_token, cloud_id,
        f"/issue/createmeta/{project_id}/issuetypes",
        params={"maxResults": 100},
    )
    # The createmeta endpoint has returned the list under both keys across
    # API revisions; accept either.
    types = data.get("issueTypes") or data.get("values") or []
    return [
        {"id": t.get("id"), "name": t.get("name"),
         "subtask": bool(t.get("subtask"))}
        for t in types
        if t.get("id") and not t.get("subtask")
    ]


# ── Write side (push generated user stories as Jira issues) ──────────────────


def adf_text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def adf_paragraph(text: str) -> dict[str, Any]:
    return {"type": "paragraph", "content": [adf_text(text)]}


def adf_heading(text: str, level: int = 3) -> dict[str, Any]:
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": [adf_text(text)],
    }


def adf_bullet_list(items: list[str]) -> dict[str, Any]:
    return {
        "type": "bulletList",
        "content": [
            {
                "type": "listItem",
                "content": [adf_paragraph(item)],
            }
            for item in items
        ],
    }


def adf_document(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap top-level ADF nodes into the document envelope API v3 expects."""
    return {"type": "doc", "version": 1, "content": blocks}


def create_issue(
    access_token: str,
    cloud_id: str,
    *,
    project_id: str,
    issue_type_id: str,
    summary: str,
    description_adf: dict[str, Any] | None = None,
    labels: list[str] | None = None,
    site_url: str | None = None,
) -> dict[str, Any]:
    """Create one Jira issue. Returns {id, key, url}.

    Priority is deliberately NOT set: it's a screen-configurable field that
    400s on projects where it isn't on the create screen, and the story's
    suggested priority already travels in the description/labels. Raises
    HTTPException on a non-OK response so the caller can isolate per-story
    failures."""
    fields: dict[str, Any] = {
        "project": {"id": str(project_id)},
        "issuetype": {"id": str(issue_type_id)},
        "summary": summary,
    }
    if description_adf is not None:
        fields["description"] = description_adf
    if labels:
        fields["labels"] = labels
    resp = requests.post(
        f"{ATLASSIAN_API_BASE}/{cloud_id}/rest/api/3/issue",
        json={"fields": fields},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=_WRITE_TIMEOUT,
    )
    if not resp.ok:
        logger.warning(
            "Jira create_issue failed: %s %s", resp.status_code, resp.text[:300]
        )
        raise HTTPException(502, "Jira issue creation failed")
    data = resp.json() or {}
    key = data.get("key")
    url = f"{site_url}/browse/{key}" if site_url and key else None
    return {"id": data.get("id"), "key": key, "url": url}


def _get(
    token: str, cloud_id: str, path: str, params: dict | None = None,
) -> dict[str, Any]:
    """Authenticated GET against the Jira Cloud v3 API."""
    r = requests.get(
        f"{ATLASSIAN_API_BASE}/{cloud_id}/rest/api/3{path}",
        params=params or {},
        headers={"Authorization": f"Bearer {token}"},
        timeout=_READ_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()
