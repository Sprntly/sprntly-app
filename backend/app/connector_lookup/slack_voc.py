"""Slack as a CUSTOMER-FEEDBACK source — the configured VoC channels, read live
and aggregated across ALL of them.

WHAT WAS BROKEN. Slack is already dual-typed `[COMMUNICATION, CUSTOMER_VOICE]`
in `connectors/catalog.py`, and Settings → Connectors → "Voice of Customer &
Support" → Slack → "Channels to pull from" already writes a channel selection.
Nothing in the CHAT path read it. A voice-of-customer question reached Slack by
exactly two routes, and neither could show a channel's contents:

  1. the cross-connector sweep's Slack leg — ONE `search.messages` call, ranked
     by RELEVANCE, over the whole workspace. Slack search matches message TEXT,
     so a sweep keyed on the question's own words ("customers", "saying",
     "feedback") only ever matches messages containing those literal words.
     Real feedback rarely says "feedback". The sweep therefore returns a
     scattering of unrelated hits from whichever channel happened to contain a
     search term — never the feedback channels' actual contents, and never all
     of them;
  2. the named path (`skill_router.is_connector_lookup`), which by its own
     design requires the question to say "slack" or name a `#channel`.

And the stored half — `slack_sync` → corpus → `kg_ingest/slack_extract` — is a
DISTILLATION on a sync cadence, not the messages. So "summarise #demos" got the
honest but useless "no stored copy of those messages was loaded".

WHAT THIS DOES. Resolves the configured channel set, reads every one of them in
parallel under one shared wall-clock budget, and renders ONE block with a
section per channel. It is a bounded gather, not a tool loop: the model is never
asked to pick a channel, because picking one is how an aggregate answer becomes
a single-channel answer that reads like an aggregate.

TWO LAYERS, AND THE SECOND ONE IS WHY THE REPORTED ANSWER WAS FACTUALLY WRONG.
Alongside the live read, every channel carries its `document_catalog` row — a
dated, per-channel extractive summary plus topics that
`kg_ingest.slack_extract.register_slack_catalog` has been writing all along,
keyed on the channel id. The live chat answer said "I am not able to confirm
whether a #demos channel exists" while a `#demos` catalog row with a summary and
eight topics sat in the table, refreshed the day before. Nothing consulted it.
Now:

  - a channel read live shows its messages AND its stored gist, labelled;
  - a channel that could NOT be read live still appears, with its stored summary
    and its date, explicitly marked as not-read-live;
  - a channel in the catalog but outside the live scope is added rather than
    dropped — the configured set and the ingested set diverge in BOTH directions
    in the live data;
  - a channel with neither is named with its reason AND the fact that nothing
    has ever been ingested from it.

`document_catalog.body_text` is NOT populated, by design — that module's
docstring calls the table "a POINTER, NOT A COPY" so connecting a source never
leaves Sprntly holding a second copy of a customer's content at rest. This
module does not change that, and does not need to: for Slack the body IS
reachable, live, from `conversations.history`. Populating `body_text` would
reverse a deliberate storage decision in order to duplicate data we can already
fetch.

THE THREE PROPERTIES THAT MUST NOT DRIFT:

  AGGREGATION. Every configured channel is read and rendered, or NAMED as unread
  with its reason. There is no first-channel-wins path and no early return once
  something is found — `read()` fans out over the whole set and only the total
  char ceiling can drop a channel, which is itself reported.

  HONESTY. "Nothing in the feedback channels about it" must never mean "one of
  them timed out". Every channel that produced no usable text carries a status
  and a human reason into the rendered block, and the block says in words that
  the not-read list is not an empty result.

  PRIVACY. The channel set is gated by `slack.is_shareable_channel` — the SAME
  predicate that gates search hits. A DM or a private channel the bot is not in
  is excluded and named as excluded, never quietly read. Bot-token
  `conversations.history` cannot reach a DM anyway; the gate is here so a
  hand-written `sync_channel_ids` containing a `D…` id cannot become a leak,
  and so there is one place to read when someone proposes widening it.

TENANCY + WRITES. Every read is keyed by the authenticated company id. TWO
write paths had to be closed for this to be honestly called read-only, and only
one of them was obvious:

  - opening the session does not write. `slack._load_tokens` decrypts stored
    tokens and never refreshes, because Slack bot tokens (`xoxb-…`) do not
    expire. See the 2026-08-05 sweep/`open_session` incident for the providers
    where this is NOT true (Jira, Confluence and HubSpot all write on open);
  - reading a channel does not JOIN it. `slack_oauth.fetch_conversation_history`
    takes `auto_join`, and the `slack_channel_history` tool passes True —
    correctly, because there the user named the channel. This path passes
    **False** (`_read_one`). `conversations.join` adds the bot to a channel and
    Slack posts a join notice into the customer's workspace; a question that
    named no channel and no source must not produce one. An unjoined channel is
    reported as not-read with `/invite @Sprntly` copy instead.

The lesson from the first incident applied to the second: a path described as
read-only in prose is worth nothing until each call it makes has been followed.
"""
from __future__ import annotations

import concurrent.futures as _futures
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: How far back a VoC channel read looks when the caller names no window.
DEFAULT_DAYS = 7

