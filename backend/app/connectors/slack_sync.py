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
    1. Resolve the COMPANY's Slack sync connection (slack_company.py) —
       voice-of-customer pulling is company-level, one sync per company
    2. Fetch user list → build ID-to-name mapping
    3. Fetch channel list (public + private the bot belongs to), then filter
       to the user's pull-channel selection when one is stored (see
       CONFIG_SYNC_CHANNEL_IDS; no selection = every bot-member channel)
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

# The corpus filename stem this module writes (:588) and that
# `synthesis_brief._seed_from_corpus` skips extracting directly — one
# source of truth so the two never drift apart. Slack's KG extraction runs
# per-channel via `kg_ingest.slack_extract` instead; the corpus file itself
# stays (it still feeds brief generation, Ask, and DS Agent — see the
# module docstring above).
SLACK_CORPUS_DOC_STEM = "slack_channels"

# Connection-config keys for the user's pull-channel selection, written by
# POST /v1/connectors/slack/sync-channels and honored by sync_slack below.
# ids is the authoritative list; names is an {id: name} display map kept so
# a selected-but-unjoined channel can be reported by name, not raw id.
CONFIG_SYNC_CHANNEL_IDS = "sync_channel_ids"
CONFIG_SYNC_CHANNEL_NAMES = "sync_channel_names"


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


def _get_company_token_and_config(
    company_id: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """(bot_token, config, row) for the COMPANY's Slack sync connection.

    Voice-of-customer pulling is company-level (one workspace install, one
    channel selection, one sync — see slack_company.py), so the sync never
    resolves a per-user row. Slack bot tokens (xoxb-...) do not expire, so
    no refresh logic needed.
    """
    from app.connectors.slack_company import (
        CompanySlackError,
        company_slack_token,
        row_config,
    )

    try:
        resolved = company_slack_token(company_id)
    except CompanySlackError as e:
        raise HTTPException(500, str(e)) from e
    if not resolved:
        raise HTTPException(404, "Slack is not connected")
    token, row = resolved
    return token, row_config(row), row


def select_sync_channels(
    channels: list[dict[str, Any]],
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply the user's pull-channel selection to the bot-visible channels.

    Returns (channels_to_sync, errors). No stored selection (or an empty
    one) keeps the legacy behavior — every channel the bot is a member of.
    Selected channels the bot can't see (not a member / archived) come back
    as errors by name so the user knows to /invite the bot, and the sync
    proceeds with whatever remains.
    """
    selected_ids = [
        str(cid) for cid in (config.get(CONFIG_SYNC_CHANNEL_IDS) or []) if cid
    ]
    if not selected_ids:
        return channels, []

    names = config.get(CONFIG_SYNC_CHANNEL_NAMES) or {}
    by_id = {ch.get("id", ""): ch for ch in channels}
    errors = [
        f"#{names.get(cid) or cid}: skipped — the bot is not in this channel "
        "(invite the Sprntly bot in Slack, then re-sync)"
        for cid in selected_ids
        if cid not in by_id
    ]
    return [by_id[cid] for cid in selected_ids if cid in by_id], errors


# ───── Slack API fetchers ─────


def _slack_get(
    url: str,
    token: str,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Make an authenticated GET to the Slack Web API."""
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=timeout,
    )
    if not resp.ok:
        logger.warning("Slack API error: %s %s", resp.status_code, resp.text[:300])
        return {"ok": False, "error": f"http_{resp.status_code}"}
    data = resp.json()
    if not data.get("ok"):
        logger.warning("Slack API error: %s", data.get("error", "unknown"))
    return data


def fetch_users(token: str, timeout: int = 30) -> dict[str, str]:
    """Fetch workspace users and return a {user_id: display_name} mapping."""
    users: dict[str, str] = {}
    cursor: str | None = None

    while True:
        params: dict[str, Any] = {"limit": 200}
        if cursor:
            params["cursor"] = cursor

        data = _slack_get(SLACK_USERS_URL, token, params, timeout=timeout)
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
    timeout: int = 30,
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

        data = _slack_get(SLACK_CONVERSATIONS_LIST_URL, token, params, timeout=timeout)
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
    timeout: int = 30,
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

        data = _slack_get(SLACK_CONVERSATIONS_HISTORY_URL, token, params, timeout=timeout)
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
    timeout: int = 30,
) -> list[dict[str, Any]]:
    """Fetch replies in a message thread."""
    params: dict[str, Any] = {
        "channel": channel_id,
        "ts": thread_ts,
        "limit": min(limit, 100),
    }
    data = _slack_get(SLACK_CONVERSATIONS_REPLIES_URL, token, params, timeout=timeout)
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


