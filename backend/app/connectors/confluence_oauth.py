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
from dataclasses import dataclass
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

# Fixed scope set for the Sprntly Confluence connector.
#
# THE V1/V2 SCOPE SPLIT — the thing that will bite anyone editing this list.
# Atlassian has two scope families and they are NOT interchangeable:
#   * CLASSIC  (read:confluence-content.all, read:confluence-space.summary…)
#     serve the v1 API under /wiki/rest/api/...
#   * GRANULAR (read:space:confluence, read:page:confluence…) serve the v2 API
#     under /wiki/api/v2/...
# Calling a v2 endpoint with classic scopes returns
# `401 {"code":401,"message":"Unauthorized; scope does not match"}` — an
# authentication-shaped error for what is really an authorization mismatch,
# which makes it easy to misread as a bad token. We read v2 for everything
# except the current-user lookup, hence granular + one classic scope.
#
#   read:space:confluence     — GET /wiki/api/v2/spaces (the space picker)
#   read:page:confluence      — GET /wiki/api/v2/pages (and, per Atlassian's
#                               endpoint docs, /blogposts too)
#   read:blogpost:confluence  — declared anyway: the scopes reference lists it
#                               as "View blogposts" while the blogposts
#                               endpoint doc claims read:page covers it. A
#                               redundant scope is harmless; a missing one is
#                               a 401 mid-sync.
#   read:confluence-user      — CLASSIC, for GET /wiki/rest/api/user/current.
#                               v2 has no current-user route, and mixing the
#                               families in one app is permitted (the console
#                               exposes Classic and Granular as separate tabs
#                               on the same app).
#   search:confluence         — CLASSIC, for CQL search (GET
#                               /wiki/rest/api/search), which chat's live
#                               lookup uses. v2 has no search endpoint either.
#                               A connection authorized before this scope was
#                               added keeps working: confluence_fetch reports
#                               search as unavailable and the adapter falls
#                               back to listing.
#   offline_access            — REQUIRED for a refresh_token (~1h tokens)
#
# Every scope here must ALSO be granted on the app in the developer console,
# or the consent screen 400s. And scopes are baked into the token at consent:
# changing this list means every existing connection must RECONNECT to gain
# the new capability.
CONFLUENCE_SCOPES = (
    "read:space:confluence read:page:confluence read:blogpost:confluence "
    "read:confluence-user search:confluence offline_access"
)

#: `connections.config` key holding the resolved site id, cached at connect so
#: readers don't re-hit accessible-resources on every call.
CONFIG_CLOUD_ID = "cloud_id"
#: The accessible-resources payload, also cached at connect — carries each
#: site's browse URL, which page permalinks are built from.
CONFIG_SITES = "sites"
#: The spaces the workspace chose to sync. EMPTY MEANS EVERY readable space —
#: the same backwards-compatible default as slack_sync's channel selection, so
#: a connection made before the picker existed keeps working unchanged.
CONFIG_SYNC_SPACE_IDS = "sync_space_ids"
#: {space_id: key} for the ids above, kept alongside so a space that has become
#: unreadable can be reported BY NAME rather than as an opaque id.
CONFIG_SYNC_SPACE_KEYS = "sync_space_keys"

JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600
_TIMEOUT = 20

