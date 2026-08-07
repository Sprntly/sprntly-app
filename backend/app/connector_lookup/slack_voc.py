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

TENANCY + WRITES. Every read is keyed by the authenticated company id. Opening
the Slack session is READ-ONLY — `slack._load_tokens` decrypts stored tokens and
never refreshes, because Slack bot tokens (`xoxb-…`) do not expire. That is what
makes this safe to run on an implicit, source-agnostic question; see the
2026-08-05 sweep/`open_session` incident for the providers where it is not
(Jira, Confluence, HubSpot all WRITE on open).
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


@dataclass(frozen=True)
class VocChannel:
    """One configured customer-feedback channel."""

    id: str
    name: str = ""

    @property
    def label(self) -> str:
        return f"#{self.name}" if self.name else (self.id or "unknown channel")


@dataclass
class ChannelRead:
    """What one channel contributed, or why it contributed nothing."""

    channel: VocChannel
    status: str = STATUS_EMPTY
    text: str = ""
    detail: str = ""
    message_count: int = 0

    @property
    def usable(self) -> bool:
        return self.status == STATUS_OK and bool(self.text.strip())

    def reason(self) -> str:
        """Why this channel is absent from the answer. Never "" — a channel with
        no reason is a channel the model will read as "nothing was said there"."""
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
        return any(r.usable for r in self.reads)

    @property
    def connected(self) -> bool:
        return not self.unavailable

    @property
    def read_channels(self) -> list[ChannelRead]:
        return [r for r in self.reads if r.usable]

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
            if r.status in (STATUS_OK, STATUS_EMPTY) or r.usable
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
        parts = [
            "SLACK CUSTOMER-FEEDBACK CHANNELS — read live just now, covering "
            f"the last {self.days} days. These are {origin}. "
            f"{len(self.reads)} channel(s) were in scope; "
            f"{len(self.read_channels)} returned messages.",
            "Attribute every quote to ITS OWN channel — this block aggregates "
            "several, and a theme heard in one channel is not a theme heard "
            "across the company.",
        ]
        for read in self.reads:
            if read.usable:
                parts.append(
                    f"\n### {read.channel.label} — {read.message_count} "
                    f"message(s)\n{read.text.strip()}"
                )
            elif read.status == STATUS_EMPTY:
                parts.append(
                    f"\n### {read.channel.label}\n(read successfully — no "
                    f"messages posted in the last {self.days} days)"
                )
        missed = [
            r for r in self.unread_channels if r.status != STATUS_EMPTY
        ]
        if missed:
            parts.append(
                "\n### Feedback channels NOT read\n"
                + "\n".join(f"- {r.channel.label}: {r.reason()}" for r in missed)
                + "\nThese channels were NOT searched. Do not describe them as "
                "quiet, and do not let their absence make a company-wide claim "
                "look complete — name them if the answer implies full coverage."
            )
        return "\n".join(parts)


# ── channel resolution ───────────────────────────────────────────────────────


def _row_config(row: dict) -> dict:
    from app.connectors.slack_company import row_config

    try:
        return row_config(row) or {}
    except Exception:  # noqa: BLE001 — an unparseable row contributes nothing
        return {}


def configured_channels(company_id: str) -> tuple[list[VocChannel], bool]:
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
            out.append(VocChannel(id=cid, name=str(names.get(cid) or "").strip()))
    return out, explicit


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
    """Fallback set: every channel the bot is a member of."""
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
    history = slack_lookup.read_channel_history(handle, channel.id, days)
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
    half-rendered channel reads as a complete one."""
    from app.connector_lookup.base import cap_text

    budget = TOTAL_CHARS
    for read in reads:
        if not read.usable:
            continue
        read.text = cap_text(read.text, limit=PER_CHANNEL_CHARS)
        if len(read.text) <= budget:
            budget -= len(read.text)
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
            return result
        handle = session.handle

    try:
        selected, explicit = configured_channels(company_id)
        result.selection = (
            SELECTION_CONFIGURED if explicit else SELECTION_MEMBERSHIP
        )
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
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
            return result
        handle.user_map()
    except Exception:  # noqa: BLE001 — resolution degrades, never breaks chat
        logger.exception("slack-voc: channel resolution failed for %s", company_id)
        result.unavailable = "the Slack channel list could not be read"
        return result

    deadline = started + max(budget_s, 0.0)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        result.budget_exceeded = True
        result.reads.extend(
            ChannelRead(channel=c, status=STATUS_TIMEOUT) for c in readable
        )
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
