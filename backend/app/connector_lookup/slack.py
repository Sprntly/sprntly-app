"""Slack adapter — live channel reads and (when granted) message search.

Wrappers over the fetchers that already back the corpus sync
(connectors/slack_sync.py) and the read-as-user helpers in
connectors/slack_oauth.py. Nothing new to authorize; no manifest change.

Two honest properties this adapter must always state, because getting either
wrong produces a confidently wrong answer:

1. SEARCH MODE. `search.messages` needs a USER token (xoxp with `search:read`),
   which only installs that granted user scopes have. Without one the adapter
   does NOT silently degrade to something narrower and call it a search: the tool
   returns a deterministic message telling the model to read specific channels
   instead, and the session records the mode so the answer can say which one it
   used.
2. VISIBILITY. Bot-token reads only see channels the bot was added to. "Not
   found in Slack" therefore means "not in the channels I can read" — the system
   block says so, so the model doesn't turn a permissions gap into a fact.
3. PRIVACY. `search.messages` reads as the authorizing USER, so its raw results
   include that person's DMs and private channels — while the answer goes to
   whichever teammate asked. Every hit is therefore gated by
   `is_shareable_match` to conversations the BOT could also read, and each result
   carries `SEARCH_DISCLOSURE` saying so. Reporting the authorizing user's DMs to
   their colleagues is not a feature we ship by accident; making it one would be
   a product decision about who may read whose messages.

Routing is explicit-name-only for now (skill_router.is_connector_lookup): a
question has to actually name Slack or a #channel. False positives are the
biggest UX risk on this surface, so it widens later, deliberately.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

import requests
from fastapi import HTTPException

from app.connector_lookup.base import HTTP_TIMEOUT, LookupSession, cap_items
from app.connectors import slack_oauth, slack_sync
from app.connectors.tokens import TokenEncryptionError, decrypt_token_json

logger = logging.getLogger(__name__)

DISPLAY_NAME = "Slack"

#: Per-read caps. A chat answer needs the recent shape of a conversation, not a
#: quarter of history; the framework truncates on top of these.
_MAX_MESSAGES = 60
_MAX_SEARCH_HITS = 20
_MAX_THREAD_REPLIES = 30
_MAX_CHANNELS = 50
_DEFAULT_DAYS = 7
_TEXT_CHARS = 1200

SEARCH_UNAVAILABLE = (
    "(slack_search_messages is unavailable for this workspace: the Slack "
    "connection granted bot access only, with no user token carrying "
    "`search:read`. Do NOT report this as 'nothing found'. Use "
    "slack_list_channels to see which channels are readable, then "
    "slack_channel_history on the likely ones — and tell the user you read "
    "specific channels rather than searching the whole workspace.)"
)

#: Appended to every search result, so the answer can state its own scope. The
#: model is told the mode in the system block too; this makes it impossible to
#: read a result set without seeing what it did and didn't cover.
SEARCH_DISCLOSURE = (
    "(searched PUBLIC / bot-readable Slack channels only — DMs, group DMs and "
    "private channels the Sprntly bot isn't in are excluded. Describe it that "
    "way; do not imply you searched anyone's private messages.)"
)

SYSTEM = (
    "Tools:\n"
    "- slack_list_channels: the channels this connection can read.\n"
    "- slack_channel_history: recent messages in one channel (by #name or id), "
    "optionally limited to the last N days.\n"
    "- slack_get_thread: the replies under one message (pass the channel and the "
    "message's `ts`, which the history/search results give you).\n"
    "- slack_search_messages: keyword search over PUBLIC / bot-readable "
    "channels. Available ONLY when this install granted a user token; if it "
    "isn't, the tool says so — read channels instead and say that's what you "
    "did.\n\n"
    "Honest limits you MUST respect: these reads see the channels the Sprntly "
    "bot was added to, plus public channels in search mode. DMs, group DMs and "
    "private channels the bot isn't in are NEVER readable: search runs as the "
    "authorizing user, but its results are FILTERED before you see them. So say "
    "you \"searched public channels\", never that you searched someone's private "
    "messages — even if asked to. And an empty result means \"not in the Slack I "
    "can read\", NEVER \"it was never said\" — say which channels you looked in. "
    "Quote messages with their author and date, and don't paraphrase a decision "
    "into something firmer than the message says.\n"
    "This connection is READ-ONLY from chat: you cannot post, reply, DM, react "
    "or edit anything in Slack. If asked to, say so plainly."
)

LIST_CHANNELS_TOOL = {
    "name": "slack_list_channels",
    "description": (
        "List the Slack channels this connection can read (name, id, whether "
        "private). Use it first when the user names a channel loosely, or to "
        "decide where to look when search isn't available."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

CHANNEL_HISTORY_TOOL = {
    "name": "slack_channel_history",
    "description": (
        "Read recent messages from one Slack channel. `channel` accepts "
        "'#general', 'general' or a channel id. `days` limits how far back to "
        "read (default 7). Returns messages oldest-to-newest with author, "
        "timestamp and whether the message has a thread."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "channel": {"type": "string", "description": "Channel name (#general) or id."},
            "days": {"type": "integer", "description": "How many days back to read (default 7)."},
        },
        "required": ["channel"],
    },
}

GET_THREAD_TOOL = {
    "name": "slack_get_thread",
    "description": (
        "Read the replies under one Slack message. Pass the `channel` (name or "
        "id) and the parent message's `thread_ts` — the `ts` value shown next to "
        "a message in slack_channel_history or slack_search_messages output."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "channel": {"type": "string", "description": "Channel name (#general) or id."},
            "thread_ts": {"type": "string", "description": "The parent message's ts."},
        },
        "required": ["channel", "thread_ts"],
    },
}

SEARCH_TOOL = {
    "name": "slack_search_messages",
    "description": (
        "Keyword-search Slack messages in PUBLIC / bot-readable channels (needs "
        "a user token; the tool tells you when the workspace didn't grant one). "
        "`query` supports Slack's own search syntax, e.g. 'pricing in:#product "
        "after:2026-07-01'. Returns matches with channel, author, date, ts and "
        "text. DM and private-channel matches are excluded before they reach you."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search terms (Slack search syntax allowed)."},
        },
        "required": ["query"],
    },
}

TOOLS = [LIST_CHANNELS_TOOL, CHANNEL_HISTORY_TOOL, GET_THREAD_TOOL, SEARCH_TOOL]


@dataclass
class SlackHandle:
    """One tenant's Slack access for the duration of a lookup.

    `bot_token` reads channels the bot is in; `user_token` (when the install
    granted user scopes) additionally enables search. Users and channels are
    resolved lazily and cached for the lookup so a three-tool answer doesn't
    re-fetch the directory three times.
    """

    # repr suppressed: these are live credentials, and a dataclass repr ends up in
    # log lines, exception context and test failure output.
    bot_token: str = field(repr=False)
    user_token: str | None = field(default=None, repr=False)
    users: dict[str, str] = field(default_factory=dict, repr=False)
    channels: list[dict] = field(default_factory=list, repr=False)
    _users_loaded: bool = False
    _channels_loaded: bool = False

    def bot_channel_ids(self) -> set[str]:
        """Channel ids the BOT is a member of (fetch_channels filters on
        is_member), i.e. the conversations a teammate reading this answer could
        also have seen through Sprntly. Used to gate search results."""
        return {c["id"] for c in self.channel_list() if c.get("id")}

    def user_map(self) -> dict[str, str]:
        if not self._users_loaded:
            self._users_loaded = True
            try:
                self.users = slack_sync.fetch_users(
                    self.bot_token, timeout=HTTP_TIMEOUT
                )
            except Exception:  # noqa: BLE001 — names are cosmetic, ids still read
                logger.warning("slack-lookup: user list fetch failed", exc_info=True)
                self.users = {}
        return self.users

    def channel_list(self) -> list[dict]:
        if not self._channels_loaded:
            self._channels_loaded = True
            try:
                self.channels = slack_sync.fetch_channels(
                    self.bot_token, limit=_MAX_CHANNELS, timeout=HTTP_TIMEOUT
                )
            except Exception:  # noqa: BLE001
                logger.warning("slack-lookup: channel list fetch failed", exc_info=True)
                self.channels = []
        return self.channels

    def resolve_channel(self, ref: str) -> str | None:
        """'#general' / 'general' / 'C123' → a channel id the API accepts."""
        ref = (ref or "").strip().lstrip("#")
        if not ref:
            return None
        for channel in self.channel_list():
            if (channel.get("name") or "").lower() == ref.lower():
                return channel.get("id")
            if channel.get("id") == ref:
                return channel.get("id")
        # Unknown to the channel list (private channel the bot is in but the
        # list call failed, or a raw id) — pass it through and let Slack decide.
        return ref


def _load_tokens(company_id: str) -> tuple[str | None, str | None]:
    """Return `(bot_token, user_token)` for the company, or (None, None).

    Slack is the one per-USER connector (see db/connections.py), so a company can
    hold several rows. A chat lookup is company-scoped: take the first row that
    carries a usable bot token, preferring one that also has a user token (that
    row can search). Legacy NULL-user rows are covered by the company-scoped
    get_connection fallback.

    Preferring a user-token row is safe ONLY because search results are filtered
    by `is_shareable_match` down to what the bot could read as well. Without that
    gate this preference would mean "answer any teammate's question out of
    whichever colleague happened to grant user scopes, DMs included" — do not
    loosen one without re-reading the other.

    Tenancy: every read is keyed by the authenticated company_id — the only
    company id in scope. Nothing here is derived from model input.
    """
    from app import db

    rows: list[dict] = []
    try:
        rows = list(db.list_slack_connections(company_id) or [])
    except Exception:  # noqa: BLE001 — fall back to the company-scoped row
        logger.warning("slack-lookup: per-user row lookup failed", exc_info=True)
    if not rows:
        try:
            row = db.get_connection(company_id, slack_oauth.SLACK_PROVIDER)
        except Exception:  # noqa: BLE001
            logger.warning("slack-lookup: connection lookup failed", exc_info=True)
            row = None
        rows = [row] if row else []

    best: tuple[str | None, str | None] = (None, None)
    for row in rows:
        if not row:
            continue
        try:
            token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
        except (TokenEncryptionError, ValueError, KeyError, TypeError):
            logger.warning("slack-lookup: could not decrypt a Slack token for %s",
                           company_id)
            continue
        bot = token_json.get("access_token")
        if not bot:
            continue
        user = token_json.get("user_access_token") or None
        if user:
            return bot, user
        if best == (None, None):
            best = (bot, None)
    return best


def is_shareable_match(match: dict, bot_channel_ids: set[str]) -> bool:
    """True when a search hit may be quoted into a Sprntly chat answer.

    `search.messages` reads as the AUTHORIZING USER, so its raw results span
    everything that person can see — their DMs, their group DMs, and private
    channels nobody else in the company is in. The answer, meanwhile, goes to
    whichever teammate asked the question. Quoting a DM verbatim into that answer
    would leak one employee's private messages to another, from a connector they
    authorized for company search. So the gate is: a hit is shareable only if it
    lives somewhere the Sprntly BOT could have read it too — which is exactly the
    set any teammate's lookup can already reach.

    Concretely:
      - `D…` ids and `is_im` → direct messages: never shareable.
      - `is_mpim` (group DM) → never shareable.
      - `G…` ids / `is_private` → private channel or legacy group: shareable ONLY
        if the bot is a member of it.
      - anything else (`C…`, not flagged private) → public channel: shareable.

    Full user-scope search (reporting the authorizing user's DMs) is deliberately
    NOT a feature here; it would need a product decision about who may read whose
    messages, not just a code change.
    """
    channel = match.get("channel") or {}
    channel_id = str(channel.get("id") or match.get("channel_id") or "")
    if channel_id.startswith("D") or channel.get("is_im") or channel.get("is_mpim"):
        return False
    if channel_id.startswith("G") or channel.get("is_private"):
        return channel_id in bot_channel_ids
    return True


def _ts_line(msg: dict, users: dict[str, str]) -> str:
    """One rendered message: when, who, what, and its ts (so a thread can be
    followed) — mirrors the corpus renderer's shape."""
    when = slack_sync._ts_to_date(str(msg.get("ts") or ""))
    uid = msg.get("user") or msg.get("bot_id") or "?"
    who = users.get(uid, msg.get("username") or uid)
    text = slack_sync._clean_message_text(msg.get("text") or "", users)[:_TEXT_CHARS]
    thread = ""
    reply_count = msg.get("reply_count")
    if reply_count:
        thread = f" (thread: {reply_count} replies, thread_ts={msg.get('ts')})"
    return f"[{when}] {who}: {text}{thread} (ts={msg.get('ts')})"


class SlackProvider:
    """LookupProvider over slack_sync / slack_oauth reads."""

    provider = "slack"
    display_name = DISPLAY_NAME
    keywords = ("slack", "#channel")

    def open_session(self, enterprise_id: str) -> LookupSession | None:
        bot, user = _load_tokens(enterprise_id)
        if not bot:
            return None
        notes = [
            # The mode line is not decoration: the answer must be able to say
            # WHICH Slack it read, and must never imply it read anyone's DMs.
            "search mode: keyword search is available. It runs against the "
            "authorizing user's Slack, but results are FILTERED to public / "
            "bot-readable channels — DMs, group DMs and private channels the "
            "Sprntly bot isn't in are dropped before you see them. Describe it "
            "as \"searched public channels\", never as searching someone's DMs."
            if user else
            "search mode: NO user token was granted, so keyword search is "
            "unavailable — read specific channels and tell the user that is "
            "what you did."
        ]
        return LookupSession(
            provider=self.provider,
            handle=SlackHandle(bot_token=bot, user_token=user),
            notes=notes,
        )

    def tools(self) -> list[dict]:
        return TOOLS

    def system_block(self) -> str:
        return SYSTEM

    def dispatch(self, session: LookupSession, name: str, inp: dict) -> str:
        handle: SlackHandle = session.handle
        try:
            if name == "slack_list_channels":
                return self._channels(handle)
            if name == "slack_channel_history":
                return self._history(handle, inp)
            if name == "slack_get_thread":
                return self._thread(handle, inp)
            if name == "slack_search_messages":
                return self._search(handle, inp)
        except requests.Timeout:
            return f"(Slack timed out on {name} — no results from this call)"
        except HTTPException as exc:
            # slack_oauth raises this with Slack's own error string
            # ("ratelimited", "invalid_auth", "channel_not_found").
            return _slack_error_text(name, str(exc.detail))
        except requests.RequestException as exc:
            return f"(Slack {name} failed to reach Slack: {exc})"
        return f"(unknown tool {name})"

    # ── tools ────────────────────────────────────────────────────────────────

    def _channels(self, handle: SlackHandle) -> str:
        channels = handle.channel_list()
        if not channels:
            return (
                "(no Slack channels are readable — the Sprntly bot may not have "
                "been added to any channel yet. Say that rather than concluding "
                "nothing was discussed.)"
            )
        kept, marker = cap_items(channels, _MAX_CHANNELS)
        lines = [
            f"- #{c.get('name')} (id={c.get('id')}"
            + (", private" if c.get("is_private") else "")
            + ")"
            for c in kept
        ]
        return "\n".join(lines) + (f"\n{marker}" if marker else "")

    def _history(self, handle: SlackHandle, inp: dict) -> str:
        ref = (inp.get("channel") or "").strip()
        if not ref:
            return "(slack_channel_history: 'channel' is required)"
        channel_id = handle.resolve_channel(ref)
        try:
            days = int(inp.get("days") or _DEFAULT_DAYS)
        except (TypeError, ValueError):
            days = _DEFAULT_DAYS
        days = max(1, min(days, 90))
        oldest = f"{int(time.time()) - days * 86400}.000000"
        data = slack_oauth.fetch_conversation_history(
            handle.bot_token, channel=channel_id, limit=_MAX_MESSAGES, oldest=oldest
        )
        messages = list(reversed(data.get("messages") or []))  # oldest first
        if not messages:
            return (
                f"(no messages in {ref} in the last {days} days — or the bot "
                "isn't in that channel)"
            )
        users = handle.user_map()
        kept, marker = cap_items(messages, _MAX_MESSAGES)
        head = f"{ref} — last {days} days ({len(kept)} messages):"
        body = "\n".join(_ts_line(m, users) for m in kept)
        tail = marker or ("(more messages exist beyond this page)"
                          if data.get("has_more") else "")
        return "\n".join(p for p in (head, body, tail) if p)

    def _thread(self, handle: SlackHandle, inp: dict) -> str:
        ref = (inp.get("channel") or "").strip()
        thread_ts = (inp.get("thread_ts") or "").strip()
        if not ref or not thread_ts:
            return "(slack_get_thread: 'channel' and 'thread_ts' are required)"
        channel_id = handle.resolve_channel(ref)
        replies = slack_sync.fetch_thread_replies(
            handle.bot_token, channel_id, thread_ts,
            limit=_MAX_THREAD_REPLIES, timeout=HTTP_TIMEOUT,
        )
        if not replies:
            return f"(no replies under {thread_ts} in {ref})"
        users = handle.user_map()
        kept, marker = cap_items(replies, _MAX_THREAD_REPLIES)
        body = "\n".join(_ts_line(m, users) for m in kept)
        return body + (f"\n{marker}" if marker else "")

    def _search(self, handle: SlackHandle, inp: dict) -> str:
        if not handle.user_token:
            return SEARCH_UNAVAILABLE
        query = (inp.get("query") or "").strip()
        if not query:
            return "(slack_search_messages: 'query' is required)"
        result = slack_oauth.search_messages(
            handle.user_token, query=query, count=_MAX_SEARCH_HITS
        )
        matches = result.get("matches") or []
        total = result.get("total") or 0
        if not matches:
            return f"(no Slack messages match {query!r})"
        # PRIVACY GATE — see is_shareable_match. search.messages reads as the
        # authorizing USER, so raw results can contain their DMs and private
        # channels; this answer goes to whoever asked in Sprntly chat.
        shareable = [m for m in matches if is_shareable_match(m, handle.bot_channel_ids())]
        excluded = len(matches) - len(shareable)
        if not shareable:
            return (
                f"(no Slack messages match {query!r} in channels I'm allowed to "
                f"report. {excluded} match(es) were in DMs or private channels "
                "and were excluded — say the search covered public channels "
                "only, and never imply you read anyone's DMs.)"
            ) if excluded else f"(no Slack messages match {query!r})"
        users = handle.user_map()
        kept, marker = cap_items(shareable, _MAX_SEARCH_HITS)
        lines = []
        for m in kept:
            channel = ((m.get("channel") or {}) or {}).get("name") or "?"
            when = slack_sync._ts_to_date(str(m.get("ts") or ""))
            who = m.get("username") or users.get(m.get("user") or "", m.get("user") or "?")
            text = slack_sync._clean_message_text(m.get("text") or "", users)[:_TEXT_CHARS]
            lines.append(
                f"- #{channel} [{when}] {who}: {text} (ts={m.get('ts')})"
            )
        notes = [SEARCH_DISCLOSURE]
        if excluded:
            notes.append(
                f"({excluded} further match(es) were in DMs or private channels "
                "and were excluded from this result.)"
            )
        if marker:
            notes.append(marker)
        elif total > len(kept):
            notes.append(f"(showing {len(kept)} of {total} matches Slack returned)")
        return "\n".join(lines + notes)


def _slack_error_text(tool: str, detail: str) -> str:
    """Turn a Slack-side rejection into something the model can act on, without
    a stack trace and without pretending the read succeeded."""
    lowered = detail.lower()
    if "ratelimited" in lowered or "429" in lowered:
        return (
            f"(Slack rate-limited {tool} — partial results at best. Say the "
            "answer may be incomplete rather than retrying in a loop.)"
        )
    if "invalid_auth" in lowered or "token_revoked" in lowered or "not_authed" in lowered:
        return (
            f"(Slack rejected the stored credentials on {tool} — the connection "
            "needs reconnecting in Settings → Connectors. Tell the user that; do "
            "not retry.)"
        )
    if "channel_not_found" in lowered or "not_in_channel" in lowered:
        return (
            f"({tool}: that channel isn't readable — the Sprntly bot isn't in "
            "it, or the name is wrong. Use slack_list_channels.)"
        )
    return f"({tool} failed: {detail})"


PROVIDER = SlackProvider()
