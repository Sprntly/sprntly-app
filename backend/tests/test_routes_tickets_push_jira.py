"""Tests for the Jira ticket push route (POST /v1/tickets/push-jira) and the
Jira project picker (POST /v1/tickets/jira/projects).

Mirrors test_routes_tickets_push.py (ClickUp). All Jira HTTP is mocked at the
jira_oauth boundary. The stored token is seeded fresh (recent obtained_at) so
the refresh path in _jira_creds isn't exercised here; cloud_id resolution falls
back to first_cloud_id (config_json is "{}"), which we stub.
"""
from __future__ import annotations

import importlib
import sys
import time
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from tests._company_helpers import company_client, seed_connection


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.jira_oauth",
        "app.routes.tickets",
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
        "JIRA_OAUTH_REDIRECT_URI", "http://testserver/v1/connectors/jira/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    _reload_app_modules()
    yield


def _connected_client(monkeypatch):
    """A company TestClient with a stored, fresh Jira connection."""
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="jira",
        token_blob={
            "access_token": "jira-access",
            "refresh_token": "jira-refresh",
            "expires_in": 3600,
            "obtained_at": int(time.time()),  # fresh → no refresh path
        },
        label="sam@acme.co",
    )
    return ctx


def _ok_issue(key: str = "PROJ-1"):
    return {"id": "1", "key": key, "url": f"https://acme.atlassian.net/browse/{key}"}


