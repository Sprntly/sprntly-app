"""Slack OAuth v2 helpers — two-way: bot install (send + bot reads) plus a
user-token grant (read the authorizing user's own messages).

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
        access_token       — the bot token (xoxb-...)  ← send + bot reads
        team               — {id, name}                ← shown as account_label
        bot_user_id        — the bot's own user id (handy for filtering messages)
        authed_user.id     — the installing user's id  ← DM-the-user target
        authed_user.access_token — the user token (xoxp-...), present only when
                             user_scope was requested  ← read-as-user
      We store both: the bot token for send + channel reads, and the user
      token for reading the installing user's own DMs/channels/search.
    - Bot install requires the installing user to be a workspace admin
      (or for the workspace to allow non-admin installs). If your
      installer isn't an admin, Slack returns an error page; the
      callback never fires. That's an account-config issue, not a code
      bug.
    - Bot tokens don't expire by default — no refresh flow needed.
"""
from __future__ import annotations

import hashlib
import hmac
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
SLACK_AUTH_REVOKE_URL = "https://slack.com/api/auth.revoke"
SLACK_CONVERSATIONS_OPEN_URL = "https://slack.com/api/conversations.open"
SLACK_CONVERSATIONS_HISTORY_URL = "https://slack.com/api/conversations.history"
SLACK_CONVERSATIONS_JOIN_URL = "https://slack.com/api/conversations.join"
SLACK_SEARCH_MESSAGES_URL = "https://slack.com/api/search.messages"
JWT_ALG = "HS256"
STATE_TTL_SECONDS = 600
# Channel-listing cap across ALL pages. Slack returns up to 1000 channels per
# page; we page through with the cursor until exhausted or this many channels
# are collected, so the picker isn't silently truncated to one page in big
# workspaces (a cause of "my channel isn't in the list").
CHANNELS_LIST_LIMIT = 1000
# Per-page size sent to conversations.list (Slack max is 1000).
CHANNELS_PAGE_SIZE = 200


def slack_configured() -> bool:
    return bool(
        settings.slack_client_id
        and settings.slack_client_secret
        and settings.slack_oauth_redirect_uri
    )


def authorize_url(
    state: str,
    scopes: str | None = None,
    user_scopes: str | None = None,
) -> str:
    """Build the URL the user gets redirected to for the Slack consent screen.

    Slack v2: bot scopes ride on `scope=`, user scopes on `user_scope=`.
    Requesting both in one install gives us a bot token (send + bot reads)
    AND a user token (read the authorizing user's own messages). Pass
    `user_scopes=""` to suppress the user-token grant (send-only install).
    """
    if not slack_configured():
        raise HTTPException(500, "Slack OAuth is not configured on the server")
    from urllib.parse import urlencode

    params = {
        "client_id": settings.slack_client_id,
        "scope": scopes or settings.slack_bot_scopes,
        "redirect_uri": settings.slack_oauth_redirect_uri,
        "state": state,
    }
    # None ⇒ fall back to the configured default; "" ⇒ explicitly omit so no
    # user token is issued. Only send `user_scope=` when non-empty (an empty
    # param still flips Slack into requesting a user token).
    user_scope = (
        settings.slack_user_scopes if user_scopes is None else user_scopes
    )
    if user_scope:
        params["user_scope"] = user_scope
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


def revoke_token(access_token: str) -> bool:
    """Revoke a Slack token via auth.revoke so disconnect tears the install down
    on Slack's side, not just locally (Slack Marketplace expects clean
    uninstall). Best-effort: returns True on `ok: true`, False otherwise; never
    raises so a revoke failure can't block the local disconnect."""
    try:
        resp = requests.post(
            SLACK_AUTH_REVOKE_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        return bool(resp.ok and resp.json().get("ok"))
    except Exception:  # noqa: BLE001 — best-effort; local delete proceeds regardless
        logger.warning("Slack auth.revoke failed", exc_info=True)
        return False


# ── Events API: request-signature verification + App Home ────────────────────

SLACK_VIEWS_PUBLISH_URL = "https://slack.com/api/views.publish"


def verify_signature(timestamp: str, raw_body: bytes, signature: str) -> bool:
    """Verify a Slack Events API request signature (per Slack's signing-secret
    scheme). Returns False on a missing secret/header, a timestamp older than
    5 minutes (replay guard), or any HMAC mismatch — constant-time compared."""
    secret = settings.slack_signing_secret
    if not secret or not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > 60 * 5:
            return False
    except (TypeError, ValueError):
        return False
    base = b"v0:" + timestamp.encode() + b":" + raw_body
    digest = hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"v0={digest}", signature)


