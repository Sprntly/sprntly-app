"""Tests for the Slack OAuth v2 connector.

Slack v2 specifics covered:
  - authorize URL points at /oauth/v2/authorize with bot scopes on `scope=`
  - oauth.v2.access response shape: token_json["access_token"] is the bot
    token; team is a sub-dict {id, name}
  - oauth.v2.access returns 200 + {ok: false, error: ...} on errors —
    must surface as 400, not 200
  - bot install — no user scopes requested
  - membership-checked routes (commit 4 multitenancy pattern)
  - workspace_id round-trips through signed state on callback
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from tests._workspace_helpers import seed_connection, workspace_client


def _reload_app_modules():
    for name in (
        "app.config",
        "app.connectors.tokens",
        "app.connectors.slack_oauth",
        "app.routes.connectors",
        "app.main",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


@pytest.fixture
def slack_env(isolated_settings, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("SLACK_CLIENT_ID", "test-slack-client-id")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "test-slack-client-secret")
    monkeypatch.setenv(
        "SLACK_OAUTH_REDIRECT_URI",
        "http://testserver/v1/connectors/slack/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    _reload_app_modules()
    yield


# ─────────────────────────── Module unit tests ───────────────────────────


def test_slack_configured_reflects_env(slack_env, monkeypatch):
    from app.connectors import slack_oauth
    assert slack_oauth.slack_configured() is True

    monkeypatch.setenv("SLACK_CLIENT_ID", "")
    _reload_app_modules()
    from app.connectors import slack_oauth as reloaded
    assert reloaded.slack_configured() is False


def test_sign_verify_oauth_state_round_trip(slack_env):
    from app.connectors import slack_oauth
    token = slack_oauth.sign_oauth_state(workspace_id="ws-x")
    payload = slack_oauth.verify_oauth_state(token)
    assert payload["provider"] == "slack"
    assert payload["workspace_id"] == "ws-x"


def test_verify_oauth_state_rejects_wrong_provider(slack_env):
    from app.connectors import figma_oauth, slack_oauth
    from fastapi import HTTPException

    figma_state = figma_oauth.sign_oauth_state(workspace_id="ws-x")
    with pytest.raises(HTTPException):
        slack_oauth.verify_oauth_state(figma_state)


def test_authorize_url_targets_v2_and_carries_bot_scopes(slack_env):
    from app.connectors import slack_oauth
    url = slack_oauth.authorize_url(state="state-token")
    assert url.startswith("https://slack.com/oauth/v2/authorize")
    assert "client_id=test-slack-client-id" in url
    assert "redirect_uri=" in url
    assert "state=state-token" in url
    # Default bot scopes ride on scope=
    assert "scope=chat" in url  # chat:write is in defaults; URL-encoded as chat%3Awrite


def test_exchange_code_for_token_posts_to_v2_access(slack_env):
    from app.connectors import slack_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "ok": True,
        "access_token": "xoxb-1234",
        "bot_user_id": "U99",
        "team": {"id": "T123", "name": "Acme"},
        "scope": "chat:write,channels:read",
    }
    with patch(
        "app.connectors.slack_oauth.requests.post", return_value=mock_resp
    ) as mock_post:
        out = slack_oauth.exchange_code_for_token("auth-code-xyz")

    assert out["access_token"] == "xoxb-1234"
    assert out["team"]["id"] == "T123"
    call_args = mock_post.call_args
    assert call_args.args[0] == "https://slack.com/api/oauth.v2.access"
    body = call_args.kwargs["data"]
    assert body["code"] == "auth-code-xyz"
    assert body["client_id"] == "test-slack-client-id"
    assert body["client_secret"] == "test-slack-client-secret"


def test_exchange_code_for_token_surfaces_slack_ok_false_as_400(slack_env):
    """Slack returns HTTP 200 with {ok: false, error: ...} on failures.
    We translate that into a 400 so the UI shows a real error, not a
    "success" with garbage data."""
    from app.connectors import slack_oauth
    from fastapi import HTTPException

    mock_resp = MagicMock()
    mock_resp.ok = True  # HTTP 200
    mock_resp.json.return_value = {"ok": False, "error": "invalid_code"}
    with patch("app.connectors.slack_oauth.requests.post", return_value=mock_resp):
        with pytest.raises(HTTPException) as exc:
            slack_oauth.exchange_code_for_token("expired-code")
    assert exc.value.status_code == 400


def test_token_payload_to_store_keeps_only_what_we_need(slack_env):
    from app.connectors import slack_oauth
    import json as _json

    blob = slack_oauth.token_payload_to_store(
        {
            "ok": True,
            "access_token": "xoxb-1234",
            "bot_user_id": "U99",
            "team": {"id": "T123", "name": "Acme"},
            "scope": "chat:write,channels:read",
            # extra fields we should NOT carry around:
            "authed_user": {"id": "U-installer", "access_token": "xoxp-secret"},
            "app_id": "A99",
        }
    )
    stored = _json.loads(blob)
    assert stored["access_token"] == "xoxb-1234"
    assert stored["bot_user_id"] == "U99"
    assert stored["team_id"] == "T123"
    assert stored["team_name"] == "Acme"
    assert stored["scope"] == "chat:write,channels:read"
    assert "obtained_at" in stored
    # Installer user-token must not be persisted.
    assert "authed_user" not in stored


def test_fetch_team_info_uses_bearer_auth(slack_env):
    from app.connectors import slack_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "ok": True,
        "team": {"id": "T123", "name": "Acme", "domain": "acme"},
    }
    with patch(
        "app.connectors.slack_oauth.requests.get", return_value=mock_resp
    ) as mock_get:
        team = slack_oauth.fetch_team_info("xoxb-1234")

    assert team["name"] == "Acme"
    call_args = mock_get.call_args
    assert call_args.args[0] == "https://slack.com/api/team.info"
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer xoxb-1234"


# ─────────────────────────── Route tests ───────────────────────────


def test_start_oauth_slack_returns_slack_url(slack_env, monkeypatch):
    ctx = workspace_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/slack/start-oauth",
        params={"workspace_id": ctx.workspace_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "authorize_url" in body
    assert body["authorize_url"].startswith("https://slack.com/oauth/v2/authorize")


def test_start_oauth_slack_500_when_not_configured(isolated_settings, monkeypatch):
    monkeypatch.setenv("SLACK_CLIENT_ID", "")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "")
    monkeypatch.setenv("SLACK_OAUTH_REDIRECT_URI", "")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    _reload_app_modules()
    ctx = workspace_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/slack/start-oauth",
        params={"workspace_id": ctx.workspace_id},
    )
    assert r.status_code == 500


def test_callback_stores_connection_with_team_name_label(slack_env, monkeypatch):
    ctx = workspace_client(monkeypatch)
    from app.connectors import slack_oauth
    state = slack_oauth.sign_oauth_state(workspace_id=ctx.workspace_id)

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "ok": True,
        "access_token": "xoxb-real",
        "bot_user_id": "U99",
        "team": {"id": "T123", "name": "Meridian"},
        "scope": "chat:write,channels:read",
    }
    with patch("app.connectors.slack_oauth.requests.post", return_value=mock_resp):
        r = ctx.client.get(
            "/v1/connectors/slack/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )

    assert r.status_code == 307
    assert "connected=slack" in r.headers["location"]

    listed = ctx.client.get(
        "/v1/connectors", params={"workspace_id": ctx.workspace_id}
    ).json()
    rows = [c for c in listed["connections"] if c["provider"] == "slack"]
    assert len(rows) == 1
    assert rows[0]["account_label"] == "Meridian"
    assert "token_json_encrypted" not in rows[0]
    assert "chat:write" in rows[0]["scopes"]


def test_callback_rejects_wrong_state(slack_env, monkeypatch):
    ctx = workspace_client(monkeypatch)
    from app.connectors import figma_oauth
    # Figma-signed state must not be accepted by the Slack callback.
    wrong_state = figma_oauth.sign_oauth_state(workspace_id=ctx.workspace_id)
    r = ctx.client.get(
        "/v1/connectors/slack/callback",
        params={"code": "x", "state": wrong_state},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_callback_400_when_slack_returns_ok_false(slack_env, monkeypatch):
    ctx = workspace_client(monkeypatch)
    from app.connectors import slack_oauth
    state = slack_oauth.sign_oauth_state(workspace_id=ctx.workspace_id)

    mock_resp = MagicMock()
    mock_resp.ok = True  # HTTP 200 with ok=false
    mock_resp.json.return_value = {"ok": False, "error": "invalid_code"}
    with patch("app.connectors.slack_oauth.requests.post", return_value=mock_resp):
        r = ctx.client.get(
            "/v1/connectors/slack/callback",
            params={"code": "x", "state": state},
            follow_redirects=False,
        )
    assert r.status_code == 400


def test_delete_slack_disconnects(slack_env, monkeypatch):
    ctx = workspace_client(monkeypatch)
    seed_connection(
        workspace_id=ctx.workspace_id,
        provider="slack",
        token_blob={"access_token": "xoxb-real", "team_id": "T1"},
        label="Meridian",
    )

    r = ctx.client.delete(
        "/v1/connectors/slack", params={"workspace_id": ctx.workspace_id}
    )
    assert r.status_code == 200
    listed = ctx.client.get(
        "/v1/connectors", params={"workspace_id": ctx.workspace_id}
    ).json()
    assert not any(c["provider"] == "slack" for c in listed["connections"])


def test_delete_slack_404_when_not_connected(slack_env, monkeypatch):
    ctx = workspace_client(monkeypatch)
    r = ctx.client.delete(
        "/v1/connectors/slack", params={"workspace_id": ctx.workspace_id}
    )
    assert r.status_code == 404


def test_disconnect_requires_membership(slack_env, monkeypatch):
    """403 when caller isn't on the target workspace's roster."""
    ctx = workspace_client(monkeypatch)
    from tests._workspace_helpers import seed_workspace
    other_ws = seed_workspace(user_id="someone-else", slug="globex")
    r = ctx.client.delete(
        "/v1/connectors/slack", params={"workspace_id": other_ws}
    )
    assert r.status_code == 403


# ─────────────────────── list_channels helper ───────────────────────


def test_list_channels_posts_with_bearer_and_correct_params(slack_env):
    from app.connectors import slack_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "ok": True,
        "channels": [
            {"id": "C1", "name": "general", "is_private": False, "is_member": True},
            {"id": "C2", "name": "design", "is_private": True, "is_member": True},
        ],
    }
    with patch(
        "app.connectors.slack_oauth.requests.get", return_value=mock_resp
    ) as mock_get:
        channels = slack_oauth.list_channels("xoxb-1234")

    assert len(channels) == 2
    assert channels[0] == {
        "id": "C1",
        "name": "general",
        "is_private": False,
        "is_member": True,
        "is_archived": False,
    }
    call_args = mock_get.call_args
    assert call_args.args[0] == "https://slack.com/api/conversations.list"
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer xoxb-1234"
    params = call_args.kwargs["params"]
    # public_channel only — see slack_oauth.list_channels for the
    # rationale (private channels would require the groups:read scope
    # we deliberately don't request).
    assert params["types"] == "public_channel"
    assert params["exclude_archived"] == "true"


def test_list_channels_returns_empty_on_ok_false(slack_env):
    """Slack returns 200+ok:false on token issues — treat as empty list,
    not an exception, so the picker can render 'no channels' instead of
    blowing up the route."""
    from app.connectors import slack_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"ok": False, "error": "invalid_auth"}
    with patch("app.connectors.slack_oauth.requests.get", return_value=mock_resp):
        assert slack_oauth.list_channels("stale-token") == []


# ─────────────────────── post_message helper ───────────────────────


def test_post_message_posts_to_chat_postmessage_with_bearer(slack_env):
    from app.connectors import slack_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "ok": True,
        "ts": "1717010000.000100",
        "channel": "C1",
        "message": {"text": "hello"},
    }
    with patch(
        "app.connectors.slack_oauth.requests.post", return_value=mock_resp
    ) as mock_post:
        out = slack_oauth.post_message("xoxb-1234", channel="C1", text="hello")

    assert out["ok"] is True
    assert out["ts"] == "1717010000.000100"
    call_args = mock_post.call_args
    assert call_args.args[0] == "https://slack.com/api/chat.postMessage"
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer xoxb-1234"
    body = call_args.kwargs["json"]
    assert body == {"channel": "C1", "text": "hello"}


def test_post_message_forwards_blocks_when_provided(slack_env):
    from app.connectors import slack_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"ok": True, "ts": "1.0", "channel": "C1"}
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "*hi*"}}]
    with patch(
        "app.connectors.slack_oauth.requests.post", return_value=mock_resp
    ) as mock_post:
        slack_oauth.post_message(
            "xoxb-1234", channel="C1", text="hi", blocks=blocks
        )
    assert mock_post.call_args.kwargs["json"]["blocks"] == blocks


def test_post_message_raises_400_on_ok_false(slack_env):
    """ok:false (e.g. channel_not_found, not_in_channel) is a real
    failure the caller needs to surface. Don't pretend it succeeded."""
    from app.connectors import slack_oauth
    from fastapi import HTTPException

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"ok": False, "error": "channel_not_found"}
    with patch("app.connectors.slack_oauth.requests.post", return_value=mock_resp):
        with pytest.raises(HTTPException) as exc:
            slack_oauth.post_message(
                "xoxb-1234", channel="C-missing", text="hi"
            )
    assert exc.value.status_code == 400
    assert "channel_not_found" in exc.value.detail