#: Wall-clock budget for the WHOLE fan-out, shared by every channel — the same
#: shape and the same number as `sweep.BUDGET_S`, for the same reason: a dead
#: channel costs the budget once, not its own HTTP bound plus everyone else's.
BUDGET_S = 8.0

#: Most channels read in one pass. A selection larger than this is truncated and
#: the dropped channels are NAMED — `slack_sync.MAX_CHANNELS` allows 50, and
#: fifty parallel history reads is a different feature (a sync) than a chat
#: answer's context gather.
MAX_CHANNELS = 12

#: Chars kept per channel, and across the whole block. The block rides the VoC
#: corpus next to live call transcripts and the KG bundle, so it is sized like
#: the sweep's context budget rather than like a transcript.
PER_CHANNEL_CHARS = 4_000
TOTAL_CHARS = 24_000

#: `connections.config` keys. Imported by value rather than from slack_sync so
#: this module stays importable without dragging in the sync path; the constants
#: are asserted equal to slack_sync's in tests.
CONFIG_SYNC_CHANNEL_IDS = "sync_channel_ids"
CONFIG_SYNC_CHANNEL_NAMES = "sync_channel_names"

#: Where the channel set came from. Load-bearing in the rendered copy: an
#: explicit selection licenses "the channels your admin configured", while the
#: fallback only licenses "every channel the bot has been invited to" — which is
#: what the Settings UI itself promises when nothing is ticked.
SELECTION_CONFIGURED = "configured"
SELECTION_MEMBERSHIP = "bot-membership"

#: Per-channel outcomes.
STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_UNREADABLE = "unreadable"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"
STATUS_DROPPED = "dropped"
STATUS_EXCLUDED = "excluded"
#: Not read live, but `document_catalog` holds a per-channel summary for it.
#: Its own status because the distinction is the whole point: a stored summary
#: is dated, distilled and second-hand, and an answer that presents one as a
#: live read is wrong about WHEN — the same failure the search adapter's
#: ordering notes exist to prevent.
STATUS_STORED = "stored"

#: Cap on a stored summary. They are already extractive and short (a few
#: hundred chars in the live data); this is a backstop, not a trim.
STORED_SUMMARY_CHARS = 1_200
#: Topics quoted per channel.
STORED_TOPICS = 10


@dataclass(frozen=True)
class VocChannel:
    """One configured customer-feedback channel."""

    id: str
    name: str = ""
    #: The Slack workspace (`team.id`) of the connection row that selected this
    #: channel, or "" when the row recorded none. Carried so the set can be
    #: resolved BEFORE a session exists and filtered by workspace afterwards,
    #: rather than re-reading the rows once the token's workspace is known.
    team_id: str = ""

    @property
    def label(self) -> str:
        return f"#{self.name}" if self.name else (self.id or "unknown channel")


@dataclass
class StoredSummary:
    """A `document_catalog` row for one channel — what Sprntly already knows
    that channel is ABOUT, without reading it again.

    The catalog is a POINTER, NOT A COPY (see `document_catalog`'s docstring):
    `body_text` is unpopulated by every writer, deliberately, so this is a
    distilled summary plus topics — never the messages. That is exactly why it
    is kept apart from a live read here rather than merged into one blob.
    """

    summary: str = ""
    topics: list[str] = field(default_factory=list)
    #: The channel this row is for, from the catalog title (`#<name>`). Carried
    #: on the summary itself so a catalog-only channel can be NAMED without
    #: reverse-searching the id→summary map for its alias.
    channel_name: str = ""
    #: Date of the newest message the summary was taken over, when known.
    doc_date: str = ""
    #: When the catalog row was last refreshed.
    updated_at: str = ""
    url: str = ""

    @property
    def present(self) -> bool:
        return bool(self.summary.strip() or self.topics)

    def render(self) -> str:
        parts = []
        if self.summary.strip():
            parts.append(self.summary.strip()[:STORED_SUMMARY_CHARS])
        if self.topics:
            parts.append(
                "Topics: " + ", ".join(str(t) for t in self.topics[:STORED_TOPICS])
            )
        return "\n".join(parts)

    def as_of(self) -> str:
        when = (self.doc_date or self.updated_at or "")[:10]
        return (
            f" (stored summary, newest message {when})" if when
            else " (stored summary)"
        )