def _slack_team_domain(access_token: str) -> str | None:
    """The workspace's Slack subdomain, for building a channel permalink —
    resolved ONCE PER SYNC, never once per channel: `fetch_team_info` is a
    real Slack API call, and the stored token payload carries `team_id` /
    `team_name` but not `domain` (see `slack_oauth.token_payload_to_store`).

    `None` on any failure — a missing permalink degrades a catalogued Slack
    document to uncited-but-named, which is honest; a guessed link would not
    be (see `kg_ingest.slack_extract`'s catalog registration)."""
    from app.connectors.slack_oauth import fetch_team_info

    try:
        team = fetch_team_info(access_token)
    except Exception:  # noqa: BLE001 — a permalink is never worth a sync failure
        logger.warning("slack sync: team domain lookup failed", exc_info=True)
        return None
    domain = str((team or {}).get("domain") or "").strip()
    return domain or None


def sync_slack(
    dataset: str,
    *,
    company_id: str,
    history_days: int = DEFAULT_HISTORY_DAYS,
) -> SyncResult:
    """Full sync: fetch channels + messages + threads → write markdown to corpus.

    Company-level: uses the COMPANY's Slack sync connection and its shared
    pull-channel selection (see slack_company.py) — whoever triggers it, one
    sync serves the whole company. Runs from the manual Sync button and the
    scheduled connector refresh.

    Args:
        dataset: The dataset slug to write corpus files into.
        company_id: Tenant the sync runs for.
        history_days: How many days of history to fetch (default 90).

    Returns:
        SyncResult with counts and any errors.
    """
    result = SyncResult(dataset=dataset)

    access_token, config, row = _get_company_token_and_config(company_id)
    sync_owner_id = row.get("user_id") or ""
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
        _update_sync_status(result, company_id=company_id, user_id=sync_owner_id)
        return result

    if not channels:
        result.errors.append(
            "No channels found — ensure the Slack bot is invited to at "
            "least one channel."
        )
        _update_sync_status(result, company_id=company_id, user_id=sync_owner_id)
        return result

    # Honor the user's pull-channel selection (picked at connect time or in
    # the connector's Configure drawer). No selection = every bot-member
    # channel, unchanged from before the picker existed.
    channels, selection_errors = select_sync_channels(channels, config)
    result.errors.extend(selection_errors)
    result.channels_count = len(channels)
    if not channels:
        # Everything the user selected is bot-invisible — the per-channel
        # errors above say which and why; nothing to write.
        _update_sync_status(result, company_id=company_id, user_id=sync_owner_id)
        return result

    # Calculate oldest timestamp for history window
    oldest_epoch = time.time() - (history_days * 86400)
    oldest_ts = f"{oldest_epoch:.6f}"

    # Per-channel KG extraction (kg_ingest.slack_extract) — lazy import
    # keeps graph/LLM/db deps off this module's load, matching
    # google_drive_sync's identical lazy import of drive_extract.
    from app.kg_ingest.slack_extract import SlackChannelDoc, kickoff_slack_extract

    # 3. Fetch messages + threads per channel, build markdown
    channel_markdowns: list[str] = []
    message_counts: dict[str, int] = {}
    slack_channel_docs: list[SlackChannelDoc] = []

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

        # Free: this markdown and metadata are already computed for the
        # corpus write above — collecting a SlackChannelDoc here costs zero
        # additional Slack API calls. `latest_ts` is the newest message ts
        # seen this pass (Slack "epoch.seq" sorts lexicographically same as
        # numerically for same-length strings, so max() by float is exact).
        latest_ts = ""
        if messages:
            latest_ts = max(
                messages, key=lambda m: float(m.get("ts", "0") or "0")
            ).get("ts", "")
        slack_channel_docs.append(SlackChannelDoc(
            channel_id=ch_id,
            channel_name=ch_name,
            text=md,
            latest_ts=latest_ts,
            message_count=len(messages),
            is_private=bool(ch.get("is_private", False)),
        ))

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
        (corpus_dir / f"{SLACK_CORPUS_DOC_STEM}.md").write_text(
            full_md, encoding="utf-8"
        )
        logger.info(
            "Wrote %s.md for %s (%d chars, %d messages)",
            SLACK_CORPUS_DOC_STEM, dataset, len(full_md), result.messages_count,
        )
    except Exception as exc:
        result.errors.append(f"write: {exc}")
        logger.error("Failed to write %s.md: %s", SLACK_CORPUS_DOC_STEM, exc,
                     exc_info=True)

    # 5b. Kick off per-channel KG extraction (kg_ingest.slack_extract) and
    # per-channel catalog registration. Fire-and-forget, off the request
    # path — never let an extraction-kick failure affect this sync's own
    # result. Extraction itself makes no new Slack API calls: the docs
    # collected above are built entirely from data already fetched for the
    # corpus write. The one genuinely new call is the team-domain lookup
    # below, for catalog permalinks — made ONCE per sync, not once per
    # channel.
    team_domain = _slack_team_domain(access_token)
    try:
        kickoff_slack_extract(company_id, slack_channel_docs, team_domain=team_domain)
    except Exception:  # noqa: BLE001 — extraction must never fail the sync
        logger.exception(
            "slack sync: KG extraction kick failed for %s", company_id
        )

    # 6. Update sync status + auto-enable input source
    _update_sync_status(result, company_id=company_id, user_id=sync_owner_id)

    return result


