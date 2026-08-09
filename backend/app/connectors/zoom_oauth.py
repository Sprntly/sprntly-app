"""Zoom cloud-recordings OAuth 2.0 helpers.

Zoom is a `meetings` connector (catalog.py), alongside Fireflies and Gong.
This module is the auth + read layer: OAuth connect / disconnect / probe, the
host picker, and the bounded HTTP helpers the KG puller
(`kg_ingest/pullers/zoom.py`) reads recordings and transcripts through.

Flow:
    1. Frontend hits POST /v1/connectors/zoom/start-oauth
    2. We build a state JWT + return Zoom's authorize URL
    3. Browser navigates to Zoom's consent screen
    4. Zoom redirects back to /v1/connectors/zoom/callback?code=…&state=…
    5. We exchange the code for {access_token, refresh_token, expires_in, …},
       label the connection from GET /users/me, and store an encrypted JSON
       blob under provider="zoom".

ORG-WIDE, NOT PERSONAL. Every scope below is an `:admin` scope, which is what
lets one connection read EVERY host's cloud recordings rather than only the
connecting user's. That is the whole point — a PM's account sees almost no
sales calls — but it also means the person who clicks Connect must be a Zoom
account owner/admin, and it is why this provider is deliberately absent from
routes/connectors._PERSONAL_PROVIDERS (contrast Slack, which is per-user).

It also constrains the Marketplace app itself: it must be General and
ADMIN-MANAGED. A user-managed app can only be granted user-level scopes, so the
`:admin` variants are not even offered on it — see backend/docs/CONNECTORS.md.
The consequence for this module is that user-addressing calls pass a REAL
userId rather than `me` (see probe_user_id).

Zoom specifics that differ from the Atlassian connectors this file mirrors:
    - The token endpoint authenticates the CLIENT with HTTP Basic
      (base64 "client_id:client_secret"), not with credentials in the body.
      Sending them in the body instead yields `{"reason":"Invalid client
      credentials","error":"invalid_client"}`, which reads like a wrong secret.
    - Access tokens last 1 hour; refresh tokens last 90 DAYS and ROTATE on
      every refresh. The newest refresh token must be persisted or the
      connection dies silently once the old one is spent — nothing fails at
      refresh time, the NEXT refresh fails.
      (https://developers.zoom.us/docs/integrations/oauth/)
    - There is no `offline_access`-style scope: the authorization-code flow
      always returns a refresh token.
    - Listing recordings is windowed: `from`/`to` span at most ONE MONTH per
      request, so a longer backfill is several calls (MAX_WINDOW_DAYS).
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import jwt
import requests
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

ZOOM_PROVIDER = "zoom"
ZOOM_AUTH_URL = "https://zoom.us/oauth/authorize"
ZOOM_TOKEN_URL = "https://zoom.us/oauth/token"
#: Best-effort teardown on disconnect. Zoom invalidates the whole grant (access
#: AND refresh token) — without it a disconnected customer's refresh token stays
#: live for the rest of its 90 days.
ZOOM_REVOKE_URL = "https://zoom.us/oauth/revoke"
ZOOM_API_BASE = "https://api.zoom.us/v2"

# Fixed scope set for the Sprntly Zoom connector.
#
# GRANULAR ONLY. Zoom's classic scope family (`recording:read:admin`,
# `user:read:admin`) is not selectable on an app created today — new Marketplace
# apps are granular-only — so asking for it fails at the consent screen with an
# invalid-scope error rather than at the API. Every scope here must ALSO be
# enabled on the app in the Marketplace console, and scopes are baked into the
# token at consent: changing this list means every existing connection must
# RECONNECT, not merely re-sync (the same trap the Confluence granular-scope
# incident hit — see confluence_oauth.CONFLUENCE_SCOPES).
#
#   cloud_recording:read:list_user_recordings:admin
#       GET /users/{userId}/recordings — the per-host recording listing, i.e.
#       the call this connector's whole value rests on.
#   cloud_recording:read:list_recording_files:admin
#       GET /meetings/{meetingId}/recordings — one meeting's files, including
#       the TRANSCRIPT (.VTT) the puller actually reads.
#   user:read:list_users:admin
#       GET /users — the host picker.
#   user:read:user:admin
#       GET /users/me — the connection's account_label.
#
# Source: https://developers.zoom.us/docs/integrations/oauth-scopes-granular/
ZOOM_SCOPES = (
    "cloud_recording:read:list_user_recordings:admin "
    "cloud_recording:read:list_recording_files:admin "
    "user:read:list_users:admin "
    "user:read:user:admin"
)

#: `connections.config` key holding the hosts whose recordings we sync. EMPTY
#: MEANS EVERY HOST — the same backwards-compatible default as Confluence's
#: space selection and Slack's channel selection, so a connection made before
#: the picker existed keeps working unchanged.
CONFIG_SYNC_USER_IDS = "sync_user_ids"
#: {user_id: email-or-name} for the ids above, stored alongside so a host who
#: has since been deactivated can be reported BY NAME rather than as an opaque
#: Zoom user id.
CONFIG_SYNC_USER_NAMES = "sync_user_names"
#: Incremental watermark: the `to` date of the last COMPLETED sync, as an ISO
#: date. Absent means "never synced", which is what triggers the 3-month
#: backfill. It exists because Zoom caps a recordings query at a ONE-MONTH
#: window, so without a cursor every run would re-walk the whole backfill —
#: three windows per host, forever, on a 6-hourly schedule.
CONFIG_LAST_SYNCED_UNTIL = "last_synced_until"
#: Sync counters, written after each completed run. `last_sync_meetings` is how
#: many recordings the run SAW; `last_sync_transcripts` how many of them had a
#: transcript we could read. The gap between the two is the signal the web layer
#: uses to say "recordings are syncing but audio transcription is off in Zoom" —
#: which is a settings problem in the customer's Zoom account, not a Sprntly
#: failure, and is invisible without both numbers.
CONFIG_LAST_SYNC_MEETINGS = "last_sync_meetings"
CONFIG_LAST_SYNC_TRANSCRIPTS = "last_sync_transcripts"

JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600
_TIMEOUT = 20
#: Transcripts are plain text over HTTP and can be long; a meeting-length VTT
#: takes noticeably more than an API round trip.
_DOWNLOAD_TIMEOUT = 60

#: Zoom refuses a `from`/`to` span longer than one month on the recordings
#: listing, so a longer backfill is several windowed calls. 30 days rather than
#: a calendar month keeps the arithmetic exact.
MAX_WINDOW_DAYS = 30
#: Zoom's documented page_size ceiling for the recordings + users listings.
MAX_PAGE_SIZE = 300

# 429 backoff. Zoom rate-limits per account and per endpoint category, and the
# recordings listing is "Medium" — a multi-host sweep is exactly the shape that
# trips it. Same bounded-attempts + honour-Retry-After shape as
# confluence_oauth.api_get.
_MAX_ATTEMPTS = 3
_MAX_BACKOFF_S = 30


def zoom_configured() -> bool:
    return bool(
        settings.zoom_client_id
        and settings.zoom_client_secret
        and settings.zoom_oauth_redirect_uri
    )


def authorize_url(state: str) -> str:
    """Build the URL the user gets redirected to for Zoom's consent screen.

    Zoom takes the scope set from the app's Marketplace configuration, but we
    send `scope` anyway: it keeps the granted set pinned to what this code
    reads, so enabling an extra scope on the console does not silently widen
    what a customer is asked to grant.
    """
    if not zoom_configured():
        raise HTTPException(500, "Zoom OAuth is not configured on the server")
    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": settings.zoom_client_id,
        "redirect_uri": settings.zoom_oauth_redirect_uri,
        "scope": ZOOM_SCOPES,
        "state": state,
    }
    return f"{ZOOM_AUTH_URL}?{urlencode(params)}"


def sign_oauth_state(*, company_id: str, return_to: str | None = None) -> str:
    """Mint a signed state JWT binding the OAuth round-trip to a company.

    The callback has no user session — Zoom calls it, not the browser's app tab
    — so this signature is the ENTIRE trust boundary deciding which company
    gets the new token.

    `return_to` is an optional relative path the callback redirects to instead
    of the default /settings?section=connectors."""
    now = int(time.time())
    payload = {
        "provider": ZOOM_PROVIDER,
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
    # Every connector's state is signed with the SAME jwt_secret, so a state
    # minted for one provider verifies fine against another's callback. The
    # provider claim is the only thing that stops a Jira (or Confluence) state
    # being replayed here to plant a token on a company that never connected
    # Zoom. Mirrors confluence_oauth.verify_oauth_state deliberately.
    if payload.get("provider") != ZOOM_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    if not payload.get("company_id"):
        raise HTTPException(400, "OAuth state missing company_id")
    return payload


class ZoomAuthExpiredError(RuntimeError):
    """The stored Zoom token was rejected and could not be refreshed (refresh
    token expired at 90 days, revoked, or the grant no longer covers what we
    ask for). The only remedy is the user reconnecting.

    Carries `status_code = 401` ON PURPOSE. kg_ingest.auto_sync decides between
    "reconnect required" and a genuine error by reading
    `getattr(exc, "status_code", None)`, and a bare requests.HTTPError keeps its
    status on `.response.status_code`, which that check does not look at. So
    without this attribute a dead token produces an ERROR traceback and a raw
    error string on the connection row instead of the reconnect prompt.
    """

    status_code = 401


class ZoomAppNotApprovedError(RuntimeError):
    """Zoom refused the authorization because the Sprntly app is not approved
    on the customer's account.

    Unpublished Marketplace apps must be pre-approved by a Zoom admin
    (Marketplace → Manage → Approved Apps). Zoom normally blocks this at its own
    consent screen and never redirects to us; this covers the case where it does
    come back through the callback, so the return page can say "ask your Zoom
    admin to approve Sprntly" instead of "something went wrong".
    """


class ZoomNotFoundError(RuntimeError):
    """A Zoom read answered 404 for a path the caller asserted must exist.

    Exists solely so the HEALTH PROBE can tell "this host recorded nothing this
    month" (healthy, and the normal state of a quiet account) apart from "the
    userId in this path does not resolve" (a broken probe). `api_get` swallows
    404 into `{}` by default, which is right for a sync racing a deleted
    recording and catastrophic for a probe: a wrong path would read as zero
    recordings, which reads as healthy, which is a green light on a connector
    that cannot see anything. Callers that cannot tolerate that pass
    `allow_missing=False`.
    """


def _client_auth() -> tuple[str, str]:
    """HTTP Basic credentials for the token endpoint.

    Zoom authenticates the CLIENT with a base64 `client_id:client_secret`
    Authorization header — NOT with credentials in the request body, the way
    Atlassian does it. Passing them in the body yields
    `{"reason":"Invalid client credentials","error":"invalid_client"}`, which
    reads like a wrong secret rather than a wrong auth style.
    """
    return (settings.zoom_client_id, settings.zoom_client_secret)


#: Phrases meaning "a Zoom admin has not approved this app on the account", as
#: opposed to a bad code, a bad secret, or a user who simply clicked Decline.
#: Matched on the prose because Zoom's OAuth errors are
#: `{"reason": "...", "error": "..."}` with the useful detail in `reason`, not
#: in a stable machine code.
#:
#: `unauthorized_client` is deliberately NOT here, though Zoom does emit it
#: alongside an approval refusal. On its own that code means the GRANT TYPE is
#: not enabled for this client — a Sprntly-side misconfiguration of our own app,
#: which telling the customer's admin to "approve Sprntly" would not fix and
#: would waste their time on. Only the approval prose decides.
_NOT_APPROVED_MARKERS = (
    "not approved",
    "not been approved",
    "app approval",
    "admin approval",
    "requires approval",
    "pre-approve",
)


def looks_like_app_not_approved(*texts: str | None) -> bool:
    """True when any of `texts` carries Zoom's app-not-approved prose.

    Shared by the token-exchange failure path and the callback's `error=`
    branch so both classify the same failure identically — the return page's
    copy map keys off one code, and two paths disagreeing about which code to
    send is how a customer gets told to chase the wrong person.
    """
    blob = " ".join(t for t in texts if t).lower()
    return any(marker in blob for marker in _NOT_APPROVED_MARKERS)


def _raise_for_approval(status_code: int, text: str) -> None:
    """Turn an approval-shaped token-endpoint rejection into
    ZoomAppNotApprovedError, so the callback can redirect with an error code the
    web layer maps to real copy."""
    if looks_like_app_not_approved(text):
        logger.warning("Zoom token endpoint reports app not approved: %s", status_code)
        raise ZoomAppNotApprovedError(
            "Sprntly is not approved on this Zoom account — ask a Zoom admin to "
            "approve it, then reconnect"
        )


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Trade an authorization code for tokens. Returns the parsed JSON
    {access_token, refresh_token, expires_in, scope, token_type}."""
    if not zoom_configured():
        raise HTTPException(500, "Zoom OAuth is not configured on the server")
    resp = requests.post(
        ZOOM_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.zoom_oauth_redirect_uri,
        },
        auth=_client_auth(),
        timeout=15,
    )
    if not resp.ok:
        # Never log resp.text wholesale on the success path shapes — a token
        # response body IS a credential. Here the request failed, so the body
        # carries an error envelope rather than tokens.
        logger.warning(
            "Zoom token exchange failed: %s %s", resp.status_code, resp.text[:300],
        )
        _raise_for_approval(resp.status_code, resp.text)
        raise HTTPException(400, "Zoom token exchange failed")
    return resp.json()


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """Exchange a refresh token for a fresh {access_token, refresh_token, …}.

    Zoom ROTATES refresh tokens — the response carries a new one and the old one
    is spent — so the caller must persist the whole payload (see
    token_payload_to_store). Raises ZoomAuthExpiredError if Zoom rejects the
    refresh token (90-day expiry, revoked grant, or a disconnected app)."""
    if not zoom_configured():
        raise HTTPException(500, "Zoom OAuth is not configured on the server")
    resp = requests.post(
        ZOOM_TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        auth=_client_auth(),
        timeout=15,
    )
    if resp.status_code in (400, 401, 403):
        logger.warning(
            "Zoom token refresh rejected: %s %s", resp.status_code, resp.text[:200],
        )
        raise ZoomAuthExpiredError(
            "Zoom rejected the refresh token — reconnect Zoom to continue"
        )
    if not resp.ok:
        logger.warning(
            "Zoom token refresh failed: %s %s", resp.status_code, resp.text[:300],
        )
        raise HTTPException(502, "Zoom token refresh failed")
    return resp.json()


