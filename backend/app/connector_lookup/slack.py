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

import datetime as _dt
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import requests
from fastapi import HTTPException

from app.connector_lookup.base import HTTP_TIMEOUT, LookupSession, cap_items
from app.connectors import slack_oauth, slack_sync
from app.connectors.tokens import TokenEncryptionError, decrypt_token_json

if TYPE_CHECKING:
    from app.kg_ingest.types import RawRecord

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

#: A raw Slack conversation id — channel (C…), private group (G…) or DM (D…),
#: followed by uppercase alphanumerics. Slack channel NAMES are lowercase-only,
#: so this never collides with one, which is what makes an id safe to pass
#: straight through without touching the channel directory.
_CHANNEL_ID = re.compile(r"[CGD][A-Z0-9]+")

#: Words the model mirrors from a "what's the latest …" question that are NOT
#: topics. Slack search matches message TEXT, so query='feedback' only returns
#: messages containing that literal word — actual feedback rarely says
#: "feedback", and a just-posted message about anything else can never match.
#: Observed live 2026-08-03: "latest feedback in slack" became query='feedback'
#: twice in a row and missed the fresh message both times. When one of these
#: arrives with sort=newest, the intent is "show me what's new", so the keyword
#: is dropped and the read widens to the whole window. Single words only —
#: multi-word queries ("pricing feedback") stay real searches.
_GENERIC_QUERY_TERMS = frozenset({
    "activity", "anything", "chatter", "conversations", "discussion",
    "discussions", "everything", "feedback", "latest", "message", "messages",
    "new", "news", "recent", "update", "updates",
})

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

