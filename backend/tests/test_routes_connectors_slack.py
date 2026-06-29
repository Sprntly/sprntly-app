"""Tests for the Slack OAuth v2 connector.

Slack v2 specifics covered:
  - authorize URL points at /oauth/v2/authorize with bot scopes on `scope=`
  - oauth.v2.access response shape: token_json["access_token"] is the bot
    token; team is a sub-dict {id, name}
  - oauth.v2.access returns 200 + {ok: false, error: ...} on errors —
    must surface as 400, not 200
  - bot install — no user scopes requested
  - membership-checked routes (commit 4 multitenancy pattern)
  - company_id round-trips through signed state on callback
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from tests._company_helpers import seed_connection, company_client


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
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-signing-secret")
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
    token = slack_oauth.sign_oauth_state(company_id="ws-x", user_id="u-1")
    payload = slack_oauth.verify_oauth_state(token)
    assert payload["provider"] == "slack"
    assert payload["company_id"] == "ws-x"
    assert payload["user_id"] == "u-1"


def test_verify_oauth_state_rejects_wrong_provider(slack_env):
    from app.connectors import figma_oauth, slack_oauth
    from fastapi import HTTPException

    figma_state = figma_oauth.sign_oauth_state(company_id="ws-x")
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


def test_token_payload_to_store_keeps_bot_and_user_tokens(slack_env):
    from app.connectors import slack_oauth
    import json as _json

    blob = slack_oauth.token_payload_to_store(
        {
            "ok": True,
            "access_token": "xoxb-1234",
            "bot_user_id": "U99",
            "team": {"id": "T123", "name": "Acme"},
            "scope": "chat:write,channels:read",
            # Two-way: the installer's user token IS persisted (read-as-user).
            "authed_user": {
                "id": "U-installer",
                "access_token": "xoxp-secret",
                "scope": "channels:history,search:read",
            },
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
    # The installing user is the DM target + owner of the user token.
    assert stored["authed_user_id"] == "U-installer"
    assert stored["user_access_token"] == "xoxp-secret"
    assert stored["user_scope"] == "channels:history,search:read"
    # The nested dict itself is flattened, not stored verbatim.
    assert "authed_user" not in stored


def test_token_payload_to_store_bot_only_has_no_user_token(slack_env):
    """A bot-only install (no user_scope granted) carries no user token."""
    from app.connectors import slack_oauth
    import json as _json

    blob = slack_oauth.token_payload_to_store(
        {
            "ok": True,
            "access_token": "xoxb-1234",
            "team": {"id": "T123", "name": "Acme"},
            "scope": "chat:write",
            "authed_user": {"id": "U-installer"},  # no access_token
        }
    )
    stored = _json.loads(blob)
    assert stored["user_access_token"] is None
    assert stored["authed_user_id"] == "U-installer"


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
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/slack/start-oauth",
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
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/slack/start-oauth",
    )
    assert r.status_code == 500


def test_callback_stores_connection_with_team_name_label(slack_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import slack_oauth
    state = slack_oauth.sign_oauth_state(
        company_id=ctx.company_id, user_id=ctx.user_id
    )

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
        "/v1/connectors"
    ).json()
    rows = [c for c in listed["connections"] if c["provider"] == "slack"]
    assert len(rows) == 1
    assert rows[0]["account_label"] == "Meridian"
    assert "token_json_encrypted" not in rows[0]
    assert "chat:write" in rows[0]["scopes"]


def test_callback_rejects_wrong_state(slack_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import figma_oauth
    # Figma-signed state must not be accepted by the Slack callback.
    wrong_state = figma_oauth.sign_oauth_state(company_id=ctx.company_id)
    r = ctx.client.get(
        "/v1/connectors/slack/callback",
        params={"code": "x", "state": wrong_state},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_callback_400_when_slack_returns_ok_false(slack_env, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.connectors import slack_oauth
    state = slack_oauth.sign_oauth_state(
        company_id=ctx.company_id, user_id=ctx.user_id
    )

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
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        provider="slack",
        token_blob={"access_token": "xoxb-real", "team_id": "T1"},
        label="Meridian",
    )

    r = ctx.client.delete(
        "/v1/connectors/slack"
    )
    assert r.status_code == 200
    listed = ctx.client.get(
        "/v1/connectors"
    ).json()
    assert not any(c["provider"] == "slack" for c in listed["connections"])


def test_delete_slack_404_when_not_connected(slack_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.delete(
        "/v1/connectors/slack"
    )
    assert r.status_code == 404


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
    # Public + private — the picker offers both; the bot still can't self-join
    # private channels, but listing them lets a user pick one they've invited
    # the bot to. Requires the groups:read bot scope.
    assert params["types"] == "public_channel,private_channel"
    assert params["exclude_archived"] == "true"


def test_list_channels_paginates_until_cursor_exhausted(slack_env):
    """conversations.list is cursor-paginated; a big workspace returns
    several pages. list_channels must follow next_cursor or it silently
    truncates the picker to the first page."""
    from app.connectors import slack_oauth

    page1 = MagicMock()
    page1.ok = True
    page1.json.return_value = {
        "ok": True,
        "channels": [
            {"id": "C1", "name": "general", "is_private": False, "is_member": True},
        ],
        "response_metadata": {"next_cursor": "CURSOR2"},
    }
    page2 = MagicMock()
    page2.ok = True
    page2.json.return_value = {
        "ok": True,
        "channels": [
            {"id": "C2", "name": "random", "is_private": False, "is_member": False},
        ],
        "response_metadata": {"next_cursor": ""},
    }
    with patch(
        "app.connectors.slack_oauth.requests.get", side_effect=[page1, page2]
    ) as mock_get:
        channels = slack_oauth.list_channels("xoxb-1234")

    assert [c["id"] for c in channels] == ["C1", "C2"]
    assert mock_get.call_count == 2
    # Second call must forward the cursor from page 1.
    assert mock_get.call_args_list[1].kwargs["params"]["cursor"] == "CURSOR2"


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


def test_post_message_auto_joins_and_retries_on_not_in_channel(slack_env):
    """The most common delivery failure: the bot was never invited to the
    target public channel, so the first post fails not_in_channel. With
    auto_join, post_message self-joins via conversations.join and retries —
    the brief lands without anyone manually inviting the bot."""
    from app.connectors import slack_oauth

    first = MagicMock()
    first.ok = True
    first.json.return_value = {"ok": False, "error": "not_in_channel"}
    retry = MagicMock()
    retry.ok = True
    retry.json.return_value = {"ok": True, "ts": "1.0", "channel": "C1"}
    join = MagicMock()
    join.ok = True
    join.json.return_value = {"ok": True, "channel": {"id": "C1"}}

    with patch(
        "app.connectors.slack_oauth.requests.post",
        side_effect=[first, join, retry],
    ) as mock_post:
        out = slack_oauth.post_message(
            "xoxb-1234", channel="C1", text="hi", auto_join=True
        )

    assert out["ok"] is True
    # post → join → post (3 calls); middle call is conversations.join.
    assert mock_post.call_count == 3
    assert mock_post.call_args_list[1].args[0] == (
        "https://slack.com/api/conversations.join"
    )


def test_post_message_not_in_channel_raises_actionable_error(slack_env):
    """A private channel can't be self-joined, so the retry still fails.
    The error must tell the user to invite the bot, not leak the raw code."""
    from app.connectors import slack_oauth
    from fastapi import HTTPException

    post_fail = MagicMock()
    post_fail.ok = True
    post_fail.json.return_value = {"ok": False, "error": "not_in_channel"}
    join_fail = MagicMock()
    join_fail.ok = True
    join_fail.json.return_value = {
        "ok": False,
        "error": "method_not_allowed_for_channel_type",
    }
    with patch(
        "app.connectors.slack_oauth.requests.post",
        side_effect=[post_fail, join_fail, post_fail],
    ):
        with pytest.raises(HTTPException) as exc:
            slack_oauth.post_message(
                "xoxb-1234", channel="G-private", text="hi", auto_join=True
            )
    assert exc.value.status_code == 400
    assert "invite" in exc.value.detail.lower()


# ─────────────────────── post_to_target helper ───────────────────────


def test_post_to_target_channel_auto_joins(slack_env):
    """A channel target posts to config.channel_id and auto-joins (so the
    bot lands in a public channel it was never invited to)."""
    from app.connectors import slack_oauth

    ok = MagicMock()
    ok.ok = True
    ok.json.return_value = {"ok": True, "ts": "1.0", "channel": "C1"}
    with patch(
        "app.connectors.slack_oauth.requests.post", return_value=ok
    ) as mock_post:
        out = slack_oauth.post_to_target(
            "xoxb-1234",
            config={"target_type": "channel", "channel_id": "C1"},
            authed_user_id="U1",
            text="hi",
        )
    assert out["ok"] is True
    # Single chat.postMessage to the channel (already a member here).
    assert mock_post.call_args.args[0] == "https://slack.com/api/chat.postMessage"
    assert mock_post.call_args.kwargs["json"]["channel"] == "C1"


def test_post_to_target_dm_opens_dm_and_posts(slack_env):
    """A dm target opens a DM with the installing user (authed_user_id) and
    posts to that DM channel — no channel_id needed."""
    from app.connectors import slack_oauth

    opened = MagicMock()
    opened.ok = True
    opened.json.return_value = {"ok": True, "channel": {"id": "D9"}}
    posted = MagicMock()
    posted.ok = True
    posted.json.return_value = {"ok": True, "ts": "1.0", "channel": "D9"}
    with patch(
        "app.connectors.slack_oauth.requests.post", side_effect=[opened, posted]
    ) as mock_post:
        out = slack_oauth.post_to_target(
            "xoxb-1234",
            config={"target_type": "dm"},
            authed_user_id="U1",
            text="hi",
        )
    assert out["ok"] is True
    # open DM → post to the returned DM channel id.
    assert mock_post.call_args_list[0].args[0] == (
        "https://slack.com/api/conversations.open"
    )
    assert mock_post.call_args_list[0].kwargs["json"]["users"] == "U1"
    assert mock_post.call_args_list[1].kwargs["json"]["channel"] == "D9"


def test_post_to_target_dm_without_user_raises(slack_env):
    """A dm target with no authed_user_id can't resolve a recipient."""
    from app.connectors import slack_oauth
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        slack_oauth.post_to_target(
            "xoxb-1234", config={"target_type": "dm"}, authed_user_id=None, text="hi"
        )
    assert exc.value.status_code == 400


