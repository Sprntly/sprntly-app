"""Marvin (heymarvin.com) connector — OAuth 2.1 + Dynamic Client Registration.

Marvin is an AI-native customer-insights / UX-research repository: interviews,
surveys, tagged notes and synthesized Insight reports. That makes it a
`customer-voice` source (catalog.py) — first-party evidence about what
customers actually said, in the same family as Dovetail.

WHY MCP AND NOT REST
    Marvin publishes no REST API. Their marketing mentions an "open API" and an
    "Import API (coming soon)", but the Import API is inbound (pushing data INTO
    Marvin) and the help centre documents 33 integrations with zero developer
    endpoints. Their MCP server is the only programmatic READ surface that
    exists, so the puller talks MCP (see connectors/mcp_client.py).

WHY DYNAMIC CLIENT REGISTRATION
    There is no developer portal to create an app in. Marvin's authorization
    server advertises a `registration_endpoint`, i.e. clients are expected to
    register themselves over the wire (RFC 7591). That's a genuine advantage —
    no partner agreement gates the integration — but it means the client
    credential is minted at runtime and must be persisted, which is what
    db/oauth_clients.py is for. `MARVIN_CLIENT_ID` / `MARVIN_CLIENT_SECRET`
    override registration entirely, for the day Marvin issues Sprntly a static
    partner client.

REGIONS
    Marvin runs two independent deployments with two different authorization
    servers (verified against each server's protected-resource metadata). A
    token, and a registered client, are valid at exactly one of them, so the
    region is chosen BEFORE the redirect and signed into the OAuth state.

PKCE WITHOUT SERVER-SIDE STATE
    The callback has no session — it trusts only the signed `state` JWT. Rather
    than stash the PKCE verifier in a store keyed by state (a second thing to
    expire and garbage-collect), the verifier is DERIVED from the state's nonce
    by HMAC under the app's JWT secret. The callback recomputes it from the
    verified nonce. The verifier therefore never travels over the wire, and an
    attacker holding the redirect URL still can't produce it.

SCOPE
    `mcp:read` is the only scope the server offers. Read-only by construction —
    there is no write path to justify, and no way for a Marvin connection to
    mutate a customer's research.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import jwt
import requests
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

MARVIN_PROVIDER = "marvin"

#: token_json key holding the packed puller credential (also the PULLERS key).
#: Like Superset's, the puller needs more than a bare token — it needs the MCP
#: endpoint too, and `token_for()` hands pullers exactly one string.
CREDENTIAL_KEY = "marvin_credential"

#: The only scope Marvin's authorization server advertises.
MARVIN_SCOPES = "mcp:read"

JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600
_TIMEOUT = 20

#: Marvin's two deployments. `mcp_url` is the MCP resource server (and the
#: RFC 8707 `resource` value); `issuer` is the authorization server that mints
#: tokens for it. Both were read from each deployment's
#: /.well-known/oauth-protected-resource.
REGIONS: dict[str, dict[str, str]] = {
    "us": {
        "label": "US / Global",
        "mcp_url": "https://mcp.heymarvin.com",
        "issuer": "https://app.heymarvin.com",
    },
    "eu": {
        "label": "EU",
        "mcp_url": "https://mcp-eu.heymarvin.com",
        "issuer": "https://app.eu.heymarvin.com",
    },
}
DEFAULT_REGION = "us"


class MarvinAuthExpiredError(RuntimeError):
    """The stored Marvin token was rejected and could not be refreshed.

    Raised so callers can prompt a reconnect instead of surfacing a generic
    upstream failure — mirrors JiraAuthExpiredError.
    """


def marvin_configured() -> bool:
    """True when a Marvin connect can even be attempted.

    Only the redirect URI is genuinely required: the client credential is
    self-registered when absent, so unlike every other OAuth connector there is
    no client id/secret to check.
    """
    return bool(settings.marvin_oauth_redirect_uri)


def normalize_region(raw: str | None) -> str:
    """Canonical region key, defaulting to US for empty/unknown input.

    Unknown values fall back rather than raising: the region arrives from the
    client, and a typo should land the user on the (overwhelmingly more common)
    US deployment where the consent screen will simply tell them if they have
    no account, not 500 the connect button.
    """
    key = (raw or "").strip().lower()
    return key if key in REGIONS else DEFAULT_REGION


def region_config(region: str | None) -> dict[str, str]:
    return REGIONS[normalize_region(region)]


# ── Authorization-server discovery ──────────────────────────────────────────
#
# Endpoints are discovered per RFC 8414 rather than hardcoded, so a Marvin-side
# path change doesn't need a Sprntly deploy. Cached in-process because the
# document is effectively static and every connect/refresh would otherwise pay
# an extra round trip.

_metadata_cache: dict[str, dict[str, Any]] = {}


def discover_metadata(issuer: str) -> dict[str, Any]:
    """Fetch (and memoize) an authorization server's RFC 8414 metadata."""
    cached = _metadata_cache.get(issuer)
    if cached:
        return cached
    url = f"{issuer.rstrip('/')}/.well-known/oauth-authorization-server"
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise HTTPException(502, f"Could not reach Marvin at {issuer}") from e
    if not resp.ok:
        logger.warning(
            "Marvin metadata fetch failed: %s %s", resp.status_code, resp.text[:200]
        )
        raise HTTPException(502, "Marvin authorization metadata is unavailable")
    metadata = resp.json() or {}
    if not metadata.get("authorization_endpoint") or not metadata.get("token_endpoint"):
        raise HTTPException(502, "Marvin authorization metadata is incomplete")
    _metadata_cache[issuer] = metadata
    return metadata


