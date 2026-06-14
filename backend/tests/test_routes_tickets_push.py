"""Tests for the PRD → Tickets ClickUp push (POST /v1/tickets/push-clickup).

The push takes the selected tasks ({task_id, title, description,
acceptance_criteria, priority}) and creates one ClickUp task each in the target
list, merging the company's saved overrides (ticket_edits / ticket_comments)
over the supplied base fields first. ClickUp's HTTP API is NEVER hit — we mock
at two layers:

  - Route behaviour tests mock `clickup_oauth.create_task` (the per-task write)
    so we can assert the request payload the route builds, response handling,
    override-merge, and partial-failure isolation.
  - One connector-level test mocks `requests.post` to assert the exact ClickUp
    payload (markdown body + 1-4 priority + raw-token auth header).

ClickUp tokens carry no expiry and ClickUp issues no refresh token, so "token
refresh" here means: re-read the freshest stored token at push time and, if
ClickUp rejects it (401/403), surface a reconnect (HTTP 401) instead of a
generic upstream error. Both paths are covered.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from tests._company_helpers import company_client, seed_connection


def _reload_app_modules():
    # NOTE: deliberately does NOT reload app.stories.push — reloading it would
    # rebind ClickUpNotConnectedError to a new class object, breaking
    # `except ClickUpNotConnectedError` identity in sibling test modules that
    # imported it earlier. app.routes.tickets re-imports the (stable) class.
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.clickup_oauth",
        "app.routes.tickets",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def clickup_env(isolated_settings, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("CLICKUP_CLIENT_ID", "test-clickup-client-id")
    monkeypatch.setenv("CLICKUP_CLIENT_SECRET", "test-clickup-client-secret")
    monkeypatch.setenv(
        "CLICKUP_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/clickup/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    _reload_app_modules()
    yield


def _connected_client(monkeypatch, token: str = "clk-token-real"):
    """A company TestClient that already has a stored ClickUp connection."""
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        provider="clickup",
        token_blob={"access_token": token},
        label="sarah@meridian.health",
    )
    return ctx


def _ok_task(task_id: str = "abc123"):
    return {"id": task_id, "url": f"https://app.clickup.com/t/{task_id}"}


# ─────────────────────────── happy path ───────────────────────────


def test_push_creates_clickup_tasks_and_returns_ids(clickup_env, monkeypatch):
    ctx = _connected_client(monkeypatch)

    with patch(
        "app.routes.tickets.clickup_oauth.create_task",
        side_effect=[_ok_task("t1"), _ok_task("t2")],
    ) as mock_create:
        r = ctx.client.post("/v1/tickets/push-clickup", json={
            "list_id": "list-99",
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
    assert body["created"][0]["clickup_task_id"] == "t1"
    assert body["created"][0]["url"] == "https://app.clickup.com/t/t1"
    assert body["created"][0]["title"] == "Login flow"

    # Each task created in the target list with the raw token.
    assert mock_create.call_count == 2
    first = mock_create.call_args_list[0]
    assert first.args[0] == "clk-token-real"
    assert first.args[1] == "list-99"
    assert first.kwargs["name"] == "Login flow"
    # P1 → ClickUp 2 (high); markdown body carries the criteria heading.
    assert first.kwargs["priority"] == 2
    assert "## Acceptance criteria" in first.kwargs["markdown_description"]
    assert "Given creds" in first.kwargs["markdown_description"]
    # P3 → ClickUp 4 (low).
    assert mock_create.call_args_list[1].kwargs["priority"] == 4


def test_push_accepts_human_priority_labels(clickup_env, monkeypatch):
    ctx = _connected_client(monkeypatch)
    with patch(
        "app.routes.tickets.clickup_oauth.create_task", return_value=_ok_task(),
    ) as mock_create:
        r = ctx.client.post("/v1/tickets/push-clickup", json={
            "list_id": "L", "tasks": [
                {"task_id": "K1", "title": "T", "priority": "urgent"},
            ],
        })
    assert r.status_code == 200, r.text
    assert mock_create.call_args.kwargs["priority"] == 1  # urgent → 1


def test_push_omits_priority_when_unset(clickup_env, monkeypatch):
    ctx = _connected_client(monkeypatch)
    with patch(
        "app.routes.tickets.clickup_oauth.create_task", return_value=_ok_task(),
    ) as mock_create:
        r = ctx.client.post("/v1/tickets/push-clickup", json={
            "list_id": "L", "tasks": [{"task_id": "K1", "title": "T"}],
        })
    assert r.status_code == 200, r.text
    assert mock_create.call_args.kwargs["priority"] is None


# ─────────────────────────── override merge ───────────────────────────


def test_push_merges_saved_edits_over_base_fields(clickup_env, monkeypatch):
    """ticket_edits (user-reviewed description + acceptance criteria) win over
    the generator's base values sent in the request."""
    ctx = _connected_client(monkeypatch)
    # Save an edit for this ticket_key first.
    ctx.client.put("/v1/tickets/MER-481/description", json={
        "description": "EDITED body the user reviewed",
        "acceptance_criteria": ["EDITED criterion A", "EDITED criterion B"],
    })

    with patch(
        "app.routes.tickets.clickup_oauth.create_task", return_value=_ok_task(),
    ) as mock_create:
        r = ctx.client.post("/v1/tickets/push-clickup", json={
            "list_id": "L", "tasks": [{
                "task_id": "MER-481", "title": "Login",
                "description": "STALE base body",
                "acceptance_criteria": ["stale criterion"],
            }],
        })

    assert r.status_code == 200, r.text
    md = mock_create.call_args.kwargs["markdown_description"]
    assert "EDITED body the user reviewed" in md
    assert "EDITED criterion A" in md
    assert "EDITED criterion B" in md
    assert "STALE base body" not in md
    assert "stale criterion" not in md


