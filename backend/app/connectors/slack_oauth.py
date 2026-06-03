"""Slack OAuth 2.0 helpers + message delivery.

Flow:
    1. Frontend hits POST /v1/connectors/slack/start-oauth
    2. Backend builds state JWT + returns Slack's authorize URL
    3. User consents on Slack, selects workspace + channel
    4. Slack redirects to /v1/connectors/slack/callback?code=...&state=...
    5. Backend exchanges code for {access_token, team, authed_user, ...}
    6. Token encrypted and stored under provider="slack"

Message delivery:
    - post_message(channel, text) posts markdown to a Slack channel
    - post_brief(channel, brief_payload) formats and posts a weekly brief
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import jwt
import requests
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

SLACK_PROVIDER = "slack"
SLACK_AUTH_URL = "https://slack.com/oauth/v2/authorize"
SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"
SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
SLACK_CONVERSATIONS_URL = "https://slack.com/api/conversations.list"
SLACK_AUTH_TEST_URL = "https://slack.com/api/auth.test"

JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600


def slack_configured() -> bool:
    return bool(
        settings.slack_client_id
        and settings.slack_client_secret
        and settings.slack_oauth_redirect_uri
    )


def authorize_url(state: str) -> str:
    """Build the Slack OAuth authorize URL."""
    if not slack_configured():
        raise HTTPException(500, "Slack OAuth is not configured on the server")
    from urllib.parse import urlencode

    params = {
        "client_id": settings.slack_client_id,
        "redirect_uri": settings.slack_oauth_redirect_uri,
        "scope": settings.slack_scopes,
        "state": state,
    }
    return f"{SLACK_AUTH_URL}?{urlencode(params)}"


def sign_oauth_state() -> str:
    now = int(time.time())
    payload = {
        "provider": SLACK_PROVIDER,
        "nonce": uuid.uuid4().hex,
        "iat": now,
        "exp": now + STATE_TTL_SECONDS,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALG)


def verify_oauth_state(state: str) -> dict:
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[JWT_ALG])
    except jwt.PyJWTError as e:
        raise HTTPException(400, "Invalid or expired OAuth state") from e
    if payload.get("provider") != SLACK_PROVIDER:
        raise HTTPException(400, "OAuth state provider mismatch")
    return payload


def exchange_code_for_token(code: str) -> dict[str, Any]:
    """Exchange an authorization code for a Slack bot token.

    Slack's oauth.v2.access returns:
    {
      "ok": true,
      "access_token": "xoxb-...",
      "token_type": "bot",
      "scope": "chat:write,channels:read,...",
      "bot_user_id": "U...",
      "app_id": "A...",
      "team": {"id": "T...", "name": "Workspace Name"},
      "authed_user": {"id": "U..."},
      ...
    }
    """
    if not slack_configured():
        raise HTTPException(500, "Slack OAuth is not configured on the server")

    resp = requests.post(
        SLACK_TOKEN_URL,
        data={
            "client_id": settings.slack_client_id,
            "client_secret": settings.slack_client_secret,
            "code": code,
            "redirect_uri": settings.slack_oauth_redirect_uri,
        },
        timeout=15,
    )
    if not resp.ok:
        logger.warning("Slack token exchange failed: %s %s", resp.status_code, resp.text[:300])
        raise HTTPException(400, "Slack token exchange failed")

    data = resp.json()
    if not data.get("ok"):
        error = data.get("error", "unknown_error")
        logger.warning("Slack token exchange error: %s", error)
        raise HTTPException(400, f"Slack authorization failed: {error}")

    return data


def fetch_auth_test(access_token: str) -> dict[str, Any]:
    """Call auth.test to verify the token and get workspace info."""
    resp = requests.post(
        SLACK_AUTH_TEST_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if not resp.ok:
        return {}
    data = resp.json()
    return data if data.get("ok") else {}


def token_payload_to_store(token_json: dict[str, Any]) -> str:
    """Wrap Slack's token response with metadata for storage."""
    payload = {
        "access_token": token_json.get("access_token"),
        "token_type": token_json.get("token_type", "bot"),
        "scope": token_json.get("scope", ""),
        "bot_user_id": token_json.get("bot_user_id"),
        "app_id": token_json.get("app_id"),
        "team_id": (token_json.get("team") or {}).get("id"),
        "team_name": (token_json.get("team") or {}).get("name"),
        "authed_user_id": (token_json.get("authed_user") or {}).get("id"),
        "obtained_at": int(time.time()),
    }
    return json.dumps(payload)


# ───── Message delivery ─────


def post_message(access_token: str, channel: str, text: str) -> dict[str, Any]:
    """Post a message to a Slack channel.

    Args:
        access_token: Slack bot token (xoxb-...)
        channel: Channel ID or name (e.g. "#product" or "C0123456789")
        text: Message text (supports Slack mrkdwn formatting)

    Returns:
        Slack API response dict.
    """
    resp = requests.post(
        SLACK_POST_MESSAGE_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={
            "channel": channel,
            "text": text,
            "unfurl_links": False,
            "unfurl_media": False,
        },
        timeout=15,
    )
    data = resp.json()
    if not data.get("ok"):
        error = data.get("error", "unknown_error")
        logger.warning("Slack post_message failed: %s", error)
        raise HTTPException(400, f"Slack message failed: {error}")
    return data


def list_channels(access_token: str, limit: int = 200) -> list[dict[str, Any]]:
    """List public channels the bot has access to."""
    resp = requests.get(
        SLACK_CONVERSATIONS_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        params={
            "types": "public_channel",
            "exclude_archived": "true",
            "limit": min(limit, 1000),
        },
        timeout=15,
    )
    data = resp.json()
    if not data.get("ok"):
        return []
    return data.get("channels", [])


def format_brief_message(brief_payload: dict[str, Any]) -> str:
    """Format a weekly brief payload into Slack mrkdwn."""
    headline = brief_payload.get("summary_headline", "Weekly Brief")
    week = brief_payload.get("week_label", "")
    insights = brief_payload.get("insights", [])

    lines = [f"*{headline}*"]
    if week:
        lines.append(f"_{week}_\n")

    for i, insight in enumerate(insights, 1):
        tag = insight.get("tag", "")
        title = insight.get("title", "")
        summary = insight.get("summary") or insight.get("headline", "")
        emoji = {"something_new": ":sparkles:", "something_better": ":chart_with_upwards_trend:", "something_broken": ":warning:"}.get(tag, ":bulb:")
        lines.append(f"{emoji} *{i}. {title}*")
        if summary:
            lines.append(f"  {summary}")
        lines.append("")

    return "\n".join(lines)