@dataclass
class ChannelRead:
    """What one channel contributed, or why it contributed nothing."""

    channel: VocChannel
    status: str = STATUS_EMPTY
    text: str = ""
    detail: str = ""
    message_count: int = 0
    #: The catalog's distilled view of this channel, when one exists. Attached
    #: to EVERY read, not only the failed ones: it is the cheapest way for an
    #: answer to say what a channel is generally about alongside this week's
    #: messages, and it is the ONLY thing available when the live read fails.
    stored: "StoredSummary" = field(default_factory=lambda: StoredSummary())

    @property
    def usable(self) -> bool:
        """Live messages are present. Deliberately NOT true for a stored-only
        channel — callers that mean "we read this channel just now" must not
        silently start counting summaries."""
        return self.status == STATUS_OK and bool(self.text.strip())

    @property
    def has_content(self) -> bool:
        """Anything at all reached the prompt for this channel — live or
        stored. This is what "the answer covered this channel" means."""
        return self.usable or self.stored.present

    def reason(self) -> str:
        """Why this channel is absent from the answer. Never "" — a channel with
        no reason is a channel the model will read as "nothing was said there"."""
        if self.status == STATUS_STORED:
            return (
                "not read live — only Sprntly's stored summary of it is "
                "available, which is dated and distilled, not this window's "
                "messages"
            )
        if self.detail:
            return self.detail
        if self.status == STATUS_EMPTY:
            return "no messages posted in this window (the channel WAS read)"
        if self.status == STATUS_TIMEOUT:
            return "did not answer within the time budget — it was NOT read"
        if self.status == STATUS_DROPPED:
            return "read, but dropped from this prompt for length"
        if self.status == STATUS_EXCLUDED:
            return (
                "excluded: it is a DM or a private conversation the Sprntly bot "
                "is not in, so its contents are not readable here"
            )
        if self.status == STATUS_ERROR:
            return "could not be read just now"
        return "was not read"


@dataclass
class VocRead:
    """One aggregated pass over a company's configured VoC channels."""

    selection: str = SELECTION_MEMBERSHIP
    days: int = DEFAULT_DAYS
    reads: list[ChannelRead] = field(default_factory=list)
    elapsed_ms: int = 0
    budget_exceeded: bool = False
    #: Set when the connector itself could not be opened (no install, revoked
    #: credential). Distinct from an empty `reads`: "Slack is not connected" and
    #: "Slack is connected and its feedback channels were quiet" are different
    #: answers, and one must never be printed for the other.
    unavailable: str = ""

    @property
    def present(self) -> bool:
        """Anything reached the prompt — live messages OR a stored summary."""
        return any(r.has_content for r in self.reads)

    @property
    def connected(self) -> bool:
        return not self.unavailable

    @property
    def read_channels(self) -> list[ChannelRead]:
        """Channels read LIVE. Not the same as `covered_channels` — keeping the
        two apart is what stops a stored summary being counted as a live read
        in the coverage banner."""
        return [r for r in self.reads if r.usable]

    @property
    def stored_channels(self) -> list[ChannelRead]:
        """Channels present ONLY as a stored catalog summary."""
        return [r for r in self.reads if not r.usable and r.stored.present]

    @property
    def covered_channels(self) -> list[ChannelRead]:
        return [r for r in self.reads if r.has_content]

    @property
    def unread_channels(self) -> list[ChannelRead]:
        return [r for r in self.reads if not r.usable]

    @property
    def channel_count(self) -> int:
        return len(self.reads)

    @property
    def message_count(self) -> int:
        return sum(r.message_count for r in self.read_channels)

    def channel_names(self) -> list[str]:
        return [r.channel.label for r in self.read_channels]

    def outcome_summary(self) -> str:
        """`#demos=ok #mvp-product=timeout` — statuses only, never contents. The
        whole observability story for this leg, and it needs to be: a pass that
        read nothing and a pass that never ran look identical from outside."""
        return " ".join(
            f"{r.channel.label}={r.status}" for r in self.reads
        ) or "no-channels"

    def render(self) -> str:
        """The prompt block, or "" when nothing usable was read.

        Unlike the sweep — which returns "" whenever it read nothing, so the
        model is never invited to assert an absence — this block IS rendered
        when every channel came back empty but readable, because "your three
        feedback channels have been quiet for a week" is a true and useful
        answer that only this leg can support. It is NOT rendered when the
        channels could not be READ, which is the case that would license a false
        absence.
        """
        if not self.reads:
            return ""
        readable = [
            r for r in self.reads
            if r.status in (STATUS_OK, STATUS_EMPTY) or r.has_content
        ]
        if not readable:
            return ""
        origin = (
            "configured under Settings → Connectors → Voice of Customer & "
            "Support → Slack"
            if self.selection == SELECTION_CONFIGURED else
            "every channel the Sprntly bot has been invited to (no explicit "
            "channel selection is saved, which the Settings picker treats as "
            "\"read them all\")"
        )
        # The header states what this block IS, and it has to change when
        # nothing was read live. "read live just now … 0 returned messages"
        # contradicted the correctly-labelled stored sections underneath it,
        # which is the header asserting a provenance its own content denies.
        live = len(self.read_channels)
        if live:
            head = (
                "SLACK CUSTOMER-FEEDBACK CHANNELS — read live just now, "
                f"covering the last {self.days} days. These are {origin}. "
                f"{len(self.reads)} channel(s) were in scope; "
                f"{live} returned messages."
            )
        else:
            head = (
                "SLACK CUSTOMER-FEEDBACK CHANNELS — NOTHING was read live this "
                f"turn. These are {origin}. {len(self.reads)} channel(s) were "
                "in scope; everything below is either a stored, dated summary "
                "or a channel that could not be read, and each section says "
                "which. Do NOT describe any of it as this week's traffic, and "
                "do not state message volumes."
            )
        parts = [
            head,
            "Attribute every quote to ITS OWN channel — this block aggregates "
            "several, and a theme heard in one channel is not a theme heard "
            "across the company.",
        ]
        for read in self.reads:
            if read.usable:
                section = (
                    f"\n### {read.channel.label} — {read.message_count} "
                    f"message(s) read live\n{read.text.strip()}"
                )
                if read.stored.present:
                    # The gist ALONGSIDE the messages, labelled — what the
                    # channel is generally about is useful next to one week of
                    # it, and mislabelling it as this week's traffic is not.
                    section += (
                        f"\nWhat this channel is about, from Sprntly's stored "
                        f"summary{read.stored.as_of()}:\n{read.stored.render()}"
                    )
                parts.append(section)
            elif read.stored.present:
                # The channel could not be read live — but we are NOT silent
                # about it, because a dated summary of it exists. This is the
                # case that produced the reported answer "I am not able to
                # confirm whether a #demos channel exists": the catalog row was
                # there the whole time and nothing consulted it.
                parts.append(
                    f"\n### {read.channel.label} — NOT read live"
                    f"{read.stored.as_of()}\n{read.stored.render()}\n"
                    f"(This is a stored, distilled summary, not this window's "
                    f"messages. Reason the live read did not happen: "
                    f"{read.reason()} Say the channel EXISTS and what it is "
                    f"about; do NOT quote it as if you read it just now, and "
                    f"do NOT claim to know its message volume in the last "
                    f"{self.days} days.)"
                )
            elif read.status == STATUS_EMPTY:
                parts.append(
                    f"\n### {read.channel.label}\n(read successfully — no "
                    f"messages posted in the last {self.days} days)"
                )
        missed = [
            r for r in self.unread_channels
            if r.status != STATUS_EMPTY and not r.stored.present
        ]
        if missed:
            parts.append(
                "\n### Feedback channels NOT read, with NOTHING stored either\n"
                + "\n".join(
                    f"- {r.channel.label}: {r.reason()} Sprntly has also never "
                    "ingested this channel, so there is no stored summary of it "
                    "to fall back on."
                    for r in missed
                )
                + "\nThese channels were NOT searched and nothing about them was "
                "loaded. Do not describe them as quiet, do not say they hold no "
                "feedback, and do not let their absence make a company-wide "
                "claim look complete — name them if the answer implies full "
                "coverage."
            )
        return "\n".join(parts)