def test_push_appends_saved_comments_as_notes(clickup_env, monkeypatch):
    ctx = _connected_client(monkeypatch)
    ctx.client.post("/v1/tickets/MER-481/comments", json={
        "author": "sarah", "body": "Watch the rate limit here",
    })
    with patch(
        "app.routes.tickets.clickup_oauth.create_task", return_value=_ok_task(),
    ) as mock_create:
        r = ctx.client.post("/v1/tickets/push-clickup", json={
            "list_id": "L", "tasks": [{
                "task_id": "MER-481", "title": "Login", "description": "Body",
            }],
        })
    assert r.status_code == 200, r.text
    md = mock_create.call_args.kwargs["markdown_description"]
    assert "## Notes" in md
    assert "Watch the rate limit here" in md
    assert "**sarah:**" in md


def test_push_uses_base_fields_when_no_overrides(clickup_env, monkeypatch):
    ctx = _connected_client(monkeypatch)
    with patch(
        "app.routes.tickets.clickup_oauth.create_task", return_value=_ok_task(),
    ) as mock_create:
        r = ctx.client.post("/v1/tickets/push-clickup", json={
            "list_id": "L", "tasks": [{
                "task_id": "NEW-1", "title": "Fresh",
                "description": "Base body only",
                "acceptance_criteria": ["base AC"],
            }],
        })
    assert r.status_code == 200, r.text
    md = mock_create.call_args.kwargs["markdown_description"]
    assert "Base body only" in md
    assert "base AC" in md


# ─────────────────────────── partial failure ───────────────────────────


def test_push_isolates_per_task_failures(clickup_env, monkeypatch):
    """One task failing doesn't abort the batch — the rest still land, and the
    failure is reported with its task_id."""
    ctx = _connected_client(monkeypatch)

    def _create(_token, _list, *, name, **_kw):
        if name == "BOOM":
            raise RuntimeError("ClickUp 500")
        return _ok_task("ok-" + name)

    with patch(
        "app.routes.tickets.clickup_oauth.create_task", side_effect=_create,
    ):
        r = ctx.client.post("/v1/tickets/push-clickup", json={
            "list_id": "L", "tasks": [
                {"task_id": "K1", "title": "GOOD"},
                {"task_id": "K2", "title": "BOOM"},
                {"task_id": "K3", "title": "ALSO-GOOD"},
            ],
        })

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert {c["task_id"] for c in body["created"]} == {"K1", "K3"}
    assert len(body["errors"]) == 1
    assert body["errors"][0]["task_id"] == "K2"
    assert body["errors"][0]["title"] == "BOOM"
    assert "ClickUp 500" in body["errors"][0]["error"]