def test_push_creates_jira_issues_and_returns_keys(jira_env, monkeypatch):
    ctx = _connected_client(monkeypatch)
    with (
        patch("app.stories.push.jira_oauth.first_cloud_id", return_value="cloud-1"),
        patch(
            "app.routes.tickets.jira_oauth.create_issue",
            side_effect=[_ok_issue("PROJ-1"), _ok_issue("PROJ-2")],
        ) as mock_create,
    ):
        r = ctx.client.post("/v1/tickets/push-jira", json={
            "project_key": "PROJ",
            "tasks": [
                {"task_id": "MER-481", "title": "Login flow",
                 "description": "As a user I want to log in",
                 "acceptance_criteria": ["Given creds, when valid, then in"],
                 "priority": "P1"},
                {"task_id": "MER-482", "title": "Logout flow",
                 "description": "As a user I want to log out", "priority": "P3"},
            ],
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["errors"] == []
    assert [c["task_id"] for c in body["created"]] == ["MER-481", "MER-482"]
    assert body["created"][0]["jira_issue_key"] == "PROJ-1"
    assert body["created"][0]["url"].endswith("/browse/PROJ-1")

    assert mock_create.call_count == 2
    first = mock_create.call_args_list[0]
    assert first.args[0] == "jira-access"
    assert first.args[1] == "cloud-1"
    assert first.kwargs["project_key"] == "PROJ"
    assert first.kwargs["summary"] == "Login flow"
    # P1 → Jira "High"; body carries the criteria heading.
    assert first.kwargs["priority_name"] == "High"
    assert "## Acceptance criteria" in first.kwargs["description"]
    # P3 → Jira "Low".
    assert mock_create.call_args_list[1].kwargs["priority_name"] == "Low"


def test_push_omits_priority_when_unset(jira_env, monkeypatch):
    ctx = _connected_client(monkeypatch)
    with (
        patch("app.stories.push.jira_oauth.first_cloud_id", return_value="cloud-1"),
        patch("app.routes.tickets.jira_oauth.create_issue", return_value=_ok_issue()) as mock_create,
    ):
        r = ctx.client.post("/v1/tickets/push-jira", json={
            "project_key": "P", "tasks": [{"task_id": "K1", "title": "T"}],
        })
    assert r.status_code == 200, r.text
    assert mock_create.call_args.kwargs["priority_name"] is None


def test_push_passes_issue_type(jira_env, monkeypatch):
    ctx = _connected_client(monkeypatch)
    with (
        patch("app.stories.push.jira_oauth.first_cloud_id", return_value="cloud-1"),
        patch("app.routes.tickets.jira_oauth.create_issue", return_value=_ok_issue()) as mock_create,
    ):
        r = ctx.client.post("/v1/tickets/push-jira", json={
            "project_key": "P", "issue_type": "Story",
            "tasks": [{"task_id": "K1", "title": "T"}],
        })
    assert r.status_code == 200, r.text
    assert mock_create.call_args.kwargs["issue_type"] == "Story"


def test_push_isolates_per_task_failures(jira_env, monkeypatch):
    ctx = _connected_client(monkeypatch)
    with (
        patch("app.stories.push.jira_oauth.first_cloud_id", return_value="cloud-1"),
        patch(
            "app.routes.tickets.jira_oauth.create_issue",
            side_effect=[_ok_issue("P-1"), RuntimeError("boom")],
        ),
    ):
        r = ctx.client.post("/v1/tickets/push-jira", json={
            "project_key": "P", "tasks": [
                {"task_id": "K1", "title": "A"},
                {"task_id": "K2", "title": "B"},
            ],
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert len(body["created"]) == 1 and body["created"][0]["task_id"] == "K1"
    assert len(body["errors"]) == 1 and body["errors"][0]["task_id"] == "K2"


def test_push_401_on_auth_expired(jira_env, monkeypatch):
    from app.connectors import jira_oauth
    ctx = _connected_client(monkeypatch)
    with (
        patch("app.stories.push.jira_oauth.first_cloud_id", return_value="cloud-1"),
        patch(
            "app.routes.tickets.jira_oauth.create_issue",
            side_effect=jira_oauth.JiraAuthExpiredError("reconnect"),
        ),
    ):
        r = ctx.client.post("/v1/tickets/push-jira", json={
            "project_key": "P", "tasks": [{"task_id": "K1", "title": "T"}],
        })
    assert r.status_code == 401


def test_push_404_when_not_connected(jira_env, monkeypatch):
    ctx = company_client(monkeypatch)  # no seeded Jira connection
    r = ctx.client.post("/v1/tickets/push-jira", json={
        "project_key": "P", "tasks": [{"task_id": "K1", "title": "T"}],
    })
    assert r.status_code == 404


def test_push_stories_to_jira_creates_then_updates_idempotently(jira_env, monkeypatch):
    """First push creates + records the map row; a re-push of the SAME story
    resolves to the mapped issue and UPDATEs it instead of duplicating."""
    from app.stories import push as push_mod
    from app.stories.generate import Story

    ctx = _connected_client(monkeypatch)
    story = Story(title="Login flow", body="log in", priority="high")

    saved: dict[str, str] = {}
    monkeypatch.setattr(push_mod, "_jira_creds", lambda cid: ("tok", "cloud-1"))
    monkeypatch.setattr(
        push_mod, "get_jira_issue_key",
        lambda cid, pk, tid: saved.get(tid),
    )
    monkeypatch.setattr(
        push_mod, "save_jira_issue_key",
        lambda cid, pk, tid, key: saved.__setitem__(tid, key),
    )

    with (
        patch.object(push_mod.jira_oauth, "create_issue",
                     return_value={"id": "1", "key": "PROJ-9", "url": "u"}) as mock_create,
        patch.object(push_mod.jira_oauth, "update_issue",
                     return_value={"key": "PROJ-9", "url": "u"}) as mock_update,
    ):
        r1 = push_mod.push_stories_to_jira(ctx.company_id, "PROJ", [story])
        r2 = push_mod.push_stories_to_jira(ctx.company_id, "PROJ", [story])

    assert mock_create.call_count == 1  # created once
    assert mock_update.call_count == 1  # re-push updated, not duplicated
    assert r1["created"][0]["updated"] is False
    assert r2["created"][0]["updated"] is True
    assert r2["created"][0]["task_id"] == "PROJ-9"


def test_jira_projects_picker(jira_env, monkeypatch):
    ctx = _connected_client(monkeypatch)
    projects = [{"id": "1", "key": "AAA", "name": "Alpha"}]
    with (
        patch("app.stories.push.jira_oauth.first_cloud_id", return_value="cloud-1"),
        patch("app.routes.tickets.jira_oauth.list_projects", return_value=projects),
    ):
        r = ctx.client.post("/v1/tickets/jira/projects")
    assert r.status_code == 200, r.text
    assert r.json()["projects"] == projects