def _update_sync_status(
    result: SyncResult, *, company_id: str, user_id: str
) -> None:
    """Stamp the sync timestamp on the company sync connection's owner row
    and enable the input source."""
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


# ───── Un-syncing a channel (the reverse of picking one) ─────
#
# Unticking a channel in the picker has to undo what ticking it did, and the
# messages it already pulled are the half that used to survive forever. The
# corpus doc is the layer where that is genuinely reversible per channel:
# `channel_messages_to_markdown` writes one `## #<name>` section per channel
# and `channels_summary_to_markdown` writes one table row per channel, so a
# channel's content is a contiguous, addressable slice of slack_channels.md
# rather than being interleaved with everything else.
#
# The KG is NOT reversible per channel and this module deliberately does not
# pretend otherwise: `_seed_from_corpus` extracts slack_channels.md as ONE
# document and stamps every signal with `provenance["doc"] = "slack_channels"`
# — there is no per-channel key to select on, so signals from the removed
# channel are indistinguishable from signals from the channels that were kept.
# Callers therefore trim the corpus here and then kick the ordinary corpus
# re-seed, which is exactly what a normal sync does; the removed channel stops
# being re-extracted and its already-extracted signals age out on the usual
# source_type window instead of being deleted. Expiring the whole slack doc's
# signals to force the issue was rejected for the reason the Drive
# file-removal commit gives: retiring evidence the user KEPT is materially
# worse than briefly retaining evidence they dropped.

_CHANNEL_HEADING_RE = re.compile(r"^## #(?P<name>.+?)\s*$")
_SUMMARY_ROW_RE = re.compile(r"^\|\s*#(?P<name>[^|]+?)\s*\|")
_TOTAL_CHANNELS_RE = re.compile(r"^\*\*Total channels synced:\*\*\s*\d+\s*$")
_HEADER_COUNTS_RE = re.compile(
    r"^\*\*Channels:\*\*\s*\d+\s*\|\s*\*\*Messages:\*\*\s*\d+\s*\|\s*"
    r"\*\*Thread replies:\*\*\s*(?P<threads>\d+)\s*$"
)


def _summary_row_message_count(line: str) -> int:
    """The "Messages Synced" cell of a summary-table row, 0 when unparseable.
    Cells are `| #name | members | messages | topic |`."""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) < 3:
        return 0
    try:
        return int(cells[2])
    except (TypeError, ValueError):
        return 0