def app_home_view() -> dict[str, Any]:
    """Block Kit view for the Slack App Home — onboarding + usage guidance, per
    Slack Marketplace UX guidance (give users a welcome + what the app does)."""
    return {
        "type": "home",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "Sprntly", "emoji": True}},
            {"type": "section", "text": {"type": "mrkdwn", "text":
                "*Your AI product manager.* Sprntly turns your tools and conversations "
                "into weekly briefs, PRDs, and prototypes."}},
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text":
                "*What this connection does*\n"
                "• Posts your weekly brief and updates into the channel you choose\n"
                "• Reads messages from channels you add Sprntly to, to surface "
                "product signals — never channels you haven't invited it to"}},
            {"type": "section", "text": {"type": "mrkdwn", "text":
                "*Get started*\nOpen Sprntly and go to *Settings → Connectors → Slack* "
                "to pick the channel for your brief."}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text":
                "Manage or disconnect anytime in Sprntly → Settings → Connectors."}]},
        ],
    }


def publish_app_home(bot_token: str, slack_user_id: str) -> bool:
    """Publish the App Home view for a user via views.publish. Best-effort —
    returns False (never raises) so an event handler can't fail the webhook."""
    try:
        resp = requests.post(
            SLACK_VIEWS_PUBLISH_URL,
            headers={"Authorization": f"Bearer {bot_token}", "Content-Type": "application/json"},
            json={"user_id": slack_user_id, "view": app_home_view()},
            timeout=10,
        )
        return bool(resp.ok and resp.json().get("ok"))
    except Exception:  # noqa: BLE001 — best-effort
        logger.warning("Slack views.publish (App Home) failed", exc_info=True)
        return False


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

    Bot token is what we send + read channels with; bot_user_id is useful
    for filtering; team {id, name} backs the account_label and the
    channel-picker UI; authed_user_id is the DM-the-user target. When user
    scopes were granted, authed_user.access_token is the xoxp token we read
    the installing user's own messages/search with — stored as
    user_access_token (None when the install was bot-only)."""
    team = token_json.get("team") or {}
    authed_user = token_json.get("authed_user") or {}
    payload = {
        "access_token": token_json.get("access_token"),
        "token_type": token_json.get("token_type", "bot"),
        "scope": token_json.get("scope") or "",
        "bot_user_id": token_json.get("bot_user_id"),
        "app_id": token_json.get("app_id"),
        "team_id": team.get("id"),
        "team_name": team.get("name"),
        "authed_user_id": authed_user.get("id"),
        # User token (xoxp-...) + its scopes — present only when user_scope
        # was requested and granted. Absent ⇒ read-as-user is unavailable.
        "user_access_token": authed_user.get("access_token"),
        "user_scope": authed_user.get("scope") or "",
        "obtained_at": int(time.time()),
    }
    return json.dumps(payload)


# ───── Message delivery ─────


def _post_message_once(
    bot_access_token: str,
    body: dict[str, Any],
) -> tuple[bool, dict[str, Any], int]:
    """Single chat.postMessage attempt. Returns (ok, parsed_body, http_status)
    without raising so callers can branch on the Slack error code (e.g. retry
    after joining the channel)."""
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
    return (resp.ok and bool(parsed.get("ok")), parsed, resp.status_code)


def post_message(
    bot_access_token: str,
    *,
    channel: str,
    text: str,
    blocks: list[dict[str, Any]] | None = None,
    thread_ts: str | None = None,
    auto_join: bool = False,
) -> dict[str, Any]:
    """Post a single message to a Slack channel. Used by the Comms Agent
    (briefs, asks, alerts) — anywhere Sprntly needs to surface output in
    Slack.

    `channel` is the channel id (e.g. "C0123456789"), not the name.
    `text` is the plain-text fallback that always renders even when
    `blocks` is set (Slack requires it for accessibility + notifications).
    `thread_ts` posts the message as a reply in that thread (used to keep
    app_mention answers attached to the mention); omit it for flat posts/DMs.

    `auto_join` (set by brief delivery) recovers the most common failure: the
    bot was never invited to the target channel, so Slack rejects the post
    with `not_in_channel`. When set, we self-join the public channel via
    conversations.join and retry once. Private channels can't be self-joined,
    so the retry still fails and we raise the actionable error below.

    On Slack-side rejection (ok:false), raises HTTPException(400) so the
    caller surfaces a real error instead of silently dropping the message.
    `not_in_channel` is rewritten into a human instruction to invite the bot."""
    body: dict[str, Any] = {"channel": channel, "text": text}
    if blocks:
        body["blocks"] = blocks
    if thread_ts:
        body["thread_ts"] = thread_ts

    ok, parsed, status = _post_message_once(bot_access_token, body)
    error = parsed.get("error")
    if not ok and auto_join and error == "not_in_channel":
        # Bot isn't a member — self-join the public channel and retry once.
        if join_channel(bot_access_token, channel):
            ok, parsed, status = _post_message_once(bot_access_token, body)
            error = parsed.get("error")

    if not ok:
        logger.warning(
            "Slack chat.postMessage failed: http=%s ok=%s err=%s",
            status,
            parsed.get("ok"),
            error,
        )
        if error == "not_in_channel":
            raise HTTPException(
                400,
                "Sprntly isn't a member of that channel. Invite the Sprntly "
                "bot to it in Slack (type /invite @Sprntly in the channel), "
                "or pick a public channel.",
            )
        raise HTTPException(
            400,
            f"Slack rejected the message: {error or 'unknown error'}",
        )
    return parsed


def open_dm(bot_access_token: str, slack_user_id: str) -> str:
    """Open (or fetch the existing) DM channel between the bot and a user and
    return its channel id (e.g. "D0123456789").

    This is the first half of "send the user a message": Slack won't let you
    chat.postMessage to a user id directly — you post to the DM channel id
    that conversations.open returns. Requires the `im:write` bot scope.

    Raises HTTPException(400) on Slack-side rejection (ok:false), matching
    post_message so callers surface a real error."""
    resp = requests.post(
        SLACK_CONVERSATIONS_OPEN_URL,
        headers={
            "Authorization": f"Bearer {bot_access_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={"users": slack_user_id},
        timeout=15,
    )
    parsed: dict[str, Any] = {}
    try:
        parsed = resp.json() or {}
    except ValueError:
        parsed = {}
    channel_id = ((parsed.get("channel") or {}).get("id")) if parsed else None
    if not resp.ok or not parsed.get("ok") or not channel_id:
        logger.warning(
            "Slack conversations.open failed: http=%s ok=%s err=%s",
            resp.status_code,
            parsed.get("ok"),
            parsed.get("error"),
        )
        raise HTTPException(
            400,
            f"Slack could not open a DM: {parsed.get('error') or 'unknown error'}",
        )
    return channel_id


def post_dm_to_user(
    bot_access_token: str,
    *,
    slack_user_id: str,
    text: str,
    blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Send a direct message to a specific Slack user as the Sprntly bot.

    Convenience wrapper: opens the DM channel (conversations.open) then posts
    to it (chat.postMessage). This realizes the "Sprntly DMs the user"
    direction. Needs `im:write` + `chat:write` bot scopes."""
    channel_id = open_dm(bot_access_token, slack_user_id)
    return post_message(
        bot_access_token, channel=channel_id, text=text, blocks=blocks
    )


