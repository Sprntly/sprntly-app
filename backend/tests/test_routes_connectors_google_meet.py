"""Tests for the Google Meet transcripts OAuth connector.

Google mechanics, all mocked at the HTTP boundary:
  authorize:  https://accounts.google.com/o/oauth2/v2/auth?...&scope=...&state=...
  token:      POST https://oauth2.googleapis.com/token   (client credentials in
              the BODY — Google's web-server flow, unlike Zoom's HTTP Basic)
              returns: {access_token, refresh_token, expires_in, scope, id_token}
  revoke:     POST https://oauth2.googleapis.com/revoke
  identity:   GET  https://openidconnect.googleapis.com/v1/userinfo
  meetings:   GET  https://meet.googleapis.com/v2/conferenceRecords
              GET  https://meet.googleapis.com/v2/{conference}/transcripts
              GET  https://meet.googleapis.com/v2/{transcript}/entries
              GET  https://meet.googleapis.com/v2/{conference}/participants

Access tokens last ~1h. Refresh tokens do NOT rotate — but Google's refresh
response omits `refresh_token` entirely, so every write path is asserted to
carry the stored one forward rather than blank it.

Meet carries its OWN OAuth client, separate from Drive's, because the two can be
pointed at two different Google accounts. The state provider claim still does
real work: both connectors sign state with the same JWT secret, so a Drive state
would otherwise verify at this callback.
"""
from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
from cryptography.fernet import Fernet