def test_post_to_target_defaults_to_channel_when_type_absent(slack_env):
    """Legacy configs (no target_type) keep posting to their channel."""
    from app.connectors import slack_oauth

    ok = MagicMock()
    ok.ok = True
    ok.json.return_value = {"ok": True, "ts": "1.0", "channel": "C1"}
    with patch("app.connectors.slack_oauth.requests.post", return_value=ok) as m:
        slack_oauth.post_to_target(
            "xoxb-1234", config={"channel_id": "C1"}, authed_user_id="U1", text="hi"
        )
    assert m.call_args.kwargs["json"]["channel"] == "C1"


# ─────────────────────── /slack/channels route ───────────────────────


def test_channels_route_lists_channels(slack_env, monkeypatch):
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
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
    ctx = company_client(monkeypatch)
    r = ctx.client.get(
        "/v1/connectors/slack/channels",
    )
    assert r.status_code == 404


# ─────────────────────── /slack/config route ───────────────────────


def test_config_route_persists_selected_channel(slack_env, monkeypatch):
    from app.connectors import slack_oauth

    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        provider="slack",
        token_blob={"access_token": "xoxb-real"},
        label="Meridian",
    )
    join_calls: list = []
    monkeypatch.setattr(
        slack_oauth, "join_channel",
        lambda tok, channel_id: join_calls.append(channel_id) or True)

    r = ctx.client.post(
        "/v1/connectors/slack/config",
        json={"channel_id": "C123", "channel_name": "product-launches"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["config"]["channel_id"] == "C123"
    assert body["config"]["channel_name"] == "product-launches"
    # The chosen public channel is auto-joined so the first brief lands.
    assert join_calls == ["C123"]
    assert body["joined"] is True

    # And persisted on the connection row.
    listed = ctx.client.get(
        "/v1/connectors"
    ).json()
    slack_row = next(c for c in listed["connections"] if c["provider"] == "slack")
    assert slack_row["config"]["channel_id"] == "C123"
    assert slack_row["config"]["channel_name"] == "product-launches"


def test_config_route_persists_dm_target_without_channel(slack_env, monkeypatch):
    """A 'dm' target saves with no channel and never attempts a join."""
    from app.connectors import slack_oauth

    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        provider="slack",
        token_blob={"access_token": "xoxb-real", "authed_user_id": "U1"},
    )
    join_calls: list = []
    monkeypatch.setattr(
        slack_oauth, "join_channel",
        lambda *a, **k: join_calls.append(a) or True)

    r = ctx.client.post(
        "/v1/connectors/slack/config", json={"target_type": "dm"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["config"]["target_type"] == "dm"
    assert body["joined"] is False
    # DM target must not try to join a channel.
    assert join_calls == []


def test_config_route_rejects_empty_channel_id(slack_env, monkeypatch):
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        provider="slack",
        token_blob={"access_token": "xoxb-real"},
    )
    r = ctx.client.post(
        "/v1/connectors/slack/config",
        json={"channel_id": "", "channel_name": "x"},
    )
    assert r.status_code == 422


def test_config_route_404_when_not_connected(slack_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/connectors/slack/config",
        json={"channel_id": "C123"},
    )
    assert r.status_code == 404


def test_test_endpoint_dispatches_to_team_info(slack_env, monkeypatch):
    """The generic POST /{provider}/test endpoint routes Slack to
    slack_oauth.fetch_team_info — the canonical "is the bot token still
    valid?" check."""
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
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
        )
    assert r.status_code == 200
    assert "Meridian" in r.json()["account_label"]
    mock_fetch.assert_called_once_with("xoxb-real")


# ───────────── Events API: signature verification, revoke, App Home ─────────────


def _sign(secret: str, ts: str, body: bytes) -> str:
    import hashlib
    import hmac
    base = b"v0:" + ts.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def test_verify_signature_round_trip(slack_env):
    import time as _t
    from app.connectors import slack_oauth
    ts = str(int(_t.time()))
    body = b'{"type":"url_verification","challenge":"abc"}'
    sig = _sign("test-signing-secret", ts, body)
    assert slack_oauth.verify_signature(ts, body, sig) is True
    # Tampered body → reject.
    assert slack_oauth.verify_signature(ts, body + b"x", sig) is False
    # Stale timestamp (>5 min) → reject (replay guard).
    old = str(int(_t.time()) - 6 * 60)
    assert slack_oauth.verify_signature(old, body, _sign("test-signing-secret", old, body)) is False


def test_revoke_token_calls_auth_revoke(slack_env):
    from app.connectors import slack_oauth
    with patch("app.connectors.slack_oauth.requests.post") as mock_post:
        mock_post.return_value = MagicMock(ok=True, json=lambda: {"ok": True})
        assert slack_oauth.revoke_token("xoxb-x") is True
        assert mock_post.call_args[0][0] == slack_oauth.SLACK_AUTH_REVOKE_URL


def test_app_home_view_is_home_with_blocks(slack_env):
    from app.connectors import slack_oauth
    view = slack_oauth.app_home_view()
    assert view["type"] == "home"
    assert len(view["blocks"]) > 0


def test_slack_events_url_verification_with_valid_signature(slack_env, monkeypatch):
    import time as _t
    ctx = company_client(monkeypatch)
    body = b'{"type":"url_verification","challenge":"ch-123"}'
    ts = str(int(_t.time()))
    headers = {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": _sign("test-signing-secret", ts, body),
        "Content-Type": "application/json",
    }
    r = ctx.client.post("/v1/connectors/slack/events", content=body, headers=headers)
    assert r.status_code == 200
    assert r.json()["challenge"] == "ch-123"


def test_slack_events_rejects_bad_signature(slack_env, monkeypatch):
    import time as _t
    ctx = company_client(monkeypatch)
    body = b'{"type":"url_verification","challenge":"x"}'
    headers = {
        "X-Slack-Request-Timestamp": str(int(_t.time())),
        "X-Slack-Signature": "v0=deadbeef",
        "Content-Type": "application/json",
    }
    r = ctx.client.post("/v1/connectors/slack/events", content=body, headers=headers)
    assert r.status_code == 401


# ─────────── two-way: user-token grant + read-as-user helpers ───────────


def test_authorize_url_includes_user_scope_by_default(slack_env):
    """Two-way install: the consent screen requests user scopes so Slack
    returns a user token alongside the bot token."""
    from app.connectors import slack_oauth

    url = slack_oauth.authorize_url(state="s")
    # user scopes ride on user_scope=, encoded (search:read -> search%3Aread)
    assert "user_scope=" in url
    assert "search" in url


def test_authorize_url_omits_user_scope_when_empty(slack_env):
    """Passing user_scopes='' suppresses the user-token grant (bot-only)."""
    from app.connectors import slack_oauth

    url = slack_oauth.authorize_url(state="s", user_scopes="")
    assert "user_scope=" not in url


def test_open_dm_returns_channel_id(slack_env):
    from app.connectors import slack_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"ok": True, "channel": {"id": "D123"}}
    with patch(
        "app.connectors.slack_oauth.requests.post", return_value=mock_resp
    ) as mock_post:
        channel = slack_oauth.open_dm("xoxb-1234", "U-target")

    assert channel == "D123"
    call_args = mock_post.call_args
    assert call_args.args[0] == "https://slack.com/api/conversations.open"
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer xoxb-1234"
    assert call_args.kwargs["json"] == {"users": "U-target"}