def fetch_conversation_history(
    access_token: str,
    *,
    channel: str,
    limit: int = 100,
    oldest: str | None = None,
    latest: str | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Read messages from a channel/DM via conversations.history.

    Works with either token: the bot token (xoxb) for channels/DMs the bot
    is in, or the user token (xoxp) to read the authorizing user's own
    conversations. `oldest`/`latest` are Slack ts bounds; `cursor` paginates.

    Returns the trimmed shape {messages: [...], has_more, next_cursor}.
    Raises HTTPException(400) on Slack-side rejection."""
    params: dict[str, str] = {"channel": channel, "limit": str(limit)}
    if oldest:
        params["oldest"] = oldest
    if latest:
        params["latest"] = latest
    if cursor:
        params["cursor"] = cursor
    resp = requests.get(
        SLACK_CONVERSATIONS_HISTORY_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
        timeout=15,
    )
    parsed: dict[str, Any] = {}
    try:
        parsed = resp.json() or {}
    except ValueError:
        parsed = {}
    if not resp.ok or not parsed.get("ok"):
        logger.warning(
            "Slack conversations.history failed: http=%s ok=%s err=%s",
            resp.status_code,
            parsed.get("ok"),
            parsed.get("error"),
        )
        raise HTTPException(
            400,
            f"Slack rejected the history read: "
            f"{parsed.get('error') or 'unknown error'}",
        )
    return {
        "messages": parsed.get("messages") or [],
        "has_more": bool(parsed.get("has_more")),
        "next_cursor": (parsed.get("response_metadata") or {}).get("next_cursor")
        or "",
    }


def search_messages(
    user_access_token: str,
    *,
    query: str,
    count: int = 20,
    page: int = 1,
) -> dict[str, Any]:
    """Search the authorizing user's own content via search.messages.

    REQUIRES a user token (xoxp) with `search:read` — search.messages is not
    available to bot tokens. Reads as the user, so results span everything
    that user can see (their DMs, private channels, etc.).

    Returns the trimmed shape {matches: [...], total}. Raises
    HTTPException(400) on Slack-side rejection."""
    resp = requests.get(
        SLACK_SEARCH_MESSAGES_URL,
        headers={"Authorization": f"Bearer {user_access_token}"},
        params={"query": query, "count": str(count), "page": str(page)},
        timeout=15,
    )
    parsed: dict[str, Any] = {}
    try:
        parsed = resp.json() or {}
    except ValueError:
        parsed = {}
    if not resp.ok or not parsed.get("ok"):
        logger.warning(
            "Slack search.messages failed: http=%s ok=%s err=%s",
            resp.status_code,
            parsed.get("ok"),
            parsed.get("error"),
        )
        raise HTTPException(
            400,
            f"Slack rejected the search: {parsed.get('error') or 'unknown error'}",
        )
    messages = parsed.get("messages") or {}
    return {
        "matches": messages.get("matches") or [],
        "total": messages.get("total") or 0,
    }


def list_channels(bot_access_token: str) -> list[dict[str, Any]]:
    """List channels the bot can see — all public channels plus any private
    channels the bot has been added to. Used to back the channel picker in
    the Configure drawer.

    Pages through conversations.list with the cursor until exhausted (or
    CHANNELS_LIST_LIMIT is reached) so large workspaces aren't truncated to
    a single page. Private channels need the `groups:read` bot scope; without
    it Slack simply omits them (no error), so older installs that haven't
    reconnected gracefully see public channels only.

    Returns a trimmed shape: [{id, name, is_private, is_member, is_archived}].
    Archived channels are filtered out. Returns whatever was collected before
    a failed page (network, ok:false, etc.) — the caller decides whether an
    empty list is a user-visible error."""
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    while len(out) < CHANNELS_LIST_LIMIT:
        params: dict[str, Any] = {
            "types": "public_channel,private_channel",
            "exclude_archived": "true",
            "limit": str(min(CHANNELS_PAGE_SIZE, CHANNELS_LIST_LIMIT - len(out))),
        }
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(
            SLACK_CONVERSATIONS_LIST_URL,
            headers={"Authorization": f"Bearer {bot_access_token}"},
            params=params,
            timeout=15,
        )
        if not resp.ok:
            logger.warning(
                "Slack conversations.list failed: %s %s",
                resp.status_code,
                resp.text[:200],
            )
            break
        body = resp.json() or {}
        if not body.get("ok"):
            logger.warning(
                "Slack conversations.list returned ok=false: %s",
                body.get("error"),
            )
            break
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
        cursor = (body.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    return out


def join_channel(bot_access_token: str, channel_id: str) -> bool:
    """Have the bot self-join a PUBLIC channel via conversations.join so it
    can post there. Idempotent — joining a channel the bot is already in
    returns ok:true. Requires the `channels:join` bot scope.

    Returns True on success. Returns False (without raising) on any Slack
    rejection — most importantly `method_not_allowed_for_channel_type`, which
    Slack returns for private channels (a bot can't self-join those; it must
    be invited). Callers use the False to fall through to an actionable
    "invite the bot" error rather than crashing delivery."""
    resp = requests.post(
        SLACK_CONVERSATIONS_JOIN_URL,
        headers={
            "Authorization": f"Bearer {bot_access_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json={"channel": channel_id},
        timeout=15,
    )
    parsed: dict[str, Any] = {}
    try:
        parsed = resp.json() or {}
    except ValueError:
        parsed = {}
    if not resp.ok or not parsed.get("ok"):
        logger.info(
            "Slack conversations.join did not join %s: http=%s err=%s",
            channel_id,
            resp.status_code,
            parsed.get("error"),
        )
        return False
    return True


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