#: 429 backoff. Atlassian's quota is a points-based cost budget, so a burst of
#: body-bearing page reads is the thing most likely to trip it. Shape mirrors
#: kg_ingest.transcription's retry — bounded attempts, honour Retry-After.
_MAX_ATTEMPTS = 3
_MAX_BACKOFF_S = 30


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
    non-2xx so callers degrade rather than fail the connect.

    The shape is validated rather than trusted: this feeds cloud_id
    resolution, which every later REST call depends on, and an unexpected
    payload (an error envelope, a gateway's HTML) would otherwise surface as
    an AttributeError deep inside a sync rather than as "no site visible"."""
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
    body = resp.json()
    if not isinstance(body, list):
        logger.warning(
            "Confluence accessible-resources returned %s, expected a list",
            type(body).__name__,
        )
        return []
    return [s for s in body if isinstance(s, dict)]


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


def site_name_for_cloud(access_token: str, cloud_id: str) -> str | None:
    """The site's display name, used as a fallback connection label when the
    identity lookup is refused (an org can hide user details while content
    reads work fine). Returns None if unavailable."""
    for site in get_accessible_resources(access_token):
        if site.get("id") == cloud_id:
            return site.get("name")
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


# ── Read API: shared HTTP policy ─────────────────────────────────────────────
#
# There is no shared connector HTTP client in this codebase (every connector
# does bare `requests` with a module-local timeout), so rate-limit and auth
# policy is hand-rolled — but hand-rolled ONCE, here, because both the space
# picker route and the KG puller need identical behaviour.


def api_get(
    access_token: str,
    url: str,
    params: dict[str, Any] | None = None,
    *,
    what: str = "read",
) -> dict[str, Any]:
    """One authenticated GET against a Confluence REST URL, with the error
    contract the callers depend on:

      429   honour Retry-After and retry, up to _MAX_ATTEMPTS
      401   ConfluenceAuthExpiredError — carries status_code=401 so
            kg_ingest.auto_sync stamps "reconnect required" rather than
            logging an ERROR traceback
      403   ConfluenceAuthExpiredError too. Unlike a tracker DELETE (where a
            403 is a per-object permission fact worth distinguishing), a 403
            on a plain read here means the grant no longer covers what we ask
            for — reconnecting IS the remedy.
      404   returned as {} rather than raised: a space can be deleted or
            restricted mid-sync, and one missing container must not read as a
            broken credential.
    """
    last_status = None
    for attempt in range(_MAX_ATTEMPTS):
        resp = requests.get(
            url,
            params=params or {},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=_TIMEOUT,
        )
        last_status = resp.status_code
        if resp.status_code == 429:
            if attempt == _MAX_ATTEMPTS - 1:
                break
            try:
                wait = int(resp.headers.get("Retry-After") or 2)
            except (TypeError, ValueError):
                wait = 2
            logger.info(
                "Confluence %s rate-limited; retrying in %ss", what,
                min(wait, _MAX_BACKOFF_S),
            )
            time.sleep(min(max(wait, 0), _MAX_BACKOFF_S))
            continue
        if resp.status_code in (401, 403):
            logger.warning("Confluence %s auth rejected: %s", what, resp.status_code)
            raise ConfluenceAuthExpiredError(
                "Confluence rejected the stored token — reconnect Confluence to continue"
            )
        if resp.status_code == 404:
            logger.info("Confluence %s: not found (404)", what)
            return {}
        if not resp.ok:
            logger.warning(
                "Confluence %s failed: %s %s", what, resp.status_code, resp.text[:200]
            )
            raise HTTPException(502, f"Confluence {what} failed")
        return resp.json() or {}
    raise HTTPException(502, f"Confluence {what} rate-limited ({last_status})")


def next_cursor(body: dict[str, Any]) -> str | None:
    """The `cursor` query param out of a v2 response's `_links.next`.

    v2 paginates by opaque cursor and advertises the next page as a RELATIVE
    url ("/wiki/api/v2/spaces?cursor=…&limit=100"). Parsing the param out
    beats concatenating that path onto our base: the base already carries
    /wiki, so naive concatenation double-prefixes it."""
    nxt = ((body.get("_links") or {}).get("next")) or ""
    if not nxt:
        return None
    from urllib.parse import parse_qs, urlparse

    values = parse_qs(urlparse(nxt).query).get("cursor") or []
    return values[0] if values else None


def list_spaces(
    access_token: str,
    cloud_id: str,
    *,
    limit: int = 100,
    max_pages: int = 3,
    include_personal: bool = False,
) -> list[dict[str, Any]]:
    """Every space this token can read, as `[{id, key, name, type}]`.

    Personal spaces (`~accountid`) are excluded by default: they are people's
    private scratch areas, and sweeping them into a company knowledge graph is
    the kind of surprise a connector should not spring. `max_pages` bounds this
    at pilot scale the way every other connector bounds its listing."""
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(max_pages):
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        body = api_get(
            access_token,
            f"{CONFLUENCE_API_BASE}/{cloud_id}/wiki/api/v2/spaces",
            params,
            what="list_spaces",
        )
        results = body.get("results") or []
        for s in results:
            if not s.get("id"):
                continue
            if not include_personal and s.get("type") == "personal":
                continue
            out.append({
                "id": str(s.get("id")),
                "key": s.get("key"),
                "name": s.get("name"),
                "type": s.get("type"),
            })
        cursor = next_cursor(body)
        if not cursor or not results:
            break
    return out


# ── Sync context ─────────────────────────────────────────────────────────────


class ConfluenceNotConnectedError(RuntimeError):
    """No usable Confluence connection for this company."""


@dataclass(frozen=True)
class ConfluenceContext:
    """Everything one Confluence read pass needs, resolved from the stored
    connection in a single place.

    This exists because of a shape mismatch: `kg_ingest.runner.token_for`
    hands a puller exactly ONE field out of the decrypted token payload, but a
    Confluence pull needs the access token AND the site id AND the picked
    spaces — the last two living in `connections.config`, which a lone token
    can't reach. So the puller's credential is the company id (the `uploads`
    puller's trick) and this resolves the rest."""

    company_id: str
    access_token: str
    cloud_id: str
    #: https://api.atlassian.com/ex/confluence/{cloud_id}/wiki — append
    #: /api/v2/... or /rest/api/...
    base: str
    #: https://acme.atlassian.net/wiki — for human-facing page permalinks.
    site_url: str | None
    #: EMPTY means every readable space (see CONFIG_SYNC_SPACE_IDS).
    space_ids: list[str]
    space_keys: dict[str, str]


def sync_context(company_id: str) -> ConfluenceContext:
    """Resolve a live read context for `company_id`.

    Refreshes AND PERSISTS an expiring access token first. Persisting is not
    optional: Atlassian rotates refresh tokens, so a throwaway refresh strands
    the stored one and the connection dies at the next cycle. Because this
    re-reads the row every pass it also picks up a token another path (the
    probe, auto_sync's pre-sync refresh) just wrote — the refresh here is a
    backstop, not a duplicate.

    Raises ConfluenceNotConnectedError when there is no row, no readable token,
    or no site the token can see."""
    from app import db
    from app.connectors.tokens import decrypt_token_json, encrypt_token_json

    row = db.get_connection(company_id, CONFLUENCE_PROVIDER)
    if not row:
        raise ConfluenceNotConnectedError(
            f"Confluence is not connected for company {company_id}"
        )
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except Exception as e:  # noqa: BLE001 — unreadable token is a dead connection
        raise ConfluenceNotConnectedError(
            "the stored Confluence token could not be read"
        ) from e

    refresh_token = token_json.get("refresh_token")
    obtained_at = token_json.get("obtained_at") or 0
    expires_in = token_json.get("expires_in") or 3600
    if refresh_token and time.time() > obtained_at + expires_in - 120:
        new_payload = token_payload_to_store(
            refresh_access_token(refresh_token),
            company_id=company_id,
            keep_refresh_token=refresh_token,
        )
        db.update_connection_tokens(
            company_id, CONFLUENCE_PROVIDER, encrypt_token_json(new_payload)
        )
        token_json = json.loads(new_payload)

    access_token = token_json.get("access_token") or ""
    if not access_token:
        raise ConfluenceNotConnectedError("the Confluence connection has no access token")

    try:
        config = json.loads(row.get("config_json") or "{}")
    except (TypeError, ValueError):
        config = {}

    cloud_id = config.get(CONFIG_CLOUD_ID) or first_cloud_id(access_token)
    if not cloud_id:
        raise ConfluenceNotConnectedError(
            "the Confluence token can no longer see any site"
        )

    # Prefer the site list cached at connect over a fresh accessible-resources
    # round trip — this runs on every sync pass.
    site_url = next(
        (
            s.get("url")
            for s in (config.get(CONFIG_SITES) or [])
            if isinstance(s, dict) and s.get("id") == cloud_id
        ),
        None,
    ) or site_url_for_cloud(access_token, cloud_id)

    raw_ids = config.get(CONFIG_SYNC_SPACE_IDS) or []
    space_ids = [str(i) for i in raw_ids if i]
    raw_keys = config.get(CONFIG_SYNC_SPACE_KEYS) or {}
    space_keys = (
        {str(k): str(v) for k, v in raw_keys.items()}
        if isinstance(raw_keys, dict) else {}
    )

    return ConfluenceContext(
        company_id=company_id,
        access_token=access_token,
        cloud_id=cloud_id,
        base=f"{CONFLUENCE_API_BASE}/{cloud_id}/wiki",
        site_url=site_url,
        space_ids=space_ids,
        space_keys=space_keys,
    )