def test_open_dm_raises_400_on_ok_false(slack_env):
    from app.connectors import slack_oauth
    from fastapi import HTTPException

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"ok": False, "error": "user_not_found"}
    with patch("app.connectors.slack_oauth.requests.post", return_value=mock_resp):
        with pytest.raises(HTTPException) as exc:
            slack_oauth.open_dm("xoxb-1234", "U-missing")
    assert exc.value.status_code == 400
    assert "user_not_found" in exc.value.detail


def test_post_dm_to_user_opens_then_posts(slack_env):
    """post_dm_to_user resolves the DM channel, then posts to it."""
    from app.connectors import slack_oauth

    open_resp = MagicMock()
    open_resp.ok = True
    open_resp.json.return_value = {"ok": True, "channel": {"id": "D9"}}
    post_resp = MagicMock()
    post_resp.ok = True
    post_resp.json.return_value = {"ok": True, "ts": "1.2", "channel": "D9"}

    with patch(
        "app.connectors.slack_oauth.requests.post",
        side_effect=[open_resp, post_resp],
    ) as mock_post:
        out = slack_oauth.post_dm_to_user(
            "xoxb-1234", slack_user_id="U-target", text="hi there"
        )

    assert out["ts"] == "1.2"
    # Two calls: conversations.open then chat.postMessage to the DM channel.
    assert mock_post.call_args_list[0].args[0].endswith("conversations.open")
    second = mock_post.call_args_list[1]
    assert second.args[0].endswith("chat.postMessage")
    assert second.kwargs["json"] == {"channel": "D9", "text": "hi there"}


