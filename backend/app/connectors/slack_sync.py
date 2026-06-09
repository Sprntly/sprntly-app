"""Sync Slack workspace data into a dataset corpus.

Fetches channels, messages, and threads from the Slack API, converts
them to markdown, and writes them into DATA_DIR/{dataset}/ so the
corpus loader picks them up for brief generation, Ask, and DS Agent.

Bot token scopes required:
    channels:read          — list public channels
    channels:history       — read messages from public channels
    groups:read            — list private channels the bot is in
    groups:history         — read messages from private channels
    users:read             — resolve user IDs to display names
    chat:write             — post messages (used by brief delivery)

Flow:
    1. Decrypt stored Slack bot token from connections table
    2. Fetch user list → build ID-to-name mapping
    3. Fetch channel list (public + private the bot belongs to)
    4. For each channel, fetch recent message history
    5. For threaded messages, fetch thread replies
    6. Convert everything to structured markdown
    7. Write to DATA_DIR/{dataset}/slack_channels.md
    8. Update sync status + auto-enable input source
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import HTTPException

from app import db
from app.config import settings
from app.connectors.slack_oauth import SLACK_PROVIDER
from app.connectors.tokens import (
    TokenEncryptionError,
    decrypt_token_json,
)

logger = logging.getLogger(__name__)

# Slack Web API endpoints
SLACK_USERS_URL = "https://slack.com/api/users.list"
SLACK_CONVERSATIONS_LIST_URL = "https://slack.com/api/conversations.list"
SLACK_CONVERSATIONS_HISTORY_URL = "https://slack.com/api/conversations.history"
SLACK_CONVERSATIONS_REPLIES_URL = "https://slack.com/api/conversations.replies"

# Sync limits (keep corpus size reasonable)
MAX_CHANNELS = 50
MAX_MESSAGES_PER_CHANNEL = 200
MAX_THREAD_REPLIES = 50
# Only sync messages from the last N days (default 90)
DEFAULT_HISTORY_DAYS = 90


class SlackSyncError(Exception):
    """Raised when a Slack sync operation fails."""


@dataclass
class SyncResult:
    dataset: str
    channels_count: int = 0
    messages_count: int = 0
    threads_count: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "channels_count": self.channels_count,
            "messages_count": self.messages_count,
            "threads_count": self.threads_count,
            "total_synced": self.messages_count + self.threads_count,
            "errors": self.errors,
        }


# ───── Token helpers ─────


def _get_valid_access_token(company_id: str, user_id: str) -> str:
    """Decrypt THIS user's stored Slack bot token and return it.

    Slack is per-user, so the token is resolved by (company_id, user_id).
    Slack bot tokens (xoxb-...) do not expire, so no refresh logic needed.
    """
    row = db.get_slack_connection(company_id, user_id)
    if not row:
        raise HTTPException(404, "Slack is not connected")

    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, json.JSONDecodeError) as e:
        raise HTTPException(500, "Slack token unreadable") from e

    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(500, "Slack token has no access_token")

    return access_token


# ───── Slack API fetchers ─────


def _slack_get(
    url: str,
    token: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make an authenticated GET to the Slack Web API."""
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=30,
    )
    if not resp.ok:
        logger.warning("Slack API error: %s %s", resp.status_code, resp.text[:300])
        return {"ok": False, "error": f"http_{resp.status_code}"}
    data = resp.json()
    if not data.get("ok"):
        logger.warning("Slack API error: %s", data.get("error", "unknown"))
    return data


def fetch_users(token: str) -> dict[str, str]:
    """Fetch workspace users and return a {user_id: display_name} mapping."""
    users: dict[str, str] = {}
    cursor: str | None = None

    while True:
        params: dict[str, Any] = {"limit": 200}
        if cursor:
            params["cursor"] = cursor

        data = _slack_get(SLACK_USERS_URL, token, params)
        if not data.get("ok"):
            break

        for member in data.get("members", []):
            uid = member.get("id", "")
            profile = member.get("profile", {})
            name = (
                profile.get("display_name")
                or profile.get("real_name")
                or member.get("real_name")
                or member.get("name")
                or uid
            )
            if not member.get("is_bot") and not member.get("deleted"):
                users[uid] = name

        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break

    return users