# ── Client registration (RFC 7591) ──────────────────────────────────────────


def _register_client(issuer: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """Register Sprntly as a client at `issuer`. Returns the raw response.

    We ask for a confidential client (`client_secret_post`) because we hold the
    secret server-side and it is strictly stronger than a public client; a
    server that only supports public clients simply omits `client_secret` from
    the response and PKCE carries the flow on its own.
    """
    endpoint = metadata.get("registration_endpoint")
    if not endpoint:
        raise HTTPException(
            500,
            "Marvin does not support dynamic client registration — set "
            "MARVIN_CLIENT_ID / MARVIN_CLIENT_SECRET instead",
        )
    body = {
        "client_name": "Sprntly",
        "client_uri": "https://sprntly.ai",
        "redirect_uris": [settings.marvin_oauth_redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
        "scope": MARVIN_SCOPES,
    }
    try:
        resp = requests.post(endpoint, json=body, timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise HTTPException(502, "Could not register with Marvin") from e
    # RFC 7591 says 201; accept any 2xx since implementations vary.
    if not resp.ok:
        logger.warning(
            "Marvin client registration failed: %s %s",
            resp.status_code, resp.text[:300],
        )
        raise HTTPException(502, "Marvin rejected the client registration")
    data = resp.json() or {}
    if not data.get("client_id"):
        raise HTTPException(502, "Marvin registration returned no client_id")
    return data


def ensure_client(region: str | None = None) -> tuple[str, str | None]:
    """The (client_id, client_secret) to use for `region`.

    Resolution order, first hit wins:
      1. `MARVIN_CLIENT_ID` / `MARVIN_CLIENT_SECRET` — a statically issued
         partner client, should Marvin ever grant one.
      2. A previously registered client persisted for this issuer.
      3. A fresh dynamic registration, persisted before returning.

    `client_secret` is None for a public client (PKCE-only).

    Registration is not locked: two workers racing a first connect both
    register and the loser's row is overwritten. Both credentials remain valid
    at Marvin, so the cost of the race is one orphan client record, which is
    cheaper than coordinating a distributed lock for a once-per-deployment
    event.
    """
    if settings.marvin_client_id:
        return settings.marvin_client_id, (settings.marvin_client_secret or None)

    from app.db.oauth_clients import get_oauth_client, save_oauth_client

    issuer = region_config(region)["issuer"]
    existing = get_oauth_client(MARVIN_PROVIDER, issuer)
    if existing:
        return existing["client_id"], existing["client_secret"]

    registration = _register_client(issuer, discover_metadata(issuer))
    client_id = registration["client_id"]
    client_secret = registration.get("client_secret") or None
    try:
        save_oauth_client(
            MARVIN_PROVIDER,
            issuer,
            client_id=client_id,
            client_secret=client_secret,
            registration=registration,
        )
    except Exception:  # noqa: BLE001 — an unpersisted client still works NOW
        # Losing the write means the next process re-registers. Failing the
        # user's connect over it would be worse than the orphan record.
        logger.warning(
            "Marvin: could not persist the registered client for %s", issuer,
            exc_info=True,
        )
    return client_id, client_secret


# ── PKCE ────────────────────────────────────────────────────────────────────


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def code_verifier_for(nonce: str) -> str:
    """Deterministically derive the PKCE verifier for an OAuth round trip.

    HMAC under the app's JWT secret, so the callback can recompute it from the
    (signed, therefore unforgeable) nonce without any server-side storage. The
    verifier itself is never transmitted, which is stronger than carrying it in
    the state parameter would be.
    """
    digest = hmac.new(
        settings.jwt_secret.encode("utf-8"),
        f"marvin-pkce:{nonce}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _b64url(digest)


def _code_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


# ── OAuth state ─────────────────────────────────────────────────────────────


def sign_oauth_state(
    *,
    company_id: str,
    region: str | None = None,
    return_to: str | None = None,
) -> str:
    """Mint the state JWT binding this round trip to a company AND a region.

    The region has to survive the redirect: the callback needs to know which
    issuer to exchange the code at, and which MCP endpoint to store.
    """
    now = int(time.time())
    payload = {
        "provider": MARVIN_PROVIDER,
        "company_id": company_id,
        "region": normalize_region(region),
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
    if payload.get("provider") != MARVIN_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    if not payload.get("company_id"):
        raise HTTPException(400, "OAuth state missing company_id")
    if not payload.get("nonce"):
        raise HTTPException(400, "OAuth state missing nonce")
    return payload


def authorize_url(state: str) -> str:
    """Build Marvin's consent URL for the round trip `state` describes.

    Region and nonce are read back out of the state rather than passed
    alongside it, so the URL and the state can never disagree about which
    deployment we're authorizing against or which PKCE verifier the callback
    will recompute.

    `resource` is RFC 8707 (Resource Indicators) and is required of MCP clients
    by the 2025-06-18 spec: it pins the issued token to one MCP server so a
    token minted for Marvin can't be replayed against another resource.
    """
    if not marvin_configured():
        raise HTTPException(500, "Marvin OAuth is not configured on the server")
    payload = verify_oauth_state(state)
    region, nonce = payload["region"], payload["nonce"]
    cfg = region_config(region)
    metadata = discover_metadata(cfg["issuer"])
    client_id, _ = ensure_client(region)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": settings.marvin_oauth_redirect_uri,
        "scope": MARVIN_SCOPES,
        "state": state,
        "resource": cfg["mcp_url"],
        "code_challenge": _code_challenge(code_verifier_for(nonce)),
        "code_challenge_method": "S256",
    }
    return f"{metadata['authorization_endpoint']}?{urlencode(params)}"


# ── Token exchange ──────────────────────────────────────────────────────────


def _token_request(region: str | None, form: dict[str, str]) -> dict[str, Any]:
    """POST to the region's token endpoint with client auth attached.

    Credentials go in the body (`client_secret_post`) because that is what
    Marvin advertises; a public client sends `client_id` alone and relies on
    PKCE. Returns the parsed token payload.
    """
    cfg = region_config(region)
    metadata = discover_metadata(cfg["issuer"])
    client_id, client_secret = ensure_client(region)
    body = dict(form)
    body["client_id"] = client_id
    if client_secret:
        body["client_secret"] = client_secret
    body["resource"] = cfg["mcp_url"]

    try:
        resp = requests.post(
            metadata["token_endpoint"],
            data=body,
            headers={"Accept": "application/json"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        raise HTTPException(502, "Could not reach Marvin's token endpoint") from e

    if resp.status_code in (400, 401, 403):
        logger.warning(
            "Marvin token request rejected (%s): %s %s",
            form.get("grant_type"), resp.status_code, resp.text[:300],
        )
        raise MarvinAuthExpiredError(
            "Marvin rejected the authorization — reconnect Marvin to continue"
        )
    if not resp.ok:
        logger.warning(
            "Marvin token request failed (%s): %s %s",
            form.get("grant_type"), resp.status_code, resp.text[:300],
        )
        raise HTTPException(502, "Marvin token request failed")
    return resp.json() or {}


def exchange_code_for_token(code: str, *, region: str | None, nonce: str) -> dict[str, Any]:
    """Trade an authorization code (plus its PKCE verifier) for tokens."""
    try:
        return _token_request(
            region,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.marvin_oauth_redirect_uri,
                "code_verifier": code_verifier_for(nonce),
            },
        )
    except MarvinAuthExpiredError as e:
        # At CONNECT time there is no stored credential to reconnect — this is
        # a plain bad/expired code, so it belongs as a 400 on the callback.
        raise HTTPException(400, "Marvin token exchange failed") from e


def refresh_access_token(refresh_token: str, *, region: str | None) -> dict[str, Any]:
    """Exchange a refresh token for a fresh token payload.

    Raises MarvinAuthExpiredError when Marvin rejects it (revoked, expired, or
    the workspace admin turned MCP off), which callers map to "reconnect".
    """
    return _token_request(
        region,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
    )


# ── Storage ─────────────────────────────────────────────────────────────────


def token_payload_to_store(
    token_json: dict[str, Any],
    *,
    region: str | None,
    keep_refresh_token: str | None = None,
) -> str:
    """The encrypted-storage payload for a Marvin connection.

    Beyond the raw token response this records:
      - `obtained_at` / `region`, so refresh knows when and where.
      - `keep_refresh_token`, carried forward when a refresh response omits one
        (some servers only rotate on issue) — same guard as the Asana connector.
      - `CREDENTIAL_KEY`, the packed (access_token, mcp_url) string the puller
        receives from `token_for()`. Built here, in the ONE place both connect
        and refresh flow through, so the two can never drift.
    """
    cfg = region_config(region)
    payload = dict(token_json)
    payload["obtained_at"] = int(time.time())
    payload["region"] = normalize_region(region)
    payload["mcp_url"] = cfg["mcp_url"]
    if not payload.get("refresh_token") and keep_refresh_token:
        payload["refresh_token"] = keep_refresh_token
    payload[CREDENTIAL_KEY] = json.dumps({
        "access_token": payload.get("access_token") or "",
        "mcp_url": cfg["mcp_url"],
    })
    return json.dumps(payload)


def parse_credential(credential: str) -> tuple[str, str]:
    """(access_token, mcp_url) out of the packed puller credential."""
    data = json.loads(credential)
    return data["access_token"], data["mcp_url"]


# ── Identity ────────────────────────────────────────────────────────────────


def fetch_server_identity(access_token: str, mcp_url: str) -> dict[str, Any]:
    """Validate a token by completing an MCP handshake. Returns `serverInfo`.

    MCP has no "who am I" call, so this doubles as the credential check: the
    handshake only succeeds with a token the resource server accepts. Returns
    {} on any failure so callers treat it as a rejection.

    A successful handshake with zero tools is still reported as a failure —
    that is what a workspace with MCP disabled by an admin looks like, and a
    connection that can read nothing is not a working connection.
    """
    from app.connectors.mcp_client import McpError, McpSession

    try:
        with McpSession(mcp_url, access_token) as session:
            info = dict(session.server_info)
            tools = session.list_tools()
    except McpError as e:
        logger.warning("Marvin MCP handshake failed at %s: %s", mcp_url, e)
        return {}

    if not tools:
        logger.warning("Marvin MCP at %s exposed no tools", mcp_url)
        return {}
    info["tool_count"] = len(tools)
    return info


def account_label(server_info: dict[str, Any], region: str | None) -> str:
    """The label shown as "Connected as …" in the connectors UI.

    Marvin's MCP surface exposes no user or workspace identity, so there is
    nothing truthful to put here beyond the server and the deployment. Saying
    "Marvin · EU" is honest; inventing an email would not be.
    """
    name = str((server_info or {}).get("name") or "Marvin").strip() or "Marvin"
    return f"{name} · {region_config(region)['label']}"