def remove_channels_from_corpus(dataset: str, channel_names: list[str]) -> int:
    """Strip the named channels out of `DATA_DIR/{dataset}/slack_channels.md`.

    Removes each channel's `## #<name>` section AND its row in the Channels
    Overview table, then rewrites the two count lines from what survives so
    the doc doesn't claim more channels than it contains — an LLM reading
    "Channels: 5" above four sections will happily reason about the fifth.
    Channel count and message count are both recomputed exactly (the table
    carries per-channel message counts); the thread-replies total is left
    as-is because it is never broken down per channel anywhere in the doc,
    and the next full sync rewrites the whole header regardless.

    Returns the number of channel sections actually removed. A missing file,
    a doc with no matching section, or an empty name list are all 0 — not
    errors. Names are matched case-insensitively; Slack channel names are
    already lowercase, but a stored display name may not be.

    Deleting the file wholesale when nothing is left is deliberate: an empty
    Slack doc still reads to the corpus loader as a Slack document and would
    keep `slack` looking like a live evidence source with zero content.
    """
    wanted = {n.strip().lstrip("#").lower() for n in channel_names if n and n.strip()}
    if not wanted:
        return 0
    path = settings.data_path / dataset / "slack_channels.md"
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return 0
    except OSError as exc:
        logger.warning("slack un-sync: cannot read %s: %s", path, exc)
        return 0

    kept_lines: list[str] = []
    removed = 0
    kept_messages = 0
    kept_channels = 0
    dropping = False
    for line in text.splitlines():
        heading = _CHANNEL_HEADING_RE.match(line)
        if heading:
            dropping = heading.group("name").strip().lower() in wanted
            if dropping:
                removed += 1
                continue
        if dropping:
            # ONLY a `## #<name>` heading closes a dropped section, not any
            # `## ` line. Message text is written into the doc verbatim, so a
            # Slack message whose body happens to be a markdown heading would
            # otherwise end the drop early and leave half a removed channel's
            # conversation in the corpus. Channel sections are the tail of the
            # file (header, then Channels Overview, then `---`, then bodies),
            # so there is no other heading down here to protect.
            continue

        row = _SUMMARY_ROW_RE.match(line)
        if row:
            if row.group("name").strip().lower() in wanted:
                continue
            kept_channels += 1
            kept_messages += _summary_row_message_count(line)
        kept_lines.append(line)

    if not removed:
        return 0

    # Rewrite the counts from what survived the trim.
    rewritten: list[str] = []
    for line in kept_lines:
        if _TOTAL_CHANNELS_RE.match(line):
            rewritten.append(f"**Total channels synced:** {kept_channels}")
            continue
        counts = _HEADER_COUNTS_RE.match(line)
        if counts:
            rewritten.append(
                f"**Channels:** {kept_channels} | "
                f"**Messages:** {kept_messages} | "
                f"**Thread replies:** {counts.group('threads')}"
            )
            continue
        rewritten.append(line)

    try:
        if kept_channels == 0:
            path.unlink()
            logger.info(
                "slack un-sync: removed the last %d channel(s) from %s — "
                "deleted the empty corpus doc", removed, dataset,
            )
        else:
            path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
            logger.info(
                "slack un-sync: removed %d channel section(s) from %s "
                "(%d channels / %d messages remain)",
                removed, dataset, kept_channels, kept_messages,
            )
    except OSError as exc:
        logger.warning("slack un-sync: cannot rewrite %s: %s", path, exc)
        return 0
    return removed


