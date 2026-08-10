"""Google Meet transcripts — OAuth 2.0 + the Meet REST API v2 read layer.

Google Meet is a `meetings` connector (catalog.py), alongside Zoom, Fireflies
and Gong. This module is the auth + read layer: OAuth connect / disconnect /
probe, and the bounded HTTP helpers the KG puller
(`kg_ingest/pullers/google_meet.py`) reads conference records and transcripts
through.

Flow:
    1. Frontend hits POST /v1/connectors/google_meet/start-oauth
    2. We build a state JWT + return Google's consent URL
    3. Browser navigates to Google's consent screen
    4. Google redirects back to /v1/connectors/google-meet/callback?code=…&state=…
    5. We exchange the code for {access_token, refresh_token, expires_in, …},
       label the connection from the OIDC id_token, and store an encrypted JSON
       blob under provider="google_meet".

A SEPARATE PROVIDER FROM DRIVE, ON PURPOSE. It carries its OWN OAuth client
(`settings.google_meet_client_id`/`_secret`), its own redirect URI, its own
connection row, its own state signer and — the load-bearing part — its own
scope list. The credentials are separate rather than shared with Drive because
the two connectors need not live in the same Google account: an operator can
run Drive against one Workspace and Meet against another, and an OAuth client
belongs to exactly one project in one account. There is deliberately NO
fallback to Drive's client — see the comment on `google_meet_client_id` in
config.py. `google_oauth.DRIVE_SCOPES` must never grow a Meet scope:
scopes are baked into a token at consent and a refresh carries the OLD set
forward, so widening that constant would leave every already-stored Drive token
claiming a capability it does not have. Nothing fails at the moment of the
mistake; every Meet read on a pre-existing Drive connection just 403s, on a
connection whose probe says healthy.

THREE CONSTRAINTS SHAPE EVERYTHING BELOW, and they are Google's, not ours:

    ORGANIZER-ONLY COVERAGE. `conferenceRecords.list` returns only conferences
    where the AUTHENTICATING USER WAS THE ORGANIZER — not merely ones they
    attended, and never a colleague's. There is no admin/whole-account listing
    the way Zoom's `:admin` scopes give one connection every host's recordings.
    So a Meet connection covers one person's meetings and each teammate must
    connect their own account. The connect modal says this before the OAuth tab
    opens, because a customer who assumes Zoom-shaped coverage will read a
    correct, complete sync as a broken one.
    https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords/list

    THIRTY-DAY RETENTION, HARD. Conference records AND their transcript entries
    are deleted 30 days after the conference ends. There is no historical
    backfill and there never will be — a first sync reaches back 30 days and
    that is the whole corpus. Nothing here windows further back or pretends to.
    https://developers.google.com/workspace/meet/api/guides/artifacts

    NO DRIVE SCOPE, EVER. Recordings (the MP4) and Gemini "smart notes" live in
    the organizer's Drive and are reachable only through `drive.readonly` /
    `drive.meet.readonly`, which are RESTRICTED-tier scopes. Taking one would
    drag Sprntly's entire Google OAuth client — Drive connector included — into
    an annual paid CASA security assessment. That is a business decision, taken
    deliberately: Meet gives us the transcript TEXT directly from the API
    (`transcripts.entries`, structured speaker + text, no file parsing and no
    Drive access), which is the part worth having.

Google specifics that differ from the Zoom connector this file mirrors:
    - The token endpoint takes client credentials in the BODY (Zoom needs HTTP
      Basic). Standard Google web-server flow.
    - Refresh tokens do NOT rotate. Zoom's and Atlassian's do, and getting that
      wrong kills a connection silently one cycle later; here the stored refresh
      token stays valid until the user revokes it, it goes six months unused, or
      the 100-tokens-per-account-per-client cap evicts it. `keep_refresh_token`
      still exists because Google simply OMITS `refresh_token` from every
      refresh response, and a blind overwrite would blank the stored one.
    - `access_type=offline` + `prompt=consent` are both required to be handed a
      refresh token at all.
    - While the OAuth consent screen is in "Testing" publishing status a refresh
      token expires after SEVEN DAYS. That is a development-time fact, not a
      customer-facing one, but it is the likeliest cause of "it worked last
      week" before verification lands — see backend/docs/CONNECTORS.md.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import requests
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

GOOGLE_MEET_PROVIDER = "google_meet"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
#: Best-effort teardown on disconnect. Revoking ANY token of a grant
#: invalidates the whole grant, access and refresh together.
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
MEET_API_BASE = "https://meet.googleapis.com/v2"

#: The one scope that actually reads anything: conference records, participants,
#: transcripts and transcript entries, all read-only. SENSITIVE tier — it needs
#: Google's OAuth verification review before an unverified-app warning stops
#: blocking real customers, but NOT the restricted tier's security assessment.
MEET_READONLY_SCOPE = "https://www.googleapis.com/auth/meetings.space.readonly"

# The openid/userinfo trio is not decoration. Google AUTO-ADDS these three to the
# granted set whenever the OAuth client is also a sign-in client (ours is,
# because the Drive connector shares it), so a request for the Meet scope alone
# comes back as a SUPERSET of what we asked — which google-auth-oauthlib rejects
# outright with "Scope has changed" at token exchange. google_oauth.DRIVE_SCOPES
# carries the same trio for the same reason (see its comment at line 26). We
# also get the connecting user's verified email straight out of the ID token,
# which saves an identity round trip at connect.
#
# `meetings.space.created` is deliberately ABSENT. It is the scope for spaces
# this app itself created via the API; Sprntly creates no meetings, and asking
# for it would put a write-shaped permission on a read-only connector's consent
# screen. Every scope Sprntly needs to read a customer's own meetings is covered
# by meetings.space.readonly.
MEET_SCOPES = [
    MEET_READONLY_SCOPE,
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
#: Space-joined, the form the authorize URL and `connections.scopes` both take.
MEET_SCOPE_STRING = " ".join(MEET_SCOPES)

#: `connections.config` key holding the trimmed identity of whoever authorized.
CONFIG_USER = "user"
#: Sync counters, written after each completed run. `last_sync_meetings` is how
#: many conferences the run SAW; `last_sync_transcripts` how many of them had a
#: transcript we could read. The gap between the two is the signal the web layer
#: needs to say "meetings are syncing but transcription was never switched on in
#: Google Meet" — a setting in the customer's own Workspace, invisible without
#: both numbers. Same pair, and the same reasoning, as the Zoom connector.
CONFIG_LAST_SYNC_MEETINGS = "last_sync_meetings"
CONFIG_LAST_SYNC_TRANSCRIPTS = "last_sync_transcripts"

JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600
_TIMEOUT = 20

#: THE retention ceiling, and the reason there is no cursor in this connector.
#: Google deletes conference records and transcript entries 30 days after the
#: conference ends, so "everything that exists" and "the last 30 days" are the
#: same set. An incremental watermark would buy nothing (a full window is
#: already the whole corpus) and would cost correctness the first time a sync
#: was paused for a month.
RETENTION_DAYS = 30

#: Google's documented page-size ceilings. Asking for more is silently coerced
#: down, which is how a "we fetched everything" assumption becomes a short
#: answer nobody notices.
MAX_CONFERENCE_PAGE_SIZE = 100   # conferenceRecords.list: default 25, max 100
MAX_ENTRY_PAGE_SIZE = 100        # transcripts.entries.list: default 10, max 100
MAX_PARTICIPANT_PAGE_SIZE = 250  # participants.list: default 100, max 250

# 429 backoff. The Meet API allows 6,000 read requests/minute per project and
# 600/minute per user; a first sync walking 30 days of conferences at three
# requests each is the shape that approaches the per-user figure. Same
# bounded-attempts + honour-Retry-After shape as zoom_oauth.api_get.
_MAX_ATTEMPTS = 3
_MAX_BACKOFF_S = 30


# ── Optional read deadline (for callers that must be abandonable) ────────────
#
# Nothing here changes for the puller or the probe: the default is None and the
# behaviour with it is byte-for-byte what it was. This exists for ONE caller —
# `connector_lookup/google_meet.py`'s keyword scan, which runs inside the chat
# sweep's shared wall-clock budget and is abandoned, not waited on, when that
# budget expires.
#
# WHY A CAP ON THE REQUEST ALONE WOULD NOT DO IT. `api_get` retries a 429 (and
# a rate-limit-flavoured 403) up to `_MAX_ATTEMPTS`, honouring `Retry-After` up
# to `_MAX_BACKOFF_S`. So one call can legitimately occupy 3 × `_TIMEOUT` of
# HTTP plus 2 × 30s of SLEEP — around two minutes — and an abandoned sweep leg
# would hold a worker thread for all of it, hammering a customer's quota with
# nobody left to read the answer. A scan that checks the clock BETWEEN requests
# cannot see inside that.
#
# A ContextVar rather than a parameter because the deadline has to reach
# `api_get` through four `list_*` helpers and two puller functions that the
# scan reuses deliberately (so live chat and the 6-hourly sync never disagree
# about which transcript is "the" transcript). Threading a keyword through all
# six would change every one of their signatures for one caller. A ContextVar
# is per-thread — the sweep's leg runs on its own worker thread — so one
# company's bounded scan cannot shorten another caller's reads.
_read_deadline: ContextVar[float | None] = ContextVar(
    "google_meet_read_deadline", default=None
)


class MeetDeadlineExceeded(RuntimeError):
    """A bounded read gave up because its caller's deadline passed. Distinct
    from an auth or transport failure: nothing is wrong with the connector, we
    simply stopped. Callers report it as "not covered", never as "not there"."""


@contextmanager
def read_deadline(deadline: float | None):
    """Bound every Meet read on THIS thread to `deadline` (a
    `time.monotonic()` value). `None` restores the unbounded default."""
    token = _read_deadline.set(deadline)
    try:
        yield
    finally:
        _read_deadline.reset(token)


def _remaining_or_raise(what: str) -> float:
    """Seconds of budget left for one request, or `_TIMEOUT` when unbounded."""
    deadline = _read_deadline.get()
    if deadline is None:
        return float(_TIMEOUT)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise MeetDeadlineExceeded(f"Google Meet {what}: read deadline passed")
    return min(float(_TIMEOUT), remaining)


def google_meet_configured() -> bool:
    """True when the shared Google OAuth client AND Meet's own redirect URI are
    set. The redirect URI is separate per connector even though the client is
    shared — that is what keeps the two connection rows distinct."""
    return bool(
        settings.google_meet_client_id
        and settings.google_meet_client_secret
        and settings.google_meet_oauth_redirect_uri
    )


def authorize_url(state: str) -> str:
    """Build the URL the user gets redirected to for Google's consent screen.

    `access_type=offline` + `prompt=consent` are both load-bearing: without
    offline access Google issues no refresh token at all, and without the forced
    consent prompt it silently omits one on any RE-authorization — so a
    reconnect would produce a connection that works for exactly one hour and
    then dies with no refresh token to recover with.

    `include_granted_scopes=true` matches the Drive connector, so a user who has
    already granted Drive keeps that grant rather than having it replaced.
    """
    if not google_meet_configured():
        raise HTTPException(500, "Google Meet OAuth is not configured on the server")
    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": settings.google_meet_client_id,
        "redirect_uri": settings.google_meet_oauth_redirect_uri,
        "scope": MEET_SCOPE_STRING,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def sign_oauth_state(*, company_id: str, return_to: str | None = None) -> str:
    """Mint a signed state JWT binding the OAuth round-trip to a company.

    The callback has no user session — Google calls it, not the browser's app
    tab — so this signature is the ENTIRE trust boundary deciding which company
    gets the new token.

    `return_to` is an optional relative path the callback redirects to instead
    of the default /settings?section=connectors."""
    now = int(time.time())
    payload = {
        "provider": GOOGLE_MEET_PROVIDER,
        "company_id": company_id,
        "return_to": return_to,
        "nonce": uuid.uuid4().hex,
        "iat": now,
        "exp": now + STATE_TTL_SECONDS,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALG)


def verify_oauth_state(state: str) -> dict:
    """Verify a state JWT minted by `sign_oauth_state` — and only by it.

    Meet needs its own verifier rather than reusing `google_oauth`'s: that one
    hard-rejects any provider claim that is not `google_drive`, so a Meet state
    handed to it 400s. The claim check cuts both ways and that is the point —
    every connector signs with the SAME jwt_secret, so a Drive (or Zoom, or
    Jira) state verifies cryptographically here, and the provider claim is the
    only thing stopping one being replayed at this callback to plant a Meet
    token on a company that never connected Meet.
    """
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[JWT_ALG])
    except jwt.PyJWTError as e:
        raise HTTPException(400, "Invalid or expired OAuth state") from e
    if payload.get("provider") != GOOGLE_MEET_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    if not payload.get("company_id"):
        raise HTTPException(400, "OAuth state missing company_id")
    return payload


class MeetAuthExpiredError(RuntimeError):
    """The stored Google token was rejected and could not be refreshed (the user
    revoked access, the grant went six months unused, the 100-token-per-account
    cap evicted it, or — during development — the 7-day "Testing" expiry hit).
    The only remedy is the user reconnecting.

    Carries `status_code = 401` ON PURPOSE. kg_ingest.auto_sync decides between
    "reconnect required" and a genuine error by reading
    `getattr(exc, "status_code", None)`, and a bare requests.HTTPError keeps its
    status on `.response.status_code`, which that check does not look at. So
    without this attribute a dead token produces an ERROR traceback and a raw
    error string on the connection row instead of the reconnect prompt.
    """

    status_code = 401


class MeetNotConnectedError(RuntimeError):
    """No usable Google Meet connection for this company."""


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Trade an authorization code for tokens. Returns the parsed JSON
    {access_token, refresh_token, expires_in, scope, token_type, id_token}.

    Client credentials go in the BODY — Google's documented web-server flow.
    (Zoom is the odd one out in this codebase, needing HTTP Basic.)
    """
    if not google_meet_configured():
        raise HTTPException(500, "Google Meet OAuth is not configured on the server")
    resp = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.google_meet_client_id,
            "client_secret": settings.google_meet_client_secret,
            "redirect_uri": settings.google_meet_oauth_redirect_uri,
        },
        timeout=15,
    )
    if not resp.ok:
        # Safe to log here precisely because the request FAILED — the body is an
        # error envelope, not tokens. A successful token response IS a
        # credential and is never logged.
        logger.warning(
            "Google Meet token exchange failed: %s %s",
            resp.status_code, resp.text[:300],
        )
        raise HTTPException(400, "Google Meet token exchange failed")
    return resp.json()


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """Exchange a refresh token for a fresh {access_token, expires_in, …}.

    Google does NOT rotate refresh tokens, so unlike Zoom and Atlassian there is
    no "persist the new one or the connection dies" hazard here — but the
    response OMITS `refresh_token` entirely, so a caller that stores the
    response verbatim blanks the stored one and reaches the same dead end by a
    different road. `token_payload_to_store(keep_refresh_token=…)` is what
    prevents that; it is not optional on this path.

    Raises MeetAuthExpiredError when Google rejects the refresh token
    (`invalid_grant` — revoked, six months unused, evicted by the 100-token cap,
    or the 7-day expiry that applies while the consent screen is in Testing).
    """
    if not google_meet_configured():
        raise HTTPException(500, "Google Meet OAuth is not configured on the server")
    resp = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.google_meet_client_id,
            "client_secret": settings.google_meet_client_secret,
        },
        timeout=15,
    )
    if resp.status_code in (400, 401, 403):
        # Google answers a dead refresh token with 400 invalid_grant, not 401.
        # Left as a generic status check rather than parsing `error` because
        # every one of these three means the same thing for this credential.
        logger.warning(
            "Google Meet token refresh rejected: %s %s",
            resp.status_code, resp.text[:200],
        )
        raise MeetAuthExpiredError(
            "Google rejected the refresh token — reconnect Google Meet to continue"
        )
    if not resp.ok:
        logger.warning(
            "Google Meet token refresh failed: %s %s",
            resp.status_code, resp.text[:300],
        )
        raise HTTPException(502, "Google Meet token refresh failed")
    return resp.json()