def revoke_token(access_token: str) -> bool:
    """Best-effort: tell Zoom to invalidate this grant. Returns True on success.

    Called from the disconnect route BEFORE the row is deleted. Never raises:
    once the user has asked to disconnect, a Zoom-side failure must not stop us
    deleting our copy of their credential — leaving the row behind would be the
    worse outcome. Revoking still matters, because a refresh token we merely
    forget stays live on Zoom's side for the rest of its 90 days."""
    if not zoom_configured() or not access_token:
        return False
    try:
        resp = requests.post(
            ZOOM_REVOKE_URL,
            data={"token": access_token},
            auth=_client_auth(),
            timeout=_TIMEOUT,
        )
    except requests.RequestException:
        logger.warning("Zoom token revoke failed (network)", exc_info=True)
        return False
    if not resp.ok:
        logger.warning("Zoom token revoke failed: %s", resp.status_code)
        return False
    return True


def token_payload_to_store(
    token_json: dict[str, Any],
    *,
    company_id: str,
    keep_refresh_token: str | None = None,
) -> str:
    """Wrap Zoom's token response before encryption.

    Three things ride along, and each has a caller that breaks without it:

    `obtained_at` — the refresh schedulers (auto_sync._token_is_fresh, the
    connector probe, sync_context) compare `obtained_at + expires_in` against
    now to decide whether to refresh before use.

    `company_id` — the credential the kg_ingest puller will receive.
    `runner.token_for` hands a puller exactly ONE field of this payload, and a
    Zoom pull needs the connection's config (which hosts to sync), which a lone
    access token cannot reach. So the field it gets is the company id and the
    puller reads its own connection row — the trick `uploads` and `confluence`
    already use. THIS MUST SURVIVE EVERY REFRESH: drop it and `token_for` raises
    "connection for 'zoom' has no 'company_id'" on the next sync, not on the
    refresh that caused it.

    `keep_refresh_token` — Zoom rotates refresh tokens and normally returns the
    new one, but a response that omitted it must not blank out the stored one
    (mirrors asana_oauth's and confluence_oauth's merge).
    """
    payload = dict(token_json)
    payload["company_id"] = company_id
    if not payload.get("refresh_token") and keep_refresh_token:
        payload["refresh_token"] = keep_refresh_token
    payload["obtained_at"] = int(time.time())
    return json.dumps(payload)