# ── channel resolution ───────────────────────────────────────────────────────


def _row_config(row: dict) -> dict:
    from app.connectors.slack_company import row_config

    try:
        return row_config(row) or {}
    except Exception:  # noqa: BLE001 — an unparseable row contributes nothing
        return {}


def configured_channels(
    company_id: str, team_id: str = ""
) -> tuple[list[VocChannel], bool]:
    """`(channels, explicit)` — the company's saved VoC channel selection.

    MERGED ACROSS EVERY ACTIVE SLACK ROW, not read from one. Slack is the one
    per-USER connector (`db/connections.py`), so a company holds several rows;
    `slack_company.resolve_company_slack_row` picks a single one for the SYNC
    (there must be exactly one sync), but a chat answer claiming to cover "the
    channels connected for VoC" must cover every channel any admin selected. In
    the live data one company carries two rows for the same channel and another
    carries selections on a row that is not the oldest — a single-row read gets
    both of those wrong.

    Deduped by id, first occurrence wins, insertion order preserved so the
    rendered block is stable across turns.

    `explicit` is False when NO row carried a selection. That is not an error:
    the Settings picker states that with nothing ticked, every channel the bot
    was invited to is read, and `slack_sync.select_sync_channels` implements
    exactly that. `read()` mirrors it.

    `team_id` SCOPES THE MERGE TO ONE SLACK WORKSPACE, and it is not optional
    correctness. The channels come from every row, but the TOKEN that reads them
    comes from one (`slack._load_session_tokens`). Two members who connected
    different workspaces would otherwise have workspace A's channel ids read
    with workspace B's token: Slack answers `channel_not_found`, and this module
    faithfully reports a perfectly healthy channel as unreadable and tells the
    user to `/invite @Sprntly` into a channel the bot is already in. A row is
    excluded ONLY when both team ids are known and differ — an unrecorded team
    on either side means no filtering is possible, and dropping channels on a
    comparison that cannot be made would lose real ones.

    Deliberately does NOT read `config.channel_id` / `config.channel_name`.
    Those are the user's brief-DELIVERY target (`POST /connectors/slack/config`
    — "Save the user's notification target"), not a customer-feedback source.
    Treating them as one would have chat mine a company's own announcement
    channel for customer sentiment.
    """
    from app import db

    try:
        rows = list(db.list_slack_connections(company_id) or [])
    except Exception:  # noqa: BLE001 — degrade to no selection, never break chat
        logger.warning(
            "slack-voc: connection lookup failed for %s", company_id, exc_info=True
        )
        return [], False

    out: list[VocChannel] = []
    seen: set[str] = set()
    explicit = False
    for row in rows:
        if not row or row.get("status") != "active":
            continue
        config = _row_config(row)
        row_team = ""
        team = config.get("team")
        if isinstance(team, dict):
            row_team = str(team.get("id") or "").strip()
        if team_id and row_team and row_team != team_id:
            continue
        ids = config.get(CONFIG_SYNC_CHANNEL_IDS) or []
        if not isinstance(ids, (list, tuple)):
            continue
        names = config.get(CONFIG_SYNC_CHANNEL_NAMES) or {}
        if not isinstance(names, dict):
            names = {}
        for raw in ids:
            cid = str(raw or "").strip()
            if not cid:
                continue
            explicit = True
            if cid in seen:
                continue
            seen.add(cid)
            out.append(VocChannel(
                id=cid, name=str(names.get(cid) or "").strip(), team_id=row_team,
            ))
    return out, explicit