def revoke_token(token: str) -> bool:
    """Best-effort: tell Google to invalidate this grant. Returns True on success.

    Called from the disconnect route BEFORE the row is deleted. Never raises:
    once the user has asked to disconnect, a Google-side failure must not stop
    us deleting our copy of their credential — leaving the row behind would be
    the worse outcome. Revoking still matters, because a refresh token we merely
    forget stays live on Google's side indefinitely (they do not expire on a
    clock the way Zoom's 90-day ones do).
    """
    if not token:
        return False
    try:
        resp = requests.post(
            GOOGLE_REVOKE_URL,
            params={"token": token},
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException:
        logger.warning("Google Meet token revoke failed (network)", exc_info=True)
        return False
    if not resp.ok:
        logger.warning("Google Meet token revoke failed: %s", resp.status_code)
        return False
    return True


def token_payload_to_store(
    token_json: dict[str, Any],
    *,
    company_id: str,
    keep_refresh_token: str | None = None,
) -> str:
    """Wrap Google's token response before encryption.

    Three things ride along, and each has a caller that breaks without it:

    `obtained_at` — the refresh schedulers (auto_sync._token_is_fresh, the
    connector probe, sync_context) compare `obtained_at + expires_in` against
    now to decide whether to refresh before use.

    `company_id` — the credential the kg_ingest puller will receive.
    `runner.token_for` hands a puller exactly ONE field of this payload, and a
    Meet pull needs the connection's config, which a lone access token cannot
    reach. So the field it gets is the company id and the puller reads its own
    connection row — the trick `uploads`, `confluence` and `zoom` already use.
    THIS MUST SURVIVE EVERY REFRESH: drop it and `token_for` raises "connection
    for 'google_meet' has no 'company_id'" on the next sync, not on the refresh
    that caused it.

    `keep_refresh_token` — Google's refresh RESPONSE carries no `refresh_token`
    field at all (the stored one stays valid; it does not rotate). Storing the
    response verbatim would therefore blank it, and the connection would die at
    the following cycle with nothing failing at the moment of the mistake.

    `id_token` is deliberately dropped rather than carried forward. It is a
    short-lived OIDC assertion we read exactly once, at connect, for the email —
    keeping a stale copy in the credential blob buys nothing and leaves a signed
    identity assertion sitting in storage for the life of the connection.
    """
    payload = dict(token_json)
    payload.pop("id_token", None)
    payload["company_id"] = company_id
    if not payload.get("refresh_token") and keep_refresh_token:
        payload["refresh_token"] = keep_refresh_token
    payload["obtained_at"] = int(time.time())
    return json.dumps(payload)


# ── Identity ─────────────────────────────────────────────────────────────────


def email_from_id_token(token_json: dict[str, Any]) -> str | None:
    """The verified email out of the OIDC ID token Google returns alongside the
    access token (we request openid + userinfo.email, so it is there).

    The ID token is signed by Google and arrived over TLS from Google's own
    token endpoint in direct response to our request, so the email claim is
    decoded WITHOUT re-verifying the signature — the same call
    `google_oauth.email_from_id_token` makes, and for the same reason: there is
    no untrusted hop for a forged token to enter through, and verifying would
    cost a JWKS fetch per connect. Returns None when there is no ID token or no
    email claim.
    """
    raw = token_json.get("id_token")
    if not raw:
        return None
    try:
        claims = jwt.decode(raw, options={"verify_signature": False})
    except jwt.PyJWTError:
        return None
    return claims.get("email") or None


def fetch_current_user(access_token: str) -> dict[str, Any]:
    """Google's OpenID userinfo payload — {sub, email, name, picture, …}.

    Returns {} on any non-2xx so callers can fall back to other label sources.
    Deliberately NOT the health probe: userinfo answers on the `userinfo.email`
    scope while every meeting read answers on `meetings.space.readonly`, so a
    connection can pass it while every sync 403s. That exact failure shipped on
    Confluence (a1e16c40, defect #2) where a green identity probe concealed a
    wholly broken connector; the probe here lists conference records instead.
    """
    try:
        resp = requests.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException:
        logger.warning("Google Meet userinfo failed (network)", exc_info=True)
        return {}
    if not resp.ok:
        logger.warning("Google Meet userinfo failed: %s", resp.status_code)
        return {}
    return resp.json() or {}


def identity_to_store(user: dict[str, Any], *, email: str | None = None) -> dict[str, Any]:
    """The two fields of Google's identity payload worth keeping, and nothing
    else.

    `connections.config_json` is returned VERBATIM to every company member by
    `GET /v1/connectors` — it is not a private column. Google's userinfo payload
    carries the connecting person's full name, profile-picture URL, locale and
    hosted domain; caching it whole would publish one employee's profile to
    everyone in the workspace as a side effect of connecting a transcripts
    integration. Nobody asked for that and nothing reads it.

    `id` is Google's stable `sub`, kept because an email can change and the
    subject id cannot — it is how a later feature would tell "the same person
    reconnected" from "somebody else connected".
    """
    return {
        "id": str(user.get("sub") or user.get("id") or ""),
        "email": str(email or user.get("email") or ""),
    }


def account_label_from(user: dict[str, Any], *, email: str | None = None) -> str | None:
    """Human label for the connection row: the connecting user's email, else
    their name. None when nothing usable is available — an unlabelled connection
    is still a working one.

    Email first, and not merely as a formatting preference: coverage here is
    ORGANIZER-ONLY, so the label is the single thing on the connectors screen
    that says WHOSE meetings this connection can see. A display name is
    ambiguous in a company with two people called Sam.
    """
    return email or user.get("email") or user.get("name") or None


# ── Read API: shared HTTP policy ─────────────────────────────────────────────
#
# There is no shared connector HTTP client in this codebase (every connector
# does bare `requests` with a module-local timeout), so rate-limit and auth
# policy is hand-rolled — but hand-rolled ONCE, here, because the health probe
# and the KG puller both need identical behaviour.

#: Google's classic-error reasons for "you are going too fast", which arrive
#: wearing a 403 rather than a 429. Mapping one of these to MeetAuthExpiredError
#: would tell a customer to reconnect a connection that is working perfectly and
#: merely busy — the same class of wrong-branch mistake as Zoom answering a
#: missing scope with a 400. Newer Google APIs prefer 429/RESOURCE_EXHAUSTED,
#: which the status check below already covers; this is the belt to that braces.
_RATE_LIMIT_REASONS = ("ratelimitexceeded", "userratelimitexceeded", "quotaexceeded")


def _is_rate_limited(resp) -> bool:
    """True when a 403 is really "slow down" rather than "you may not"."""
    text = (getattr(resp, "text", "") or "").lower()
    return any(reason in text.replace(" ", "") for reason in _RATE_LIMIT_REASONS)


def api_get(
    access_token: str,
    url: str,
    params: dict[str, Any] | None = None,
    *,
    what: str = "read",
) -> dict[str, Any]:
    """One authenticated GET against a Meet REST URL, with the error contract
    the callers depend on:

      429   honour Retry-After and retry, up to _MAX_ATTEMPTS
      403   rate-limit reason → same retry path as 429 (see _RATE_LIMIT_REASONS);
            anything else → MeetAuthExpiredError, because on a plain read a 403
            means the grant no longer covers what we ask for (access revoked, or
            the Workspace admin turned the Meet API off for the org) and
            reconnecting IS the remedy
      401   MeetAuthExpiredError — carries status_code=401 so
            kg_ingest.auto_sync stamps "reconnect required" rather than logging
            an ERROR traceback
      404   returned as {} rather than raised: a conference record can pass its
            30-day expiry between the listing and the transcript read, and one
            vanished container must not read as a broken credential

    There is no `allow_missing=False` escape hatch (Zoom has one) because
    nothing here addresses a caller-asserted path: the probe lists conference
    records at the collection root, which cannot 404 for a valid credential.
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
            # `_TIMEOUT` unless a caller has bound this thread to a deadline —
            # see `read_deadline`. Raises MeetDeadlineExceeded rather than
            # issuing a request there is no time left to receive.
            timeout=_remaining_or_raise(what),
        )
        last_status = resp.status_code
        if resp.status_code == 429 or (
            resp.status_code == 403 and _is_rate_limited(resp)
        ):
            if attempt == _MAX_ATTEMPTS - 1:
                break
            try:
                wait = int(resp.headers.get("Retry-After") or 2)
            except (TypeError, ValueError):
                wait = 2
            wait = min(max(wait, 0), _MAX_BACKOFF_S)
            # The sleep is the part a between-requests clock check cannot see.
            # A bounded caller must not spend its remaining budget waiting —
            # and must not sleep PAST the deadline and then issue a request
            # nobody is waiting for.
            deadline = _read_deadline.get()
            if deadline is not None and time.monotonic() + wait >= deadline:
                raise MeetDeadlineExceeded(
                    f"Google Meet {what}: rate-limited, and the backoff would "
                    "outlast this read's deadline"
                )
            logger.info(
                "Google Meet %s rate-limited; retrying in %ss", what, wait,
            )
            time.sleep(wait)
            continue
        if resp.status_code in (401, 403):
            logger.warning("Google Meet %s auth rejected: %s", what, resp.status_code)
            raise MeetAuthExpiredError(
                "Google rejected the stored token — reconnect Google Meet to continue"
            )
        if resp.status_code == 404:
            logger.info("Google Meet %s: not found (404)", what)
            return {}
        if not resp.ok:
            logger.warning(
                "Google Meet %s failed: %s %s",
                what, resp.status_code, resp.text[:200],
            )
            raise HTTPException(502, f"Google Meet {what} failed")
        return resp.json() or {}
    raise HTTPException(502, f"Google Meet {what} rate-limited ({last_status})")


def retention_window_start(now: datetime | None = None) -> str:
    """The oldest timestamp worth asking for, RFC 3339 with milliseconds.

    Pinned to RETENTION_DAYS because that is genuinely all that exists — asking
    for more returns the same rows and asking for less loses data the customer
    still has. Google's filter grammar wants the `.000Z` millisecond form; a
    bare-seconds timestamp is rejected as a malformed filter.
    """
    moment = (now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_DAYS)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def list_conference_records(
    access_token: str,
    *,
    page_size: int = MAX_CONFERENCE_PAGE_SIZE,
    max_pages: int = 3,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """The conferences this connection can see, as Google's own dicts
    ({name, startTime, endTime, expireTime, space}). Newest first — Google
    orders by start time descending, which is what makes a record cap drop the
    oldest calls rather than this week's.

    ORGANIZER-ONLY. This endpoint returns conferences the authenticating user
    ORGANIZED, not ones they merely attended. Nothing in this signature can
    widen that, and no scope exists that would.

    `since` defaults to the 30-day retention floor. It is passed as a filter
    rather than relied on implicitly because a filterless listing walks whatever
    Google still holds, and being explicit about the window is what lets a caller
    reason about how many pages it is buying.
    """
    start = since or retention_window_start()
    out: list[dict[str, Any]] = []
    token: str | None = None
    for _ in range(max_pages):
        params: dict[str, Any] = {
            "pageSize": min(page_size, MAX_CONFERENCE_PAGE_SIZE),
            "filter": f'start_time>="{start}"',
        }
        if token:
            params["pageToken"] = token
        body = api_get(
            access_token,
            f"{MEET_API_BASE}/conferenceRecords",
            params,
            what="list_conferences",
        )
        records = body.get("conferenceRecords") or []
        out.extend(r for r in records if isinstance(r, dict))
        token = body.get("nextPageToken") or None
        if not token or not records:
            break
    return out


def list_transcripts(
    access_token: str, conference_name: str, *, page_size: int = 10,
) -> list[dict[str, Any]]:
    """A conference's transcripts ({name, state, startTime, endTime,
    docsDestination}).

    Usually zero or one: a transcript exists only when somebody switched on
    "Record the transcript" for that meeting, and Google will not transcribe
    retroactively. A meeting can have more than one when transcription was
    stopped and restarted mid-call, which is why this is a list and the puller
    joins them all rather than taking `[0]`.

    Unpaged on purpose — the ceiling is the number of times one meeting's
    transcription was toggled, and a second page of that does not happen.
    """
    body = api_get(
        access_token,
        f"{MEET_API_BASE}/{conference_name}/transcripts",
        {"pageSize": page_size},
        what="list_transcripts",
    )
    return [t for t in (body.get("transcripts") or []) if isinstance(t, dict)]


def list_transcript_entries(
    access_token: str,
    transcript_name: str,
    *,
    page_size: int = MAX_ENTRY_PAGE_SIZE,
    max_pages: int = 10,
) -> list[dict[str, Any]]:
    """A transcript's entries ({name, participant, text, languageCode,
    startTime, endTime}), in order.

    THIS IS THE PAYLOAD — structured speaker-plus-text straight from the Meet
    API. No Drive access, no file download, no format parsing (contrast Zoom,
    where the same information means fetching and parsing a WebVTT file).

    PAGING IS MANDATORY, not an optimisation. `pageSize` defaults to TEN and
    caps at 100, and an entry is a single utterance — so an hour-long meeting is
    hundreds of entries and a single unpaged call returns the first ten seconds
    of the conversation while looking exactly like a complete short meeting.
    That is the failure mode this function exists to make impossible.

    `max_pages` bounds it at 1,000 entries, which is a long meeting; the puller
    truncates the joined text well before that anyway.
    """
    out: list[dict[str, Any]] = []
    token: str | None = None
    for _ in range(max_pages):
        params: dict[str, Any] = {"pageSize": min(page_size, MAX_ENTRY_PAGE_SIZE)}
        if token:
            params["pageToken"] = token
        body = api_get(
            access_token,
            f"{MEET_API_BASE}/{transcript_name}/entries",
            params,
            what="list_transcript_entries",
        )
        entries = body.get("transcriptEntries") or []
        out.extend(e for e in entries if isinstance(e, dict))
        token = body.get("nextPageToken") or None
        if not token or not entries:
            break
    return out


def list_participants(
    access_token: str,
    conference_name: str,
    *,
    page_size: int = MAX_PARTICIPANT_PAGE_SIZE,
    max_pages: int = 2,
) -> list[dict[str, Any]]:
    """A conference's participants ({name, signedinUser|anonymousUser|phoneUser,
    earliestStartTime, latestEndTime}).

    Not optional garnish: a transcript entry references its speaker by
    PARTICIPANT RESOURCE NAME, not by display name, so this listing is the join
    table that turns "conferenceRecords/x/participants/y said …" into "Sam Lee
    said …". Without it every transcript is unattributed.
    """
    out: list[dict[str, Any]] = []
    token: str | None = None
    for _ in range(max_pages):
        params: dict[str, Any] = {
            "pageSize": min(page_size, MAX_PARTICIPANT_PAGE_SIZE),
        }
        if token:
            params["pageToken"] = token
        body = api_get(
            access_token,
            f"{MEET_API_BASE}/{conference_name}/participants",
            params,
            what="list_participants",
        )
        participants = body.get("participants") or []
        out.extend(p for p in participants if isinstance(p, dict))
        token = body.get("nextPageToken") or None
        if not token or not participants:
            break
    return out


def participant_display_name(participant: dict[str, Any]) -> str:
    """One participant → the name to attribute their words to.

    Google models three mutually-exclusive kinds and each carries its own
    `displayName`: a `signedinUser` (a Workspace account), an `anonymousUser`
    (someone who joined without signing in) and a `phoneUser` (dial-in). All
    three are handled because a customer call routinely has all three in it, and
    a missing branch would silently un-attribute exactly the external guest
    whose words are the point of the record.
    """
    for key in ("signedinUser", "anonymousUser", "phoneUser"):
        who = participant.get(key)
        if isinstance(who, dict) and who.get("displayName"):
            return str(who["displayName"])
    return ""


# ── Sync context ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MeetContext:
    """Everything one Meet read pass needs, resolved from the stored connection
    in a single place.

    This exists because of a shape mismatch: `kg_ingest.runner.token_for` hands
    a puller exactly ONE field out of the decrypted token payload, but a Meet
    pull wants the connection's config as well as the token. So the puller's
    credential is the company id (the `uploads`/`confluence`/`zoom` trick) and
    this resolves the rest.

    `account_email` is carried because coverage is ORGANIZER-ONLY: every record
    this context produces is a meeting THIS account organized, and saying so on
    the record is the difference between an honest partial corpus and a corpus
    that silently implies it is the whole company's.
    """

    company_id: str
    access_token: str
    account_email: str = ""


def sync_context(company_id: str) -> MeetContext:
    """Resolve a live read context for `company_id`.

    Refreshes AND PERSISTS an expiring access token first. Persisting matters
    less here than on Zoom (Google does not rotate refresh tokens, so a
    throwaway refresh strands nothing) but it still saves every later caller in
    the same hour a redundant round trip — and it keeps `obtained_at` truthful,
    which is what the auto_sync freshness check reads.

    Raises MeetNotConnectedError when there is no row, or no readable token."""
    from app import db
    from app.connectors.tokens import decrypt_token_json, encrypt_token_json

    row = db.get_connection(company_id, GOOGLE_MEET_PROVIDER)
    if not row:
        raise MeetNotConnectedError(
            f"Google Meet is not connected for company {company_id}"
        )
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except Exception as e:  # noqa: BLE001 — unreadable token is a dead connection
        raise MeetNotConnectedError(
            "the stored Google Meet token could not be read"
        ) from e

    refresh_token = token_json.get("refresh_token")
    obtained_at = token_json.get("obtained_at") or 0
    expires_in = token_json.get("expires_in") or 3600
    if refresh_token and time.time() > obtained_at + expires_in - 120:
        new_payload = token_payload_to_store(
            refresh_access_token(refresh_token),
            company_id=company_id,
            # Google's refresh response has NO refresh_token field. Without this
            # the stored one is blanked and the connection dies next cycle.
            keep_refresh_token=refresh_token,
        )
        db.update_connection_tokens(
            company_id, GOOGLE_MEET_PROVIDER, encrypt_token_json(new_payload)
        )
        token_json = json.loads(new_payload)

    access_token = token_json.get("access_token") or ""
    if not access_token:
        raise MeetNotConnectedError("the Google Meet connection has no access token")

    try:
        config = json.loads(row.get("config_json") or "{}")
    except (TypeError, ValueError):
        config = {}
    user = config.get(CONFIG_USER) if isinstance(config, dict) else None
    email = ""
    if isinstance(user, dict):
        email = str(user.get("email") or "")

    return MeetContext(
        company_id=company_id,
        access_token=access_token,
        account_email=email or str(row.get("account_label") or ""),
    )