# ── Read API: shared HTTP policy ─────────────────────────────────────────────
#
# There is no shared connector HTTP client in this codebase (every connector
# does bare `requests` with a module-local timeout), so rate-limit and auth
# policy is hand-rolled — but hand-rolled ONCE, here, because the host picker
# route, the health probe and the (later) KG puller all need identical
# behaviour.


#: Zoom's scope-mismatch signature, and the reason this check exists at all.
#:
#: Zoom does NOT answer a scope problem with 401 or 403. It answers
#: `HTTP 400 {"code":4711,"message":"Invalid access token, does not contain
#: scopes: cloud_recording:read:list_user_recordings:admin"}` — an ordinary bad
#: request as far as the status line is concerned. Without this mapping every
#: consequence keys off the wrong branch: the picker returns 502 instead of the
#: 400 reconnect prompt, `getattr(exc, "status_code")` in auto_sync sees 502 and
#: takes the "genuine error" path instead of "reconnect required", and worst,
#: the health probe raises an HTTPException that connector_health's fail-open
#: bare-except swallows — leaving a wholly broken connector showing GREEN. That
#: is the same class of silent-green failure as the Confluence identity-probe
#: incident (a1e16c40), arriving by a different door.
#:
#: 4700 (invalid redirect / app not authorized for this action) is included for
#: the same reason: it is an authorization fact wearing a 400.
_SCOPE_ERROR_CODES = (4711, 4700)
_SCOPE_ERROR_PHRASES = ("does not contain scopes", "invalid access token")