#: The OTHER thing a search result must state about itself. Slack's
#: `search.messages` defaults to `sort=score` — relevance — so an unsorted
#: search returns the top-scoring matches of ALL TIME, in no date order
#: whatsoever. A model handed those for "what's the latest in Slack?" will
#: summarise a 2024 thread as this week's news, which is exactly the reported
#: failure: the answer was confidently wrong about WHEN, and nothing in the
#: result said otherwise. So the ordering ships with the rows, every time,
#: alongside the privacy disclosure.
SEARCH_ORDER_NOTES = {
    "relevance": (
        "(ordered by RELEVANCE, Slack's default — these are the highest-scoring "
        "matches from ANY date, not the newest. Do NOT describe them as "
        "\"the latest\" or infer recency from this list; re-run "
        "slack_search_messages with sort=\"newest\" if the user asked what is "
        "most recent.)"
    ),
    "newest": (
        "(ordered NEWEST FIRST — these are the most recent messages matching the "
        "query, not the most relevant ones. A strong older match may be absent.)"
    ),
}

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
    "did. It sorts by RELEVANCE unless you pass sort=\"newest\", so a "
    "latest/what's-new question MUST pass sort=\"newest\" or you will be "
    "reading the top-scoring messages of all time and calling them recent. "
    "For a no-topic \"what's the latest in Slack\" question, omit `query` "
    "entirely — that returns the newest messages of the last 7 days with no "
    "keyword filter.\n\n"
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
        "text. DM and private-channel matches are excluded before they reach you. "
        "`sort` picks the ORDER, and it matters: \"relevance\" (the default) "
        "returns the best keyword matches from any date, while \"newest\" "
        "returns the most recent matches first. Use \"newest\" for anything "
        "asking what is latest / new / most recent / happening now, and "
        "\"relevance\" when the user is looking for a topic regardless of when "
        "it was said. Omitting `query` altogether is the third mode: the newest "
        "messages across every searchable channel, no keyword filter — use that "
        "when the user asks what's new WITHOUT naming a topic. The result "
        "states which order it used — repeat that framing and never call a "
        "relevance-ordered list \"the latest\"."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search terms (Slack search syntax allowed). OMIT entirely "
                    "for 'what's the latest in Slack' — no keyword filter, just "
                    "the newest messages from the last 7 days. Generic words "
                    "('feedback', 'updates', 'messages') are NOT topics — omit "
                    "the query for those too; search only matches messages "
                    "containing the literal word."
                ),
            },
            "sort": {
                "type": "string",
                "enum": ["relevance", "newest"],
                "description": (
                    "Result order. \"relevance\" (default) = best keyword "
                    "matches, any date. \"newest\" = most recent first, for "
                    "latest/what's-new questions."
                ),
            },
        },
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
    workspace_channels: list[dict] = field(default_factory=list, repr=False)
    _users_loaded: bool = False
    _channels_loaded: bool = False
    _workspace_loaded: bool = False

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

    def workspace_channel_list(self) -> list[dict]:
        """EVERY channel this connection can see — all public channels in the
        workspace plus the private ones the bot was added to.

        Distinct from `channel_list()` on purpose, and the distinction is the
        bug this exists to fix. `channel_list()` is the bot's MEMBERSHIP
        (slack_sync.fetch_channels filters on `is_member`), which is the right
        set for the privacy gate and for "what can I read" — and the wrong set
        for turning a name into an id. conversations.history takes an ID only,
        so a channel the bot hadn't been invited to resolved to nothing, the
        raw NAME went to Slack, and the read came back `channel_not_found`.
        The model read that as "no such channel", fell back to search, and
        answered from whatever search returned.

        slack_oauth.list_channels (conversations.list, `channels:read` +
        `groups:read`) returns non-member channels too — that is precisely what
        its `is_member` flag is for — so a name always has something to resolve
        against. Best-effort: an unavailable list yields [] and resolution falls
        back to the membership list exactly as before.
        """
        if not self._workspace_loaded:
            self._workspace_loaded = True
            try:
                self.workspace_channels = slack_oauth.list_channels(self.bot_token)
            except Exception:  # noqa: BLE001 — resolution degrades, never fails
                logger.warning(
                    "slack-lookup: workspace channel list fetch failed", exc_info=True
                )
                self.workspace_channels = []
        return self.workspace_channels

    def find_channel(self, ref: str) -> dict | None:
        """The channel record `ref` names, or None when the workspace has no
        such channel at all.

        Membership list first — it is usually already warm (the model tends to
        call slack_list_channels before reading one) and costs nothing when it
        is — then the full workspace list. None from here is the ONLY thing
        that justifies telling the user the name is wrong; every other failure
        is an access problem with different copy.
        """
        ref = (ref or "").strip().lstrip("#")
        if not ref:
            return None
        wanted = ref.lower()
        for source in (self.channel_list(), self.workspace_channel_list()):
            for channel in source:
                if (channel.get("name") or "").lower() == wanted:
                    return channel
                if channel.get("id") == ref:
                    return channel
        return None

    def resolve_channel(self, ref: str) -> str | None:
        """'#general' / 'general' / 'C123' → a channel id the API accepts."""
        ref = (ref or "").strip().lstrip("#")
        if not ref:
            return None
        # A raw id needs no directory read at all. Slack conversation ids are
        # uppercase (C/G/D + uppercase alphanumerics) while channel NAMES are
        # lowercase-only, so the two can never be confused — and short-circuiting
        # here keeps a model that already has an id from paying for a
        # conversations.list page to hand it back unchanged.
        if _CHANNEL_ID.fullmatch(ref):
            return ref
        found = self.find_channel(ref)
        if found and found.get("id"):
            return found["id"]
        # Nothing matched anywhere — pass it through and let Slack decide, which
        # is what produces the `channel_not_found` the caller turns into honest
        # "no channel by that name" copy.
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
        # INFO, not DEBUG: successful Slack reads used to be invisible — the
        # 2026-08-03 "stale answer" report could only be traced through a
        # failure line, with no record of which tools ran or with what input.
        # One line per call makes "did chat actually go to Slack?" answerable
        # from the logs alone.
        logger.info("slack-lookup: call %s %s", name, inp)
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
        try:
            # auto_join mirrors the delivery path (slack_oauth.post_message):
            # "the bot was never invited" is the single most common reason a
            # read fails, and a public channel is one idempotent
            # conversations.join away from working. Private channels can't be
            # self-joined, so those still fail — with copy that says why.
            data = slack_oauth.fetch_conversation_history(
                handle.bot_token, channel=channel_id, limit=_MAX_MESSAGES,
                oldest=oldest, auto_join=True,
            )
        except HTTPException as exc:
            access = _channel_access_text(handle, ref, str(exc.detail))
            if access:
                return access
            raise
        messages = list(reversed(data.get("messages") or []))  # oldest first
        logger.info(
            "slack-lookup: history %s (id=%s, days=%d) -> %d messages",
            ref, channel_id, days, len(messages),
        )
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
        """`dispatch`'s entry point — unchanged behaviour, now a thin wrapper
        over `_search_and_hits` so the sweep's `dispatch_records` can reuse the
        SAME single Slack API call for text and records rather than searching
        twice. Nothing about this method's return value changed by that split."""
        text, _kept = self._search_and_hits(handle, inp)
        return text

    def _search_and_hits(
        self, handle: SlackHandle, inp: dict
    ) -> "tuple[str, list[dict]]":
        """`(rendered text, shareable matches actually rendered)`. Everything
        below is `_search`'s original body, unmodified, with `kept` (the
        shareable, capped match list `lines` was built from) now also
        returned instead of discarded — that discard was the exact gap AC2
        exists to close (see the module docstring on `RawRecord`-producing
        adapters generally). `kept` is `[]` for every early return (no user
        token, no matches, nothing shareable): there is nothing to build
        records from in those cases either."""
        if not handle.user_token:
            return SEARCH_UNAVAILABLE, []
        query = (inp.get("query") or "").strip()
        # Model input, so it is validated, not trusted: anything that isn't the
        # explicit "newest" falls back to Slack's own relevance default. The
        # default deliberately stays relevance — a keyword question ("what did
        # we decide about pricing") wants the best match, not last Tuesday's
        # passing mention — so only an explicit ask flips it.
        order = "newest" if str(inp.get("sort") or "").strip().lower() == "newest" else "relevance"
        window_note = None
        generic = order == "newest" and query.lower() in _GENERIC_QUERY_TERMS
        if not query or generic:
            # No keyword means "the latest, whatever it is" — and so does a
            # generic one + newest (see _GENERIC_QUERY_TERMS). search.messages
            # REQUIRES a query string, but accepts a modifier-only one —
            # verified live 2026-08-03 against this app: query="after:<date>"
            # returns ok with every indexed message after that date (a "*"
            # wildcard quietly searches a smaller corpus, so it is not used).
            # Relevance is meaningless with nothing to rank, so the order is
            # forced to newest regardless of what the model passed.
            since = _dt.date.fromtimestamp(time.time() - _DEFAULT_DAYS * 86400).isoformat()
            if generic:
                window_note = (
                    f"(the keyword {query!r} was dropped — it is a generic "
                    "word that would only match messages containing it "
                    f"literally. These are ALL the newest indexed messages "
                    f"since {since}, across every channel search can see. A "
                    "just-posted message can lag the search index by a minute "
                    "or two; read the channel directly for up-to-the-second "
                    "data.)"
                )
            else:
                window_note = (
                    f"(no keyword given — these are the newest indexed messages "
                    f"since {since}, across every channel search can see. A "
                    f"just-posted message can lag the search index by a minute or "
                    f"two; read the channel directly for up-to-the-second data.)"
                )
            order = "newest"
            query = f"after:{since}"
        result = slack_oauth.search_messages(
            handle.user_token,
            query=query,
            count=_MAX_SEARCH_HITS,
            sort=(
                slack_oauth.SEARCH_SORT_NEWEST if order == "newest"
                else slack_oauth.SEARCH_SORT_RELEVANCE
            ),
            sort_dir="desc",
        )
        matches = result.get("matches") or []
        total = result.get("total") or 0
        if not matches:
            if window_note:
                return (
                    f"(no Slack messages in the last {_DEFAULT_DAYS} days in "
                    "channels search can see)"
                ), []
            return f"(no Slack messages match {query!r})", []
        # PRIVACY GATE — see is_shareable_match. search.messages reads as the
        # authorizing USER, so raw results can contain their DMs and private
        # channels; this answer goes to whoever asked in Sprntly chat.
        shareable = [m for m in matches if is_shareable_match(m, handle.bot_channel_ids())]
        excluded = len(matches) - len(shareable)
        logger.info(
            "slack-lookup: search %r sort=%s -> %d matches (%d shareable, %d total)",
            query, order, len(matches), len(shareable), total,
        )
        if not shareable:
            return (
                (
                    f"(no Slack messages match {query!r} in channels I'm allowed to "
                    f"report. {excluded} match(es) were in DMs or private channels "
                    "and were excluded — say the search covered public channels "
                    "only, and never imply you read anyone's DMs.)"
                ) if excluded else f"(no Slack messages match {query!r})"
            ), []
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
        # Ordering first: it is the one property of this list that changes what
        # the rows MEAN, and an answer that gets it wrong is wrong about time.
        notes = [SEARCH_ORDER_NOTES[order], SEARCH_DISCLOSURE]
        if window_note:
            notes.insert(0, window_note)
        if excluded:
            notes.append(
                f"({excluded} further match(es) were in DMs or private channels "
                "and were excluded from this result.)"
            )
        if marker:
            notes.append(marker)
        elif total > len(kept):
            notes.append(f"(showing {len(kept)} of {total} matches Slack returned)")
        return "\n".join(lines + notes), kept

    def dispatch_records(self, session: LookupSession, name: str, inp: dict):
        """`(text, records)` for `slack_search_messages`, `None` for anything
        else. Calls `_search_and_hits` — the SAME single Slack API call
        `dispatch` makes for this tool — so `text` is byte-identical to
        `dispatch`'s own output by construction, including on the SAME
        exceptions `dispatch`'s outer try/except turns into friendly text:
        this method wraps the call itself rather than relying on `dispatch`'s
        wrapper, since `_AdapterLeg.run` calls it INSTEAD of `dispatch`. See
        `_match_to_record` for why AC4's byte-identity claim does not apply to
        Slack at all: there is no `RawRecord`-producing puller for Slack to be
        identical WITH (see that function's docstring)."""
        if name != "slack_search_messages":
            return None
        handle: SlackHandle = session.handle
        try:
            text, kept = self._search_and_hits(handle, inp)
        except requests.Timeout:
            return f"(Slack timed out on {name} — no results from this call)", None
        except HTTPException as exc:
            return _slack_error_text(name, str(exc.detail)), None
        except requests.RequestException as exc:
            return f"(Slack {name} failed to reach Slack: {exc})", None
        if not kept:
            return text, None
        # Cache-hit, not a new call: `_search_and_hits` already populated
        # `handle`'s lazily-loaded user map to render the `who` in `text`
        # above (SlackHandle.user_map caches on `_users_loaded`).
        users = handle.user_map()
        return text, [_match_to_record(m, users) for m in kept]


