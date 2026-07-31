"""Tests for the Confluence Cloud (Atlassian) OAuth 2.0 3LO connector.

Confluence uses the same Atlassian 3LO mechanics as Jira, through a SEPARATE
console app (one 3LO integration carries exactly one callback URL):
  authorize: https://auth.atlassian.com/authorize?audience=api.atlassian.com
             &client_id=...&scope=...&redirect_uri=...&state=...
             &response_type=code&prompt=consent
  token:     POST https://auth.atlassian.com/oauth/token
             returns: {access_token, refresh_token, expires_in, scope, token_type}
  sites:     GET https://api.atlassian.com/oauth/token/accessible-resources
             returns: [{id (cloud_id), name, url, scopes}]
  user info: GET https://api.atlassian.com/ex/confluence/{cloud_id}
                 /wiki/rest/api/user/current   (v1 — v2 has no such route)
             returns: {accountId, email, displayName, publicName}

Tokens expire (~1h) and refresh tokens ROTATE. All outbound HTTP is mocked.

CURRENT SCOPE: connect / disconnect / probe. There is no kg_ingest puller yet.
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
        "app.connectors.confluence_oauth",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def confluence_env(isolated_settings, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("CONFLUENCE_CLIENT_ID", "test-confluence-client-id")
    monkeypatch.setenv("CONFLUENCE_CLIENT_SECRET", "test-confluence-client-secret")
    monkeypatch.setenv(
        "CONFLUENCE_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/confluence/callback",
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


# ─────────────────────────── OAuth module unit tests ───────────────────────────


def test_confluence_configured_reflects_env(confluence_env, monkeypatch):
    from app.connectors import confluence_oauth
    assert confluence_oauth.confluence_configured() is True

    monkeypatch.setenv("CONFLUENCE_CLIENT_ID", "")
    _reload_app_modules()
    from app.connectors import confluence_oauth as reloaded
    assert reloaded.confluence_configured() is False


def test_sign_verify_oauth_state_round_trip(confluence_env):
    from app.connectors import confluence_oauth
    token = confluence_oauth.sign_oauth_state(company_id="co-x")
    payload = confluence_oauth.verify_oauth_state(token)
    assert payload["provider"] == "confluence"
    assert payload["company_id"] == "co-x"


def test_confluence_and_jira_states_are_not_interchangeable(confluence_env):
    """The two Atlassian connectors share an identity provider and the JWT
    secret. The provider claim is the ONLY thing keeping either callback from
    accepting the other's state — which is also why they are separate apps
    rather than one app with a shared callback."""
    from fastapi import HTTPException

    from app.connectors import confluence_oauth, jira_oauth

    jira_state = jira_oauth.sign_oauth_state(company_id="co-x")
    with pytest.raises(HTTPException):
        confluence_oauth.verify_oauth_state(jira_state)

    confluence_state = confluence_oauth.sign_oauth_state(company_id="co-x")
    with pytest.raises(HTTPException):
        jira_oauth.verify_oauth_state(confluence_state)


def test_authorize_url_has_required_params(confluence_env):
    from app.connectors import confluence_oauth
    url = confluence_oauth.authorize_url(state="state-token")
    assert url.startswith("https://auth.atlassian.com/authorize")
    assert "client_id=test-confluence-client-id" in url
    assert "audience=api.atlassian.com" in url
    assert "state=state-token" in url
    assert "response_type=code" in url
    # offline_access + prompt=consent are what make Atlassian issue a refresh token.
    assert "offline_access" in url
    assert "prompt=consent" in url
    # Guards against copy-pasting Jira's redirect: a shared callback URL is
    # exactly the thing a separate app exists to avoid.
    assert "confluence%2Fcallback" in url
    assert "jira" not in url


def test_authorize_url_requests_content_and_space_scopes(confluence_env):
    """The space picker needs space.summary; the KG ingest needs content.all
    (bodies). Losing either silently degrades a whole feature."""
    from app.connectors import confluence_oauth
    scopes = confluence_oauth.CONFLUENCE_SCOPES.split()
    assert "read:confluence-space.summary" in scopes
    assert "read:confluence-content.all" in scopes
    assert "read:confluence-user" in scopes
    assert "offline_access" in scopes


def test_exchange_code_for_token_posts_correctly(confluence_env):
    from app.connectors import confluence_oauth

    body = {
        "access_token": "cf-access",
        "refresh_token": "cf-refresh",
        "expires_in": 3600,
    }
    with patch("app.connectors.confluence_oauth.requests.post",
               return_value=_resp(json_body=body)) as mock_post:
        out = confluence_oauth.exchange_code_for_token("auth-code-123")
    assert out["access_token"] == "cf-access"
    assert out["refresh_token"] == "cf-refresh"

    sent = mock_post.call_args.kwargs["json"]
    assert sent["grant_type"] == "authorization_code"
    assert sent["client_id"] == "test-confluence-client-id"
    assert sent["client_secret"] == "test-confluence-client-secret"
    assert sent["code"] == "auth-code-123"
    assert sent["redirect_uri"].endswith("/v1/connectors/confluence/callback")


def test_exchange_code_for_token_handles_error(confluence_env):
    from fastapi import HTTPException

    from app.connectors import confluence_oauth
    with patch("app.connectors.confluence_oauth.requests.post",
               return_value=_resp(ok=False, status=400, text="bad code")):
        with pytest.raises(HTTPException):
            confluence_oauth.exchange_code_for_token("bad-code")


def test_refresh_access_token_returns_rotated_payload(confluence_env):
    from app.connectors import confluence_oauth

    body = {"access_token": "new-access", "refresh_token": "new-refresh",
            "expires_in": 3600}
    with patch("app.connectors.confluence_oauth.requests.post",
               return_value=_resp(json_body=body)) as mock_post:
        out = confluence_oauth.refresh_access_token("old-refresh")
    assert out["access_token"] == "new-access"
    assert out["refresh_token"] == "new-refresh"  # rotated
    assert mock_post.call_args.kwargs["json"]["grant_type"] == "refresh_token"


def test_refresh_access_token_raises_auth_expired_on_400(confluence_env):
    from app.connectors import confluence_oauth
    with patch("app.connectors.confluence_oauth.requests.post",
               return_value=_resp(ok=False, status=400, text="invalid_grant")):
        with pytest.raises(confluence_oauth.ConfluenceAuthExpiredError):
            confluence_oauth.refresh_access_token("dead-refresh")


def test_auth_expired_error_carries_401_status_code(confluence_env):
    """kg_ingest.auto_sync picks the "reconnect required" branch by reading
    `getattr(exc, "status_code", None)`. A bare requests.HTTPError keeps its
    status on `.response.status_code`, which that check never looks at — so
    without this attribute a dead token produces an ERROR traceback and a raw
    error string on the connection row instead of a reconnect prompt."""
    from app.connectors import confluence_oauth
    err = confluence_oauth.ConfluenceAuthExpiredError("dead")
    assert getattr(err, "status_code", None) == 401


# ── token_payload_to_store: the company_id contract ──────────────────────────
#
# company_id is the credential the (later) kg_ingest puller receives, because
# runner.token_for hands a puller exactly ONE field of this payload and the
# puller needs the connection's config. Dropping it on a refresh breaks the
# NEXT sync, not the refresh — so it is asserted on every path that writes the
# payload.


def test_token_payload_carries_company_id_and_obtained_at(confluence_env):
    from app.connectors import confluence_oauth

    stored = json.loads(confluence_oauth.token_payload_to_store(
        {"access_token": "a", "refresh_token": "r", "expires_in": 3600},
        company_id="co-42",
    ))
    assert stored["company_id"] == "co-42"
    assert isinstance(stored["obtained_at"], int)
    assert stored["access_token"] == "a"


def test_token_payload_keeps_refresh_token_when_response_omits_it(confluence_env):
    from app.connectors import confluence_oauth

    stored = json.loads(confluence_oauth.token_payload_to_store(
        {"access_token": "new", "expires_in": 3600},
        company_id="co-42",
        keep_refresh_token="previous-refresh",
    ))
    assert stored["refresh_token"] == "previous-refresh"


def test_token_payload_prefers_a_rotated_refresh_token(confluence_env):
    from app.connectors import confluence_oauth

    stored = json.loads(confluence_oauth.token_payload_to_store(
        {"access_token": "new", "refresh_token": "rotated", "expires_in": 3600},
        company_id="co-42",
        keep_refresh_token="previous-refresh",
    ))
    assert stored["refresh_token"] == "rotated"


def test_token_payload_requires_company_id_keyword(confluence_env):
    """Silent defaults are how a missing credential reaches production —
    the signature catches it at the call site instead."""
    from app.connectors import confluence_oauth
    with pytest.raises(TypeError):
        confluence_oauth.token_payload_to_store({"access_token": "a"})  # type: ignore[call-arg]


# ── Site resolution + identity ───────────────────────────────────────────────


def test_get_accessible_resources_and_first_cloud_id(confluence_env):
    from app.connectors import confluence_oauth

    sites = [{"id": "cloud-1", "name": "Acme", "url": "https://acme.atlassian.net"}]
    with patch("app.connectors.confluence_oauth.requests.get",
               return_value=_resp(json_body=sites)):
        assert confluence_oauth.get_accessible_resources("tok")[0]["id"] == "cloud-1"
        assert confluence_oauth.first_cloud_id("tok") == "cloud-1"
        assert confluence_oauth.site_url_for_cloud("tok", "cloud-1") == \
            "https://acme.atlassian.net"


def test_first_cloud_id_none_when_no_sites(confluence_env):
    from app.connectors import confluence_oauth
    with patch("app.connectors.confluence_oauth.requests.get",
               return_value=_resp(json_body=[])):
        assert confluence_oauth.first_cloud_id("tok") is None


def test_fetch_current_user_hits_the_v1_endpoint(confluence_env):
    """v2 has no current-user route — the v1 one is correct here and is what
    read:confluence-user covers."""
    from app.connectors import confluence_oauth

    body = {"accountId": "a1", "email": "sam@acme.co", "displayName": "Sam"}
    with patch("app.connectors.confluence_oauth.requests.get",
               return_value=_resp(json_body=body)) as mock_get:
        user = confluence_oauth.fetch_current_user("tok", "cloud-1")
    assert user["email"] == "sam@acme.co"
    url = mock_get.call_args.args[0]
    assert url.endswith("/ex/confluence/cloud-1/wiki/rest/api/user/current")
    assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


def test_fetch_current_user_returns_empty_on_error(confluence_env):
    from app.connectors import confluence_oauth
    with patch("app.connectors.confluence_oauth.requests.get",
               return_value=_resp(ok=False, status=403, text="forbidden")):
        assert confluence_oauth.fetch_current_user("tok", "cloud-1") == {}


def test_account_label_falls_back_through_identity_then_site(confluence_env):
    """An org privacy setting can refuse read:confluence-user while content
    reads still work — the connection must still get a usable label."""
    from app.connectors import confluence_oauth

    sites = [{"id": "c1", "name": "Acme Wiki"}]
    assert confluence_oauth.account_label_from(
        {"email": "sam@acme.co", "displayName": "Sam"}, sites) == "sam@acme.co"
    assert confluence_oauth.account_label_from({"displayName": "Sam"}, sites) == "Sam"
    assert confluence_oauth.account_label_from({"publicName": "sam"}, sites) == "sam"
    assert confluence_oauth.account_label_from({}, sites) == "Acme Wiki"
    assert confluence_oauth.account_label_from({}, []) is None


# ── Read API: rate limits, auth, pagination ──────────────────────────────────


def test_api_get_retries_on_429_and_honours_retry_after(confluence_env, monkeypatch):
    from app.connectors import confluence_oauth

    limited = _resp(ok=False, status=429)
    limited.headers = {"Retry-After": "0"}
    ok = _resp(json_body={"results": [{"id": "1"}]})

    slept: list[float] = []
    monkeypatch.setattr(confluence_oauth.time, "sleep", lambda s: slept.append(s))
    with patch("app.connectors.confluence_oauth.requests.get",
               side_effect=[limited, ok]) as mock_get:
        body = confluence_oauth.api_get("tok", "https://x.test/y")
    assert body["results"] == [{"id": "1"}]
    assert mock_get.call_count == 2
    assert slept == [0]


def test_api_get_gives_up_after_max_attempts(confluence_env, monkeypatch):
    from fastapi import HTTPException

    from app.connectors import confluence_oauth

    limited = _resp(ok=False, status=429)
    limited.headers = {"Retry-After": "0"}
    monkeypatch.setattr(confluence_oauth.time, "sleep", lambda s: None)
    with patch("app.connectors.confluence_oauth.requests.get", return_value=limited):
        with pytest.raises(HTTPException):
            confluence_oauth.api_get("tok", "https://x.test/y")


@pytest.mark.parametrize("status", [401, 403])
def test_api_get_maps_auth_failures_to_reconnect(confluence_env, status):
    """Both statuses mean the grant no longer covers this read, and
    reconnecting IS the remedy — unlike a tracker DELETE, where 403 is a
    per-object permission fact worth distinguishing."""
    from app.connectors import confluence_oauth

    with patch("app.connectors.confluence_oauth.requests.get",
               return_value=_resp(ok=False, status=status, text="nope")):
        with pytest.raises(confluence_oauth.ConfluenceAuthExpiredError):
            confluence_oauth.api_get("tok", "https://x.test/y")


def test_api_get_treats_404_as_empty(confluence_env):
    """A space can be deleted or restricted mid-sync; one missing container
    must not read as a broken credential."""
    from app.connectors import confluence_oauth

    with patch("app.connectors.confluence_oauth.requests.get",
               return_value=_resp(ok=False, status=404)):
        assert confluence_oauth.api_get("tok", "https://x.test/y") == {}


def test_next_cursor_parses_the_param_not_the_path(confluence_env):
    """v2 advertises the next page as a RELATIVE url that already carries
    /wiki — concatenating it onto our base would double-prefix."""
    from app.connectors import confluence_oauth

    body = {"_links": {"next": "/wiki/api/v2/spaces?cursor=abc123&limit=100"}}
    assert confluence_oauth.next_cursor(body) == "abc123"
    assert confluence_oauth.next_cursor({"_links": {}}) is None
    assert confluence_oauth.next_cursor({}) is None


def test_list_spaces_paginates_and_drops_personal_spaces(confluence_env):
    """Personal spaces are people's private scratch areas — sweeping them into
    a company knowledge graph is not a surprise a connector should spring."""
    from app.connectors import confluence_oauth

    page1 = _resp(json_body={
        "results": [
            {"id": 1, "key": "ENG", "name": "Engineering", "type": "global"},
            {"id": 2, "key": "~alice", "name": "Alice", "type": "personal"},
        ],
        "_links": {"next": "/wiki/api/v2/spaces?cursor=CUR2&limit=100"},
    })
    page2 = _resp(json_body={
        "results": [{"id": 3, "key": "PROD", "name": "Product", "type": "global"}],
    })
    with patch("app.connectors.confluence_oauth.requests.get",
               side_effect=[page1, page2]) as mock_get:
        spaces = confluence_oauth.list_spaces("tok", "cloud-1")
    assert [s["key"] for s in spaces] == ["ENG", "PROD"]
    # ids are stringified so they compare against the stored selection.
    assert [s["id"] for s in spaces] == ["1", "3"]
    assert mock_get.call_args_list[1].kwargs["params"]["cursor"] == "CUR2"


def test_list_spaces_can_include_personal(confluence_env):
    from app.connectors import confluence_oauth

    body = _resp(json_body={"results": [
        {"id": 2, "key": "~alice", "name": "Alice", "type": "personal"},
    ]})
    with patch("app.connectors.confluence_oauth.requests.get", return_value=body):
        spaces = confluence_oauth.list_spaces("tok", "c1", include_personal=True)
    assert [s["key"] for s in spaces] == ["~alice"]


# ── sync_context ─────────────────────────────────────────────────────────────


def _seed_confluence_row(company_id: str, token: dict, config: dict) -> None:
    from app import db
    from app.connectors.tokens import encrypt_token_json

    db.upsert_connection(
        company_id=company_id,
        provider="confluence",
        token_encrypted=encrypt_token_json(json.dumps(token)),
        scopes="",
        account_label="sam@acme.co",
        config_json=json.dumps(config),
    )


def test_sync_context_resolves_from_the_connection_row(confluence_env, monkeypatch):
    import time

    from app.connectors import confluence_oauth

    ctx_company = company_client(monkeypatch).company_id
    _seed_confluence_row(
        ctx_company,
        {"access_token": "live", "refresh_token": "r",
         "obtained_at": int(time.time()), "expires_in": 3600,
         "company_id": ctx_company},
        {"cloud_id": "cloud-9",
         "sites": [{"id": "cloud-9", "url": "https://acme.atlassian.net"}],
         "sync_space_ids": ["s1", "s2"],
         "sync_space_keys": {"s1": "ENG", "s2": "PROD"}},
    )
    ctx = confluence_oauth.sync_context(ctx_company)
    assert ctx.access_token == "live"
    assert ctx.cloud_id == "cloud-9"
    assert ctx.base == "https://api.atlassian.com/ex/confluence/cloud-9/wiki"
    assert ctx.space_ids == ["s1", "s2"]
    assert ctx.space_keys == {"s1": "ENG", "s2": "PROD"}


def test_sync_context_uses_the_cached_site_list_for_permalinks(
    confluence_env, monkeypatch
):
    """accessible-resources on every sync pass is a round trip we already paid
    for at connect."""
    import time

    from app.connectors import confluence_oauth

    ctx_company = company_client(monkeypatch).company_id
    _seed_confluence_row(
        ctx_company,
        {"access_token": "live", "obtained_at": int(time.time()),
         "expires_in": 3600},
        {"cloud_id": "cloud-9",
         "sites": [{"id": "cloud-9", "url": "https://acme.atlassian.net"}]},
    )
    with patch("app.connectors.confluence_oauth.requests.get") as mock_get:
        ctx = confluence_oauth.sync_context(ctx_company)
    assert ctx.site_url == "https://acme.atlassian.net"
    assert mock_get.call_count == 0


def test_sync_context_refreshes_and_persists_keeping_company_id(
    confluence_env, monkeypatch
):
    """Atlassian rotates refresh tokens, so a throwaway refresh strands the
    stored one. And company_id must survive, or the NEXT sync loses its
    credential."""
    from app.connectors import confluence_oauth
    from app.connectors.tokens import decrypt_token_json

    ctx_company = company_client(monkeypatch).company_id
    _seed_confluence_row(
        ctx_company,
        # obtained_at 0 → provably expired.
        {"access_token": "stale", "refresh_token": "old",
         "obtained_at": 0, "expires_in": 3600, "company_id": ctx_company},
        {"cloud_id": "cloud-9", "sites": []},
    )
    with patch("app.connectors.confluence_oauth.requests.post",
               return_value=_resp(json_body={
                   "access_token": "fresh", "refresh_token": "rotated",
                   "expires_in": 3600,
               })):
        with patch("app.connectors.confluence_oauth.requests.get",
                   return_value=_resp(json_body=[])):
            ctx = confluence_oauth.sync_context(ctx_company)

    assert ctx.access_token == "fresh"

    from app import db
    row = db.get_connection(ctx_company, "confluence")
    stored = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    assert stored["access_token"] == "fresh"
    assert stored["refresh_token"] == "rotated"
    assert stored["company_id"] == ctx_company


def test_sync_context_raises_when_not_connected(confluence_env, monkeypatch):
    from app.connectors import confluence_oauth

    ctx_company = company_client(monkeypatch).company_id
    with pytest.raises(confluence_oauth.ConfluenceNotConnectedError):
        confluence_oauth.sync_context(ctx_company)


def test_sync_context_raises_when_no_site_is_visible(confluence_env, monkeypatch):
    import time

    from app.connectors import confluence_oauth

    ctx_company = company_client(monkeypatch).company_id
    _seed_confluence_row(
        ctx_company,
        {"access_token": "live", "obtained_at": int(time.time()),
         "expires_in": 3600},
        {},  # no cached cloud_id
    )
    with patch("app.connectors.confluence_oauth.requests.get",
               return_value=_resp(json_body=[])):
        with pytest.raises(confluence_oauth.ConfluenceNotConnectedError):
            confluence_oauth.sync_context(ctx_company)


# ─────────────────────────── Route tests ───────────────────────────


def test_start_oauth_confluence_returns_url(confluence_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/connectors/confluence/start-oauth")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "auth.atlassian.com" in body["authorize_url"]
    assert "client_id=test-confluence-client-id" in body["authorize_url"]


def test_start_oauth_confluence_500_when_not_configured(isolated_settings, monkeypatch):
    monkeypatch.setenv("CONFLUENCE_CLIENT_ID", "")
    monkeypatch.setenv("CONFLUENCE_CLIENT_SECRET", "")
    monkeypatch.setenv("CONFLUENCE_OAUTH_REDIRECT_URI", "")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    _reload_app_modules()
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/connectors/confluence/start-oauth")
    assert r.status_code == 500


def test_callback_stores_connection_with_cloud_id(confluence_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import confluence_oauth
    state = confluence_oauth.sign_oauth_state(company_id=ctx.company_id)

    token_resp = _resp(json_body={
        "access_token": "cf-access", "refresh_token": "cf-refresh",
        "expires_in": 3600,
    })
    sites_resp = _resp(json_body=[
        {"id": "cloud-77", "name": "Acme", "url": "https://acme.atlassian.net"},
    ])
    user_resp = _resp(json_body={
        "accountId": "a1", "email": "sam@acme.co", "displayName": "Sam",
    })

    # post → token exchange; get is called twice (accessible-resources, user/current).
    with (
        patch("app.connectors.confluence_oauth.requests.post", return_value=token_resp),
        patch("app.connectors.confluence_oauth.requests.get",
              side_effect=[sites_resp, user_resp]),
    ):
        r = ctx.client.get(
            "/v1/connectors/confluence/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )

    assert r.status_code == 307, r.text
    assert "connected=confluence" in r.headers["location"]

    listed = ctx.client.get("/v1/connectors").json()
    rows = [c for c in listed["connections"] if c["provider"] == "confluence"]
    assert len(rows) == 1
    assert rows[0]["account_label"] == "sam@acme.co"
    assert "token_json_encrypted" not in rows[0]

    from app import db
    row = db.get_connection(ctx.company_id, "confluence")
    cfg = json.loads(row.get("config_json") or "{}")
    assert cfg["cloud_id"] == "cloud-77"


def test_callback_stores_company_id_inside_the_encrypted_payload(
    confluence_env, monkeypatch
):
    """The puller's credential. `runner.token_for` reads exactly one field of
    this payload, so company_id has to be IN it — not merely on the row."""
    ctx = company_client(monkeypatch)
    from app.connectors import confluence_oauth
    state = confluence_oauth.sign_oauth_state(company_id=ctx.company_id)

    token_resp = _resp(json_body={
        "access_token": "cf-access", "refresh_token": "cf-refresh", "expires_in": 3600,
    })
    sites_resp = _resp(json_body=[{"id": "cloud-1", "name": "Acme"}])
    user_resp = _resp(json_body={"email": "sam@acme.co"})
    with (
        patch("app.connectors.confluence_oauth.requests.post", return_value=token_resp),
        patch("app.connectors.confluence_oauth.requests.get",
              side_effect=[sites_resp, user_resp]),
    ):
        ctx.client.get(
            "/v1/connectors/confluence/callback",
            params={"code": "c", "state": state}, follow_redirects=False,
        )

    from app import db
    from app.connectors.tokens import decrypt_token_json

    row = db.get_connection(ctx.company_id, "confluence")
    payload = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    assert payload["company_id"] == ctx.company_id
    assert payload["access_token"] == "cf-access"


def test_callback_400s_when_no_access_token(confluence_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import confluence_oauth
    state = confluence_oauth.sign_oauth_state(company_id=ctx.company_id)
    with patch("app.connectors.confluence_oauth.requests.post",
               return_value=_resp(json_body={"scope": "read:confluence-content.all"})):
        r = ctx.client.get(
            "/v1/connectors/confluence/callback",
            params={"code": "c", "state": state}, follow_redirects=False,
        )
    assert r.status_code == 400


def test_callback_rejects_wrong_state(confluence_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import jira_oauth
    wrong = jira_oauth.sign_oauth_state(company_id=ctx.company_id)
    r = ctx.client.get(
        "/v1/connectors/confluence/callback",
        params={"code": "x", "state": wrong},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_disconnect_removes_connection(confluence_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import confluence_oauth
    state = confluence_oauth.sign_oauth_state(company_id=ctx.company_id)
    token_resp = _resp(json_body={
        "access_token": "a", "refresh_token": "r", "expires_in": 3600,
    })
    sites_resp = _resp(json_body=[
        {"id": "cloud-1", "name": "Acme", "url": "https://acme.atlassian.net"},
    ])
    user_resp = _resp(json_body={"email": "sam@acme.co"})
    with (
        patch("app.connectors.confluence_oauth.requests.post", return_value=token_resp),
        patch("app.connectors.confluence_oauth.requests.get",
              side_effect=[sites_resp, user_resp]),
    ):
        ctx.client.get(
            "/v1/connectors/confluence/callback",
            params={"code": "c", "state": state}, follow_redirects=False,
        )
    r = ctx.client.delete("/v1/connectors/confluence")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True
    listed = ctx.client.get("/v1/connectors").json()
    assert not [c for c in listed["connections"] if c["provider"] == "confluence"]


def test_disconnect_404_when_not_connected(confluence_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.delete("/v1/connectors/confluence")
    assert r.status_code == 404