def _is_scope_error(resp) -> bool:
    """True when a Zoom 400 is really "your token lacks the scope"."""
    text = (getattr(resp, "text", "") or "").lower()
    if any(phrase in text for phrase in _SCOPE_ERROR_PHRASES):
        return True
    try:
        body = resp.json() or {}
    except Exception:  # noqa: BLE001 — a non-JSON 400 is just a 400
        return False
    if not isinstance(body, dict):
        return False
    try:
        return int(body.get("code")) in _SCOPE_ERROR_CODES
    except (TypeError, ValueError):
        return False


def api_get(
    access_token: str,
    url: str,
    params: dict[str, Any] | None = None,
    *,
    what: str = "read",
    allow_missing: bool = True,
) -> dict[str, Any]:
    """One authenticated GET against a Zoom REST URL, with the error contract
    the callers depend on:

      429   honour Retry-After and retry, up to _MAX_ATTEMPTS
      401   ZoomAuthExpiredError — carries status_code=401 so
            kg_ingest.auto_sync stamps "reconnect required" rather than logging
            an ERROR traceback
      403   ZoomAuthExpiredError too. On a plain read a 403 means the grant no
            longer covers what we ask for (a scope removed from the app, or the
            connecting user demoted out of admin), and reconnecting IS the
            remedy — unlike a tracker DELETE, where 403 is a per-object
            permission fact worth distinguishing.
      400   ZoomAuthExpiredError **when the body is a scope error** (code 4711 /
            4700, or "does not contain scopes"). Zoom signals a missing scope
            with a 400, not a 401 — see _SCOPE_ERROR_CODES. Every other 400 is a
            genuine bad request and stays a 502.
      404   returned as {} rather than raised, UNLESS `allow_missing=False`: a
            meeting's recordings can be deleted or trashed between listing and
            reading, and one missing container must not read as a broken
            credential. A caller that is asserting the path itself is valid (the
            health probe) passes False and gets ZoomNotFoundError, because for
            it "nothing there" and "wrong path" have opposite meanings.
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
                "Zoom %s rate-limited; retrying in %ss", what,
                min(wait, _MAX_BACKOFF_S),
            )
            time.sleep(min(max(wait, 0), _MAX_BACKOFF_S))
            continue
        if resp.status_code in (401, 403):
            logger.warning("Zoom %s auth rejected: %s", what, resp.status_code)
            raise ZoomAuthExpiredError(
                "Zoom rejected the stored token — reconnect Zoom to continue"
            )
        if resp.status_code == 400 and _is_scope_error(resp):
            logger.warning(
                "Zoom %s rejected for missing scopes (400): %s",
                what, resp.text[:200],
            )
            raise ZoomAuthExpiredError(
                "The Zoom connection is missing a required permission — "
                "reconnect Zoom to continue"
            )
        if resp.status_code == 404:
            if not allow_missing:
                logger.warning("Zoom %s: 404 on a path that must resolve", what)
                raise ZoomNotFoundError(f"Zoom {what}: not found (404)")
            logger.info("Zoom %s: not found (404)", what)
            return {}
        if not resp.ok:
            logger.warning(
                "Zoom %s failed: %s %s", what, resp.status_code, resp.text[:200]
            )
            raise HTTPException(502, f"Zoom {what} failed")
        return resp.json() or {}
    raise HTTPException(502, f"Zoom {what} rate-limited ({last_status})")


def fetch_current_user(access_token: str) -> dict[str, Any]:
    """Zoom's payload for the AUTHORIZING account — {id, email, first_name,
    last_name, account_id, …} — for `connections.account_label`.

    Returns {} on any non-2xx so callers can fall back to other label sources.
    Deliberately NOT the health probe: an identity call answers on a different
    scope from the recordings listing, so a connection can pass it while every
    sync 401s. That exact failure shipped on Confluence (a1e16c40); the probe
    lists recordings instead.

    This is the ONE place `me` is unavoidable — at connect time we do not yet
    know any user id, and learning it is the whole point of the call. Everything
    downstream uses the real id this returns (see identity_to_store /
    probe_user_id), because an admin-managed app is documented to address users
    explicitly.
    """
    resp = requests.get(
        f"{ZOOM_API_BASE}/users/me",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=_TIMEOUT,
    )
    if not resp.ok:
        logger.warning(
            "Zoom users/me failed: %s %s", resp.status_code, resp.text[:200],
        )
        return {}
    return resp.json() or {}


def account_label_from(user: dict[str, Any]) -> str | None:
    """Human label for the connection row: the authorizing admin's email, their
    name, or the Zoom account id. None when nothing usable is available — an
    unlabelled connection is still a working one (see fetch_current_user)."""
    name = " ".join(
        part for part in (user.get("first_name"), user.get("last_name")) if part
    ).strip()
    return (
        user.get("email")
        or name
        or user.get("display_name")
        or user.get("account_id")
        or None
    )


#: `connections.config` key holding the trimmed identity of whoever authorized.
CONFIG_USER = "user"


def identity_to_store(user: dict[str, Any]) -> dict[str, Any]:
    """The three fields of Zoom's `/users/me` payload worth keeping, and nothing
    else.

    `connections.config_json` is returned VERBATIM to every company member by
    `GET /v1/connectors` — it is not a private column. Zoom's user payload
    carries the admin's personal meeting id, personal meeting URL, phone number,
    department, job title and timezone; caching it whole would publish one
    person's contact details to everyone in the workspace as a side effect of
    connecting a recordings integration. Nobody asked for that and nothing reads
    it.

    `id` is the load-bearing one: it is the real userId the health probe (and
    the puller) address instead of `me`.
    """
    return {
        "id": user.get("id") or "",
        "email": user.get("email") or "",
        "account_id": user.get("account_id") or "",
    }


def probe_user_id(config: dict[str, Any] | None) -> str:
    """The Zoom userId to address for a single-host read, from a connection's
    config. Falls back to `"me"`.

    An admin-managed (account-level) app is documented to pass an explicit
    userId; `me` happens to work in many cases but is not guaranteed to resolve,
    and a path that does not resolve answers 404 — which, without care, reads as
    "this host recorded nothing", which reads as healthy. So: prefer the id
    cached at connect, keep `me` only so a connection made before this was
    stored still probes rather than erroring.
    """
    user = (config or {}).get(CONFIG_USER) or {}
    if isinstance(user, dict) and user.get("id"):
        return str(user["id"])
    return "me"


def _normalize_user(u: dict[str, Any]) -> dict[str, Any] | None:
    """One Zoom user row → the picker's shape, or None if unusable."""
    uid = u.get("id")
    if not uid:
        return None
    name = " ".join(
        part for part in (u.get("first_name"), u.get("last_name")) if part
    ).strip()
    return {
        "id": str(uid),
        "email": u.get("email") or "",
        "display_name": name or u.get("display_name") or u.get("email") or str(uid),
        # Zoom's `type`: 1 Basic, 2 Licensed, 3 On-prem. Only Licensed accounts
        # can record to the cloud at all, so this is what tells a picker which
        # hosts could ever have anything to sync — a Basic host with zero
        # recordings is not a bug to chase.
        "licensed": u.get("type") == 2,
    }