def _ts_to_iso(ts: str) -> str | None:
    """A Slack `epoch.seq` timestamp as ISO-8601 UTC, or `None` for an empty
    or unparseable one. Mirrors `kg_ingest.slack_extract._latest_message_iso`
    — kept local rather than imported, same reasoning `slack_extract` itself
    gives for not importing `pullers.jira`'s ADF flattener: this module stays
    testable in isolation."""
    if not ts:
        return None
    try:
        epoch = float(str(ts).split(".")[0])
    except (TypeError, ValueError, IndexError):
        return None
    return _dt.datetime.fromtimestamp(epoch, tz=_dt.timezone.utc).isoformat()


def _match_to_record(match: dict, users: dict[str, str]) -> "RawRecord":
    """One shareable `search.messages` hit → a `RawRecord`.

    AC4 (byte-identity with the scheduled pull's record for the same item)
    does not apply here, for a reason none of the other four providers share:
    **Slack has no `RawRecord`-producing puller at all.** `kg_ingest.runner
    .PULLERS` has no "slack" entry, and Slack's OWN KG path
    (`kg_ingest/slack_extract.py`) hashes whole chunks of a channel's synced
    markdown (`_chunk_hash(channel_id, chunk)`, keyed on channel + chunk text)
    — never one message, and never `RawRecord.render()`. There is structurally
    nothing for this record to collide with in `sweep_persist`'s ledger; a
    Slack sweep will never register a `skipped` hit against the scheduled
    ingestion, no matter how this method is implemented.

    Built anyway, for what it still buys: `external_id` is `channel_id:ts`,
    Slack's own compound key for one message (AC3), which is at least a STABLE
    identity across repeated sweeps — two different questions that both
    resurface the same message now hash identically to EACH OTHER, so a
    second, differently-worded sweep skips re-extracting a message a prior
    sweep already paid for. That is real, if narrower, value: sweep-to-sweep
    dedup, not sweep-to-pull dedup.

    NOR IS MAKING THEM COLLIDE ON THE TABLE (recorded here so nobody reopens
    it — see the amendment to the sweep-persist ticket this closed). Doing so
    would mean per-message Slack ingestion: `slack_extract._chunk_hash`
    hashes `_CHUNK_CHARS`-sized (6,000-char) chunks of a channel's
    CONCATENATED synced markdown, capped at `_MAX_KG_CHARS` (60,000) per
    channel — a message becoming its own hashable unit is a different
    ingestion shape entirely, not a fix to this one. At
    `slack_sync.MAX_CHANNELS` (50) x `MAX_MESSAGES_PER_CHANNEL` (200) that is
    up to ~10,000 extraction calls where today's per-channel-chunk ingestion
    pays for at most 50 x (60,000 / 6,000) = 500 — roughly 20x, to enable
    dedupe on a feature whose own value (the sweep's live hit rate) is still
    unmeasured. Not worth it.

    What actually bounds Slack's persistence cost instead is the
    per-(company, provider) cooldown in sweep_persist.py (AC-A2): without a
    puller to collide with, Slack has NO dedupe at all against a repeated,
    differently-worded sweep beyond the sweep-to-sweep case above, so the
    cooldown — not the content-hash ledger — is the only thing standing
    between Slack and a fresh batch of message-sized extraction calls on
    every question that happens to sweep it.
    """
    from app.kg_ingest.types import RawRecord

    channel = (match.get("channel") or {}) or {}
    channel_id = str(channel.get("id") or match.get("channel_id") or "")
    channel_name = channel.get("name") or "?"
    ts = str(match.get("ts") or "")
    # Same cleaning + cap as the rendered `text` line above, so the record's
    # text is the same string the user-facing result already showed.
    text = slack_sync._clean_message_text(match.get("text") or "", users)[:_TEXT_CHARS]
    who = match.get("username") or users.get(match.get("user") or "", match.get("user") or "?")
    return RawRecord(
        provider="slack",
        kind="message",
        external_id=f"{channel_id}:{ts}",
        title="",
        text=text,
        properties={"channel": channel_name, "user": who},
        timestamp=_ts_to_iso(ts),
    )


