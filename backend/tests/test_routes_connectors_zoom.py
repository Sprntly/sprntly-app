"""Tests for the Zoom cloud-recordings OAuth connector.

Zoom mechanics, all mocked at the HTTP boundary:
  authorize: https://zoom.us/oauth/authorize?response_type=code&client_id=...
             &redirect_uri=...&scope=...&state=...
  token:     POST https://zoom.us/oauth/token   (HTTP BASIC client auth —
             credentials in the header, NOT the body)
             returns: {access_token, refresh_token, expires_in, scope, token_type}
  revoke:    POST https://zoom.us/oauth/revoke
  users:     GET https://api.zoom.us/v2/users          (host picker)
             GET https://api.zoom.us/v2/users/me       (account label)
  recordings:GET https://api.zoom.us/v2/users/{id}/recordings   (from/to ≤1 month)
             GET https://api.zoom.us/v2/meetings/{uuid}/recordings

Access tokens last ~1h; refresh tokens last 90 days and ROTATE on every
refresh, so every write path is asserted to persist the new one.

CURRENT SCOPE: connect / disconnect / probe / host picker. There is no
kg_ingest puller yet.
"""
from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from tests._company_helpers import company_client


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.zoom_oauth",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def zoom_env(isolated_settings, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("ZOOM_CLIENT_ID", "test-zoom-client-id")
    monkeypatch.setenv("ZOOM_CLIENT_SECRET", "test-zoom-client-secret")
    monkeypatch.setenv(
        "ZOOM_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/zoom/callback",
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
    return m


def _demote_to_member(company_id: str, user_id: str) -> None:
    """Drop the test user from owner to plain member, so the org-connector
    admin gate can be exercised."""
    from app.db.authcache import invalidate_user
    from app.db.client import require_client

    require_client().table("company_members").update({"role": "member"}).eq(
        "company_id", company_id
    ).eq("user_id", user_id).execute()
    invalidate_user(user_id)


# ─────────────────────────── OAuth module unit tests ───────────────────────────


def test_zoom_configured_reflects_env(zoom_env, monkeypatch):
    from app.connectors import zoom_oauth
    assert zoom_oauth.zoom_configured() is True

    monkeypatch.setenv("ZOOM_CLIENT_ID", "")
    _reload_app_modules()
    from app.connectors import zoom_oauth as reloaded
    assert reloaded.zoom_configured() is False


def test_authorize_url_has_required_params(zoom_env):
    from app.connectors import zoom_oauth
    url = zoom_oauth.authorize_url(state="state-token")
    assert url.startswith("https://zoom.us/oauth/authorize")
    assert "client_id=test-zoom-client-id" in url
    assert "response_type=code" in url
    assert "state=state-token" in url
    assert "zoom%2Fcallback" in url


def test_authorize_url_requests_the_granular_admin_scopes(zoom_env):
    """New Zoom Marketplace apps are GRANULAR-ONLY — the classic
    `recording:read:admin` family is not selectable, so asking for it fails at
    the consent screen rather than at the API. And every scope must be the
    `:admin` variant: without it one connection reads only the connecting
    user's own recordings, which for a PM is close to nothing. This pins both."""
    from app.connectors import zoom_oauth

    scopes = zoom_oauth.ZOOM_SCOPES.split()
    assert "cloud_recording:read:list_user_recordings:admin" in scopes
    assert "cloud_recording:read:list_recording_files:admin" in scopes
    assert "user:read:list_users:admin" in scopes
    assert "user:read:user:admin" in scopes
    # The classic family must not come back: it looks right and is not grantable.
    assert "recording:read:admin" not in scopes
    assert "user:read:admin" not in scopes
    # Org-wide by construction — a non-admin scope here would silently narrow
    # every customer's coverage to one person's calls.
    assert all(s.endswith(":admin") for s in scopes), scopes


def test_authorize_url_carries_every_scope_verbatim(zoom_env):
    from urllib.parse import parse_qs, urlparse

    from app.connectors import zoom_oauth

    url = zoom_oauth.authorize_url(state="s")
    sent = parse_qs(urlparse(url).query)["scope"][0]
    assert sent == zoom_oauth.ZOOM_SCOPES


def test_sign_verify_oauth_state_round_trip(zoom_env):
    from app.connectors import zoom_oauth
    token = zoom_oauth.sign_oauth_state(company_id="co-x")
    payload = zoom_oauth.verify_oauth_state(token)
    assert payload["provider"] == "zoom"
    assert payload["company_id"] == "co-x"


def test_state_rejects_a_foreign_provider_claim(zoom_env):
    """Every connector signs its state with the SAME jwt_secret, so another
    provider's state verifies cryptographically. The provider claim is the only
    thing stopping a Confluence or Jira state being replayed at this callback to
    plant a token on a company that never connected Zoom."""
    from fastapi import HTTPException

    from app.connectors import confluence_oauth, jira_oauth, zoom_oauth

    for foreign in (
        confluence_oauth.sign_oauth_state(company_id="co-x"),
        jira_oauth.sign_oauth_state(company_id="co-x"),
    ):
        with pytest.raises(HTTPException):
            zoom_oauth.verify_oauth_state(foreign)

    zoom_state = zoom_oauth.sign_oauth_state(company_id="co-x")
    with pytest.raises(HTTPException):
        confluence_oauth.verify_oauth_state(zoom_state)


def test_state_without_company_id_is_rejected(zoom_env):
    import time

    import jwt as pyjwt
    from fastapi import HTTPException

    from app.config import settings
    from app.connectors import zoom_oauth

    now = int(time.time())
    forged = pyjwt.encode(
        {"provider": "zoom", "iat": now, "exp": now + 600},
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException):
        zoom_oauth.verify_oauth_state(forged)


def test_exchange_code_uses_http_basic_client_auth(zoom_env):
    """Zoom authenticates the CLIENT with a base64 Authorization header, not
    with credentials in the body. Sending them in the body yields
    `invalid_client`, which reads like a wrong secret rather than a wrong auth
    style — so pin the shape."""
    from app.connectors import zoom_oauth

    body = {"access_token": "z-access", "refresh_token": "z-refresh",
            "expires_in": 3600}
    with patch("app.connectors.zoom_oauth.requests.post",
               return_value=_resp(json_body=body)) as mock_post:
        out = zoom_oauth.exchange_code_for_token("auth-code-123")
    assert out["access_token"] == "z-access"

    kwargs = mock_post.call_args.kwargs
    assert kwargs["auth"] == ("test-zoom-client-id", "test-zoom-client-secret")
    assert kwargs["data"]["grant_type"] == "authorization_code"
    assert kwargs["data"]["code"] == "auth-code-123"
    assert kwargs["data"]["redirect_uri"].endswith("/v1/connectors/zoom/callback")
    # The secret must never ride in the body.
    assert "client_secret" not in kwargs["data"]


def test_exchange_code_for_token_handles_error(zoom_env):
    from fastapi import HTTPException

    from app.connectors import zoom_oauth
    with patch("app.connectors.zoom_oauth.requests.post",
               return_value=_resp(ok=False, status=400, text="bad code")):
        with pytest.raises(HTTPException):
            zoom_oauth.exchange_code_for_token("bad-code")


def test_exchange_code_flags_an_unapproved_app_distinctly(zoom_env):
    """An app a Zoom admin has not pre-approved is a DIFFERENT failure from a
    bad code: the remedy is a person in the customer's org, not a retry. It gets
    its own exception so the callback can redirect with a code the web layer
    turns into that instruction."""
    from app.connectors import zoom_oauth

    with patch("app.connectors.zoom_oauth.requests.post",
               return_value=_resp(ok=False, status=400,
                                  text='{"reason":"App not approved for this '
                                       'account","error":"unauthorized_client"}')):
        with pytest.raises(zoom_oauth.ZoomAppNotApprovedError):
            zoom_oauth.exchange_code_for_token("c")


def test_bare_unauthorized_client_is_not_an_approval_failure(zoom_env):
    """`unauthorized_client` alone means the GRANT TYPE is not enabled on OUR
    app — a Sprntly-side misconfiguration. Telling the customer's admin to
    "approve Sprntly" would send them to fix something they cannot see and do
    not control."""
    from fastapi import HTTPException

    from app.connectors import zoom_oauth

    assert zoom_oauth.looks_like_app_not_approved("unauthorized_client") is False
    with patch("app.connectors.zoom_oauth.requests.post",
               return_value=_resp(ok=False, status=400,
                                  text='{"error":"unauthorized_client",'
                                       '"reason":"Invalid grant type"}')):
        with pytest.raises(HTTPException):
            zoom_oauth.exchange_code_for_token("c")


def test_refresh_access_token_returns_rotated_payload(zoom_env):
    from app.connectors import zoom_oauth

    body = {"access_token": "new-access", "refresh_token": "new-refresh",
            "expires_in": 3600}
    with patch("app.connectors.zoom_oauth.requests.post",
               return_value=_resp(json_body=body)) as mock_post:
        out = zoom_oauth.refresh_access_token("old-refresh")
    assert out["refresh_token"] == "new-refresh"  # rotated
    kwargs = mock_post.call_args.kwargs
    assert kwargs["data"]["grant_type"] == "refresh_token"
    assert kwargs["auth"] == ("test-zoom-client-id", "test-zoom-client-secret")


@pytest.mark.parametrize("status", [400, 401, 403])
def test_refresh_access_token_raises_auth_expired(zoom_env, status):
    from app.connectors import zoom_oauth
    with patch("app.connectors.zoom_oauth.requests.post",
               return_value=_resp(ok=False, status=status, text="invalid_grant")):
        with pytest.raises(zoom_oauth.ZoomAuthExpiredError):
            zoom_oauth.refresh_access_token("dead-refresh")


def test_auth_expired_error_carries_401_status_code(zoom_env):
    """kg_ingest.auto_sync picks the "reconnect required" branch by reading
    `getattr(exc, "status_code", None)`. A bare requests.HTTPError keeps its
    status on `.response.status_code`, which that check never looks at — so
    without this attribute a dead token produces an ERROR traceback and a raw
    error string on the connection row instead of a reconnect prompt."""
    from app.connectors import zoom_oauth
    err = zoom_oauth.ZoomAuthExpiredError("dead")
    assert getattr(err, "status_code", None) == 401


def test_revoke_token_posts_with_basic_auth_and_never_raises(zoom_env):
    import requests as _requests

    from app.connectors import zoom_oauth

    with patch("app.connectors.zoom_oauth.requests.post",
               return_value=_resp()) as mock_post:
        assert zoom_oauth.revoke_token("live-token") is True
    assert mock_post.call_args.kwargs["auth"] == (
        "test-zoom-client-id", "test-zoom-client-secret",
    )
    assert mock_post.call_args.kwargs["data"]["token"] == "live-token"

    # A Zoom-side failure must not stop a disconnect.
    with patch("app.connectors.zoom_oauth.requests.post",
               side_effect=_requests.RequestException("boom")):
        assert zoom_oauth.revoke_token("live-token") is False
    with patch("app.connectors.zoom_oauth.requests.post",
               return_value=_resp(ok=False, status=500)):
        assert zoom_oauth.revoke_token("live-token") is False


# ── token_payload_to_store: the company_id contract ──────────────────────────


def test_token_payload_carries_company_id_and_obtained_at(zoom_env):
    from app.connectors import zoom_oauth

    stored = json.loads(zoom_oauth.token_payload_to_store(
        {"access_token": "a", "refresh_token": "r", "expires_in": 3600},
        company_id="co-42",
    ))
    assert stored["company_id"] == "co-42"
    assert isinstance(stored["obtained_at"], int)
    assert stored["access_token"] == "a"


def test_token_payload_keeps_refresh_token_when_response_omits_it(zoom_env):
    from app.connectors import zoom_oauth

    stored = json.loads(zoom_oauth.token_payload_to_store(
        {"access_token": "new", "expires_in": 3600},
        company_id="co-42",
        keep_refresh_token="previous-refresh",
    ))
    assert stored["refresh_token"] == "previous-refresh"


def test_token_payload_prefers_a_rotated_refresh_token(zoom_env):
    from app.connectors import zoom_oauth

    stored = json.loads(zoom_oauth.token_payload_to_store(
        {"access_token": "new", "refresh_token": "rotated", "expires_in": 3600},
        company_id="co-42",
        keep_refresh_token="previous-refresh",
    ))
    assert stored["refresh_token"] == "rotated"


def test_token_payload_requires_company_id_keyword(zoom_env):
    """Silent defaults are how a missing credential reaches production — the
    signature catches it at the call site instead."""
    from app.connectors import zoom_oauth
    with pytest.raises(TypeError):
        zoom_oauth.token_payload_to_store({"access_token": "a"})  # type: ignore[call-arg]


# ── Read API: rate limits, auth, pagination ──────────────────────────────────


def test_api_get_retries_on_429_and_honours_retry_after(zoom_env, monkeypatch):
    from app.connectors import zoom_oauth

    limited = _resp(ok=False, status=429)
    limited.headers = {"Retry-After": "0"}
    ok = _resp(json_body={"users": [{"id": "u1"}]})

    slept: list[float] = []
    monkeypatch.setattr(zoom_oauth.time, "sleep", lambda s: slept.append(s))
    with patch("app.connectors.zoom_oauth.requests.get",
               side_effect=[limited, ok]) as mock_get:
        body = zoom_oauth.api_get("tok", "https://x.test/y")
    assert body["users"] == [{"id": "u1"}]
    assert mock_get.call_count == 2
    assert slept == [0]


def test_api_get_gives_up_after_max_attempts(zoom_env, monkeypatch):
    from fastapi import HTTPException

    from app.connectors import zoom_oauth

    limited = _resp(ok=False, status=429)
    limited.headers = {"Retry-After": "0"}
    monkeypatch.setattr(zoom_oauth.time, "sleep", lambda s: None)
    with patch("app.connectors.zoom_oauth.requests.get", return_value=limited):
        with pytest.raises(HTTPException):
            zoom_oauth.api_get("tok", "https://x.test/y")


@pytest.mark.parametrize("status", [401, 403])
def test_api_get_maps_auth_failures_to_reconnect(zoom_env, status):
    """Both statuses mean the grant no longer covers this read — a scope pulled
    off the app, or the connecting admin demoted — and reconnecting IS the
    remedy."""
    from app.connectors import zoom_oauth

    with patch("app.connectors.zoom_oauth.requests.get",
               return_value=_resp(ok=False, status=status, text="nope")):
        with pytest.raises(zoom_oauth.ZoomAuthExpiredError):
            zoom_oauth.api_get("tok", "https://x.test/y")


@pytest.mark.parametrize(
    "body,text",
    [
        # Zoom's real scope-mismatch envelope.
        ({"code": 4711,
          "message": "Invalid access token, does not contain scopes: "
                     "cloud_recording:read:list_user_recordings:admin"},
         '{"code":4711,"message":"Invalid access token, does not contain '
         'scopes: cloud_recording:read:list_user_recordings:admin"}'),
        # Same class, different code.
        ({"code": 4700, "message": "Invalid access token."},
         '{"code":4700,"message":"Invalid access token."}'),
        # Code missing/garbled — the phrase alone must still catch it.
        ({"message": "Invalid access token, does not contain scopes"},
         '{"message":"Invalid access token, does not contain scopes"}'),
    ],
)
def test_api_get_maps_zooms_400_scope_error_to_reconnect(zoom_env, body, text):
    """THE trap. Zoom does not answer a missing scope with 401 or 403 — it
    answers HTTP 400 with code 4711. Treated as a generic 400 it becomes a 502,
    and then every consequence keys off the wrong branch: the picker shows a
    server error instead of the reconnect prompt, auto_sync's
    `getattr(exc, "status_code")` sees 502 and takes the "genuine error" path,
    and the health probe raises an HTTPException that connector_health's
    fail-open catch swallows — leaving a wholly broken connector GREEN.

    Driven from a mocked HTTP RESPONSE, not a pre-raised exception: the earlier
    tests stubbed the error and so could never have caught this."""
    from app.connectors import zoom_oauth

    resp = _resp(ok=False, status=400, json_body=body, text=text)
    with patch("app.connectors.zoom_oauth.requests.get", return_value=resp):
        with pytest.raises(zoom_oauth.ZoomAuthExpiredError):
            zoom_oauth.api_get("tok", "https://x.test/y")


def test_api_get_leaves_an_ordinary_400_as_a_bad_request(zoom_env):
    """Only the scope signature is a reconnect. A genuine bad request (a
    malformed date on the recordings window, say) must not tell the user their
    connection is broken."""
    from fastapi import HTTPException

    from app.connectors import zoom_oauth

    resp = _resp(ok=False, status=400,
                 json_body={"code": 300, "message": "Invalid date format."},
                 text='{"code":300,"message":"Invalid date format."}')
    with patch("app.connectors.zoom_oauth.requests.get", return_value=resp):
        with pytest.raises(HTTPException) as ei:
            zoom_oauth.api_get("tok", "https://x.test/y")
    assert ei.value.status_code == 502


def test_api_get_survives_a_non_json_400_body(zoom_env):
    """A gateway's HTML error page must not blow up the classifier."""
    from fastapi import HTTPException

    from app.connectors import zoom_oauth

    resp = _resp(ok=False, status=400, text="<html>Bad Request</html>")
    resp.json.side_effect = ValueError("not json")
    with patch("app.connectors.zoom_oauth.requests.get", return_value=resp):
        with pytest.raises(HTTPException):
            zoom_oauth.api_get("tok", "https://x.test/y")


def test_api_get_treats_404_as_empty(zoom_env):
    """A meeting's recordings can be deleted or trashed between listing and
    reading; one missing container must not read as a broken credential."""
    from app.connectors import zoom_oauth

    with patch("app.connectors.zoom_oauth.requests.get",
               return_value=_resp(ok=False, status=404)):
        assert zoom_oauth.api_get("tok", "https://x.test/y") == {}


def test_api_get_can_refuse_to_swallow_a_404(zoom_env):
    """The health probe asserts the PATH is valid, so for it "nothing there"
    and "wrong userId" have opposite meanings — a 404 collapsing to {} would
    read as an account with no recordings, which reads as healthy."""
    from app.connectors import zoom_oauth

    with patch("app.connectors.zoom_oauth.requests.get",
               return_value=_resp(ok=False, status=404)):
        with pytest.raises(zoom_oauth.ZoomNotFoundError):
            zoom_oauth.api_get("tok", "https://x.test/y", allow_missing=False)


def test_list_users_paginates_and_normalizes(zoom_env):
    from app.connectors import zoom_oauth

    page1 = _resp(json_body={
        "users": [
            {"id": "u1", "email": "sam@acme.co", "first_name": "Sam",
             "last_name": "Lee", "type": 2},
            {"id": "u2", "email": "basic@acme.co", "type": 1},
            {"email": "no-id@acme.co", "type": 2},   # unusable — dropped
        ],
        "next_page_token": "PAGE2",
    })
    page2 = _resp(json_body={
        "users": [{"id": "u3", "email": "kim@acme.co", "type": 2}],
        "next_page_token": "",
    })
    with patch("app.connectors.zoom_oauth.requests.get",
               side_effect=[page1, page2]) as mock_get:
        users, capped = zoom_oauth.list_users("tok")

    assert [u["id"] for u in users] == ["u1", "u2", "u3"]
    assert users[0]["display_name"] == "Sam Lee"
    # Zoom `type`: 1 Basic, 2 Licensed. Only Licensed hosts can record to the
    # cloud, so a Basic host with nothing to sync is expected, not a bug.
    assert users[0]["licensed"] is True
    assert users[1]["licensed"] is False
    assert mock_get.call_args_list[1].kwargs["params"]["next_page_token"] == "PAGE2"
    assert mock_get.call_args_list[0].kwargs["params"]["page_size"] <= 300
    # Zoom said there was no next page — the walk finished, it was not cut off.
    assert capped is False


def test_list_users_reports_when_the_page_budget_ran_out(zoom_env):
    """A picker that shows a prefix of a 5,000-host account while reporting a
    total of 1,200 is stating a falsehood about the customer's own org. The
    caller cannot tell "that's everyone" from "that's where we stopped" out of
    the list alone, so the walk says so."""
    from app.connectors import zoom_oauth

    # Every page still advertises a next one.
    page = _resp(json_body={
        "users": [{"id": "u1", "type": 2}], "next_page_token": "MORE",
    })
    with patch("app.connectors.zoom_oauth.requests.get", return_value=page):
        users, capped = zoom_oauth.list_users("tok", max_pages=2)
    assert len(users) == 2
    assert capped is True


def test_window_bounds_clamps_to_zooms_one_month_maximum(zoom_env):
    """Zoom REJECTS a longer `from`/`to` span rather than truncating it, so a
    caller asking for a quarter would get an error instead of a short answer."""
    from datetime import date

    from app.connectors import zoom_oauth

    start, end = zoom_oauth.window_bounds("2026-01-01", "2026-06-01")
    assert end == "2026-06-01"
    assert (date.fromisoformat(end) - date.fromisoformat(start)).days == \
        zoom_oauth.MAX_WINDOW_DAYS

    # A sane window is passed through untouched.
    assert zoom_oauth.window_bounds("2026-05-20", "2026-06-01") == \
        ("2026-05-20", "2026-06-01")
    # Inverted input collapses rather than asking Zoom for a negative range.
    assert zoom_oauth.window_bounds("2026-06-02", "2026-06-01") == \
        ("2026-06-01", "2026-06-01")


def test_list_user_recordings_windows_and_paginates(zoom_env):
    from app.connectors import zoom_oauth

    page1 = _resp(json_body={
        "meetings": [{"uuid": "abc==", "topic": "Acme QBR"}],
        "next_page_token": "P2",
    })
    page2 = _resp(json_body={"meetings": [{"uuid": "def==", "topic": "Renewal"}]})
    with patch("app.connectors.zoom_oauth.requests.get",
               side_effect=[page1, page2]) as mock_get:
        meetings = zoom_oauth.list_user_recordings(
            "tok", "u1", frm="2026-05-01", to="2026-05-20",
        )
    assert [m["topic"] for m in meetings] == ["Acme QBR", "Renewal"]
    params = mock_get.call_args_list[0].kwargs["params"]
    assert params["from"] == "2026-05-01" and params["to"] == "2026-05-20"
    assert mock_get.call_args_list[0].args[0].endswith("/users/u1/recordings")


def test_meeting_uuid_is_double_encoded_only_when_zoom_requires_it(zoom_env):
    """A Zoom meeting UUID is base64 and can legitimately start with `/` or
    contain `//`. Those must be DOUBLE URL-encoded or Zoom's router mis-splits
    the path segment and answers 404 for a meeting that plainly exists — the
    single most common cause of "the recording is right there and the API says
    it isn't"."""
    from app.connectors import zoom_oauth

    assert zoom_oauth.encode_meeting_uuid("/ajXpjLoSSGWkNhH0/xqZQ==") == \
        "%252FajXpjLoSSGWkNhH0%252FxqZQ%253D%253D"
    assert zoom_oauth.encode_meeting_uuid("a//b") == "a%252F%252Fb"
    # No slash → single-encoded, exactly as Zoom's note prescribes.
    assert zoom_oauth.encode_meeting_uuid("abcd1234==") == "abcd1234%3D%3D"


def test_get_meeting_recordings_returns_empty_when_gone(zoom_env):
    from app.connectors import zoom_oauth

    with patch("app.connectors.zoom_oauth.requests.get",
               return_value=_resp(ok=False, status=404)):
        assert zoom_oauth.get_meeting_recordings("tok", "uuid==") == {}


def test_transcript_file_is_chosen_by_extension_not_by_type(zoom_env):
    """Zoom's own docs contradict themselves about whether a TRANSCRIPT file is
    .vtt or .json, so the extension — which is the payload describing itself —
    decides. A TRANSCRIPT whose extension says JSON is SKIPPED rather than
    downloaded and fed to a WebVTT parser that would make nonsense of it."""
    from app.connectors import zoom_oauth

    meeting = {"recording_files": [
        {"file_type": "MP4", "file_extension": "MP4", "download_url": "u1"},
        {"file_type": "TRANSCRIPT", "file_extension": "VTT", "download_url": "u3"},
    ]}
    assert zoom_oauth.transcript_file_from(meeting)["download_url"] == "u3"

    # A TRANSCRIPT with no extension at all is still tried — that is the shape
    # the docs describe as VTT.
    assert zoom_oauth.transcript_file_from({"recording_files": [
        {"file_type": "TRANSCRIPT", "download_url": "u4"},
    ]})["download_url"] == "u4"

    # …but one that declares an unparseable shape is not.
    assert zoom_oauth.transcript_file_from({"recording_files": [
        {"file_type": "TRANSCRIPT", "file_extension": "JSON", "download_url": "u5"},
    ]}) is None

    assert zoom_oauth.transcript_file_from({"recording_files": []}) is None
    assert zoom_oauth.transcript_file_from({}) is None


def test_cc_captions_are_never_selected(zoom_env):
    """CC is the live closed-captions file and duplicates TRANSCRIPT's content
    when both exist, so selecting it (or falling back to it) buys a second copy
    of the same conversation at the cost of a second download and a second
    extraction."""
    from app.connectors import zoom_oauth

    assert zoom_oauth.transcript_file_from({"recording_files": [
        {"file_type": "CC", "file_extension": "VTT", "download_url": "cc"},
    ]}) is None
    picked = zoom_oauth.transcript_file_from({"recording_files": [
        {"file_type": "CC", "file_extension": "VTT", "download_url": "cc"},
        {"file_type": "TRANSCRIPT", "file_extension": "VTT", "download_url": "t"},
    ]})
    assert picked["download_url"] == "t"


def test_fetch_transcript_sends_the_token_in_a_header_not_the_url(zoom_env):
    """The `?access_token=` query form Zoom's older docs show puts a live
    credential into every proxy and access log on the path."""
    from app.connectors import zoom_oauth

    resp = _resp(text="WEBVTT\n\n1\n00:00:01.000 --> 00:00:03.000\nHello.")
    with patch("app.connectors.zoom_oauth.requests.get",
               return_value=resp) as mock_get:
        text = zoom_oauth.fetch_transcript_text("tok", "https://zoom.test/dl/1")
    assert text.startswith("WEBVTT")
    assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"
    assert "access_token" not in mock_get.call_args.args[0]


def test_fetch_transcript_degrades_on_a_bad_file_but_not_on_a_bad_token(zoom_env):
    from app.connectors import zoom_oauth

    # One unreadable file must not fail a whole sync.
    with patch("app.connectors.zoom_oauth.requests.get",
               return_value=_resp(ok=False, status=500)):
        assert zoom_oauth.fetch_transcript_text("tok", "https://zoom.test/dl") == ""
    assert zoom_oauth.fetch_transcript_text("tok", "") == ""
    # A rejected credential is not a bad file — it must surface as a reconnect.
    with patch("app.connectors.zoom_oauth.requests.get",
               return_value=_resp(ok=False, status=401)):
        with pytest.raises(zoom_oauth.ZoomAuthExpiredError):
            zoom_oauth.fetch_transcript_text("tok", "https://zoom.test/dl")


def test_account_label_falls_back_through_email_then_name(zoom_env):
    from app.connectors import zoom_oauth

    assert zoom_oauth.account_label_from(
        {"email": "admin@acme.co", "first_name": "Sam"}) == "admin@acme.co"
    assert zoom_oauth.account_label_from(
        {"first_name": "Sam", "last_name": "Lee"}) == "Sam Lee"
    assert zoom_oauth.account_label_from({"account_id": "acct-1"}) == "acct-1"
    assert zoom_oauth.account_label_from({}) is None


def test_identity_to_store_keeps_three_fields_and_drops_the_personal_ones(zoom_env):
    """`config_json` is handed VERBATIM to every company member by
    GET /v1/connectors. Zoom's user payload carries the connecting admin's
    personal meeting URL, phone number and department — caching it whole would
    publish one person's contact details to the workspace as a side effect of
    connecting a recordings integration."""
    from app.connectors import zoom_oauth

    stored = zoom_oauth.identity_to_store({
        "id": "zu-1", "email": "admin@acme.co", "account_id": "acct-9",
        "pmi": 5551234567, "personal_meeting_url": "https://zoom.us/j/5551234567",
        "phone_number": "+15550001111", "dept": "Revenue", "job_title": "VP Sales",
        "timezone": "America/New_York",
    })
    assert stored == {
        "id": "zu-1", "email": "admin@acme.co", "account_id": "acct-9",
    }
    for leaked in ("pmi", "personal_meeting_url", "phone_number", "dept",
                   "job_title", "timezone"):
        assert leaked not in stored


def test_probe_user_id_prefers_the_real_id_over_me(zoom_env):
    """An admin-managed app is documented to address users by explicit id; `me`
    is not guaranteed to resolve, and a path that does not resolve 404s — which,
    swallowed, reads as "no recordings", which reads as healthy."""
    from app.connectors import zoom_oauth

    assert zoom_oauth.probe_user_id({"user": {"id": "zu-1"}}) == "zu-1"
    # Fallbacks, so a connection made before the id was stored still probes.
    assert zoom_oauth.probe_user_id({"user": {}}) == "me"
    assert zoom_oauth.probe_user_id({"user": "junk"}) == "me"
    assert zoom_oauth.probe_user_id({}) == "me"
    assert zoom_oauth.probe_user_id(None) == "me"


# ── sync_context ─────────────────────────────────────────────────────────────


def _seed_zoom_row(company_id: str, token: dict, config: dict) -> None:
    from app import db
    from app.connectors.tokens import encrypt_token_json

    db.upsert_connection(
        company_id=company_id,
        provider="zoom",
        token_encrypted=encrypt_token_json(json.dumps(token)),
        scopes="",
        account_label="admin@acme.co",
        config_json=json.dumps(config),
    )


def test_sync_context_resolves_from_the_connection_row(zoom_env, monkeypatch):
    import time

    from app.connectors import zoom_oauth

    ctx_company = company_client(monkeypatch).company_id
    _seed_zoom_row(
        ctx_company,
        {"access_token": "live", "refresh_token": "r",
         "obtained_at": int(time.time()), "expires_in": 3600,
         "company_id": ctx_company},
        {"sync_user_ids": ["u1", "u2"],
         "sync_user_names": {"u1": "sam@acme.co", "u2": "kim@acme.co"}},
    )
    ctx = zoom_oauth.sync_context(ctx_company)
    assert ctx.access_token == "live"
    assert ctx.user_ids == ["u1", "u2"]
    assert ctx.user_names == {"u1": "sam@acme.co", "u2": "kim@acme.co"}


def test_sync_context_refreshes_and_persists_keeping_company_id(
    zoom_env, monkeypatch
):
    """Zoom rotates refresh tokens, so a throwaway refresh SPENDS the stored one
    and the connection dies at the next cycle with nothing failing at the moment
    the mistake is made. And company_id must survive, or the next sync loses the
    credential its puller is handed."""
    from app.connectors import zoom_oauth
    from app.connectors.tokens import decrypt_token_json

    ctx_company = company_client(monkeypatch).company_id
    _seed_zoom_row(
        ctx_company,
        # obtained_at 0 → provably expired.
        {"access_token": "stale", "refresh_token": "old",
         "obtained_at": 0, "expires_in": 3600, "company_id": ctx_company},
        {},
    )
    with patch("app.connectors.zoom_oauth.requests.post",
               return_value=_resp(json_body={
                   "access_token": "fresh", "refresh_token": "rotated",
                   "expires_in": 3600,
               })):
        ctx = zoom_oauth.sync_context(ctx_company)

    assert ctx.access_token == "fresh"

    from app import db
    row = db.get_connection(ctx_company, "zoom")
    stored = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    assert stored["access_token"] == "fresh"
    assert stored["refresh_token"] == "rotated"
    assert stored["company_id"] == ctx_company


def test_sync_context_raises_when_not_connected(zoom_env, monkeypatch):
    from app.connectors import zoom_oauth

    ctx_company = company_client(monkeypatch).company_id
    with pytest.raises(zoom_oauth.ZoomNotConnectedError):
        zoom_oauth.sync_context(ctx_company)


# ─────────────────────────── Route tests ───────────────────────────


def test_start_oauth_zoom_returns_url(zoom_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/connectors/zoom/start-oauth")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "zoom.us/oauth/authorize" in body["authorize_url"]
    assert "client_id=test-zoom-client-id" in body["authorize_url"]


def test_start_oauth_zoom_500_when_not_configured(isolated_settings, monkeypatch):
    monkeypatch.setenv("ZOOM_CLIENT_ID", "")
    monkeypatch.setenv("ZOOM_CLIENT_SECRET", "")
    monkeypatch.setenv("ZOOM_OAUTH_REDIRECT_URI", "")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    _reload_app_modules()
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/connectors/zoom/start-oauth")
    assert r.status_code == 500


def test_start_oauth_zoom_is_admin_only(zoom_env, monkeypatch):
    """Zoom is org-wide (every scope is `:admin`), so it is deliberately NOT in
    _PERSONAL_PROVIDERS — one member must not be able to bind the whole
    account's recordings."""
    ctx = company_client(monkeypatch)
    _demote_to_member(ctx.company_id, ctx.user_id)
    r = ctx.client.post("/v1/connectors/zoom/start-oauth")
    assert r.status_code == 403, r.text


def _connect(ctx, *, label_email="admin@acme.co"):
    """Drive the callback end to end with mocked Zoom HTTP."""
    from app.connectors import zoom_oauth

    state = zoom_oauth.sign_oauth_state(company_id=ctx.company_id)
    token_resp = _resp(json_body={
        "access_token": "z-access", "refresh_token": "z-refresh",
        "expires_in": 3600, "scope": zoom_oauth.ZOOM_SCOPES,
    })
    me_resp = _resp(json_body={
        "id": "zu-1", "email": label_email, "account_id": "acct-9",
        "first_name": "Sam", "last_name": "Lee",
    })
    with (
        patch("app.connectors.zoom_oauth.requests.post", return_value=token_resp),
        patch("app.connectors.zoom_oauth.requests.get", return_value=me_resp),
    ):
        return ctx.client.get(
            "/v1/connectors/zoom/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )


def test_callback_stores_the_connection_and_labels_it(zoom_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = _connect(ctx)
    assert r.status_code == 307, r.text
    assert "connected=zoom" in r.headers["location"]

    listed = ctx.client.get("/v1/connectors").json()
    rows = [c for c in listed["connections"] if c["provider"] == "zoom"]
    assert len(rows) == 1
    assert rows[0]["account_label"] == "admin@acme.co"
    assert "token_json_encrypted" not in rows[0]


def test_callback_stores_company_id_inside_the_encrypted_payload(
    zoom_env, monkeypatch
):
    """The puller's credential. `runner.token_for` reads exactly ONE field of
    this payload, so company_id has to be IN it — not merely on the row — and
    the blob must be genuinely Fernet-encrypted, not stored in the clear."""
    ctx = company_client(monkeypatch)
    _connect(ctx)

    from app import db
    from app.connectors.tokens import decrypt_token_json

    row = db.get_connection(ctx.company_id, "zoom")
    blob = row["token_json_encrypted"]
    assert "z-access" not in blob          # not plaintext on the row
    payload = json.loads(decrypt_token_json(blob))
    assert payload["company_id"] == ctx.company_id
    assert payload["access_token"] == "z-access"
    assert payload["refresh_token"] == "z-refresh"
    assert isinstance(payload["obtained_at"], int)


def test_callback_caches_only_the_three_safe_identity_fields(zoom_env, monkeypatch):
    """GET /v1/connectors hands config_json to every company member, so the
    connecting admin's personal meeting URL and phone number must not be in
    it."""
    ctx = company_client(monkeypatch)
    from app.connectors import zoom_oauth
    state = zoom_oauth.sign_oauth_state(company_id=ctx.company_id)
    me = _resp(json_body={
        "id": "zu-1", "email": "admin@acme.co", "account_id": "acct-9",
        "pmi": 5551234567, "personal_meeting_url": "https://zoom.us/j/555",
        "phone_number": "+15550001111", "dept": "Revenue",
    })
    with (
        patch("app.connectors.zoom_oauth.requests.post",
              return_value=_resp(json_body={"access_token": "a",
                                            "refresh_token": "r",
                                            "expires_in": 3600})),
        patch("app.connectors.zoom_oauth.requests.get", return_value=me),
    ):
        ctx.client.get(
            "/v1/connectors/zoom/callback",
            params={"code": "c", "state": state}, follow_redirects=False,
        )

    from app import db
    raw = db.get_connection(ctx.company_id, "zoom").get("config_json") or "{}"
    assert json.loads(raw)["user"] == {
        "id": "zu-1", "email": "admin@acme.co", "account_id": "acct-9",
    }
    for leaked in ("personal_meeting_url", "phone_number", "5551234567", "Revenue"):
        assert leaked not in raw


def test_reconnecting_keeps_the_host_selection(zoom_env, monkeypatch):
    """`upsert_connection` REPLACES config_json, and Zoom's 90-day refresh
    expiry means every long-lived customer reconnects on a schedule. A fresh
    config would drop sync_user_ids — and an empty selection means EVERY HOST —
    so a workspace that deliberately narrowed sync to three sales hosts would
    silently widen to the whole company once a quarter, with no event to trace
    it to."""
    ctx = company_client(monkeypatch)
    _connect(ctx)

    r = ctx.client.post(
        "/v1/connectors/zoom/users",
        json={"users": [{"id": "u1", "email": "sam@acme.co"}]},
    )
    assert r.status_code == 200, r.text

    # …90 days later, the refresh token expires and the admin reconnects.
    _connect(ctx)

    from app import db
    config = json.loads(
        db.get_connection(ctx.company_id, "zoom").get("config_json") or "{}"
    )
    assert config["sync_user_ids"] == ["u1"]
    assert config["sync_user_names"] == {"u1": "sam@acme.co"}
    # …and the reconnect still refreshed the identity it is allowed to cache.
    assert config["user"]["id"] == "zu-1"


def test_a_failed_identity_lookup_does_not_stamp_over_a_good_one(
    zoom_env, monkeypatch
):
    """An empty `{}` written over a cached identity would cost the probe its
    real userId and send it back to `me`."""
    ctx = company_client(monkeypatch)
    _connect(ctx)

    from app.connectors import zoom_oauth
    state = zoom_oauth.sign_oauth_state(company_id=ctx.company_id)
    with (
        patch("app.connectors.zoom_oauth.requests.post",
              return_value=_resp(json_body={"access_token": "a2",
                                            "refresh_token": "r2",
                                            "expires_in": 3600})),
        patch("app.connectors.zoom_oauth.requests.get",
              return_value=_resp(ok=False, status=403, text="forbidden")),
    ):
        ctx.client.get(
            "/v1/connectors/zoom/callback",
            params={"code": "c", "state": state}, follow_redirects=False,
        )

    from app import db
    config = json.loads(
        db.get_connection(ctx.company_id, "zoom").get("config_json") or "{}"
    )
    assert config["user"]["id"] == "zu-1"


def test_callback_survives_an_identity_lookup_failure(zoom_env, monkeypatch):
    """`GET /users/me` answers on a different scope from every recording read,
    so a failure there says nothing about whether the connector works — it must
    cost the label, not the connection."""
    ctx = company_client(monkeypatch)
    from app.connectors import zoom_oauth

    state = zoom_oauth.sign_oauth_state(company_id=ctx.company_id)
    with (
        patch("app.connectors.zoom_oauth.requests.post",
              return_value=_resp(json_body={"access_token": "a",
                                            "refresh_token": "r",
                                            "expires_in": 3600})),
        patch("app.connectors.zoom_oauth.requests.get",
              return_value=_resp(ok=False, status=403, text="forbidden")),
    ):
        r = ctx.client.get(
            "/v1/connectors/zoom/callback",
            params={"code": "c", "state": state}, follow_redirects=False,
        )
    assert r.status_code == 307
    from app import db
    assert db.get_connection(ctx.company_id, "zoom") is not None


def test_callback_400s_when_no_access_token(zoom_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import zoom_oauth
    state = zoom_oauth.sign_oauth_state(company_id=ctx.company_id)
    with patch("app.connectors.zoom_oauth.requests.post",
               return_value=_resp(json_body={"scope": "cloud_recording:read"})):
        r = ctx.client.get(
            "/v1/connectors/zoom/callback",
            params={"code": "c", "state": state}, follow_redirects=False,
        )
    assert r.status_code == 400


def test_callback_rejects_another_connectors_state(zoom_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import confluence_oauth
    wrong = confluence_oauth.sign_oauth_state(company_id=ctx.company_id)
    r = ctx.client.get(
        "/v1/connectors/zoom/callback",
        params={"code": "x", "state": wrong},
        follow_redirects=False,
    )
    assert r.status_code == 400
    from app import db
    assert db.get_connection(ctx.company_id, "zoom") is None


def test_callback_redirects_with_a_stable_code_when_the_app_is_unapproved(
    zoom_env, monkeypatch
):
    """The remedy is a person in the customer's org approving Sprntly in the
    Zoom Marketplace — so the return page needs to say that, which means a
    STABLE code it can map to copy. Zoom's own error string must never reach
    the URL: it changes without notice and would land straight on a screen."""
    ctx = company_client(monkeypatch)
    from app.connectors import zoom_oauth
    state = zoom_oauth.sign_oauth_state(company_id=ctx.company_id)

    r = ctx.client.get(
        "/v1/connectors/zoom/callback",
        params={"code": "", "state": state, "error": "unauthorized_client",
                "error_description": "App not approved for this account"},
        follow_redirects=False,
    )
    assert r.status_code == 307, r.text
    location = r.headers["location"]
    assert "error=zoom_app_not_approved" in location
    assert "connected=" not in location
    assert "unauthorized_client" not in location
    from app import db
    assert db.get_connection(ctx.company_id, "zoom") is None


def test_callback_tells_a_decline_apart_from_an_approval_problem(
    zoom_env, monkeypatch
):
    """A user who clicked Decline has nothing to escalate — they just try again
    and accept. Answering that with "ask your Zoom admin to approve Sprntly"
    sends them to interrupt a colleague over their own click, and it is what a
    single catch-all code would do."""
    ctx = company_client(monkeypatch)
    from app.connectors import zoom_oauth
    state = zoom_oauth.sign_oauth_state(company_id=ctx.company_id)

    r = ctx.client.get(
        "/v1/connectors/zoom/callback",
        params={"state": state, "error": "access_denied",
                "error_description": "user denied the request"},
        follow_redirects=False,
    )
    assert r.status_code == 307, r.text
    location = r.headers["location"]
    assert "error=zoom_consent_declined" in location
    assert "not_approved" not in location


def test_callback_falls_back_to_a_generic_code_for_an_unknown_error(
    zoom_env, monkeypatch
):
    """Three codes, and the third is honest rather than a guess. M3's copy map
    keys off all three, so they have to stay stable and distinct."""
    ctx = company_client(monkeypatch)
    from app.connectors import zoom_oauth
    state = zoom_oauth.sign_oauth_state(company_id=ctx.company_id)

    r = ctx.client.get(
        "/v1/connectors/zoom/callback",
        params={"state": state, "error": "server_error"},
        follow_redirects=False,
    )
    assert r.status_code == 307, r.text
    location = r.headers["location"]
    assert "error=zoom_oauth_failed" in location
    assert "server_error" not in location


def test_callback_maps_an_unapproved_token_exchange_too(zoom_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import zoom_oauth
    state = zoom_oauth.sign_oauth_state(company_id=ctx.company_id)
    with patch("app.connectors.zoom_oauth.requests.post",
               return_value=_resp(ok=False, status=400,
                                  text='{"reason":"App not approved"}')):
        r = ctx.client.get(
            "/v1/connectors/zoom/callback",
            params={"code": "c", "state": state}, follow_redirects=False,
        )
    assert r.status_code == 307
    assert "error=zoom_app_not_approved" in r.headers["location"]


def test_disconnect_revokes_the_grant_then_removes_the_connection(
    zoom_env, monkeypatch
):
    """A refresh token we merely forget stays live on Zoom's side for the rest
    of its 90 days — so revoke first, then delete."""
    ctx = company_client(monkeypatch)
    _connect(ctx)

    with patch("app.connectors.zoom_oauth.requests.post",
               return_value=_resp()) as mock_post:
        r = ctx.client.delete("/v1/connectors/zoom")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True
    assert mock_post.call_args.args[0] == "https://zoom.us/oauth/revoke"

    listed = ctx.client.get("/v1/connectors").json()
    assert not [c for c in listed["connections"] if c["provider"] == "zoom"]


def test_disconnect_deletes_even_when_the_revoke_fails(zoom_env, monkeypatch):
    """The user asked to disconnect. Keeping our copy of their credential
    because Zoom was unreachable is the worse of the two outcomes."""
    import requests as _requests

    ctx = company_client(monkeypatch)
    _connect(ctx)
    with patch("app.connectors.zoom_oauth.requests.post",
               side_effect=_requests.RequestException("down")):
        r = ctx.client.delete("/v1/connectors/zoom")
    assert r.status_code == 200, r.text
    from app import db
    assert db.get_connection(ctx.company_id, "zoom") is None


def test_disconnect_404_when_not_connected(zoom_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.delete("/v1/connectors/zoom")
    assert r.status_code == 404


def test_disconnect_is_admin_only(zoom_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _connect(ctx)
    _demote_to_member(ctx.company_id, ctx.user_id)
    r = ctx.client.delete("/v1/connectors/zoom")
    assert r.status_code == 403, r.text
    from app import db
    assert db.get_connection(ctx.company_id, "zoom") is not None


# ─────────────────────────── Host picker routes ───────────────────────────


def _seed_live_zoom(ctx, config=None):
    import time

    _seed_zoom_row(
        ctx.company_id,
        {"access_token": "live", "obtained_at": int(time.time()),
         "expires_in": 3600, "company_id": ctx.company_id},
        config or {},
    )


def test_list_users_returns_hosts_and_the_current_selection(zoom_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_live_zoom(ctx, {"sync_user_ids": ["u1"]})

    body = _resp(json_body={"users": [
        {"id": "u1", "email": "sam@acme.co", "first_name": "Sam", "type": 2},
        {"id": "u2", "email": "kim@acme.co", "first_name": "Kim", "type": 1},
    ]})
    with patch("app.connectors.zoom_oauth.requests.get", return_value=body):
        r = ctx.client.get("/v1/connectors/zoom/users")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert [u["id"] for u in payload["users"]] == ["u1", "u2"]
    assert payload["selected_ids"] == ["u1"]
    assert payload["total"] == 2
    assert payload["truncated"] is False
    assert payload["fetch_capped"] is False
    # recording_count ships as a declared null in this slice rather than a
    # missing key, so the client renders "—" from day one.
    assert all("recording_count" in u for u in payload["users"])
    assert payload["users"][0]["recording_count"] is None


def test_list_users_returns_the_names_stored_with_the_selection(
    zoom_env, monkeypatch
):
    """The listing is ACTIVE-ONLY, so a selected host who has since been
    deactivated is absent from `users`. Without their stored name the picker can
    only render a bare opaque id — or silently show a shorter selection than the
    one actually in force. That is the whole reason the names are persisted."""
    ctx = company_client(monkeypatch)
    _seed_live_zoom(ctx, {
        "sync_user_ids": ["u1", "gone-1"],
        "sync_user_names": {"u1": "sam@acme.co", "gone-1": "left@acme.co"},
    })
    body = _resp(json_body={"users": [
        {"id": "u1", "email": "sam@acme.co", "type": 2},
    ]})
    with patch("app.connectors.zoom_oauth.requests.get", return_value=body):
        r = ctx.client.get("/v1/connectors/zoom/users")
    payload = r.json()

    assert payload["selected_ids"] == ["u1", "gone-1"]
    assert payload["selected_names"]["gone-1"] == "left@acme.co"
    # The deactivated host is genuinely not in the listing — which is exactly
    # why the name has to come from the stored selection.
    assert [u["id"] for u in payload["users"]] == ["u1"]


def test_list_users_says_when_the_account_is_bigger_than_what_it_fetched(
    zoom_env, monkeypatch
):
    """`total` is the FETCHED count. On an account bigger than the listing
    budget the UI must be able to say "of at least N" rather than assert a
    number it cannot know."""
    ctx = company_client(monkeypatch)
    _seed_live_zoom(ctx)
    page = _resp(json_body={
        "users": [{"id": "u1", "type": 2}], "next_page_token": "MORE",
    })
    with patch("app.connectors.zoom_oauth.requests.get", return_value=page):
        r = ctx.client.get("/v1/connectors/zoom/users")
    assert r.status_code == 200, r.text
    assert r.json()["fetch_capped"] is True


def test_list_users_is_readable_by_a_plain_member(zoom_env, monkeypatch):
    """Seeing what COULD be synced is not a privileged action; changing the
    selection is. Mirrors confluence_list_spaces."""
    ctx = company_client(monkeypatch)
    _seed_live_zoom(ctx)
    _demote_to_member(ctx.company_id, ctx.user_id)

    with patch("app.connectors.zoom_oauth.requests.get",
               return_value=_resp(json_body={"users": [{"id": "u1", "type": 2}]})):
        r = ctx.client.get("/v1/connectors/zoom/users")
    assert r.status_code == 200, r.text


def test_list_users_404_when_not_connected(zoom_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/connectors/zoom/users")
    assert r.status_code == 404


def test_list_users_returns_400_not_500_on_a_rejected_token(zoom_env, monkeypatch):
    """An escaping exception becomes an unhandled 500 with no CORS headers,
    which the browser reports as a bare "Failed to fetch" — the picker then
    shows a network error for what is really a reconnect prompt. That exact
    defect shipped on Confluence's space picker (a1e16c40)."""
    ctx = company_client(monkeypatch)
    _seed_live_zoom(ctx)
    with patch("app.connectors.zoom_oauth.requests.get",
               return_value=_resp(ok=False, status=401, text="Invalid token")):
        r = ctx.client.get("/v1/connectors/zoom/users")
    assert r.status_code == 400, r.text
    assert "reconnect" in r.text.lower()


def test_save_users_persists_ids_and_names(zoom_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_live_zoom(ctx, {"user": {"account_id": "acct-9"}})

    r = ctx.client.post(
        "/v1/connectors/zoom/users",
        json={"users": [
            {"id": "u1", "email": "sam@acme.co"},
            {"id": "u2", "email": "kim@acme.co"},
            {"id": "u1", "email": "sam@acme.co"},   # duplicate — deduped
        ]},
    )
    assert r.status_code == 200, r.text

    from app import db
    config = json.loads(
        db.get_connection(ctx.company_id, "zoom").get("config_json") or "{}"
    )
    assert config["sync_user_ids"] == ["u1", "u2"]
    assert config["sync_user_names"] == {"u1": "sam@acme.co", "u2": "kim@acme.co"}
    # Anything written at connect must survive a config PATCH.
    assert config["user"] == {"account_id": "acct-9"}


def test_save_users_empty_list_clears_back_to_every_host(zoom_env, monkeypatch):
    """Empty selection is the backwards-compatible default, not a no-op: a
    connection made before this picker existed has no stored selection and must
    keep syncing every host."""
    ctx = company_client(monkeypatch)
    _seed_live_zoom(ctx, {"sync_user_ids": ["u1"],
                          "sync_user_names": {"u1": "sam@acme.co"}})
    r = ctx.client.post("/v1/connectors/zoom/users", json={"users": []})
    assert r.status_code == 200, r.text

    from app import db
    config = json.loads(
        db.get_connection(ctx.company_id, "zoom").get("config_json") or "{}"
    )
    assert config["sync_user_ids"] == []


def test_save_users_is_admin_only(zoom_env, monkeypatch):
    """Changing what the whole company ingests is a privileged action, unlike
    reading the list."""
    ctx = company_client(monkeypatch)
    _seed_live_zoom(ctx)
    _demote_to_member(ctx.company_id, ctx.user_id)
    r = ctx.client.post(
        "/v1/connectors/zoom/users", json={"users": [{"id": "u1"}]}
    )
    assert r.status_code == 403, r.text


def test_save_users_404_when_not_connected(zoom_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/zoom/users", json={"users": [{"id": "u1"}]}
    )
    assert r.status_code == 404


def test_save_users_rejects_a_blank_id(zoom_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/connectors/zoom/users", json={"users": [{"id": "  "}]})
    assert r.status_code == 422


def test_save_users_rejects_a_selection_past_the_puller_cap(zoom_env, monkeypatch):
    """Refuse a selection the puller could never honor rather than silently
    truncating one — mirrors Confluence's 25-space cap. Sized for Zoom's cost
    shape: one windowed recordings call per selected host per pass."""
    ctx = company_client(monkeypatch)
    _seed_live_zoom(ctx)
    r = ctx.client.post(
        "/v1/connectors/zoom/users",
        json={"users": [{"id": f"u{i}"} for i in range(101)]},
    )
    assert r.status_code == 422, r.text
    # Exactly at the cap is fine.
    ok = ctx.client.post(
        "/v1/connectors/zoom/users",
        json={"users": [{"id": f"u{i}"} for i in range(100)]},
    )
    assert ok.status_code == 200, ok.text