def list_users(
    access_token: str,
    *,
    page_size: int = 300,
    max_pages: int = 4,
    status: str = "active",
) -> tuple[list[dict[str, Any]], bool]:
    """Every user on the Zoom account, as
    `([{id, email, display_name, licensed}], fetch_capped)`.

    `status="active"` on purpose: deactivated users keep their recordings, but
    offering them in a picker invites a selection that can never grow. Bounded
    by `max_pages` the way every other connector bounds its listing — at 300 a
    page that is 1,200 hosts.

    The second element says whether the walk STOPPED AT THE CAP with Zoom still
    offering a next page. It is returned rather than inferred because the caller
    cannot tell the two cases apart from the list alone, and a picker that
    silently shows a prefix of a 5,000-host account while reporting a total of
    1,200 is stating a falsehood about the customer's own org.
    """
    out: list[dict[str, Any]] = []
    token: str | None = None
    capped = False
    for page in range(max_pages):
        params: dict[str, Any] = {
            "page_size": min(page_size, MAX_PAGE_SIZE),
            "status": status,
        }
        if token:
            params["next_page_token"] = token
        body = api_get(
            access_token, f"{ZOOM_API_BASE}/users", params, what="list_users",
        )
        users = body.get("users") or []
        for u in users:
            if not isinstance(u, dict):
                continue
            row = _normalize_user(u)
            if row:
                out.append(row)
        token = body.get("next_page_token") or None
        if not token or not users:
            break
        if page == max_pages - 1:
            # Zoom still has more and we are out of budget — say so.
            capped = True
    return out, capped


