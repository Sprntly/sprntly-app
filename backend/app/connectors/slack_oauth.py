"""Slack OAuth v2 helpers — bot install for posting notifications.

Flow:
    1. Frontend hits POST /v1/connectors/slack/start-oauth
    2. We build a state JWT (carrying company_id) + return Slack's
       authorize URL
    3. Browser navigates to Slack's bot-install consent screen
    4. Slack redirects back to /v1/connectors/slack/callback?code=...&state=...
    5. We exchange the code at slack.com/api/oauth.v2.access and store
       an encrypted JSON blob under provider="slack"

Message delivery:
    - post_message(channel, text) posts markdown to a Slack channel
    - post_brief(channel, brief_payload) formats and posts a weekly brief

Slack v2 specifics worth knowing:
    - The exchange response separates bot creds from user creds:
        access_token       — the bot token (xoxb-...)  ← what we store + post with
        team               — {id, name}                ← shown as account_label
        bot_user_id        — the bot's own user id (handy for filtering messages)
        authed_user.id     — the installing user's id
      We only need the bot pieces for the notification-target use case;
      we don't request user scopes.
    - Bot install requires the installing user to be a workspace admin
      (or for the workspace to allow non-admin installs). If your
      installer isn't an admin, Slack returns an error page; the
      callback never fires. That's an account-config issue, not a code
      bug.
    - Bot tokens don't expire by default — no refresh flow needed.
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
# v2 authorize URL — note the explicit /v2/ path. The legacy /authorize
# (v1) is still live but uses a different scopes shape and returns a
# user-token-shaped payload; we want bot install, not user install.
SLACK_AUTH_URL = "https://slack.com/oauth/v2/authorize"
SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"
SLACK_TEAM_INFO_URL = "https://slack.com/api/team.info"
SLACK_CONVERSATIONS_LIST_URL = "https://slack.com/api/conversations.list"
SLACK_CONVERSATIONS_URL = "https://slack.com/api/conversations.list"
SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
SLACK_AUTH_TEST_URL = "https://slack.com/api/auth.test"
JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600
# Channel-listing cap. Slack supports up to 1000 per page; 200 is a
# generous default for the UI picker and avoids paginating in v1.
CHANNELS_LIST_LIMIT = 200


def slack_configured() -> bool:
    return bool(
        settings.slack_client_id
        and settings.slack_client_secret
        and settings.slack_oauth_redirect_uri
    )


def authorize_url(state: str, scopes: str | None = None) -> str:
    """Build the URL the user gets redirected to for the Slack consent screen."""
    if not slack_configured():
        raise HTTPException(500, "Slack OAuth is not configured on the server")
    from urllib.parse import urlencode

    # Slack v2: bot scopes ride on `scope=`, optional user scopes on
    # `user_scope=`. We only want bot scopes for the notification target.
    params = {
        "client_id": settings.slack_client_id,
        "scope": scopes or settings.slack_bot_scopes,
        "redirect_uri": settings.slack_oauth_redirect_uri,
        "state": state,
    }
    return f"{SLACK_AUTH_URL}?{urlencode(params)}"


def sign_oauth_state(
    *, company_id: str, user_id: str, return_to: str | None = None,
) -> str:
    """Mint a signed state JWT that binds the OAuth round-trip to a
    specific company AND the connecting user. The callback (which has no
    user session) trusts only this signature to know which company + user
    gets the new token.

    Slack is per-user: the bot install belongs to the individual who
    started the flow, not the whole company, so `user_id` rides in the
    signed state and is the only trusted source of the owning user at
    callback time.

    `return_to` is an optional relative path the callback redirects
    to instead of the default /settings?section=connectors."""
    now = int(time.time())
    payload = {
        "provider": SLACK_PROVIDER,
        "company_id": company_id,
        "user_id": user_id,
        "return_to": return_to,
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
    if not payload.get("company_id"):
        raise HTTPException(400, "OAuth state missing company_id")
    if not payload.get("user_id"):
        raise HTTPException(400, "OAuth state missing user_id")
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

    Slack's `oauth.v2.access` is unusual: it returns 200 even on
    failure, with `ok: false` + `error: "..."` in the body. We translate
    that into a 400 like other providers.
    """
    if not slack_configured():
        raise HTTPException(500, "Slack OAuth is not configured on the server")
    resp = requests.post(
        SLACK_TOKEN_URL,
        data={
            "client_id": settings.slack_client_id,
            "client_secret": settings.slack_client_secret,
            "redirect_uri": settings.slack_oauth_redirect_uri,
            "code": code,
        },
        timeout=15,
    )
    body: dict[str, Any] = {}
    try:
        body = resp.json() or {}
    except ValueError:
        body = {}
    if not resp.ok or not body.get("ok"):
        logger.warning(
            "Slack token exchange failed: http=%s ok=%s err=%s",
            resp.status_code,
            body.get("ok"),
            body.get("error"),
        )
        raise HTTPException(400, "Slack token exchange failed")
    return body


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