def channel_section(text: str, channel_name: str) -> str | None:
    """One channel's `## #<name>` section out of a `slack_channels.md` body
    (the whole slice `channel_messages_to_markdown` wrote for that channel,
    heading included), or `None` when the channel has no section in `text`.

    Built on the SAME `_CHANNEL_HEADING_RE` module constant and the same
    "only a `## #<name>` heading closes a section" rule
    `remove_channels_from_corpus` already enforces (see the trap noted
    there): a message whose own text happens to be written as a markdown
    heading must not end the slice early. Matches names case-insensitively
    with the same `strip().lstrip("#").lower()` normalisation used there.

    Deliberately does NOT reuse `remove_channels_from_corpus`'s loop — that
    function is a working, tested single-pass filter that also rewrites two
    count lines; sharing the regex and the closing rule is the duplication
    that matters, sharing the loop is not."""
    wanted = channel_name.strip().lstrip("#").lower()
    if not wanted or not text:
        return None
    lines: list[str] = []
    collecting = False
    found = False
    for line in text.splitlines():
        heading = _CHANNEL_HEADING_RE.match(line)
        if heading:
            if collecting:
                # The next channel's heading — this channel's section is over.
                break
            collecting = heading.group("name").strip().lower() == wanted
            if collecting:
                found = True
            else:
                continue
        if collecting:
            lines.append(line)
    if not found:
        return None
    # splitlines() strips line-ending characters but preserves blank lines as
    # empty elements, so rejoining with "\n" and appending one trailing "\n"
    # exactly reproduces the original slice (including its own trailing
    # blank line, if any) rather than only approximating it.
    return "\n".join(lines) + "\n"


def company_dataset_slugs(company_id: str) -> list[str]:
    """Every dataset slug this company owns — the company's bare slug plus one
    per workspace (`{company}--{workspace}`).

    Every slug comes from the company's OWN rows; nothing here is derived from
    request input, so this cannot be steered at another tenant's corpus
    directory the way a client-supplied `dataset` could. Order is stable
    (default first) and duplicates are collapsed, because the default
    workspace's dataset IS the bare company slug.
    """
    from app.db.companies import slug_for_company_id
    from app.db.workspaces import (
        dataset_slug_for_workspace,
        list_workspaces_for_company,
    )

    slugs: list[str] = []
    try:
        default = slug_for_company_id(company_id)
        if default:
            slugs.append(default)
    except Exception:  # noqa: BLE001 — a missing company must not break cleanup
        logger.warning("slack un-sync: no company slug for %s", company_id,
                       exc_info=True)
    try:
        for ws in list_workspaces_for_company(company_id):
            slug = dataset_slug_for_workspace(str(ws.get("id") or ""))
            if slug:
                slugs.append(slug)
    except Exception:  # noqa: BLE001 — workspaces are optional
        logger.warning("slack un-sync: workspace lookup failed for %s",
                       company_id, exc_info=True)
    return list(dict.fromkeys(s for s in slugs if s))


def purge_channels_from_synced_data(
    company_id: str, channel_names: list[str]
) -> dict[str, Any]:
    """Remove unticked channels' pulled messages from everywhere the company's
    synced Slack data lives, then re-seed the KG the way a sync does.

    Sweeps EVERY dataset the company owns, not just the default one: the
    scheduled refresh writes to the company slug but the manual
    /slack/sync-to-corpus route writes to whichever owned dataset the caller
    passed, so a workspace dataset can hold its own slack_channels.md.

    Returns {"datasets": [...], "sections_removed": N, "reseeded": [...]}.
    Fully best-effort — this is cleanup behind a save that has already
    committed, so any failure is logged and reported, never raised.
    """
    summary: dict[str, Any] = {
        "datasets": [], "sections_removed": 0, "reseeded": [],
    }
    if not channel_names:
        return summary

    from app.kg_ingest.auto_sync import kickoff_corpus_seed

    for slug in company_dataset_slugs(company_id):
        summary["datasets"].append(slug)
        try:
            removed = remove_channels_from_corpus(slug, channel_names)
        except Exception:  # noqa: BLE001 — one bad dataset never stops the rest
            logger.exception("slack un-sync: corpus trim failed for %s", slug)
            continue
        if not removed:
            continue
        summary["sections_removed"] += removed
        # Same refresh path a normal sync uses (see the section comment above):
        # the trimmed doc is a new content hash, so it re-extracts, and the
        # removed channel is simply never seen again.
        try:
            if kickoff_corpus_seed(company_id, slug):
                summary["reseeded"].append(slug)
        except Exception:  # noqa: BLE001 — a seed kickoff must never surface
            logger.exception("slack un-sync: corpus re-seed failed for %s", slug)
    return summary