def _channel_access_text(handle: SlackHandle, ref: str, detail: str) -> str | None:
    """Copy for a channel read Slack refused, or None when the rejection wasn't
    about channel access (leave those to `_slack_error_text`).

    The old copy said one thing for three different situations — "that channel
    isn't readable — the Sprntly bot isn't in it, or the name is wrong" — and
    the "or the name is wrong" half is what made the reported failure worse: the
    channel existed and was spelled correctly, so a model told its name might be
    wrong stopped reading channels and went to search instead. Now the workspace
    directory is consulted before anything is claimed, and "the name is wrong" is
    only ever said when the name genuinely matches NOTHING.
    """
    lowered = (detail or "").lower()
    if "not_in_channel" not in lowered and "channel_not_found" not in lowered:
        return None
    known = handle.find_channel(ref)
    ref = (ref or "").strip().lstrip("#")   # quote the NAME, not the sigil
    if known is None:
        return (
            f"(slack_channel_history: no channel called {ref!r} is visible to "
            "this connection. Check the exact name with slack_list_channels — "
            "it may be spelled differently, archived, or a private channel the "
            "Sprntly bot has never been invited to. Do NOT report this as "
            "\"nothing was said there\".)"
        )
    name = known.get("name") or ref
    if known.get("is_private"):
        return (
            f"(slack_channel_history: #{name} is a PRIVATE channel and the "
            "Sprntly bot isn't in it. A bot cannot add itself to a private "
            "channel, so this will keep failing until someone invites it — tell "
            f"the user to run /invite @Sprntly in #{name}. Do NOT report this "
            "as \"nothing was said there\", and do not silently answer from "
            "another channel instead.)"
        )
    return (
        f"(slack_channel_history: #{name} exists but the Sprntly bot could not "
        "join it automatically, so its messages are unreadable right now. Tell "
        f"the user to run /invite @Sprntly in #{name}. Do NOT report this as "
        "\"nothing was said there\".)"
    )


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
