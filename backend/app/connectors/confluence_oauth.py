"""Confluence Cloud (Atlassian) OAuth 2.0 3LO helpers.

Confluence is a `documents` connector (catalog.py). CURRENT SCOPE: OAuth
connect only — no KG puller yet (PULLERS), so a connected Confluence shows
up healthy in Settings → Connectors and ingests nothing. The puller and the
space picker are deliberate later phases.

Flow:
    1. Frontend hits POST /v1/connectors/authorize?provider=confluence
    2. We build a state JWT + return Atlassian's authorize URL
    3. Browser navigates to Atlassian's consent screen
    4. Atlassian redirects back to /v1/connectors/confluence/callback?code=…&state=…
    5. We exchange the code for {access_token, refresh_token, expires_in, …},
       resolve the accessible Confluence site(s) → `cloud_id`, and store an
       encrypted JSON blob under provider="confluence".

WHY A SEPARATE ATLASSIAN APP FROM JIRA (not a shared one):
    An Atlassian OAuth 2.0 integration carries exactly ONE callback URL. A
    shared app would force both connectors through a single
    /atlassian/callback that disambiguates on the state JWT's provider claim
    — a refactor of the shipped, tested Jira connector to enable this one.
    It would also mean a customer connecting only Jira is asked to grant
    `read:confluence-content.all`, an over-broad consent for zero benefit.
    So: two console apps, two client-id triples, two independent connection
    rows and refresh chains. There is deliberately NO
    `confluence_client_id or jira_client_id` fallback — that would produce a
    silent misconfiguration where consent 400s on undeclared scopes.

Atlassian specifics (identical to Jira — see jira_oauth.py, which this
mirrors deliberately so the two can be diffed):
    - Auth + token endpoints live on `auth.atlassian.com`; the REST API lives
      on `api.atlassian.com/ex/confluence/{cloud_id}/wiki/...`. You CANNOT
      call a customer's `*.atlassian.net` host with a 3LO token.
    - `cloud_id` is NOT in the token response. Resolve it separately via
      `GET /oauth/token/accessible-resources`.
    - Access tokens expire in ~1 hour. To get a `refresh_token` at all you
      MUST request `offline_access` AND `prompt=consent` on the authorize URL.
    - Refresh tokens ROTATE: each refresh returns a NEW refresh_token, so we
      persist the whole new payload.

WHAT THE TOKEN CAN SEE (the thing support will ask about):
    3LO acts AS THE AUTHORIZING USER. The token reads exactly what that
    person can read — space permissions and per-page restrictions are
    enforced by Atlassian, not by us. There is no scope that widens this and
    none that narrows it to particular spaces, so coverage depends on who
    clicked Connect and changes silently if their permissions change.
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

CONFLUENCE_PROVIDER = "confluence"
CONFLUENCE_AUTH_URL = "https://auth.atlassian.com/authorize"
CONFLUENCE_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
CONFLUENCE_ACCESSIBLE_RESOURCES_URL = (
    "https://api.atlassian.com/oauth/token/accessible-resources"
)
# + /{cloud_id}/wiki/api/v2/... (v2) or /{cloud_id}/wiki/rest/api/... (v1)
CONFLUENCE_API_BASE = "https://api.atlassian.com/ex/confluence"

# Fixed scope set for the Sprntly Confluence connector. Classic scopes, which
# Atlassian recommends over the granular ones where they exist.
#   read:confluence-space.summary    — list spaces (the space picker)
#   read:confluence-content.summary  — content metadata without expansions
#   read:confluence-content.all      — page/blogpost BODIES (the KG ingest)
#   search:confluence                — CQL search (content discovery)
#   read:confluence-user             — resolve the authorizing user for the label
#   offline_access                   — REQUIRED for a refresh_token (~1h tokens)
CONFLUENCE_SCOPES = (
    "read:confluence-space.summary read:confluence-content.summary "
    "read:confluence-content.all search:confluence read:confluence-user "
    "offline_access"
)

#: `connections.config` key holding the resolved site id, cached at connect so
#: readers don't re-hit accessible-resources on every call.
CONFIG_CLOUD_ID = "cloud_id"

JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600
_TIMEOUT = 20


def confluence_configured() -> bool:
    return bool(
        settings.confluence_client_id
        and settings.confluence_client_secret
        and settings.confluence_oauth_redirect_uri
    )


def authorize_url(state: str) -> str:
    """Build the URL the user gets redirected to for the Atlassian consent screen.

    `audience=api.atlassian.com` and `prompt=consent` are both required: the
    former scopes the token to the Confluence REST API, the latter (together
    with the `offline_access` scope) guarantees a refresh_token is issued.
    """
    if not confluence_configured():
        raise HTTPException(500, "Confluence OAuth is not configured on the server")
    from urllib.parse import urlencode

    params = {
        "audience": "api.atlassian.com",
        "client_id": settings.confluence_client_id,
        "scope": CONFLUENCE_SCOPES,
        "redirect_uri": settings.confluence_oauth_redirect_uri,
        "state": state,
        "response_type": "code",
        "prompt": "consent",
    }
    return f"{CONFLUENCE_AUTH_URL}?{urlencode(params)}"


def sign_oauth_state(*, company_id: str, return_to: str | None = None) -> str:
    """Mint a signed state JWT binding the OAuth round-trip to a company —
    the callback (which has no user session) trusts only this signature to
    know which company gets the new token.

    `return_to` is an optional relative path the callback redirects to instead
    of the default /settings?section=connectors."""
    now = int(time.time())
    payload = {
        "provider": CONFLUENCE_PROVIDER,
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
    # The provider claim is what keeps the two Atlassian connectors' callbacks
    # from accepting each other's state — they share an identity provider and
    # a JWT secret, so this check is the only thing separating them.
    if payload.get("provider") != CONFLUENCE_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    if not payload.get("company_id"):
        raise HTTPException(400, "OAuth state missing company_id")
    return payload


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Trade an authorization code for tokens. Returns the parsed JSON
    {access_token, refresh_token, expires_in, scope, token_type}."""
    if not confluence_configured():
        raise HTTPException(500, "Confluence OAuth is not configured on the server")
    resp = requests.post(
        CONFLUENCE_TOKEN_URL,
        json={
            "grant_type": "authorization_code",
            "client_id": settings.confluence_client_id,
            "client_secret": settings.confluence_client_secret,
            "code": code,
            "redirect_uri": settings.confluence_oauth_redirect_uri,
        },
        timeout=15,
    )
    if not resp.ok:
        logger.warning(
            "Confluence token exchange failed: %s %s",
            resp.status_code, resp.text[:300],
        )
        raise HTTPException(400, "Confluence token exchange failed")
    return resp.json()


