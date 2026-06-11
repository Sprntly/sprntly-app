"""Tests for the Jira (Atlassian OAuth 2.0 3LO) connector.

Flow under test:
  authorize:  https://auth.atlassian.com/authorize?audience=...&scope=...&state=...
  token:      POST https://auth.atlassian.com/oauth/token
              {grant_type: authorization_code|refresh_token, ...}
              returns: {access_token, refresh_token, expires_in}
  resources:  GET https://api.atlassian.com/oauth/token/accessible-resources
              returns: [{id: cloud_id, url, name}]
  identity:   GET https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/myself

Jira tokens expire hourly with ROTATING refresh tokens — get_valid_access_token
refreshes at use and must store the rotated refresh token.
All outbound HTTP is mocked.
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from tests._company_helpers import company_client, seed_connection


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


_RESOURCES = [
    {"id": "cloud-1", "url": "https://acme.atlassian.net", "name": "acme",
     "scopes": ["read:jira-work", "write:jira-work"]},
]


def _get_side_effect(responses_by_url):
    """requests.get stub keyed on URL substring."""
    def _get(url, headers=None, params=None, timeout=None):
        for marker, payload in responses_by_url.items():
            if marker in url:
                return SimpleNamespace(
                    ok=True, status_code=200, json=lambda p=payload: p,
                    text="", raise_for_status=lambda: None,
                )
        raise AssertionError(f"unexpected GET {url}")
    return _get


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
    token = jira_oauth.sign_oauth_state(company_id="ws-x")
    payload = jira_oauth.verify_oauth_state(token)
    assert payload["provider"] == "jira"
    assert payload["company_id"] == "ws-x"


def test_verify_oauth_state_rejects_wrong_provider(jira_env):
    from fastapi import HTTPException

    from app.connectors import clickup_oauth, jira_oauth
    clickup_state = clickup_oauth.sign_oauth_state(company_id="ws-x")
    with pytest.raises(HTTPException):
        jira_oauth.verify_oauth_state(clickup_state)


def test_authorize_url_has_required_params(jira_env):
    from app.connectors import jira_oauth
    url = jira_oauth.authorize_url(state="state-token")
    assert url.startswith("https://auth.atlassian.com/authorize")
    assert "audience=api.atlassian.com" in url
    assert "client_id=test-jira-client-id" in url
    assert "response_type=code" in url
    assert "state=state-token" in url
    # offline_access is what grants the refresh token — without it the
    # connection silently dies after an hour.
    assert "offline_access" in url
    assert "write%3Ajira-work" in url


def test_exchange_code_for_token_posts_correctly(jira_env):
    from app.connectors import jira_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "access_token": "jira-at", "refresh_token": "jira-rt",
        "expires_in": 3600,
    }
    with patch(
        "app.connectors.jira_oauth.requests.post", return_value=mock_resp
    ) as mock_post:
        out = jira_oauth.exchange_code_for_token("auth-code-123")
    assert out["access_token"] == "jira-at"

    call_args = mock_post.call_args
    assert call_args.args[0] == "https://auth.atlassian.com/oauth/token"
    body = call_args.kwargs.get("json") or {}
    assert body.get("grant_type") == "authorization_code"
    assert body.get("client_id") == "test-jira-client-id"
    assert body.get("client_secret") == "test-jira-client-secret"
    assert body.get("code") == "auth-code-123"
    assert body.get("redirect_uri")


def test_refresh_rotates_refresh_token(jira_env):
    from app.connectors import jira_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "access_token": "new-at", "refresh_token": "new-rt", "expires_in": 3600,
    }
    token_json = {"access_token": "old-at", "refresh_token": "old-rt"}
    with patch("app.connectors.jira_oauth.requests.post", return_value=mock_resp):
        out = jira_oauth.refresh_access_token(token_json)
    assert out["access_token"] == "new-at"
    # Atlassian rotates refresh tokens: the NEW one must be stored.
    assert out["refresh_token"] == "new-rt"
    assert out["obtained_at"] > 0


def test_refresh_without_refresh_token_raises(jira_env):
    from fastapi import HTTPException

    from app.connectors import jira_oauth
    with pytest.raises(HTTPException):
        jira_oauth.refresh_access_token({"access_token": "at-only"})


def test_token_payload_to_store_records_cloud_site(jira_env):
    from app.connectors import jira_oauth
    stored = json.loads(jira_oauth.token_payload_to_store(
        {"access_token": "at"}, resources=_RESOURCES,
    ))
    assert stored["cloud_id"] == "cloud-1"
    assert stored["site_url"] == "https://acme.atlassian.net"
    assert stored["obtained_at"] > 0


# ─────────────────────────── refresh-at-use ───────────────────────────


def _seed_jira(monkeypatch, company_id, *, obtained_at):
    seed_connection(
        company_id=company_id, provider="jira",
        token_blob={
            "access_token": "stored-at", "refresh_token": "stored-rt",
            "expires_in": 3600, "obtained_at": obtained_at,
            "cloud_id": "cloud-1", "site_url": "https://acme.atlassian.net",
        },
    )


def test_get_valid_access_token_skips_refresh_when_fresh(jira_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_jira(monkeypatch, ctx.company_id, obtained_at=int(time.time()))

    from app.connectors import jira_oauth
    with patch("app.connectors.jira_oauth.requests.post") as mock_post:
        token, token_json = jira_oauth.get_valid_access_token(ctx.company_id)
    mock_post.assert_not_called()
    assert token == "stored-at"
    assert token_json["cloud_id"] == "cloud-1"


def test_get_valid_access_token_refreshes_and_persists_when_stale(
    jira_env, monkeypatch,
):
    ctx = company_client(monkeypatch)
    _seed_jira(monkeypatch, ctx.company_id, obtained_at=int(time.time()) - 7200)

    from app.connectors import jira_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "access_token": "fresh-at", "refresh_token": "fresh-rt",
        "expires_in": 3600,
    }
    with patch("app.connectors.jira_oauth.requests.post", return_value=mock_resp):
        token, token_json = jira_oauth.get_valid_access_token(ctx.company_id)
    assert token == "fresh-at"

    # The rotated token must have been persisted — a second read needs no
    # refresh.
    with patch("app.connectors.jira_oauth.requests.post") as mock_post:
        token2, token_json2 = jira_oauth.get_valid_access_token(ctx.company_id)
    mock_post.assert_not_called()
    assert token2 == "fresh-at"
    assert token_json2["refresh_token"] == "fresh-rt"


def test_get_valid_access_token_404_when_not_connected(jira_env, monkeypatch):
    from fastapi import HTTPException

    from app.connectors import jira_oauth
    ctx = company_client(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        jira_oauth.get_valid_access_token(ctx.company_id)
    assert exc.value.status_code == 404


# ─────────────────────────── pickers + create_issue ───────────────────────────


def test_list_projects_paginates(jira_env, monkeypatch):
    from app.connectors import jira_oauth

    pages = [
        {"values": [{"id": "1", "key": "ENG", "name": "Engineering"}],
         "isLast": False},
        {"values": [{"id": "2", "key": "OPS", "name": "Operations"}],
         "isLast": True},
    ]
    calls = []

    def _get(token, cloud_id, path, params=None):
        calls.append(params)
        return pages[len(calls) - 1]

    monkeypatch.setattr(jira_oauth, "_get", _get)
    projects = jira_oauth.list_projects("tok", "cloud-1")
    assert [p["key"] for p in projects] == ["ENG", "OPS"]
    assert len(calls) == 2


def test_list_issue_types_excludes_subtasks(jira_env, monkeypatch):
    from app.connectors import jira_oauth

    monkeypatch.setattr(
        jira_oauth, "_get",
        lambda token, cloud_id, path, params=None: {
            "issueTypes": [
                {"id": "10001", "name": "Story", "subtask": False},
                {"id": "10003", "name": "Sub-task", "subtask": True},
            ]
        },
    )
    types = jira_oauth.list_issue_types("tok", "cloud-1", "1")
    assert [t["name"] for t in types] == ["Story"]


def test_create_issue_posts_adf_and_builds_browse_url(jira_env):
    from app.connectors import jira_oauth

    calls: dict = {}

    def _fake_post(url, json=None, headers=None, timeout=None):
        calls["url"] = url
        calls["json"] = json
        calls["headers"] = headers
        return SimpleNamespace(
            ok=True, status_code=201,
            json=lambda: {"id": "10500", "key": "ENG-42"},
        )

    with patch("app.connectors.jira_oauth.requests.post", side_effect=_fake_post):
        out = jira_oauth.create_issue(
            "at", "cloud-1",
            project_id="1", issue_type_id="10001", summary="Story title",
            description_adf=jira_oauth.adf_document(
                [jira_oauth.adf_paragraph("body")]
            ),
            labels=["sprntly"],
            site_url="https://acme.atlassian.net",
        )
    assert calls["url"] == (
        "https://api.atlassian.com/ex/jira/cloud-1/rest/api/3/issue"
    )
    fields = calls["json"]["fields"]
    assert fields["project"] == {"id": "1"}
    assert fields["issuetype"] == {"id": "10001"}
    assert fields["summary"] == "Story title"
    assert fields["description"]["type"] == "doc"
    assert fields["labels"] == ["sprntly"]
    assert calls["headers"]["Authorization"] == "Bearer at"
    assert out == {"id": "10500", "key": "ENG-42",
                   "url": "https://acme.atlassian.net/browse/ENG-42"}


def test_create_issue_raises_on_jira_error(jira_env):
    from fastapi import HTTPException

    from app.connectors import jira_oauth

    err = SimpleNamespace(ok=False, status_code=400, text="field error")
    with patch("app.connectors.jira_oauth.requests.post", return_value=err):
        with pytest.raises(HTTPException):
            jira_oauth.create_issue(
                "at", "cloud-1", project_id="1", issue_type_id="10001",
                summary="x",
            )


# ─────────────────────────── Route tests ───────────────────────────


def test_start_oauth_jira_returns_atlassian_url(jira_env, monkeypatch):
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


def test_callback_stores_connection_with_cloud_site(jira_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import jira_oauth
    state = jira_oauth.sign_oauth_state(company_id=ctx.company_id)

    mock_token_resp = MagicMock()
    mock_token_resp.ok = True
    mock_token_resp.json.return_value = {
        "access_token": "jira-at", "refresh_token": "jira-rt",
        "expires_in": 3600, "scope": "read:jira-work write:jira-work",
    }
    get_stub = _get_side_effect({
        "accessible-resources": _RESOURCES,
        "/myself": {"accountId": "acc-1",
                    "emailAddress": "sarah@meridian.health",
                    "displayName": "Sarah Chen"},
    })

    with (
        patch("app.connectors.jira_oauth.requests.post",
              return_value=mock_token_resp),
        patch("app.connectors.jira_oauth.requests.get", side_effect=get_stub),
    ):
        r = ctx.client.get(
            "/v1/connectors/jira/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )

    assert r.status_code == 307
    assert "connected=jira" in r.headers["location"]

    listed = ctx.client.get("/v1/connectors").json()
    rows = [c for c in listed["connections"] if c["provider"] == "jira"]
    assert len(rows) == 1
    assert rows[0]["account_label"] == "sarah@meridian.health"
    assert rows[0]["config"]["site_url"] == "https://acme.atlassian.net"
    assert "token_json_encrypted" not in rows[0]


def test_callback_400_when_no_accessible_site(jira_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import jira_oauth
    state = jira_oauth.sign_oauth_state(company_id=ctx.company_id)

    mock_token_resp = MagicMock()
    mock_token_resp.ok = True
    mock_token_resp.json.return_value = {"access_token": "jira-at"}
    get_stub = _get_side_effect({"accessible-resources": []})

    with (
        patch("app.connectors.jira_oauth.requests.post",
              return_value=mock_token_resp),
        patch("app.connectors.jira_oauth.requests.get", side_effect=get_stub),
    ):
        r = ctx.client.get(
            "/v1/connectors/jira/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )
    assert r.status_code == 400


def test_callback_rejects_wrong_state(jira_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import clickup_oauth
    wrong_state = clickup_oauth.sign_oauth_state(company_id=ctx.company_id)
    r = ctx.client.get(
        "/v1/connectors/jira/callback",
        params={"code": "x", "state": wrong_state},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_delete_jira_disconnects(jira_env, monkeypatch):
    ctx = company_client(monkeypatch)
    _seed_jira(monkeypatch, ctx.company_id, obtained_at=int(time.time()))

    r = ctx.client.delete("/v1/connectors/jira")
    assert r.status_code == 200
    listed = ctx.client.get("/v1/connectors").json()
    assert not any(c["provider"] == "jira" for c in listed["connections"])


def test_delete_jira_404_when_not_connected(jira_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.delete("/v1/connectors/jira")
    assert r.status_code == 404