def window_bounds(
    frm: str | None = None, to: str | None = None
) -> tuple[str, str]:
    """Resolve a `from`/`to` pair for the recordings listing, clamped to Zoom's
    ONE-MONTH maximum span.

    Zoom rejects a longer window outright rather than truncating it, so a caller
    that asks for a quarter gets an error instead of a short answer. Defaulting
    and clamping here means every caller (probe, picker, puller) obeys the same
    rule, and a longer backfill is explicitly several windows rather than an
    accidental failure.
    """
    end = date.fromisoformat(to) if to else date.today()
    start = date.fromisoformat(frm) if frm else end - timedelta(days=MAX_WINDOW_DAYS)
    if start > end:
        start = end
    if (end - start).days > MAX_WINDOW_DAYS:
        start = end - timedelta(days=MAX_WINDOW_DAYS)
    return start.isoformat(), end.isoformat()


#: Windows one sync pass may walk. Three x MAX_WINDOW_DAYS IS the 3-month
#: backfill a fresh connection gets; it also bounds the catch-up after a long
#: outage, so a connector paused for a year resumes with the last quarter rather
#: than twelve windows per host.
DEFAULT_MAX_WINDOWS = 3


def sync_windows(
    cursor: str | None,
    *,
    today: date | None = None,
    max_windows: int = DEFAULT_MAX_WINDOWS,
) -> list[tuple[str, str]]:
    """The `(from, to)` date pairs a sync pass should walk, NEWEST FIRST.

    No cursor → the full backfill as `max_windows` ≤1-month windows. A cursor →
    the gap since the last completed sync, still chopped into ≤1-month pieces
    because Zoom SILENTLY CLAMPS anything wider (a naive "last 90 days" request
    returns a month and looks like a quiet quarter), and bounded by
    `max_windows`.

    Newest first matters whenever a caller's record cap trips: whatever gets
    dropped should be the oldest calls, not this week's.

    Lives here rather than in the puller because the KG puller and the call
    index both walk Zoom recordings on the same rules, and two copies of a
    windowing rule this easy to get subtly wrong would drift.
    """
    end = today or date.today()
    if cursor:
        try:
            start = date.fromisoformat(cursor)
        except (TypeError, ValueError):
            logger.info(
                "zoom: unreadable sync cursor %r — treating as a first sync", cursor
            )
            start = end - timedelta(days=MAX_WINDOW_DAYS * max_windows)
    else:
        start = end - timedelta(days=MAX_WINDOW_DAYS * max_windows)
    if start >= end:
        # Already synced through today. One short window still runs, because
        # recordings for calls that happened EARLIER TODAY land after the
        # morning's sync and would otherwise wait until tomorrow.
        return [window_bounds(end.isoformat(), end.isoformat())]

    out: list[tuple[str, str]] = []
    upper = end
    while upper > start and len(out) < max_windows:
        lower = max(start, upper - timedelta(days=MAX_WINDOW_DAYS))
        out.append(window_bounds(lower.isoformat(), upper.isoformat()))
        upper = lower
    if upper > start:
        logger.info(
            "zoom: %s of backlog is older than the %d-window budget and is not "
            "being walked this pass", (upper - start), max_windows,
        )
    return out