# ─────────────────────── /slack/channels route ───────────────────────


def test_channels_route_lists_channels(slack_env, monkeypatch):
    ctx = workspace_client(monkeypatch)
    seed_connection(
        workspace_id=ctx.workspace_id,
        provider="slack",
        token_blob={"access_token": "xoxb-real"},
        label="Meridian",
    )
    with patch(
        "app.routes.connectors.slack_oauth.list_channels",
        return_value=[
            {
                "id": "C1",
                "name": "general",
                "is_private": False,
                "is_member": True,
                "is_archived": False,
            },
        ],
    ) as mock_list:
        r = ctx.client.get(
            "/v1/connectors/slack/channels",
            params={"workspace_id": ctx.workspace_id},
        )
    assert r.status_code == 200
    assert r.json() == {
        "channels": [
            {
                "id": "C1",
                "name": "general",
                "is_private": False,
                "is_member": True,
                "is_archived": False,
            },
        ],
    }
    mock_list.assert_called_once_with("xoxb-real")


def test_channels_route_404_when_not_connected(slack_env, monkeypatch):
    ctx = workspace_client(monkeypatch)
    r = ctx.client.get(
        "/v1/connectors/slack/channels",
        params={"workspace_id": ctx.workspace_id},
    )
    assert r.status_code == 404