def fetch_channels(
    token: str,
    limit: int = MAX_CHANNELS,
) -> list[dict[str, Any]]:
    """Fetch public + private channels the bot belongs to."""
    channels: list[dict[str, Any]] = []
    cursor: str | None = None

    while len(channels) < limit:
        params: dict[str, Any] = {
            "types": "public_channel,private_channel",
            "exclude_archived": "true",
            "limit": min(limit - len(channels), 200),
        }
        if cursor:
            params["cursor"] = cursor

        data = _slack_get(SLACK_CONVERSATIONS_LIST_URL, token, params)
        if not data.get("ok"):
            break

        for ch in data.get("channels", []):
            if ch.get("is_member", False):
                channels.append(ch)

        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break

    return channels[:limit]


def fetch_channel_history(
    token: str,
    channel_id: str,
    limit: int = MAX_MESSAGES_PER_CHANNEL,
    oldest_ts: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch recent messages from a channel."""
    messages: list[dict[str, Any]] = []
    cursor: str | None = None

    while len(messages) < limit:
        params: dict[str, Any] = {
            "channel": channel_id,
            "limit": min(limit - len(messages), 100),
        }
        if oldest_ts:
            params["oldest"] = oldest_ts
        if cursor:
            params["cursor"] = cursor

        data = _slack_get(SLACK_CONVERSATIONS_HISTORY_URL, token, params)
        if not data.get("ok"):
            error = data.get("error", "unknown")
            if error in ("channel_not_found", "not_in_channel"):
                break
            logger.warning("Slack history fetch failed for %s: %s", channel_id, error)
            break

        messages.extend(data.get("messages", []))

        if not data.get("has_more"):
            break
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break

    return messages[:limit]


def fetch_thread_replies(
    token: str,
    channel_id: str,
    thread_ts: str,
    limit: int = MAX_THREAD_REPLIES,
) -> list[dict[str, Any]]:
    """Fetch replies in a message thread."""
    params: dict[str, Any] = {
        "channel": channel_id,
        "ts": thread_ts,
        "limit": min(limit, 100),
    }
    data = _slack_get(SLACK_CONVERSATIONS_REPLIES_URL, token, params)
    if not data.get("ok"):
        return []

    replies = data.get("messages", [])
    # First message is the parent — skip it, return only replies
    return replies[1:limit] if len(replies) > 1 else []


# ───── Markdown converters ─────


def _ts_to_date(ts: str) -> str:
    """Convert a Slack timestamp (epoch.seq) to YYYY-MM-DD HH:MM."""
    try:
        epoch = float(ts.split(".")[0])
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, IndexError):
        return ts


def _resolve_user(text: str, user_map: dict[str, str]) -> str:
    """Replace <@U12345> mentions with readable @names."""
    def _replace(match: re.Match) -> str:
        uid = match.group(1)
        name = user_map.get(uid, uid)
        return f"@{name}"

    return re.sub(r"<@(U[A-Z0-9]+)>", _replace, text)


def _clean_message_text(text: str, user_map: dict[str, str]) -> str:
    """Clean up Slack mrkdwn for corpus markdown."""
    if not text:
        return ""
    text = _resolve_user(text, user_map)
    # Strip Slack link formatting: <url|label> → label, <url> → url
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"[\2](\1)", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    # Strip channel references: <#C123|channel-name> → #channel-name
    text = re.sub(r"<#[A-Z0-9]+\|([^>]+)>", r"#\1", text)
    text = re.sub(r"<#([A-Z0-9]+)>", r"#\1", text)
    return text.strip()


def _format_attachments(msg: dict[str, Any]) -> str:
    """Extract text from message attachments and files."""
    parts: list[str] = []

    for att in msg.get("attachments", []):
        title = att.get("title", "")
        text = att.get("text") or att.get("fallback", "")
        if title or text:
            parts.append(f"  > **{title}** {text}" if title else f"  > {text}")

    for f in msg.get("files", []):
        name = f.get("name") or f.get("title", "file")
        filetype = f.get("filetype", "")
        parts.append(f"  [Attached file: {name} ({filetype})]")

    return "\n".join(parts)


def channel_messages_to_markdown(
    channel_name: str,
    channel_topic: str,
    channel_purpose: str,
    messages: list[dict[str, Any]],
    threads: dict[str, list[dict[str, Any]]],
    user_map: dict[str, str],
) -> str:
    """Convert a channel's messages + threads to markdown."""
    lines: list[str] = []
    lines.append(f"## #{channel_name}\n")

    if channel_topic:
        lines.append(f"**Topic:** {channel_topic}")
    if channel_purpose:
        lines.append(f"**Purpose:** {channel_purpose}")
    if channel_topic or channel_purpose:
        lines.append("")

    if not messages:
        lines.append("_No recent messages._\n")
        return "\n".join(lines)

    # Sort messages chronologically (oldest first)
    sorted_msgs = sorted(messages, key=lambda m: float(m.get("ts", "0")))

    for msg in sorted_msgs:
        # Skip join/leave/bot system messages
        subtype = msg.get("subtype", "")
        if subtype in (
            "channel_join", "channel_leave", "channel_topic",
            "channel_purpose", "channel_name", "bot_add",
            "bot_remove", "channel_archive", "channel_unarchive",
        ):
            continue

        user_id = msg.get("user", "")
        user_name = user_map.get(user_id, user_id)
        text = _clean_message_text(msg.get("text", ""), user_map)
        timestamp = _ts_to_date(msg.get("ts", ""))
        attachments = _format_attachments(msg)

        if not text and not attachments:
            continue

        lines.append(f"**{user_name}** ({timestamp}):")
        if text:
            lines.append(text)
        if attachments:
            lines.append(attachments)

        # Append thread replies if this message has a thread
        thread_ts = msg.get("ts", "")
        reply_count = msg.get("reply_count", 0)
        if reply_count > 0 and thread_ts in threads:
            thread_replies = threads[thread_ts]
            if thread_replies:
                lines.append(f"  *Thread ({reply_count} replies):*")
                for reply in thread_replies:
                    r_user = user_map.get(reply.get("user", ""), reply.get("user", ""))
                    r_text = _clean_message_text(reply.get("text", ""), user_map)
                    r_time = _ts_to_date(reply.get("ts", ""))
                    r_attach = _format_attachments(reply)
                    if r_text or r_attach:
                        lines.append(f"  > **{r_user}** ({r_time}): {r_text}")
                        if r_attach:
                            lines.append(f"  {r_attach}")

        lines.append("")

    return "\n".join(lines) + "\n"


def channels_summary_to_markdown(
    channels: list[dict[str, Any]],
    message_counts: dict[str, int],
) -> str:
    """Create a summary table of synced channels."""
    lines = [
        "## Channels Overview\n",
        f"**Total channels synced:** {len(channels)}\n",
        "| Channel | Members | Messages Synced | Topic |",
        "|---------|---------|-----------------|-------|",
    ]
    for ch in channels:
        name = ch.get("name", "unknown")
        members = ch.get("num_members", 0)
        count = message_counts.get(ch.get("id", ""), 0)
        topic = (ch.get("topic", {}).get("value", "") or "")[:60]
        lines.append(f"| #{name} | {members} | {count} | {topic} |")

    return "\n".join(lines) + "\n"


# ───── Sync orchestrator ─────


def sync_slack(
    dataset: str,
    *,
    company_id: str,
    user_id: str,
    history_days: int = DEFAULT_HISTORY_DAYS,
) -> SyncResult:
    """Full sync: fetch channels + messages + threads → write markdown to corpus.

    Args:
        dataset: The dataset slug to write corpus files into.
        company_id: Tenant the sync runs for.
        user_id: The user whose own Slack connection is used (per-user).
        history_days: How many days of history to fetch (default 90).

    Returns:
        SyncResult with counts and any errors.
    """
    result = SyncResult(dataset=dataset)

    access_token = _get_valid_access_token(company_id, user_id)
    corpus_dir = settings.data_path / dataset
    corpus_dir.mkdir(parents=True, exist_ok=True)

    # 1. Build user ID → name mapping
    try:
        user_map = fetch_users(access_token)
        logger.info("Fetched %d Slack users for name resolution", len(user_map))
    except Exception as exc:
        user_map = {}
        result.errors.append(f"user lookup: {exc}")
        logger.warning("Slack user fetch failed: %s", exc, exc_info=True)

    # 2. Fetch channels
    try:
        channels = fetch_channels(access_token)
        result.channels_count = len(channels)
        logger.info("Found %d Slack channels for %s", len(channels), dataset)
    except Exception as exc:
        msg = f"channels: {exc}"
        result.errors.append(msg)
        logger.warning("Slack channels fetch failed: %s", exc, exc_info=True)
        # Can't continue without channels
        _update_sync_status(result)
        return result

    if not channels:
        result.errors.append(
            "No channels found — ensure the Slack bot is invited to at "
            "least one channel."
        )
        _update_sync_status(result)
        return result

    # Calculate oldest timestamp for history window
    oldest_epoch = time.time() - (history_days * 86400)
    oldest_ts = f"{oldest_epoch:.6f}"

    # 3. Fetch messages + threads per channel, build markdown
    channel_markdowns: list[str] = []
    message_counts: dict[str, int] = {}

    for ch in channels:
        ch_id = ch.get("id", "")
        ch_name = ch.get("name", "unknown")

        try:
            messages = fetch_channel_history(
                access_token, ch_id, oldest_ts=oldest_ts,
            )
        except Exception as exc:
            result.errors.append(f"#{ch_name}: {exc}")
            logger.warning("Slack history failed for #%s: %s", ch_name, exc)
            continue

        message_counts[ch_id] = len(messages)
        result.messages_count += len(messages)

        # Fetch threads for messages that have replies
        threads: dict[str, list[dict[str, Any]]] = {}
        for msg in messages:
            reply_count = msg.get("reply_count", 0)
            thread_ts = msg.get("ts", "")
            if reply_count > 0 and thread_ts:
                try:
                    replies = fetch_thread_replies(
                        access_token, ch_id, thread_ts,
                    )
                    threads[thread_ts] = replies
                    result.threads_count += len(replies)
                except Exception:
                    pass  # Thread fetch failures are non-critical

        topic = (ch.get("topic", {}).get("value", "") or "")
        purpose = (ch.get("purpose", {}).get("value", "") or "")

        md = channel_messages_to_markdown(
            ch_name, topic, purpose, messages, threads, user_map,
        )
        channel_markdowns.append(md)

    # 4. Assemble final markdown document
    header = (
        f"# Slack Workspace Messages\n\n"
        f"**Synced:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"**History window:** last {history_days} days\n"
        f"**Channels:** {result.channels_count} | "
        f"**Messages:** {result.messages_count} | "
        f"**Thread replies:** {result.threads_count}\n\n"
    )

    summary = channels_summary_to_markdown(channels, message_counts)
    body = "\n".join(channel_markdowns)
    full_md = header + summary + "\n---\n\n" + body

    # 5. Write to corpus
    try:
        (corpus_dir / "slack_channels.md").write_text(full_md, encoding="utf-8")
        logger.info(
            "Wrote slack_channels.md for %s (%d chars, %d messages)",
            dataset, len(full_md), result.messages_count,
        )
    except Exception as exc:
        result.errors.append(f"write: {exc}")
        logger.error("Failed to write slack_channels.md: %s", exc, exc_info=True)

    # 6. Update sync status + auto-enable input source
    _update_sync_status(result, company_id=company_id, user_id=user_id)

    return result


def _update_sync_status(
    result: SyncResult, *, company_id: str, user_id: str
) -> None:
    """Update THIS user's Slack connection sync timestamp and enable input source."""
    try:
        error_msg = "; ".join(result.errors) if result.errors else None
        db.update_slack_connection_sync(
            company_id, user_id, last_sync_error=error_msg
        )
    except Exception:
        logger.warning("Failed to update Slack sync status", exc_info=True)

    try:
        db.upsert_input_source(
            result.dataset, "slack", enabled=True,
            config={"last_sync_at": db.utc_now()},
        )
    except Exception:
        logger.warning("Failed to auto-enable slack input source", exc_info=True)