def list_user_recordings(
    access_token: str,
    user_id: str,
    *,
    frm: str | None = None,
    to: str | None = None,
    page_size: int = 300,
    max_pages: int = 5,
    allow_missing: bool = True,
) -> list[dict[str, Any]]:
    """A host's cloud recordings in one ≤1-month window, as Zoom's own meeting
    dicts ({uuid, id, topic, start_time, duration, recording_files, …}).

    `user_id` should be a REAL Zoom user id. `"me"` is accepted and works for a
    user-managed app, but an ADMIN-MANAGED (account-level) app — which is what
    this connector installs, because its scopes are all `:admin` — is documented
    to pass an explicit userId, and `me` is not guaranteed to resolve. A caller
    that cannot tolerate that ambiguity (the health probe) passes the id cached
    at connect and `allow_missing=False`, so an unresolvable path raises instead
    of quietly looking like an account with no recordings.

    Returned raw rather than normalized because the puller needs Zoom's native
    `recording_files` shape to find the transcript, and the health probe needs
    only the call to succeed.
    """
    start, end = window_bounds(frm, to)
    out: list[dict[str, Any]] = []
    token: str | None = None
    for _ in range(max_pages):
        params: dict[str, Any] = {
            "from": start,
            "to": end,
            "page_size": min(page_size, MAX_PAGE_SIZE),
        }
        if token:
            params["next_page_token"] = token
        body = api_get(
            access_token,
            f"{ZOOM_API_BASE}/users/{user_id}/recordings",
            params,
            what="list_recordings",
        )
        meetings = body.get("meetings") or []
        out.extend(m for m in meetings if isinstance(m, dict))
        token = body.get("next_page_token") or None
        if not token or not meetings:
            break
    return out


def encode_meeting_uuid(meeting_uuid: str) -> str:
    """URL-path form of a meeting UUID.

    Zoom's meeting UUIDs are base64 and can legitimately start with `/` or
    contain `//`. Those must be DOUBLE URL-encoded before going into the path or
    Zoom's router mis-splits the segment and answers 404 for a meeting that
    exists — the single most common cause of "the recording is right there and
    the API says it isn't". Any other UUID is passed through untouched, which is
    what Zoom's own note prescribes.
    """
    from urllib.parse import quote

    if meeting_uuid.startswith("/") or "//" in meeting_uuid:
        return quote(quote(meeting_uuid, safe=""), safe="")
    return quote(meeting_uuid, safe="")


def get_meeting_recordings(
    access_token: str, meeting_uuid: str
) -> dict[str, Any]:
    """One meeting's recording payload, including `recording_files`.

    Returns {} when the meeting's recordings are gone (404 — deleted or in the
    trash), which is a normal race during a sync, not a broken credential."""
    return api_get(
        access_token,
        f"{ZOOM_API_BASE}/meetings/{encode_meeting_uuid(meeting_uuid)}/recordings",
        what="get_recordings",
    )


def transcript_file_from(meeting: dict[str, Any]) -> dict[str, Any] | None:
    """The parseable transcript entry in a meeting's `recording_files`, or None.

    Selected on `file_extension == "VTT"` FIRST, not on `file_type`, because
    Zoom's own documentation contradicts itself about what a `TRANSCRIPT` file
    actually is — some pages say `.vtt`, others `.json`. Branching on the
    extension asks the payload what it is instead of trusting a doc page, and a
    `TRANSCRIPT` whose extension says something we cannot parse is SKIPPED
    rather than downloaded and fed to a WebVTT parser that will make nonsense of
    it. A `TRANSCRIPT` with no extension at all is still tried: that is the
    shape the docs describe as VTT.

    `CC` is deliberately ignored. It is the live closed-captions file and
    duplicates TRANSCRIPT's content when both exist, so preferring it (or
    falling back to it) buys a second copy of the same conversation at the cost
    of a second download and a second extraction.
    """
    vtt: dict[str, Any] | None = None
    transcript: dict[str, Any] | None = None
    for f in meeting.get("recording_files") or []:
        if not isinstance(f, dict):
            continue
        file_type = str(f.get("file_type") or "").strip().upper()
        extension = str(f.get("file_extension") or "").strip().upper()
        if file_type == "CC":
            continue
        if extension == "VTT":
            vtt = vtt or f
        elif file_type == "TRANSCRIPT" and not extension:
            transcript = transcript or f
    return vtt or transcript