from tests._company_helpers import company_client


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.google_meet",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def meet_env(isolated_settings, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    # Meet's OWN client, deliberately DIFFERENT from Drive's below: the two
    # connectors can be pointed at two different Google accounts, so every
    # assertion that a Meet request carries the Meet client id would still pass
    # if the code read Drive's — unless the two values differ. They differ.
    monkeypatch.setenv("GOOGLE_MEET_CLIENT_ID", "test-meet-client-id")
    monkeypatch.setenv("GOOGLE_MEET_CLIENT_SECRET", "test-meet-client-secret")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-drive-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-drive-client-secret")
    monkeypatch.setenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/google-drive/callback",
    )
    monkeypatch.setenv(
        "GOOGLE_MEET_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/google-meet/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    _reload_app_modules()
    yield


def _resp(ok=True, status=200, json_body=None, text=""):
    m = MagicMock()
    m.ok = ok
    m.status_code = status
    m.json.return_value = json_body if json_body is not None else {}
    m.text = text
    m.headers = {}
    return m


def _id_token(email="pm@acme.co", sub="google-sub-1") -> str:
    """An unsigned-verification OIDC assertion, the shape Google returns."""
    return pyjwt.encode({"email": email, "sub": sub}, "irrelevant", algorithm="HS256")


def _demote_to_member(company_id: str, user_id: str) -> None:
    from app.db.authcache import invalidate_user
    from app.db.client import require_client

    require_client().table("company_members").update({"role": "member"}).eq(
        "company_id", company_id
    ).eq("user_id", user_id).execute()
    invalidate_user(user_id)


# ─────────────────────────── OAuth module unit tests ───────────────────────────


def test_google_meet_configured_reflects_env(meet_env, monkeypatch):
    from app.connectors import google_meet
    assert google_meet.google_meet_configured() is True

    # The Meet redirect URI is its OWN setting — clearing it must disable Meet
    # without touching the Drive connector alongside it.
    monkeypatch.setenv("GOOGLE_MEET_OAUTH_REDIRECT_URI", "")
    _reload_app_modules()
    from app.connectors import google_meet as reloaded
    assert reloaded.google_meet_configured() is False


def test_authorize_url_requests_exactly_the_documented_scopes(meet_env):
    """`meetings.space.readonly` is the one scope that reads anything, and the
    openid/userinfo trio is what stops google-auth-oauthlib's "Scope has
    changed" rejection (Google auto-adds them for a sign-in client).

    The negative assertions are the load-bearing half. A Drive scope here would
    be a RESTRICTED-tier grant, dragging this whole OAuth client through an
    annual paid CASA assessment — the business decision this connector was
    designed around."""
    from app.connectors import google_meet

    assert google_meet.MEET_SCOPES == [
        "https://www.googleapis.com/auth/meetings.space.readonly",
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ]
    joined = " ".join(google_meet.MEET_SCOPES)
    assert "drive" not in joined
    # `meetings.space.created` is a write-shaped scope for spaces this app made;
    # Sprntly creates no meetings.
    assert "meetings.space.created" not in joined


def test_meet_uses_its_own_client_and_never_falls_back_to_drives(meet_env, monkeypatch):
    """Drive and Meet can be pointed at two DIFFERENT Google accounts, so Meet
    reads its own client triple and must never borrow Drive's.

    The second half is the one that matters: with the Meet client cleared, the
    connector must report NOT CONFIGURED. A fallback to Drive's client would
    look like it worked here and then fail deep inside Google's consent flow
    with a redirect_uri_mismatch — Drive's project has no Meet redirect URI
    registered — which is a far harder failure to trace back to a missing
    environment variable."""
    from app.connectors import google_meet

    assert google_meet.settings.google_meet_client_id == "test-meet-client-id"
    assert google_meet.settings.google_meet_client_secret == "test-meet-client-secret"

    monkeypatch.setenv("GOOGLE_MEET_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_MEET_CLIENT_SECRET", "")
    _reload_app_modules()
    from app.connectors import google_meet as reloaded

    # Drive's client is still fully populated — and is NOT borrowed.
    assert reloaded.settings.google_client_id == "test-drive-client-id"
    assert reloaded.google_meet_configured() is False


def test_meet_scopes_never_leak_into_the_drive_connector(meet_env):
    """THE cross-connector trap. Scopes bake into a token at consent and a
    refresh carries the old set forward, so adding a Meet scope to DRIVE_SCOPES
    would leave every already-stored Drive token claiming a capability it does
    not have — silent 403s on connections whose probe reads healthy. The two
    lists must stay disjoint apart from the common OIDC trio."""
    from app.connectors import google_meet, google_oauth

    assert google_meet.MEET_READONLY_SCOPE not in google_oauth.DRIVE_SCOPES
    assert google_oauth.DRIVE_FILE_SCOPE not in google_meet.MEET_SCOPES


def test_authorize_url_has_required_params(meet_env):
    from urllib.parse import parse_qs, urlparse

    from app.connectors import google_meet

    url = google_meet.authorize_url(state="state-token")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["test-meet-client-id"]
    assert q["response_type"] == ["code"]
    assert q["state"] == ["state-token"]
    # Every scope, verbatim and in order.
    assert q["scope"] == [google_meet.MEET_SCOPE_STRING]
    # Both are required to be handed a refresh token at all — without the
    # forced prompt a RE-authorization silently omits one, and the connection
    # works for exactly one hour.
    assert q["access_type"] == ["offline"]
    assert q["prompt"] == ["consent"]
    # Meet's own redirect URI and own client, never Drive's.
    assert q["redirect_uri"] == [
        "http://testserver/v1/connectors/google-meet/callback"
    ]


def test_sign_verify_oauth_state_round_trip(meet_env):
    from app.connectors import google_meet
    token = google_meet.sign_oauth_state(company_id="co-x")
    payload = google_meet.verify_oauth_state(token)
    assert payload["provider"] == "google_meet"
    assert payload["company_id"] == "co-x"


def test_state_rejects_a_foreign_provider_claim_in_both_directions(meet_env):
    """Every connector signs its state with the SAME jwt_secret, so another
    provider's state verifies cryptographically. That matters more here than
    anywhere else: Drive is the same Google OAuth client, so a Drive state is
    the closest thing to a valid Meet state that exists. The provider claim is
    the only thing stopping one being replayed at this callback to plant a Meet
    token on a company that never connected Meet — and google_oauth's own
    verifier hard-rejects anything that is not `google_drive`, which is why Meet
    needs its own rather than reusing it."""
    from fastapi import HTTPException

    from app.connectors import google_meet, google_oauth, zoom_oauth

    for foreign in (
        google_oauth.sign_oauth_state(company_id="co-x"),
        zoom_oauth.sign_oauth_state(company_id="co-x"),
    ):
        with pytest.raises(HTTPException):
            google_meet.verify_oauth_state(foreign)

    meet_state = google_meet.sign_oauth_state(company_id="co-x")
    with pytest.raises(HTTPException):
        google_oauth.verify_oauth_state(meet_state)
    with pytest.raises(HTTPException):
        zoom_oauth.verify_oauth_state(meet_state)


def test_state_without_company_id_is_rejected(meet_env):
    import time

    from fastapi import HTTPException

    from app.config import settings
    from app.connectors import google_meet

    now = int(time.time())
    forged = pyjwt.encode(
        {"provider": "google_meet", "iat": now, "exp": now + 600},
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException):
        google_meet.verify_oauth_state(forged)


def test_exchange_code_sends_client_credentials_in_the_body(meet_env):
    """Google's documented web-server flow. (Zoom is the odd one out in this
    codebase, needing HTTP Basic.)"""
    from app.connectors import google_meet

    body = {"access_token": "g-access", "refresh_token": "g-refresh",
            "expires_in": 3599}
    with patch("app.connectors.google_meet.requests.post",
               return_value=_resp(json_body=body)) as mock_post:
        out = google_meet.exchange_code_for_token("auth-code-123")
    assert out["access_token"] == "g-access"

    data = mock_post.call_args.kwargs["data"]
    assert data["grant_type"] == "authorization_code"
    assert data["code"] == "auth-code-123"
    assert data["client_id"] == "test-meet-client-id"
    assert data["client_secret"] == "test-meet-client-secret"
    assert data["redirect_uri"].endswith("/v1/connectors/google-meet/callback")


def test_exchange_code_for_token_handles_error(meet_env):
    from fastapi import HTTPException

    from app.connectors import google_meet
    with patch("app.connectors.google_meet.requests.post",
               return_value=_resp(ok=False, status=400, text="invalid_grant")):
        with pytest.raises(HTTPException):
            google_meet.exchange_code_for_token("bad-code")


@pytest.mark.parametrize("status", [400, 401, 403])
def test_refresh_access_token_raises_auth_expired(meet_env, status):
    """Google answers a dead refresh token with 400 invalid_grant, not 401 —
    revoked, six months unused, evicted by the 100-token cap, or the 7-day
    expiry that applies while the consent screen is in Testing."""
    from app.connectors import google_meet
    with patch("app.connectors.google_meet.requests.post",
               return_value=_resp(ok=False, status=status, text="invalid_grant")):
        with pytest.raises(google_meet.MeetAuthExpiredError):
            google_meet.refresh_access_token("dead-refresh")


def test_auth_expired_error_carries_401_status_code(meet_env):
    """kg_ingest.auto_sync picks the "reconnect required" branch by reading
    `getattr(exc, "status_code", None)`. Without this attribute a dead token
    produces an ERROR traceback and a raw error string on the connection row
    instead of a reconnect prompt."""
    from app.connectors import google_meet
    assert getattr(google_meet.MeetAuthExpiredError("dead"), "status_code", None) == 401


def test_revoke_token_never_raises(meet_env):
    import requests as _requests

    from app.connectors import google_meet

    with patch("app.connectors.google_meet.requests.post",
               return_value=_resp()) as mock_post:
        assert google_meet.revoke_token("live-token") is True
    assert mock_post.call_args.args[0] == "https://oauth2.googleapis.com/revoke"

    with patch("app.connectors.google_meet.requests.post",
               side_effect=_requests.RequestException("boom")):
        assert google_meet.revoke_token("live-token") is False
    with patch("app.connectors.google_meet.requests.post",
               return_value=_resp(ok=False, status=500)):
        assert google_meet.revoke_token("live-token") is False


# ── token_payload_to_store: the company_id + refresh-carry contract ──────────


def test_token_payload_carries_company_id_and_obtained_at(meet_env):
    from app.connectors import google_meet

    stored = json.loads(google_meet.token_payload_to_store(
        {"access_token": "a", "refresh_token": "r", "expires_in": 3600},
        company_id="co-42",
    ))
    assert stored["company_id"] == "co-42"
    assert isinstance(stored["obtained_at"], int)
    assert stored["access_token"] == "a"


def test_token_payload_keeps_refresh_token_google_never_returns(meet_env):
    """Google's refresh response has NO refresh_token field — the stored one
    stays valid because it does not rotate. Storing the response verbatim would
    therefore blank it, and the connection would die at the following cycle with
    nothing failing at the moment of the mistake."""
    from app.connectors import google_meet

    stored = json.loads(google_meet.token_payload_to_store(
        {"access_token": "new", "expires_in": 3600},
        company_id="co-42",
        keep_refresh_token="the-only-refresh-token",
    ))
    assert stored["refresh_token"] == "the-only-refresh-token"


def test_token_payload_drops_the_id_token(meet_env):
    """A short-lived signed identity assertion we read exactly once, at connect.
    Keeping a stale copy in the credential blob buys nothing."""
    from app.connectors import google_meet

    stored = json.loads(google_meet.token_payload_to_store(
        {"access_token": "a", "id_token": _id_token()}, company_id="co-1",
    ))
    assert "id_token" not in stored


def test_token_payload_requires_company_id_keyword(meet_env):
    """Silent defaults are how a missing credential reaches production — the
    signature catches it at the call site instead."""
    from app.connectors import google_meet
    with pytest.raises(TypeError):
        google_meet.token_payload_to_store({"access_token": "a"})  # type: ignore[call-arg]


# ── Read API: rate limits, auth, pagination ──────────────────────────────────


def test_api_get_retries_on_429_and_honours_retry_after(meet_env, monkeypatch):
    from app.connectors import google_meet

    limited = _resp(ok=False, status=429)
    limited.headers = {"Retry-After": "0"}
    ok = _resp(json_body={"conferenceRecords": [{"name": "conferenceRecords/c1"}]})

    slept: list[float] = []
    monkeypatch.setattr(google_meet.time, "sleep", lambda s: slept.append(s))
    with patch("app.connectors.google_meet.requests.get",
               side_effect=[limited, ok]) as mock_get:
        body = google_meet.api_get("tok", "https://x.test/y")
    assert body["conferenceRecords"][0]["name"] == "conferenceRecords/c1"
    assert mock_get.call_count == 2
    assert slept == [0]


def test_api_get_treats_a_quota_403_as_a_rate_limit_not_a_dead_token(
    meet_env, monkeypatch
):
    """Google's classic errors deliver "you are going too fast" wearing a 403.
    Mapping that to MeetAuthExpiredError would tell a customer to reconnect a
    connection that works perfectly and is merely busy — the same wrong-branch
    class of mistake as Zoom answering a missing scope with a 400."""
    from app.connectors import google_meet

    limited = _resp(
        ok=False, status=403,
        text='{"error":{"code":403,"errors":[{"reason":"rateLimitExceeded"}]}}',
    )
    limited.headers = {"Retry-After": "0"}
    ok = _resp(json_body={"conferenceRecords": []})
    monkeypatch.setattr(google_meet.time, "sleep", lambda s: None)
    with patch("app.connectors.google_meet.requests.get",
               side_effect=[limited, ok]):
        assert google_meet.api_get("tok", "https://x.test/y") == {
            "conferenceRecords": []
        }


@pytest.mark.parametrize("status", [401, 403])
def test_api_get_maps_auth_failures_to_reconnect(meet_env, status):
    """On a plain read both mean the grant no longer covers what we ask for —
    access revoked, or the Workspace admin turned the Meet API off — and
    reconnecting IS the remedy."""
    from app.connectors import google_meet

    with patch("app.connectors.google_meet.requests.get",
               return_value=_resp(ok=False, status=status, text="forbidden")):
        with pytest.raises(google_meet.MeetAuthExpiredError):
            google_meet.api_get("tok", "https://x.test/y")


def test_api_get_treats_404_as_empty(meet_env):
    """A conference record can pass its 30-day expiry between the listing and
    the transcript read; one vanished container must not read as a broken
    credential."""
    from app.connectors import google_meet

    with patch("app.connectors.google_meet.requests.get",
               return_value=_resp(ok=False, status=404)):
        assert google_meet.api_get("tok", "https://x.test/y") == {}


def test_list_conference_records_filters_to_the_retention_window(meet_env):
    """30 days is not a tuning choice — it is everything that exists. Google
    deletes conference records after that, so a wider filter returns the same
    rows and a narrower one loses data the customer still has."""
    from app.connectors import google_meet

    with patch("app.connectors.google_meet.requests.get",
               return_value=_resp(json_body={"conferenceRecords": []})) as mock_get:
        google_meet.list_conference_records("tok")
    params = mock_get.call_args.kwargs["params"]
    assert params["pageSize"] == 100          # Google's documented ceiling
    assert params["filter"].startswith('start_time>="')
    # The millisecond form Google's filter grammar requires; a bare-seconds
    # timestamp is rejected as a malformed filter.
    assert params["filter"].endswith('.000Z"')


def test_list_transcript_entries_pages_past_the_100_cap(meet_env):
    """PAGING IS MANDATORY, not an optimisation. pageSize defaults to TEN and
    caps at 100, and an entry is one utterance — so an unpaged call returns the
    first few seconds of a meeting while looking exactly like a complete short
    one."""
    from app.connectors import google_meet

    page1 = _resp(json_body={
        "transcriptEntries": [{"text": f"line {i}"} for i in range(100)],
        "nextPageToken": "PAGE2",
    })
    page2 = _resp(json_body={"transcriptEntries": [{"text": "the last word"}]})
    with patch("app.connectors.google_meet.requests.get",
               side_effect=[page1, page2]) as mock_get:
        entries = google_meet.list_transcript_entries("tok", "conferenceRecords/c/transcripts/t")

    assert len(entries) == 101
    assert entries[-1]["text"] == "the last word"
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[0].kwargs["params"]["pageSize"] == 100
    assert mock_get.call_args_list[1].kwargs["params"]["pageToken"] == "PAGE2"


def test_participant_display_name_handles_all_three_kinds(meet_env):
    """A customer call routinely has a signed-in host, an anonymous guest and a
    dial-in. A missing branch would silently un-attribute exactly the external
    guest whose words are the point of the record."""
    from app.connectors import google_meet

    assert google_meet.participant_display_name(
        {"signedinUser": {"displayName": "Sam Lee"}}) == "Sam Lee"
    assert google_meet.participant_display_name(
        {"anonymousUser": {"displayName": "Guest"}}) == "Guest"
    assert google_meet.participant_display_name(
        {"phoneUser": {"displayName": "+1 555…"}}) == "+1 555…"
    assert google_meet.participant_display_name({}) == ""


# ─────────────────────────── Routes ───────────────────────────


def test_start_oauth_returns_googles_consent_url(meet_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/connectors/google_meet/start-oauth")
    assert r.status_code == 200, r.text
    url = r.json()["authorize_url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "meetings.space.readonly" in url


def test_start_oauth_is_admin_only(meet_env, monkeypatch):
    """The connection row is company-scoped — one row per company — so whoever
    holds it decides what the whole workspace ingests."""
    ctx = company_client(monkeypatch)
    _demote_to_member(ctx.company_id, ctx.user_id)
    r = ctx.client.post("/v1/connectors/google_meet/start-oauth")
    assert r.status_code == 403, r.text


def _connect(ctx, *, email="pm@acme.co"):
    """Drive the callback end to end with mocked Google HTTP."""
    from app.connectors import google_meet

    state = google_meet.sign_oauth_state(company_id=ctx.company_id)
    token_resp = _resp(json_body={
        "access_token": "g-access", "refresh_token": "g-refresh",
        "expires_in": 3599, "scope": google_meet.MEET_SCOPE_STRING,
        "id_token": _id_token(email=email),
    })
    with patch("app.connectors.google_meet.requests.post", return_value=token_resp):
        return ctx.client.get(
            "/v1/connectors/google-meet/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )


def test_callback_stores_the_connection_and_labels_it(meet_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = _connect(ctx)
    assert r.status_code == 307, r.text
    assert "connected=google_meet" in r.headers["location"]

    listed = ctx.client.get("/v1/connectors").json()
    rows = [c for c in listed["connections"] if c["provider"] == "google_meet"]
    assert len(rows) == 1
    # Email, not a display name: coverage is organizer-only, so the label is the
    # single thing on the connectors screen saying WHOSE meetings this sees.
    assert rows[0]["account_label"] == "pm@acme.co"
    assert rows[0]["types"] == ["meetings"]
    assert "token_json_encrypted" not in rows[0]


def test_callback_stores_company_id_inside_the_encrypted_payload(
    meet_env, monkeypatch
):
    """The puller's credential. `runner.token_for` reads exactly ONE field of
    this payload, so company_id has to be IN it — not merely on the row — and
    the blob must be genuinely Fernet-encrypted, not stored in the clear."""
    ctx = company_client(monkeypatch)
    _connect(ctx)

    from app import db
    from app.connectors.tokens import decrypt_token_json

    row = db.get_connection(ctx.company_id, "google_meet")
    blob = row["token_json_encrypted"]
    assert "g-access" not in blob          # not plaintext on the row
    payload = json.loads(decrypt_token_json(blob))
    assert payload["company_id"] == ctx.company_id
    assert payload["access_token"] == "g-access"
    assert payload["refresh_token"] == "g-refresh"
    assert isinstance(payload["obtained_at"], int)
    # The OIDC assertion is read once, at connect, and not retained.
    assert "id_token" not in payload


def test_callback_caches_only_id_and_email_of_the_identity(meet_env, monkeypatch):
    """GET /v1/connectors hands config_json to every company member, so the
    connecting person's profile picture, locale and hosted domain must not be
    in it."""
    ctx = company_client(monkeypatch)
    from app.connectors import google_meet

    state = google_meet.sign_oauth_state(company_id=ctx.company_id)
    # No id_token, so the userinfo fallback runs and returns a full profile.
    token_resp = _resp(json_body={
        "access_token": "a", "refresh_token": "r", "expires_in": 3600,
    })
    userinfo = _resp(json_body={
        "sub": "google-sub-9", "email": "pm@acme.co", "name": "Sam Lee",
        "picture": "https://lh3.googleusercontent.com/a/secret-avatar",
        "locale": "en-GB", "hd": "acme.co", "given_name": "Sam",
    })
    with (
        patch("app.connectors.google_meet.requests.post", return_value=token_resp),
        patch("app.connectors.google_meet.requests.get", return_value=userinfo),
    ):
        ctx.client.get(
            "/v1/connectors/google-meet/callback",
            params={"code": "c", "state": state}, follow_redirects=False,
        )

    from app import db
    raw = db.get_connection(ctx.company_id, "google_meet").get("config_json") or "{}"
    assert json.loads(raw)["user"] == {"id": "google-sub-9", "email": "pm@acme.co"}
    for leaked in ("picture", "googleusercontent", "locale", "en-GB", "Sam Lee"):
        assert leaked not in raw


def test_reconnecting_merges_config_rather_than_replacing_it(meet_env, monkeypatch):
    """`upsert_connection` REPLACES config_json, and this callback runs on every
    reconnect. Writing a fresh dict would drop everything else living there —
    today the puller's sync counters, tomorrow whatever a config surface adds.
    That is exactly the regression the Zoom callback had to be fixed for, where
    it silently widened a narrowed host selection back to every host."""
    ctx = company_client(monkeypatch)
    _connect(ctx)

    from app import db

    db.patch_connection_config(
        ctx.company_id, "google_meet",
        {"last_sync_meetings": 12, "last_sync_transcripts": 9},
    )

    # …the user reconnects (a re-consent, a scope change, a revoked grant).
    _connect(ctx)

    config = json.loads(
        db.get_connection(ctx.company_id, "google_meet").get("config_json") or "{}"
    )
    assert config["last_sync_meetings"] == 12
    assert config["last_sync_transcripts"] == 9
    # …and the reconnect still refreshed the identity it is allowed to cache.
    assert config["user"]["email"] == "pm@acme.co"


def test_a_failed_identity_lookup_does_not_stamp_over_a_good_one(
    meet_env, monkeypatch
):
    """An empty `{}` written over a cached identity would cost the puller the
    account email every record's `organizer_email` is built from."""
    ctx = company_client(monkeypatch)
    _connect(ctx)

    from app.connectors import google_meet
    state = google_meet.sign_oauth_state(company_id=ctx.company_id)
    with (
        patch("app.connectors.google_meet.requests.post",
              return_value=_resp(json_body={"access_token": "a2",
                                            "refresh_token": "r2",
                                            "expires_in": 3600})),
        patch("app.connectors.google_meet.requests.get",
              return_value=_resp(ok=False, status=403, text="forbidden")),
    ):
        ctx.client.get(
            "/v1/connectors/google-meet/callback",
            params={"code": "c", "state": state}, follow_redirects=False,
        )

    from app import db
    config = json.loads(
        db.get_connection(ctx.company_id, "google_meet").get("config_json") or "{}"
    )
    assert config["user"]["email"] == "pm@acme.co"


def test_callback_survives_an_identity_lookup_failure(meet_env, monkeypatch):
    """Userinfo answers on a different scope from every meeting read, so a
    failure there says nothing about whether the connector works — it must cost
    the label, not the connection. The probe validates the read that matters."""
    ctx = company_client(monkeypatch)
    from app.connectors import google_meet

    state = google_meet.sign_oauth_state(company_id=ctx.company_id)
    with (
        patch("app.connectors.google_meet.requests.post",
              return_value=_resp(json_body={"access_token": "a",
                                            "refresh_token": "r",
                                            "expires_in": 3600})),
        patch("app.connectors.google_meet.requests.get",
              return_value=_resp(ok=False, status=403, text="forbidden")),
    ):
        r = ctx.client.get(
            "/v1/connectors/google-meet/callback",
            params={"code": "c", "state": state}, follow_redirects=False,
        )
    assert r.status_code == 307
    from app import db
    assert db.get_connection(ctx.company_id, "google_meet") is not None


def test_callback_400s_when_no_access_token(meet_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import google_meet
    state = google_meet.sign_oauth_state(company_id=ctx.company_id)
    with patch("app.connectors.google_meet.requests.post",
               return_value=_resp(json_body={"scope": "openid"})):
        r = ctx.client.get(
            "/v1/connectors/google-meet/callback",
            params={"code": "c", "state": state}, follow_redirects=False,
        )
    assert r.status_code == 400


def test_callback_rejects_the_drive_connectors_state(meet_env, monkeypatch):
    """Same Google OAuth client, same jwt_secret — a Drive state is the closest
    thing to a valid Meet state that exists. Only the provider claim stops it
    planting a Meet token on a company that never connected Meet."""
    ctx = company_client(monkeypatch)
    from app.connectors import google_oauth
    wrong = google_oauth.sign_oauth_state(company_id=ctx.company_id)
    r = ctx.client.get(
        "/v1/connectors/google-meet/callback",
        params={"code": "x", "state": wrong},
        follow_redirects=False,
    )
    assert r.status_code == 400
    from app import db
    assert db.get_connection(ctx.company_id, "google_meet") is None


def test_callback_tells_a_decline_apart_from_a_generic_failure(meet_env, monkeypatch):
    """A user who clicked Decline needs "try again and accept", not a support
    thread. Google's own error string is never forwarded — it changes without
    notice and would land straight on a screen."""
    ctx = company_client(monkeypatch)
    from app.connectors import google_meet
    state = google_meet.sign_oauth_state(company_id=ctx.company_id)

    r = ctx.client.get(
        "/v1/connectors/google-meet/callback",
        params={"code": "", "state": state, "error": "access_denied"},
        follow_redirects=False,
    )
    assert r.status_code == 307, r.text
    assert "error=google_meet_consent_declined" in r.headers["location"]
    assert "connected=" not in r.headers["location"]

    r = ctx.client.get(
        "/v1/connectors/google-meet/callback",
        params={"code": "", "state": state, "error": "admin_policy_enforced",
                "error_description": "blocked by your administrator"},
        follow_redirects=False,
    )
    assert "error=google_meet_oauth_failed" in r.headers["location"]
    assert "admin_policy_enforced" not in r.headers["location"]

    from app import db
    assert db.get_connection(ctx.company_id, "google_meet") is None


def test_disconnect_revokes_the_grant_then_removes_the_connection(
    meet_env, monkeypatch
):
    """Google refresh tokens do not expire on a clock, so one we merely forget
    stays live indefinitely — a permanent credential to this customer's meeting
    transcripts. Revoke first, then delete."""
    ctx = company_client(monkeypatch)
    _connect(ctx)

    with patch("app.connectors.google_meet.requests.post",
               return_value=_resp()) as mock_post:
        r = ctx.client.delete("/v1/connectors/google-meet")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True
    assert mock_post.call_args.args[0] == "https://oauth2.googleapis.com/revoke"
    # The REFRESH token is the one worth killing: revoking any token of a grant
    # invalidates the whole grant, and the access token expires within the hour
    # anyway.
    assert mock_post.call_args.kwargs["params"]["token"] == "g-refresh"

    listed = ctx.client.get("/v1/connectors").json()
    assert not [c for c in listed["connections"] if c["provider"] == "google_meet"]


def test_disconnect_deletes_even_when_the_revoke_fails(meet_env, monkeypatch):
    """The user asked to disconnect. Keeping our copy of their credential
    because Google was unreachable is the worse of the two outcomes."""
    import requests as _requests

    ctx = company_client(monkeypatch)
    _connect(ctx)
    with patch("app.connectors.google_meet.requests.post",
               side_effect=_requests.RequestException("down")):
        r = ctx.client.delete("/v1/connectors/google-meet")
    assert r.status_code == 200, r.text
    from app import db
    assert db.get_connection(ctx.company_id, "google_meet") is None


def test_disconnect_404_when_not_connected(meet_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.delete("/v1/connectors/google-meet")
    assert r.status_code == 404


def test_disconnect_is_admin_only(meet_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _connect(ctx)
    _demote_to_member(ctx.company_id, ctx.user_id)
    r = ctx.client.delete("/v1/connectors/google-meet")
    assert r.status_code == 403, r.text
    from app import db
    assert db.get_connection(ctx.company_id, "google_meet") is not None


def test_a_dead_token_is_a_400_never_a_500(meet_env, monkeypatch):
    """The third defect of the Confluence granular-scopes incident (a1e16c40):
    an auth exception escaping as an unhandled 500 has no CORS headers, so the
    browser reports a bare "Failed to fetch" and the user sees a network error
    for what is really a reconnect prompt."""
    ctx = company_client(monkeypatch)
    _connect(ctx)

    from app import connector_probe

    def _rejected(*a, **kw):
        # The message api_get actually raises with, so the assertion below is
        # about what a user is told and not about this stub's wording.
        raise connector_probe.google_meet.MeetAuthExpiredError(
            "Google rejected the stored token — reconnect Google Meet to continue"
        )

    monkeypatch.setattr(
        connector_probe.google_meet, "list_conference_records", _rejected
    )
    r = ctx.client.post("/v1/connectors/google_meet/test")
    assert r.status_code == 400, r.text
    assert "reconnect google meet" in r.json()["detail"].lower()
