"""Tests for the Jira (Atlassian) OAuth 2.0 3LO connector.

Jira uses Atlassian 3LO:
  authorize: https://auth.atlassian.com/authorize?audience=api.atlassian.com
             &client_id=...&scope=...&redirect_uri=...&state=...
             &response_type=code&prompt=consent
  token:     POST https://auth.atlassian.com/oauth/token
             body: {grant_type, client_id, client_secret, code, redirect_uri}
             returns: {access_token, refresh_token, expires_in, scope, token_type}
  sites:     GET https://api.atlassian.com/oauth/token/accessible-resources
             header: Authorization: Bearer <access_token>
             returns: [{id (cloud_id), name, url, scopes}]
  user info: GET https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/myself
             returns: {accountId, emailAddress, displayName}

Tokens expire (~1h) and refresh tokens ROTATE. All outbound HTTP is mocked.
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
        "app.connectors.jira_oauth",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def jira_env(isolated_settings, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("JIRA_CLIENT_ID", "test-jira-client-id")
    monkeypatch.setenv("JIRA_CLIENT_SECRET", "test-jira-client-secret")
    monkeypatch.setenv(
        "JIRA_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/jira/callback",
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


def test_jira_configured_reflects_env(jira_env, monkeypatch):
    from app.connectors import jira_oauth
    assert jira_oauth.jira_configured() is True

    monkeypatch.setenv("JIRA_CLIENT_ID", "")
    _reload_app_modules()
    from app.connectors import jira_oauth as reloaded
    assert reloaded.jira_configured() is False


def test_sign_verify_oauth_state_round_trip(jira_env):
    from app.connectors import jira_oauth
    token = jira_oauth.sign_oauth_state(company_id="co-x")
    payload = jira_oauth.verify_oauth_state(token)
    assert payload["provider"] == "jira"
    assert payload["company_id"] == "co-x"


def test_verify_oauth_state_rejects_wrong_provider(jira_env):
    from fastapi import HTTPException

    from app.connectors import clickup_oauth, jira_oauth
    other = clickup_oauth.sign_oauth_state(company_id="co-x")
    with pytest.raises(HTTPException):
        jira_oauth.verify_oauth_state(other)


def test_authorize_url_has_required_params(jira_env):
    from app.connectors import jira_oauth
    url = jira_oauth.authorize_url(state="state-token")
    assert url.startswith("https://auth.atlassian.com/authorize")
    assert "client_id=test-jira-client-id" in url
    assert "audience=api.atlassian.com" in url
    assert "state=state-token" in url
    assert "response_type=code" in url
    # offline_access + prompt=consent are what make Atlassian issue a refresh token.
    assert "offline_access" in url
    assert "prompt=consent" in url


def test_exchange_code_for_token_posts_correctly(jira_env):
    from app.connectors import jira_oauth

    body = {
        "access_token": "jira-access",
        "refresh_token": "jira-refresh",
        "expires_in": 3600,
    }
    with patch("app.connectors.jira_oauth.requests.post",
               return_value=_resp(json_body=body)) as mock_post:
        out = jira_oauth.exchange_code_for_token("auth-code-123")
    assert out["access_token"] == "jira-access"
    assert out["refresh_token"] == "jira-refresh"

    sent = mock_post.call_args.kwargs["json"]
    assert sent["grant_type"] == "authorization_code"
    assert sent["client_id"] == "test-jira-client-id"
    assert sent["client_secret"] == "test-jira-client-secret"
    assert sent["code"] == "auth-code-123"
    assert sent["redirect_uri"].endswith("/v1/connectors/jira/callback")


def test_exchange_code_for_token_handles_error(jira_env):
    from fastapi import HTTPException

    from app.connectors import jira_oauth
    with patch("app.connectors.jira_oauth.requests.post",
               return_value=_resp(ok=False, status=400, text="bad code")):
        with pytest.raises(HTTPException):
            jira_oauth.exchange_code_for_token("bad-code")


def test_refresh_access_token_returns_rotated_payload(jira_env):
    from app.connectors import jira_oauth

    body = {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
    with patch("app.connectors.jira_oauth.requests.post",
               return_value=_resp(json_body=body)) as mock_post:
        out = jira_oauth.refresh_access_token("old-refresh")
    assert out["access_token"] == "new-access"
    assert out["refresh_token"] == "new-refresh"  # rotated
    assert mock_post.call_args.kwargs["json"]["grant_type"] == "refresh_token"


def test_refresh_access_token_raises_auth_expired_on_400(jira_env):
    from app.connectors import jira_oauth
    with patch("app.connectors.jira_oauth.requests.post",
               return_value=_resp(ok=False, status=400, text="invalid_grant")):
        with pytest.raises(jira_oauth.JiraAuthExpiredError):
            jira_oauth.refresh_access_token("dead-refresh")


def test_get_accessible_resources_and_first_cloud_id(jira_env):
    from app.connectors import jira_oauth

    sites = [{"id": "cloud-1", "name": "Acme", "url": "https://acme.atlassian.net"}]
    with patch("app.connectors.jira_oauth.requests.get",
               return_value=_resp(json_body=sites)):
        assert jira_oauth.get_accessible_resources("tok")[0]["id"] == "cloud-1"
        assert jira_oauth.first_cloud_id("tok") == "cloud-1"


def test_first_cloud_id_none_when_no_sites(jira_env):
    from app.connectors import jira_oauth
    with patch("app.connectors.jira_oauth.requests.get", return_value=_resp(json_body=[])):
        assert jira_oauth.first_cloud_id("tok") is None


def test_fetch_authenticated_user_hits_myself(jira_env):
    from app.connectors import jira_oauth

    body = {"accountId": "a1", "emailAddress": "sam@acme.co", "displayName": "Sam"}
    with patch("app.connectors.jira_oauth.requests.get",
               return_value=_resp(json_body=body)) as mock_get:
        user = jira_oauth.fetch_authenticated_user("tok", "cloud-1")
    assert user["emailAddress"] == "sam@acme.co"
    url = mock_get.call_args.args[0]
    assert url.endswith("/ex/jira/cloud-1/rest/api/3/myself")
    assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


# ─────────────────────────── Write side ───────────────────────────


def test_create_issue_builds_adf_and_priority(jira_env):
    from app.connectors import jira_oauth

    post_resp = _resp(json_body={"id": "10001", "key": "PROJ-1"})
    # get is used by _site_url_for_cloud (accessible-resources) for the browse URL.
    get_resp = _resp(json_body=[{"id": "cloud-1", "url": "https://acme.atlassian.net"}])
    with (
        patch("app.connectors.jira_oauth.requests.post", return_value=post_resp) as mock_post,
        patch("app.connectors.jira_oauth.requests.get", return_value=get_resp),
    ):
        out = jira_oauth.create_issue(
            "tok", "cloud-1",
            project_key="PROJ", summary="Do the thing",
            description="line one\n\nline two", priority_name="High",
        )
    assert out["key"] == "PROJ-1"
    assert out["url"] == "https://acme.atlassian.net/browse/PROJ-1"

    fields = mock_post.call_args.kwargs["json"]["fields"]
    assert fields["project"] == {"key": "PROJ"}
    assert fields["summary"] == "Do the thing"
    assert fields["issuetype"] == {"name": "Task"}
    assert fields["priority"] == {"name": "High"}
    # description is ADF, not a raw string.
    assert fields["description"]["type"] == "doc"
    texts = [c["content"][0]["text"] for c in fields["description"]["content"]]
    assert texts == ["line one", "line two"]


def test_create_issue_auth_expired_on_403(jira_env):
    from app.connectors import jira_oauth
    with patch("app.connectors.jira_oauth.requests.post",
               return_value=_resp(ok=False, status=403, text="forbidden")):
        with pytest.raises(jira_oauth.JiraAuthExpiredError):
            jira_oauth.create_issue("tok", "cloud-1", project_key="P", summary="x")


def test_create_issue_omits_priority_when_none(jira_env):
    from app.connectors import jira_oauth
    post_resp = _resp(json_body={"id": "1", "key": "P-1"})
    get_resp = _resp(json_body=[])
    with (
        patch("app.connectors.jira_oauth.requests.post", return_value=post_resp) as mock_post,
        patch("app.connectors.jira_oauth.requests.get", return_value=get_resp),
    ):
        jira_oauth.create_issue("tok", "cloud-1", project_key="P", summary="x")
    assert "priority" not in mock_post.call_args.kwargs["json"]["fields"]


def test_list_projects_paginates(jira_env):
    from app.connectors import jira_oauth

    page1 = _resp(json_body={
        "values": [{"id": "1", "key": "AAA", "name": "Alpha"}], "isLast": False,
    })
    page2 = _resp(json_body={
        "values": [{"id": "2", "key": "BBB", "name": "Beta"}], "isLast": True,
    })
    with patch("app.connectors.jira_oauth.requests.get", side_effect=[page1, page2]):
        projects = jira_oauth.list_projects("tok", "cloud-1")
    keys = {p["key"] for p in projects}
    assert keys == {"AAA", "BBB"}


def test_update_issue_puts_fields(jira_env):
    from app.connectors import jira_oauth
    put_resp = _resp(json_body={})
    get_resp = _resp(json_body=[{"id": "cloud-1", "url": "https://acme.atlassian.net"}])
    with (
        patch("app.connectors.jira_oauth.requests.put", return_value=put_resp) as mock_put,
        patch("app.connectors.jira_oauth.requests.get", return_value=get_resp),
    ):
        out = jira_oauth.update_issue("tok", "cloud-1", "PROJ-1", summary="New title")
    assert out["url"] == "https://acme.atlassian.net/browse/PROJ-1"
    assert mock_put.call_args.kwargs["json"]["fields"]["summary"] == "New title"


# ─────────────────────────── Route tests ───────────────────────────


def test_start_oauth_jira_returns_url(jira_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/connectors/jira/start-oauth")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "auth.atlassian.com" in body["authorize_url"]
    assert "client_id=test-jira-client-id" in body["authorize_url"]


def test_start_oauth_jira_500_when_not_configured(isolated_settings, monkeypatch):
    monkeypatch.setenv("JIRA_CLIENT_ID", "")
    monkeypatch.setenv("JIRA_CLIENT_SECRET", "")
    monkeypatch.setenv("JIRA_OAUTH_REDIRECT_URI", "")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    _reload_app_modules()
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/connectors/jira/start-oauth")
    assert r.status_code == 500


def test_callback_stores_connection_with_cloud_id(jira_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import jira_oauth
    state = jira_oauth.sign_oauth_state(company_id=ctx.company_id)

    token_resp = _resp(json_body={
        "access_token": "jira-access", "refresh_token": "jira-refresh", "expires_in": 3600,
    })
    sites_resp = _resp(json_body=[
        {"id": "cloud-77", "name": "Acme", "url": "https://acme.atlassian.net"},
    ])
    myself_resp = _resp(json_body={
        "accountId": "a1", "emailAddress": "sam@acme.co", "displayName": "Sam",
    })

    # post → token exchange; get is called twice (accessible-resources, myself).
    with (
        patch("app.connectors.jira_oauth.requests.post", return_value=token_resp),
        patch("app.connectors.jira_oauth.requests.get", side_effect=[sites_resp, myself_resp]),
    ):
        r = ctx.client.get(
            "/v1/connectors/jira/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )

    assert r.status_code == 307, r.text
    assert "connected=jira" in r.headers["location"]

    listed = ctx.client.get("/v1/connectors").json()
    rows = [c for c in listed["connections"] if c["provider"] == "jira"]
    assert len(rows) == 1
    assert rows[0]["account_label"] == "sam@acme.co"
    assert "token_json_encrypted" not in rows[0]

    # cloud_id cached in config_json for later REST calls — the list endpoint
    # strips config_json, so read the stored row directly.
    from app import db
    row = db.get_connection(ctx.company_id, "jira")
    cfg = json.loads(row.get("config_json") or "{}")
    assert cfg["cloud_id"] == "cloud-77"


def test_callback_rejects_wrong_state(jira_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import clickup_oauth
    wrong = clickup_oauth.sign_oauth_state(company_id=ctx.company_id)
    r = ctx.client.get(
        "/v1/connectors/jira/callback",
        params={"code": "x", "state": wrong},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_disconnect_removes_connection(jira_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import jira_oauth
    state = jira_oauth.sign_oauth_state(company_id=ctx.company_id)
    token_resp = _resp(json_body={"access_token": "a", "refresh_token": "r", "expires_in": 3600})
    sites_resp = _resp(json_body=[{"id": "cloud-1", "name": "Acme", "url": "https://acme.atlassian.net"}])
    myself_resp = _resp(json_body={"emailAddress": "sam@acme.co"})
    with (
        patch("app.connectors.jira_oauth.requests.post", return_value=token_resp),
        patch("app.connectors.jira_oauth.requests.get", side_effect=[sites_resp, myself_resp]),
    ):
        ctx.client.get(
            "/v1/connectors/jira/callback",
            params={"code": "c", "state": state}, follow_redirects=False,
        )
    r = ctx.client.delete("/v1/connectors/jira")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True
    listed = ctx.client.get("/v1/connectors").json()
    assert not [c for c in listed["connections"] if c["provider"] == "jira"]