def fetch_transcript_text(access_token: str, download_url: str) -> str:
    """Download a transcript file's raw text (WebVTT).

    The download host is not the API host and takes the SAME bearer token in an
    Authorization header — the `?access_token=` query form Zoom's older docs show
    puts a live credential in every proxy and access log in the path, so it is
    deliberately not used here.

    Returns "" on any failure: one unreadable transcript must degrade to a
    meeting with no text, not fail a whole sync. A 401/403 is the exception —
    that is the credential itself being rejected, so it raises like every other
    read.
    """
    if not download_url:
        return ""
    try:
        resp = requests.get(
            download_url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_DOWNLOAD_TIMEOUT,
        )
    except requests.RequestException:
        # Never log the URL: a Zoom download_url is itself a credential-bearing
        # link to customer conversation.
        logger.warning("Zoom transcript download failed (network)", exc_info=True)
        return ""
    if resp.status_code in (401, 403):
        raise ZoomAuthExpiredError(
            "Zoom rejected the stored token — reconnect Zoom to continue"
        )
    if not resp.ok:
        logger.warning("Zoom transcript download failed: %s", resp.status_code)
        return ""
    return resp.text or ""


# ── Sync context ─────────────────────────────────────────────────────────────


class ZoomNotConnectedError(RuntimeError):
    """No usable Zoom connection for this company."""


@dataclass(frozen=True)
class ZoomContext:
    """Everything one Zoom read pass needs, resolved from the stored connection
    in a single place.

    This exists because of a shape mismatch: `kg_ingest.runner.token_for` hands
    a puller exactly ONE field out of the decrypted token payload, but a Zoom
    pull needs the access token AND the picked hosts — the latter living in
    `connections.config`, which a lone token can't reach. So the puller's
    credential is the company id (the `uploads`/`confluence` trick) and this
    resolves the rest."""

    company_id: str
    access_token: str
    #: EMPTY means every host on the account (see CONFIG_SYNC_USER_IDS).
    user_ids: list[str]
    user_names: dict[str, str]
    #: ISO date of the last COMPLETED sync's upper bound, or None when this
    #: company has never synced. The puller's whole windowing strategy turns on
    #: it: None means backfill, a date means walk forward from there.
    last_synced_until: str | None = None


def sync_context(company_id: str) -> ZoomContext:
    """Resolve a live read context for `company_id`.

    Refreshes AND PERSISTS an expiring access token first. Persisting is not
    optional: Zoom rotates refresh tokens, so a throwaway refresh spends the
    stored one and the connection dies at the next cycle — with nothing failing
    at the moment the mistake is made. Because this re-reads the row every pass
    it also picks up a token another path (the probe, auto_sync's pre-sync
    refresh) just wrote, so the refresh here is a backstop, not a duplicate.

    Raises ZoomNotConnectedError when there is no row, or no readable token."""
    from app import db
    from app.connectors.tokens import decrypt_token_json, encrypt_token_json

    row = db.get_connection(company_id, ZOOM_PROVIDER)
    if not row:
        raise ZoomNotConnectedError(f"Zoom is not connected for company {company_id}")
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except Exception as e:  # noqa: BLE001 — unreadable token is a dead connection
        raise ZoomNotConnectedError("the stored Zoom token could not be read") from e

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
            company_id, ZOOM_PROVIDER, encrypt_token_json(new_payload)
        )
        token_json = json.loads(new_payload)

    access_token = token_json.get("access_token") or ""
    if not access_token:
        raise ZoomNotConnectedError("the Zoom connection has no access token")

    try:
        config = json.loads(row.get("config_json") or "{}")
    except (TypeError, ValueError):
        config = {}

    raw_ids = config.get(CONFIG_SYNC_USER_IDS) or []
    user_ids = [str(i) for i in raw_ids if i]
    raw_names = config.get(CONFIG_SYNC_USER_NAMES) or {}
    user_names = (
        {str(k): str(v) for k, v in raw_names.items()}
        if isinstance(raw_names, dict) else {}
    )

    cursor = config.get(CONFIG_LAST_SYNCED_UNTIL)

    return ZoomContext(
        company_id=company_id,
        access_token=access_token,
        user_ids=user_ids,
        user_names=user_names,
        last_synced_until=str(cursor) if cursor else None,
    )