def test_fetch_conversation_history_trims_shape(slack_env):
    from app.connectors import slack_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "ok": True,
        "messages": [{"ts": "1.0", "text": "a"}, {"ts": "2.0", "text": "b"}],
        "has_more": True,
        "response_metadata": {"next_cursor": "CURSOR2"},
    }
    with patch(
        "app.connectors.slack_oauth.requests.get", return_value=mock_resp
    ) as mock_get:
        out = slack_oauth.fetch_conversation_history(
            "xoxp-user", channel="C1", limit=2
        )

    assert out["messages"][1]["text"] == "b"
    assert out["has_more"] is True
    assert out["next_cursor"] == "CURSOR2"
    call_args = mock_get.call_args
    assert call_args.args[0] == "https://slack.com/api/conversations.history"
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer xoxp-user"
    assert call_args.kwargs["params"]["channel"] == "C1"


def test_fetch_conversation_history_raises_400_on_ok_false(slack_env):
    from app.connectors import slack_oauth
    from fastapi import HTTPException

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"ok": False, "error": "not_in_channel"}
    with patch("app.connectors.slack_oauth.requests.get", return_value=mock_resp):
        with pytest.raises(HTTPException) as exc:
            slack_oauth.fetch_conversation_history("xoxp-user", channel="C1")
    assert exc.value.status_code == 400
    assert "not_in_channel" in exc.value.detail