def fetch_team_info(bot_access_token: str) -> dict[str, Any]:
    """Return Slack's team.info payload — {id, name, domain, ...}.

    The token-exchange response already includes team info, so this is
    a fallback / re-fetch for the Test connection button. Slack's
    team.info uses Bearer auth (unlike, say, ClickUp's raw header)."""
    resp = requests.get(
        SLACK_TEAM_INFO_URL,
        headers={"Authorization": f"Bearer {bot_access_token}"},
        timeout=10,
    )
    if not resp.ok:
        logger.warning(
            "Slack team.info failed: %s %s", resp.status_code, resp.text[:200]
        )
        return {}
    body = resp.json() or {}
    if not body.get("ok"):
        logger.warning("Slack team.info returned ok=false: %s", body.get("error"))
        return {}
    return body.get("team") or {}


def token_payload_to_store(token_json: dict[str, Any]) -> str:
    """Pack the parts of Slack's oauth.v2.access response that we actually
    need into a compact JSON blob for Fernet encryption.

    Storing the whole response would also work, but it includes the
    installing user's token (when user scopes are requested) plus other
    pieces we'd rather not carry around. Bot token is what we post with;
    bot_user_id is useful for filtering; team {id, name} backs the
    account_label and the channel-picker UI."""
    team = token_json.get("team") or {}
    payload = {
        "access_token": token_json.get("access_token"),
        "token_type": token_json.get("token_type", "bot"),
        "scope": token_json.get("scope") or "",
        "bot_user_id": token_json.get("bot_user_id"),
        "app_id": token_json.get("app_id"),
        "team_id": team.get("id"),
        "team_name": team.get("name"),
        "authed_user_id": (token_json.get("authed_user") or {}).get("id"),
        "obtained_at": int(time.time()),
    }
    return json.dumps(payload)


# ───── Message delivery ─────


def post_message(
    bot_access_token: str,
    *,
    channel: str,
    text: str,
    blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Post a single message to a Slack channel. Used by the Comms Agent
    (briefs, asks, alerts) — anywhere Sprntly needs to surface output in
    Slack.

    `channel` is the channel id (e.g. "C0123456789"), not the name.
    `text` is the plain-text fallback that always renders even when
    `blocks` is set (Slack requires it for accessibility + notifications).

    On Slack-side rejection (ok:false), raises HTTPException(400) so the
    caller surfaces a real error instead of silently dropping the message."""
    body: dict[str, Any] = {"channel": channel, "text": text}
    if blocks:
        body["blocks"] = blocks
    resp = requests.post(
        SLACK_POST_MESSAGE_URL,
        headers={
            "Authorization": f"Bearer {bot_access_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json=body,
        timeout=15,
    )
    parsed: dict[str, Any] = {}
    try:
        parsed = resp.json() or {}
    except ValueError:
        parsed = {}
    if not resp.ok or not parsed.get("ok"):
        logger.warning(
            "Slack chat.postMessage failed: http=%s ok=%s err=%s",
            resp.status_code,
            parsed.get("ok"),
            parsed.get("error"),
        )
        raise HTTPException(
            400,
            f"Slack rejected the message: {parsed.get('error') or 'unknown error'}",
        )
    return parsed


def list_channels(bot_access_token: str) -> list[dict[str, Any]]:
    """List channels the bot can see — public + any private channels the
    bot has been added to. Used to back the channel picker in the
    Configure drawer.

    Returns a trimmed shape: [{id, name, is_private, is_member, is_archived}].
    Archived channels are filtered out. Returns [] if the call fails
    (network, ok:false, etc.) — the caller decides whether that's a
    user-visible error."""
    resp = requests.get(
        SLACK_CONVERSATIONS_LIST_URL,
        headers={"Authorization": f"Bearer {bot_access_token}"},
        params={
            # Public channels only. Listing private channels would require
            # the `groups:read` scope, which we don't ask for by default —
            # most users pick a public channel for notifications anyway,
            # and asking for fewer scopes makes the install consent
            # screen less intimidating. Add `groups:read` here + as a bot
            # scope on the Slack app if private channels become a need.
            "types": "public_channel",
            "exclude_archived": "true",
            "limit": str(CHANNELS_LIST_LIMIT),
        },
        timeout=15,
    )
    if not resp.ok:
        logger.warning(
            "Slack conversations.list failed: %s %s",
            resp.status_code,
            resp.text[:200],
        )
        return []
    body = resp.json() or {}
    if not body.get("ok"):
        logger.warning(
            "Slack conversations.list returned ok=false: %s",
            body.get("error"),
        )
        return []
    out: list[dict[str, Any]] = []
    for ch in body.get("channels") or []:
        out.append(
            {
                "id": ch.get("id"),
                "name": ch.get("name"),
                "is_private": bool(ch.get("is_private")),
                "is_member": bool(ch.get("is_member")),
                "is_archived": bool(ch.get("is_archived")),
            }
        )
    return out


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