# ─────────────────────────── auth / connection ───────────────────────────


def test_push_404_when_not_connected(clickup_env, monkeypatch):
    ctx = company_client(monkeypatch)  # no ClickUp connection seeded
    r = ctx.client.post("/v1/tickets/push-clickup", json={
        "list_id": "L", "tasks": [{"task_id": "K1", "title": "T"}],
    })
    assert r.status_code == 404


def test_push_401_when_token_rejected(clickup_env, monkeypatch):
    """An expired/invalid stored token aborts the whole push with 401 so the UI
    prompts a reconnect, rather than marking every task as failed."""
    from app.connectors import clickup_oauth

    ctx = _connected_client(monkeypatch)
    with patch(
        "app.routes.tickets.clickup_oauth.create_task",
        side_effect=clickup_oauth.ClickUpAuthExpiredError("reconnect ClickUp"),
    ):
        r = ctx.client.post("/v1/tickets/push-clickup", json={
            "list_id": "L", "tasks": [{"task_id": "K1", "title": "T"}],
        })
    assert r.status_code == 401
    assert "reconnect" in r.json()["detail"].lower()


def test_push_validates_empty_tasks(clickup_env, monkeypatch):
    ctx = _connected_client(monkeypatch)
    r = ctx.client.post("/v1/tickets/push-clickup", json={
        "list_id": "L", "tasks": [],
    })
    assert r.status_code == 422


# ─────────────────── connector-level: real ClickUp payload ───────────────────


def test_create_task_posts_markdown_and_priority(clickup_env):
    """create_task hits POST /list/{id}/task with markdown_content, the 1-4
    priority, and the RAW token in Authorization (no Bearer prefix)."""
    from app.connectors import clickup_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "ct-1", "url": "https://app.clickup.com/t/ct-1"}

    with patch(
        "app.connectors.clickup_oauth.requests.post", return_value=mock_resp,
    ) as mock_post:
        out = clickup_oauth.create_task(
            "clk-raw-token", "list-7",
            name="Login flow",
            markdown_description="## Acceptance criteria\n- works",
            priority=2,
        )

    assert out == {"id": "ct-1", "url": "https://app.clickup.com/t/ct-1"}
    call = mock_post.call_args
    assert call.args[0] == "https://api.clickup.com/api/v2/list/list-7/task"
    assert call.kwargs["headers"]["Authorization"] == "clk-raw-token"
    sent = call.kwargs["json"]
    assert sent["name"] == "Login flow"
    assert sent["priority"] == 2
    assert sent["markdown_content"] == "## Acceptance criteria\n- works"
    assert "description" not in sent  # markdown takes precedence


def test_create_task_raises_auth_expired_on_401(clickup_env):
    from app.connectors import clickup_oauth

    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 401
    mock_resp.text = "token expired"
    with patch("app.connectors.clickup_oauth.requests.post", return_value=mock_resp):
        with pytest.raises(clickup_oauth.ClickUpAuthExpiredError):
            clickup_oauth.create_task("bad", "L", name="T")


def test_create_task_raises_http_502_on_other_error(clickup_env):
    from app.connectors import clickup_oauth
    from fastapi import HTTPException

    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 500
    mock_resp.text = "server error"
    with patch("app.connectors.clickup_oauth.requests.post", return_value=mock_resp):
        with pytest.raises(HTTPException) as ei:
            clickup_oauth.create_task("tok", "L", name="T")
    assert ei.value.status_code == 502