def test_search_messages_uses_user_token(slack_env):
    from app.connectors import slack_oauth

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "ok": True,
        "messages": {
            "total": 1,
            "matches": [{"ts": "1.0", "text": "found it"}],
        },
    }
    with patch(
        "app.connectors.slack_oauth.requests.get", return_value=mock_resp
    ) as mock_get:
        out = slack_oauth.search_messages("xoxp-user", query="roadmap")

    assert out["total"] == 1
    assert out["matches"][0]["text"] == "found it"
    call_args = mock_get.call_args
    assert call_args.args[0] == "https://slack.com/api/search.messages"
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer xoxp-user"
    assert call_args.kwargs["params"]["query"] == "roadmap"


# ─────────────────── /slack/dm, /history, /search routes ───────────────────


def test_dm_route_sends_dm_to_installing_user(slack_env, monkeypatch):
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        provider="slack",
        token_blob={"access_token": "xoxb-real", "authed_user_id": "U-me"},
        label="Meridian",
    )
    with patch(
        "app.routes.connectors.slack_oauth.post_dm_to_user",
        return_value={"ok": True, "ts": "1.5", "channel": "D9"},
    ) as mock_dm:
        r = ctx.client.post("/v1/connectors/slack/dm", json={"text": "ping"})

    assert r.status_code == 200, r.text
    assert r.json()["ts"] == "1.5"
    mock_dm.assert_called_once_with(
        "xoxb-real", slack_user_id="U-me", text="ping"
    )