def catalog_summaries(company_id: str) -> dict[str, StoredSummary]:
    """`{channel_id: StoredSummary}` from `document_catalog`, or `{}`.

    Keyed on `external_id`, which
    `kg_ingest.slack_extract.register_slack_catalog` writes as the raw CHANNEL
    ID — so the join to a configured `sync_channel_ids` entry is exact, not a
    title match. The row's `title` is `#<name>`, added as a secondary key for a
    selection that carries a name where an id belongs.

    Reads through `document_catalog.list_documents`, the single accessor for
    that table (its module docstring explains why the table name appears
    nowhere else): the tenant filter is that function's job, not a filter this
    module hand-writes. Fails open to `{}` — a missing catalog degrades the
    answer to live-only, exactly what it was before.
    """
    if not company_id:
        return {}
    try:
        from app import document_catalog

        rows = document_catalog.list_documents(
            company_id, provider=document_catalog.PROVIDER_SLACK
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "slack-voc: catalog read failed for %s", company_id, exc_info=True
        )
        return {}
    out: dict[str, StoredSummary] = {}
    for row in rows or []:
        title = str(getattr(row, "title", "") or "").strip().lstrip("#")
        summary = StoredSummary(
            summary=str(getattr(row, "summary", "") or ""),
            topics=list(getattr(row, "topics", None) or []),
            channel_name=title,
            doc_date=str(getattr(row, "doc_date", "") or ""),
            updated_at=str(getattr(row, "updated_at", "") or ""),
            url=str(getattr(row, "url", "") or ""),
        )
        # An empty summary contributes nothing and must not mark a channel
        # covered — one live row (#spryntly) has exactly that shape.
        if not summary.present:
            continue
        external_id = str(getattr(row, "external_id", "") or "").strip()
        if external_id:
            out[external_id] = summary
        if title:
            out.setdefault(title.lower(), summary)
    return out


def _catalog_channel(key: str, catalog: dict[str, StoredSummary]) -> StoredSummary:
    """The stored summary for a channel id or name, or an empty one."""
    return (
        catalog.get(key)
        or catalog.get((key or "").lstrip("#").lower())
        or StoredSummary()
    )


def _attach_stored(
    reads: list[ChannelRead], catalog: dict[str, StoredSummary]
) -> None:
    """Give every channel its catalog summary, whatever the live read did.

    Attached to SUCCESSFUL reads too, not only failed ones. A week of messages
    plus "what this channel is generally about" is a better answer than either
    alone, and attaching it only on failure would make the summary look like an
    error artifact rather than what it is.
    """
    if not catalog:
        return
    for read in reads:
        if read.stored.present:
            continue
        stored = _catalog_channel(read.channel.id, catalog)
        if not stored.present and read.channel.name:
            stored = _catalog_channel(read.channel.name, catalog)
        read.stored = stored


def _append_stored_only(
    result: VocRead,
    catalog: dict[str, StoredSummary],
    allowed: "set[str] | None" = None,
) -> None:
    """Add channels the catalog knows about that the live scope did not cover.

    THE CONFIGURED SET AND THE INGESTED SET DIVERGE IN THE LIVE DATA, in both
    directions. One company's catalog holds four channels while its connection
    row carries no selection at all and the bot's membership shows a different
    five; three other companies have configured channels and zero catalog rows.
    An answer that silently covered only the intersection would be narrower
    than either set the user can see in the product.

    WHY THE SETS DIVERGE, traced: `register_slack_catalog` upserts one row per
    channel the SYNC passed it, and `deregister_document` is never called for
    Slack anywhere in the codebase. So a catalog row outlives the membership or
    selection that created it — permanently. The rows are a record of what was
    ever synced, not of what is configured now.

    `allowed` IS WHAT KEEPS A DESELECTED CHANNEL OUT. When an admin has ticked
    channels, that selection is a deliberate narrowing — the unticking flow
    purges synced data precisely to remove that content — so a never-collected
    catalog row from before it must not put the content back. `allowed` is the
    ticked id set, and nothing outside it is appended. `None` means nothing was
    ticked, where the product's own contract is "read them all" and the catalog
    is additional evidence about the same set rather than a way around a choice.

    This replaced a guard of `selection == SELECTION_CONFIGURED and connected`,
    which failed open on the exact path it mattered most: when `open_session`
    fails, the old code had not resolved the selection yet, so `selection` was
    still its `MEMBERSHIP` default AND `connected` was False — both halves
    false, every catalog row appended, the deselected channel back in the
    answer. A guard whose inputs are computed after the early return it guards
    is not a guard. `allowed` is now resolved before the session, so it is
    correct on every path including the failure ones.

    A CONFIGURED channel that merely could not be READ still gets its stored
    summary — that is `_attach_stored`, and it is unaffected by this guard. The
    other direction, configured but never ingested, is handled by the render's
    NOTHING-stored-either section, which names those channels rather than
    dropping them.

    These are added as STATUS_STORED: represented, clearly second-hand, and
    never counted as a live read.
    """
    if not catalog:
        return
    # Computed here rather than passed in: every call site would otherwise have
    # to remember to include NAMES as well as ids, and the one that forgot
    # would silently render a channel twice.
    seen = {r.channel.id for r in result.reads if r.channel.id}
    seen_lower = {s.lower() for s in seen}
    seen_lower |= {r.channel.name.lower() for r in result.reads if r.channel.name}
    for key, stored in catalog.items():
        # Skip the name-keyed aliases added alongside ids. A Slack conversation
        # id is uppercase C/G/D + uppercase alphanumerics and a channel NAME is
        # lowercase-only, so the two can never collide.
        if not key or not key[0].isupper():
            continue
        if allowed is not None and key not in allowed:
            continue
        if key in seen or key.lower() in seen_lower:
            continue
        if stored.channel_name and stored.channel_name.lower() in seen_lower:
            continue
        result.reads.append(ChannelRead(
            channel=VocChannel(id=key, name=stored.channel_name),
            status=STATUS_STORED,
            stored=stored,
        ))