def test_channels_route_requires_membership(slack_env, monkeypatch):
    ctx = workspace_client(monkeypatch)
    from tests._workspace_helpers import seed_workspace
    other_ws = seed_workspace(user_id="someone-else", slug="globex")
    r = ctx.client.get(
        "/v1/connectors/slack/channels",
        params={"workspace_id": other_ws},
    )
    assert r.status_code == 403


# ─────────────────────── /slack/config route ───────────────────────


def test_config_route_persists_selected_channel(slack_env, monkeypatch):
    ctx = workspace_client(monkeypatch)
    seed_connection(
        workspace_id=ctx.workspace_id,
        provider="slack",
        token_blob={"access_token": "xoxb-real"},
        label="Meridian",
    )
    r = ctx.client.post(
        "/v1/connectors/slack/config",
        params={"workspace_id": ctx.workspace_id},
        json={"channel_id": "C123", "channel_name": "product-launches"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["config"]["channel_id"] == "C123"
    assert body["config"]["channel_name"] == "product-launches"

    # And persisted on the connection row.
    listed = ctx.client.get(
        "/v1/connectors", params={"workspace_id": ctx.workspace_id}
    ).json()
    slack_row = next(c for c in listed["connections"] if c["provider"] == "slack")
    assert slack_row["config"]["channel_id"] == "C123"
    assert slack_row["config"]["channel_name"] == "product-launches"


def test_config_route_rejects_empty_channel_id(slack_env, monkeypatch):
    ctx = workspace_client(monkeypatch)
    seed_connection(
        workspace_id=ctx.workspace_id,
        provider="slack",
        token_blob={"access_token": "xoxb-real"},
    )
    r = ctx.client.post(
        "/v1/connectors/slack/config",
        params={"workspace_id": ctx.workspace_id},
        json={"channel_id": "", "channel_name": "x"},
    )
    assert r.status_code == 422


def test_config_route_404_when_not_connected(slack_env, monkeypatch):
    ctx = workspace_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/slack/config",
        params={"workspace_id": ctx.workspace_id},
        json={"channel_id": "C123"},
    )
    assert r.status_code == 404


def test_config_route_does_not_leak_across_workspaces(slack_env, monkeypatch):
    """Saving config in workspace A must not be visible in workspace B,
    even if both have Slack connected."""
    ctx = workspace_client(monkeypatch)
    from tests._workspace_helpers import seed_workspace

    other_ws = seed_workspace(user_id=ctx.user_id, slug="globex")
    seed_connection(
        workspace_id=ctx.workspace_id,
        provider="slack",
        token_blob={"access_token": "xoxb-A"},
    )
    seed_connection(
        workspace_id=other_ws,
        provider="slack",
        token_blob={"access_token": "xoxb-B"},
    )

    ctx.client.post(
        "/v1/connectors/slack/config",
        params={"workspace_id": ctx.workspace_id},
        json={"channel_id": "C-A", "channel_name": "a-channel"},
    )

    a_listed = ctx.client.get(
        "/v1/connectors", params={"workspace_id": ctx.workspace_id}
    ).json()
    b_listed = ctx.client.get(
        "/v1/connectors", params={"workspace_id": other_ws}
    ).json()

    a_row = next(c for c in a_listed["connections"] if c["provider"] == "slack")
    b_row = next(c for c in b_listed["connections"] if c["provider"] == "slack")
    assert a_row["config"].get("channel_id") == "C-A"
    assert "channel_id" not in (b_row["config"] or {})


def test_test_endpoint_dispatches_to_team_info(slack_env, monkeypatch):
    """The generic POST /{provider}/test endpoint routes Slack to
    slack_oauth.fetch_team_info — the canonical "is the bot token still
    valid?" check."""
    ctx = workspace_client(monkeypatch)
    seed_connection(
        workspace_id=ctx.workspace_id,
        provider="slack",
        token_blob={"access_token": "xoxb-real"},
        label="Meridian",
    )
    with patch(
        "app.routes.connectors.slack_oauth.fetch_team_info",
        return_value={"id": "T123", "name": "Meridian", "domain": "meridian"},
    ) as mock_fetch:
        r = ctx.client.post(
            "/v1/connectors/slack/test",
            params={"workspace_id": ctx.workspace_id},
        )
    assert r.status_code == 200
    assert "Meridian" in r.json()["account_label"]
    mock_fetch.assert_called_once_with("xoxb-real")