def test_dm_route_rejects_empty_text(slack_env, monkeypatch):
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        provider="slack",
        token_blob={"access_token": "xoxb-real", "authed_user_id": "U-me"},
    )
    r = ctx.client.post("/v1/connectors/slack/dm", json={"text": "  "})
    assert r.status_code == 422


def test_history_route_uses_user_token(slack_env, monkeypatch):
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        provider="slack",
        token_blob={"access_token": "xoxb-real", "user_access_token": "xoxp-me"},
        label="Meridian",
    )
    with patch(
        "app.routes.connectors.slack_oauth.fetch_conversation_history",
        return_value={"messages": [{"ts": "1.0"}], "has_more": False, "next_cursor": ""},
    ) as mock_hist:
        r = ctx.client.get("/v1/connectors/slack/history?channel=C1&limit=5")

    assert r.status_code == 200, r.text
    assert r.json()["messages"] == [{"ts": "1.0"}]
    assert mock_hist.call_args.args[0] == "xoxp-me"
    assert mock_hist.call_args.kwargs["channel"] == "C1"
    assert mock_hist.call_args.kwargs["limit"] == 5


def test_history_route_400_when_no_user_token(slack_env, monkeypatch):
    """A bot-only install can't read as the user — 400, reconnect needed."""
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        provider="slack",
        token_blob={"access_token": "xoxb-real"},  # no user_access_token
    )
    r = ctx.client.get("/v1/connectors/slack/history?channel=C1")
    assert r.status_code == 400
    assert "read-as-user" in r.json()["detail"]


def test_search_route_uses_user_token(slack_env, monkeypatch):
    ctx = company_client(monkeypatch)
    seed_connection(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        provider="slack",
        token_blob={"access_token": "xoxb-real", "user_access_token": "xoxp-me"},
    )
    with patch(
        "app.routes.connectors.slack_oauth.search_messages",
        return_value={"matches": [{"text": "hit"}], "total": 1},
    ) as mock_search:
        r = ctx.client.get("/v1/connectors/slack/search?q=launch&count=5")

    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    assert mock_search.call_args.args[0] == "xoxp-me"
    assert mock_search.call_args.kwargs["query"] == "launch"
    assert mock_search.call_args.kwargs["count"] == 5


def test_search_route_404_when_not_connected(slack_env, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/connectors/slack/search?q=x")
    assert r.status_code == 404