def has_voc_channels(company_id: str) -> bool:
    """True when this company has Slack as a readable customer-feedback source.

    An ACTIVE Slack row is enough, and deliberately so: with no explicit
    selection the product's own contract is "every channel the bot was invited
    to", so a company that ticked nothing still has VoC channels. Cheap by
    design — one DB read, no decrypt, no network — because it gates a routing
    decision on the chat path.
    """
    from app import db

    try:
        return any(
            (row or {}).get("status") == "active"
            for row in (db.list_slack_connections(company_id) or [])
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "slack-voc: connectedness probe failed for %s", company_id, exc_info=True
        )
        return False


def _membership_channels(handle) -> list[VocChannel]:
    """Fallback set: every channel the bot is a member of, PRIVATE ONES
    INCLUDED.

    DELIBERATE, AND APURVA-APPROVED 2026-08-07 — do not "fix" this. With no
    explicit `sync_channel_*` selection the product's contract is the Settings
    copy: "with nothing ticked, every channel the bot has been invited to is
    read". `slack_sync.select_sync_channels` already implements exactly that for
    the ingest sync, and this is the same set, so chat and the sync cannot
    disagree about what a company's voice of customer is.

    Three consequences that look like bugs and are not:

      - a PRIVATE channel the bot was invited to IS read. An invitation is the
        grant; the bot cannot see a private channel it was not invited to, so
        there is nothing here for a public-only restriction to protect;
      - a company that connected Slack only for brief DELIVERY still has VoC
        channels, because the bot is in whatever it is in. Reviewed and
        accepted rather than gated behind an explicit selection;
      - `has_voc_channels` is therefore true for any active Slack row.

    This does NOT weaken the privacy gate, which is a different question:
    `_gate_and_name` still excludes DMs outright and still excludes private
    conversations the bot is NOT in. Membership is what both rules turn on.
    """
    out: list[VocChannel] = []
    seen: set[str] = set()
    for channel in handle.channel_list():
        cid = str(channel.get("id") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append(VocChannel(id=cid, name=str(channel.get("name") or "").strip()))
    return out


def _gate_and_name(
    handle, channels: list[VocChannel]
) -> tuple[list[VocChannel], list[ChannelRead]]:
    """`(readable, excluded)` — apply the privacy gate and fill in display names.

    A configured id is looked up in the channel directory so the block can say
    `#product-feedback` rather than `C0BLG8LV9D2`. The gate runs on the id ALONE
    when the directory has no record of it — fail-closed, because the question
    is not "is this private" but "can I prove reading this is shareable", and
    the safe answer to *I cannot tell* about a `D…` or `G…` id is no.

    A `C…` id the directory does not know still passes: a public channel the bot
    was never invited to is a read failure with actionable copy
    (`/invite @Sprntly`, or the idempotent auto-join), and calling that a
    privacy exclusion would tell the user the wrong thing to do.
    """
    bot_ids = handle.bot_channel_ids()
    readable: list[VocChannel] = []
    excluded: list[ChannelRead] = []
    for channel in channels:
        record = handle.find_channel(channel.id) or {}
        name = channel.name or str(record.get("name") or "").strip()
        resolved = VocChannel(id=channel.id, name=name)
        probe = dict(record)
        probe["id"] = record.get("id") or channel.id
        if not _shareable(probe, bot_ids):
            excluded.append(ChannelRead(channel=resolved, status=STATUS_EXCLUDED))
            continue
        readable.append(resolved)
    return readable, excluded


def _enabled() -> bool:
    """The operational kill switch, read at the ONE choke point every caller
    goes through (`read`). Put here rather than at the call sites for the reason
    the sweep's flag failed in 2026-08-05: a per-call-site check disarms exactly
    the call sites someone remembered, and the next caller added is exposed.

    Fails OPEN — an unreadable setting means the feature stays on, matching
    `_cross_connector_sweep_enabled`: this read is over sources the tenant
    already connected, through the same read-only adapter, so an unknown flag
    state risks latency rather than exposure, and latency is what the switch is
    for in the first place.
    """
    try:
        from app.config import settings

        return bool(getattr(settings, "slack_voc_channels", True))
    except Exception:  # noqa: BLE001
        return True


def _shareable(channel: dict, bot_ids: set[str]) -> bool:
    from app.connector_lookup.slack import is_shareable_channel

    return is_shareable_channel(channel, bot_ids)


# ── the read ─────────────────────────────────────────────────────────────────


def _read_one(handle, channel: VocChannel, days: int) -> ChannelRead:
    """One channel, through the SAME reader the `slack_channel_history` chat
    tool uses — same auto-join, same access-failure copy."""
    from app.connector_lookup import slack as slack_lookup

    result = ChannelRead(channel=channel)
    ref = f"#{channel.name}" if channel.name else channel.id
    # auto_join=False IS THE LOAD-BEARING ARGUMENT ON THIS LINE.
    # `conversations.join` adds the Sprntly bot to a channel and Slack posts a
    # join notice into the customer's workspace — an outward-facing WRITE. This
    # path runs on an implicit question ("what are our customers saying?") that
    # named no channel and no source, so it must never cause one. A channel the
    # bot is not in is reported as not-read with copy telling the user to
    # `/invite @Sprntly`, which is the same repair without us performing it on
    # their behalf. Do not "fix" a not_in_channel here by flipping this.
    history = slack_lookup.read_channel_history(
        handle, channel.id, days, auto_join=False
    )
    if history.status == slack_lookup.HISTORY_UNREADABLE:
        result.status = STATUS_UNREADABLE
        result.detail = history.detail
        return result
    if history.status == slack_lookup.HISTORY_EMPTY:
        result.status = STATUS_EMPTY
        return result
    # Render through the tool's own renderer so a message reads identically
    # whether it arrived here or through a named "read #demos" question. `ref`
    # is swapped in for the id so the heading says #product-feedback.
    history.ref = ref
    result.status = STATUS_OK
    result.text = slack_lookup.render_channel_history(handle, history)
    result.message_count = len(history.messages)
    return result


def _apply_caps(reads: list[ChannelRead]) -> None:
    """Per-channel truncation with an honest marker, then a total ceiling that
    DROPS whole low-priority channels rather than cutting one mid-message — a
    half-rendered channel reads as a complete one.

    THE FIRST CHANNEL IS ALWAYS KEPT, truncated to whatever budget exists.
    Without that floor a single channel larger than `TOTAL_CHARS` drops every
    channel behind it too, `render()` sees nothing usable and returns "", and
    the whole aggregate disappears from the answer with nothing said about it —
    the exact silent-absence failure this module exists to prevent, arrived at
    through the length path instead of the read path. `PER_CHANNEL_CHARS` is
    well under `TOTAL_CHARS` today so this cannot fire in production; the floor
    is here so the invariant does not depend on two constants staying in a
    relationship nothing enforces (a test asserts that relationship too).
    """
    from app.connector_lookup.base import cap_text

    budget = TOTAL_CHARS
    kept_any = False
    for read in reads:
        if not read.usable:
            continue
        read.text = cap_text(read.text, limit=PER_CHANNEL_CHARS)
        if len(read.text) <= budget:
            budget -= len(read.text)
            kept_any = True
            continue
        if not kept_any and budget > 0:
            read.text = cap_text(read.text, limit=budget)
            budget = 0
            kept_any = True
            continue
        read.status = STATUS_DROPPED
        read.text = ""


def read(
    company_id: str,
    *,
    days: int = DEFAULT_DAYS,
    budget_s: float = BUDGET_S,
    max_channels: int = MAX_CHANNELS,
    handle=None,
) -> VocRead:
    """Aggregate the company's configured VoC channels. NEVER raises.

    Every channel in scope appears in the result — with its messages, or with
    the reason it has none. A channel is dropped from the result only when the
    selection exceeds `max_channels`, and those are reported too.

    `handle` reuses a Slack session the caller already opened (the adapter's
    `slack_voc_channels` tool has one, with a warm channel directory). Omitted,
    one is opened here from `company_id` — never from anything a model supplied.
    """
    started = time.monotonic()
    result = VocRead(days=max(1, min(int(days or DEFAULT_DAYS), 90)))
    if not company_id:
        result.unavailable = "no company in scope"
        return result
    if not _enabled():
        result.unavailable = "the live Slack feedback-channel read is switched off"
        return result
    # Fetched BEFORE the session, deliberately. It is the fallback for the very
    # case where opening Slack fails, and fetching it after would make the
    # failure path the one with no data — which is how "no stored copy of those
    # messages" came to be reported about a channel the catalog had a summary
    # for.
    catalog = catalog_summaries(company_id)

    # RESOLVED BEFORE THE SESSION, and that ordering is load-bearing. It used to
    # run after, so on the open_session failure path it never ran at all:
    # `result.selection` kept its `SELECTION_MEMBERSHIP` default while a
    # selection genuinely existed, which (a) let `_append_stored_only`'s guard
    # fail open and hand back a DESELECTED channel from a stale catalog row, and
    # (b) made `render()` state the wrong provenance for its own content —
    # "no explicit channel selection is saved" when one was. One DB read either
    # way; doing it first makes both correct on every path.
    selected, explicit = configured_channels(company_id)
    result.selection = SELECTION_CONFIGURED if explicit else SELECTION_MEMBERSHIP
    #: The ids an admin actually ticked. `None` (not `set()`) means "nothing
    #: ticked", which is the read-them-all fallback; an empty set would read as
    #: "ticked nothing", the opposite.
    allowed = {c.id for c in selected} if explicit else None

    if handle is None:
        try:
            from app.connector_lookup import slack as slack_lookup

            session = slack_lookup.PROVIDER.open_session(company_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "slack-voc: could not open Slack for %s", company_id, exc_info=True
            )
            session = None
        if session is None:
            result.unavailable = (
                "Slack is not connected for this company, or its stored "
                "credential could not be used"
            )
            _append_stored_only(result, catalog, allowed)
            return result
        handle = session.handle

    try:
        team_id = getattr(handle, "team_id", "") or ""
        if team_id:
            # Same rule as `configured_channels`' own filter, applied to the set
            # already resolved above: drop a channel only when both workspace
            # ids are known and differ.
            selected = [
                c for c in selected
                if not (c.team_id and c.team_id != team_id)
            ]
            if explicit:
                allowed = {c.id for c in selected}
        # Warm the directory + user map on THIS thread. Both are lazily loaded
        # and shared by every worker below; letting N threads race to populate
        # them costs N identical fetches and (worse) makes the rendered author
        # names depend on which thread won.
        handle.channel_list()
        if not explicit:
            selected = _membership_channels(handle)
        readable, excluded = _gate_and_name(handle, selected)
        result.reads.extend(excluded)
        if len(readable) > max_channels:
            for channel in readable[max_channels:]:
                result.reads.append(ChannelRead(
                    channel=channel, status=STATUS_DROPPED,
                    detail=(
                        f"not read — more than {max_channels} channels are "
                        "configured and this pass reads the first "
                        f"{max_channels}"
                    ),
                ))
            readable = readable[:max_channels]
        if not readable:
            _attach_stored(result.reads, catalog)
            _append_stored_only(result, catalog, allowed)
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
            return result
        handle.user_map()
    except Exception:  # noqa: BLE001 — resolution degrades, never breaks chat
        logger.exception("slack-voc: channel resolution failed for %s", company_id)
        result.unavailable = "the Slack channel list could not be read"
        _attach_stored(result.reads, catalog)
        _append_stored_only(result, catalog, allowed)
        return result

    # THE BUDGET STARTS HERE, NOT AT `started`. `budget_s` bounds the parallel
    # fan-out — the thing that can hang on a dead upstream — and everything
    # above it is preparation: the catalog round-trip, the connection read, the
    # channel directory, the user map. Charging those to the same clock meant a
    # slow `document_catalog` read could consume the entire budget and every
    # channel came back TIMEOUT with zero reads attempted, reported as if Slack
    # had been unresponsive. Measured: a 0.6s catalog read against a 0.5s
    # budget returned all-timeout, nothing read.
    deadline = time.monotonic() + max(budget_s, 0.0)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        result.budget_exceeded = True
        result.reads.extend(
            ChannelRead(channel=c, status=STATUS_TIMEOUT) for c in readable
        )
        _attach_stored(result.reads, catalog)
        _append_stored_only(result, catalog, allowed)
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result

    executor = _futures.ThreadPoolExecutor(
        max_workers=len(readable), thread_name_prefix="slack-voc"
    )
    fanned: list[ChannelRead] = []
    try:
        pending = {
            executor.submit(_read_one, handle, channel, result.days): channel
            for channel in readable
        }
        done, not_done = _futures.wait(pending, timeout=remaining)
        for future in done:
            channel = pending[future]
            try:
                fanned.append(future.result())
            except Exception as exc:  # noqa: BLE001 — one channel, not the pass
                logger.warning(
                    "slack-voc: %s failed for %s", channel.label, company_id,
                    exc_info=True,
                )
                fanned.append(ChannelRead(
                    channel=channel, status=STATUS_ERROR,
                    detail=f"could not be read ({type(exc).__name__})",
                ))
        for future in not_done:
            fanned.append(ChannelRead(channel=pending[future], status=STATUS_TIMEOUT))
        result.budget_exceeded = bool(not_done)
    finally:
        # A hung upstream costs one leaked worker that dies on its own HTTP
        # timeout — never a held chat answer.
        executor.shutdown(wait=False, cancel_futures=True)

    order = {c.id: i for i, c in enumerate(readable)}
    fanned.sort(key=lambda r: order.get(r.channel.id, len(order)))
    result.reads.extend(fanned)
    _attach_stored(result.reads, catalog)
    _append_stored_only(result, catalog, allowed)
    _apply_caps(result.reads)
    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "slack-voc: %s selection=%s channels=%d read=%d in %dms [%s]",
        company_id, result.selection, result.channel_count,
        len(result.read_channels), result.elapsed_ms, result.outcome_summary(),
    )
    return result


def context_block(company_id: str, *, days: int = DEFAULT_DAYS) -> tuple[str, VocRead]:
    """`(prompt block, result)` — "" when nothing readable was found."""
    result = read(company_id, days=days)
    return result.render(), result