class ConfluenceAuthExpiredError(RuntimeError):
    """The stored Confluence token was rejected and could not be refreshed
    (refresh token expired ~90 days / revoked). The only remedy is the user
    reconnecting.

    Carries `status_code = 401` ON PURPOSE. kg_ingest.auto_sync decides
    between "reconnect required" and a genuine error by reading
    `getattr(exc, "status_code", None)` — and a bare requests.HTTPError keeps
    its status on `.response.status_code`, which that check does not look at.
    So a puller that only calls raise_for_status() on a 401 gets an ERROR
    traceback and a raw error string on the connection row instead of the
    reconnect prompt. Setting the attribute here makes the right branch fire
    without depending on a fix to shared code.
    """

    status_code = 401


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """Exchange a refresh token for a fresh {access_token, refresh_token, …}.

    Atlassian ROTATES refresh tokens — the response carries a new one, so the
    caller must persist the whole payload (see token_payload_to_store).
    Raises ConfluenceAuthExpiredError if Atlassian rejects the refresh token."""
    if not confluence_configured():
        raise HTTPException(500, "Confluence OAuth is not configured on the server")
    resp = requests.post(
        CONFLUENCE_TOKEN_URL,
        json={
            "grant_type": "refresh_token",
            "client_id": settings.confluence_client_id,
            "client_secret": settings.confluence_client_secret,
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    if resp.status_code in (400, 401, 403):
        logger.warning(
            "Confluence token refresh rejected: %s %s",
            resp.status_code, resp.text[:200],
        )
        raise ConfluenceAuthExpiredError(
            "Confluence rejected the refresh token — reconnect Confluence to continue"
        )
    if not resp.ok:
        logger.warning(
            "Confluence token refresh failed: %s %s",
            resp.status_code, resp.text[:300],
        )
        raise HTTPException(502, "Confluence token refresh failed")
    return resp.json()


def token_payload_to_store(
    token_json: dict[str, Any],
    *,
    company_id: str,
    keep_refresh_token: str | None = None,
) -> str:
    """Wrap Atlassian's token response before encryption.

    Three things ride along, and each has a caller that breaks without it:

    `obtained_at` — the refresh schedulers (auto_sync._token_is_fresh, the
    connector probe) compare `obtained_at + expires_in` against now to decide
    whether to refresh before use.

    `company_id` — the credential the kg_ingest puller will receive. Unlike
    every other OAuth connector, Confluence's puller needs more than an access
    token: it needs the picked spaces, which live in `connections.config`,
    which `runner.token_for` cannot reach because it hands the puller exactly
    ONE field of this payload. So the field we hand it is the company id, and
    the puller reads its own connection row — the same trick the `uploads`
    puller already uses. THIS MUST SURVIVE EVERY REFRESH: drop it and
    `token_for` raises "connection for 'confluence' has no 'company_id'" on
    the next sync, not on the refresh that caused it.

    `keep_refresh_token` — Atlassian rotates refresh tokens and normally
    returns the new one, but a response that omitted it must not blank out the
    stored one (mirrors asana_oauth's merge).
    """
    payload = dict(token_json)
    payload["company_id"] = company_id
    if not payload.get("refresh_token") and keep_refresh_token:
        payload["refresh_token"] = keep_refresh_token
    payload["obtained_at"] = int(time.time())
    return json.dumps(payload)


# ── Site (cloud) resolution ──────────────────────────────────────────────────
#
# A 3LO token can be authorized against multiple Confluence sites. Every REST
# call needs the target site's cloud_id, which the token response does NOT
# contain.


def get_accessible_resources(access_token: str) -> list[dict[str, Any]]:
    """Return the Atlassian sites this token can act on, each as Atlassian's
    native {id (cloud_id), name, url, scopes, avatarUrl}. Returns [] on any
    non-2xx so callers degrade rather than fail the connect."""
    resp = requests.get(
        CONFLUENCE_ACCESSIBLE_RESOURCES_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=_TIMEOUT,
    )
    if not resp.ok:
        logger.warning(
            "Confluence accessible-resources failed: %s %s",
            resp.status_code, resp.text[:200],
        )
        return []
    return resp.json() or []


def first_cloud_id(access_token: str) -> str | None:
    """Resolve the first accessible site's cloud_id, or None if the token can't
    see any site. Used as the fallback when a connection row has no cached
    CONFIG_CLOUD_ID."""
    sites = get_accessible_resources(access_token)
    return (sites[0].get("id") if sites else None) or None


def site_url_for_cloud(access_token: str, cloud_id: str) -> str | None:
    """Best-effort lookup of a site's browse base URL (e.g.
    https://acme.atlassian.net) from accessible-resources, for building page
    permalinks. Returns None if unavailable — the caller then omits the url."""
    for site in get_accessible_resources(access_token):
        if site.get("id") == cloud_id:
            return site.get("url")
    return None


def fetch_current_user(access_token: str, cloud_id: str) -> dict[str, Any]:
    """Return Confluence's current-user payload — {accountId, email,
    publicName, displayName, …} — for `connections.account_label` and the
    health probe's identity check.

    Uses the v1 endpoint deliberately: the v2 API has no current-user route.
    Covered by the `read:confluence-user` scope. Returns {} on any non-2xx so
    callers can fall back to other label sources."""
    resp = requests.get(
        f"{CONFLUENCE_API_BASE}/{cloud_id}/wiki/rest/api/user/current",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=_TIMEOUT,
    )
    if not resp.ok:
        logger.warning(
            "Confluence user/current failed: %s %s",
            resp.status_code, resp.text[:200],
        )
        return {}
    return resp.json() or {}


def account_label_from(user: dict[str, Any], sites: list[dict[str, Any]]) -> str | None:
    """Human label for the connection row: the authorizing user's email or
    name, falling back to the site name when the identity call came back empty
    (a token can be valid for content while `read:confluence-user` is refused
    by an org privacy setting). None when nothing usable is available."""
    return (
        user.get("email")
        or user.get("displayName")
        or user.get("publicName")
        or (sites[0].get("name") if sites else None)
        or None
    )
