"""Slack channels -> KG extraction, one document per channel (§1b).

Slack already feeds the KG today, but only as a single undifferentiated
`slack_channels.md` corpus blob with a model-guessed `source_type` and no
per-channel identity (see `connectors.slack_sync` module docstring for the
full history). This module gives each synced channel its own extraction
document, modelled file-for-file on `kg_ingest.drive_extract` (the existing
corpus-path-provider-that-also-feeds-the-KG template):

  - one `extract_document` call per ~6k-char chunk of a channel's markdown,
    capped per channel at `_MAX_KG_CHARS` (mirrors Drive's caps exactly)
  - `source_type_default="communication"` so Slack's decay window is a
    deterministic 7 days rather than whatever the model happened to type a
    signal as (a genuine `customer_voice` verbatim is still kept on merit —
    see `source_type_default` vs `force_source_type` in
    `graph.extractor.extract_document`)
  - `origin="upload"`, UNCHANGED from today — flipping it to "connector"
    is a deliberate, out-of-scope deferral (see `connectors.slack_sync`)
  - a content-hash ledger gate (`db.kg_ingest_ledger`), not a watermark: a
    re-run with no new messages in a channel costs zero LLM calls, and an
    edited message re-hashes and re-extracts just that chunk

Zero additional Slack API calls: the channel markdown this module extracts
is collected by `connectors.slack_sync.sync_slack` from data it already
fetched to build the corpus file, and handed in as `SlackChannelDoc`s.

Error-isolated per chunk (not per channel): a chunk raising during
extraction is logged (company id, channel name, error class only — never
message text, never a token, never channel body content) and its hash is
never recorded, so it retries on the next sync; every other chunk, in the
same channel or a different one, proceeds unaffected.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app import document_catalog
from app.db.kg_ingest_ledger import record_hashes, seen_hashes
from app.graph.extractor import extract_document
from app.graph.facade import GraphFacade
from app.kg_ingest.drive_extract import _chunks

logger = logging.getLogger(__name__)

#: Catalog `source_name` for Slack documents — mirrors `drive_extract`'s
#: `_DRIVE_SOURCE_NAME`: every Slack row carries the connector's own label,
#: which is what a user would say ("the #support channel in Slack").
_SLACK_SOURCE_NAME = "Slack"

# Mirrors drive_extract._CHUNK_CHARS / runner._BATCH_CHAR_BUDGET exactly —
# one extraction call per ~6k chars across every ingestion path.
_CHUNK_CHARS = 6000
# Mirrors drive_extract._MAX_KG_CHARS — but PER CHANNEL, not per workspace:
# Slack is the largest corpus document by construction (MAX_CHANNELS=50 x
# MAX_MESSAGES_PER_CHANNEL=200), so a workspace-wide cap would starve later
# channels; a per-channel cap treats every channel the same regardless of
# sync order.
_MAX_KG_CHARS = 60_000

# The ledger's provider label for record_hashes — a plain string, not a
# connector name from PULLERS (Slack's KG path bypasses that registry
# entirely; see the sync path in connectors.slack_sync).
_LEDGER_PROVIDER = "slack"

_SLACK_SOURCE_HINT = (
    "communication (Slack channel messages — team + customer discussion, "
    "support threads, decisions made in-channel: treat a quoted line as "
    "first-party evidence of what was actually said, not as a decision of "
    "record)"
)


@dataclass
class SlackChannelDoc:
    """One synced channel's markdown section, ready for per-channel
    extraction. Built inline in `connectors.slack_sync.sync_slack`'s
    existing per-channel loop — the channel markdown, id and name are
    already computed there for the corpus write, so collecting this costs
    no additional Slack API call."""
    channel_id: str      # "C0123ABCD" — stable, the catalog external_id in a future ticket
    channel_name: str    # "product-feedback", no leading '#'
    text: str            # the channel's markdown section, verbatim from
                          # channel_messages_to_markdown
    latest_ts: str = ""  # newest message ts seen this pass (Slack "epoch.seq")
    message_count: int = 0
    is_private: bool = False


def _chunk_hash(channel_id: str, chunk: str) -> str:
    """Ledger key for one chunk. Scoped by channel id (not just chunk text)
    so two different channels that happen to share identical message text
    can never collide in the ledger — content-hash dedupe must never cause
    one channel's chunk to suppress another channel's otherwise-identical
    one."""
    return hashlib.sha256(
        f"{channel_id}|{chunk}".encode("utf-8", "replace")
    ).hexdigest()


def extract_slack_channels(
    facade: GraphFacade, company_id: str, docs: list[SlackChannelDoc]
) -> dict:
    """Extract each channel's markdown into the KG as its own document(s).

    Ledger-gated (content-hash, before any LLM call) and error-isolated per
    chunk: one bad chunk is logged + reported, every other chunk — in the
    same channel or a different one — still extracts. Returns totals plus
    ``errors``.
    """
    totals = {
        "channels": 0, "chunks": 0, "signals": 0, "themes": 0,
        "skipped": 0, "deduped": 0,
    }
    errors: list[str] = []

    # Build every (channel, doc_name, chunk_text, hash) this run would
    # extract, skipping empty channels and truncating oversized ones up
    # front — the ledger gate below has to see every candidate chunk before
    # any LLM call is made.
    items: list[tuple[SlackChannelDoc, str, str, str]] = []
    channels_considered = 0
    for doc in docs:
        if doc.message_count == 0:
            # The `_No recent messages._` body slack_sync writes for an
            # empty channel section — nothing to extract.
            continue
        channels_considered += 1
        text = doc.text
        if len(text) > _MAX_KG_CHARS:
            logger.info(
                "slack-extract: channel %r truncated %d -> %d chars "
                "for company %s",
                doc.channel_name, len(text), _MAX_KG_CHARS, company_id,
            )
            text = text[:_MAX_KG_CHARS]
        parts = _chunks(text)
        n = len(parts)
        for i, part in enumerate(parts):
            doc_name = (
                f"slack/#{doc.channel_name}" if n == 1
                else f"slack/#{doc.channel_name} (part {i + 1}/{n})"
            )
            items.append((doc, doc_name, part, _chunk_hash(doc.channel_id, part)))

    totals["channels"] = channels_considered

    # Ledger gate — BEFORE any LLM call. Fail-open by construction:
    # seen_hashes returns {} on any error, so this degrades to extracting
    # everything (pre-ledger behavior) rather than skipping unread data.
    hashes = list({h for *_rest, h in items})
    seen = seen_hashes(company_id, hashes)
    fresh = [it for it in items if it[3] not in seen]
    totals["deduped"] = len(items) - len(fresh)

    for doc, doc_name, part, chash in fresh:
        totals["chunks"] += 1
        try:
            r = extract_document(
                facade, company_id,
                doc_name=doc_name,
                text=part,
                agent="ingest:slack",
                source_hint=_SLACK_SOURCE_HINT,
                # "upload", UNCHANGED from today — see module docstring.
                origin="upload",
                # Deterministic floor: a model-guessed seeded/invalid type
                # is re-stamped `communication` (7-day window); a genuine
                # customer_voice verbatim is kept on merit.
                source_type_default="communication",
                # NEVER "channel" — that key promotes into the typed
                # Signal.channel column, whose only meaning in this
                # codebase is "upload" (read by the brief sufficiency
                # gate). See extractor.py's provenance-construction
                # docstring for the trap this avoids.
                provenance_extra={
                    "slack_channel_id": doc.channel_id,
                    "slack_channel_name": doc.channel_name,
                    "slack_latest_ts": doc.latest_ts,
                },
                # Haiku relevance + category triage ahead of every chunk —
                # per-channel documents put message text in the triage
                # window, fixing the summary-table-classification defect.
                triage=True,
            )
            for k in ("signals", "themes", "skipped"):
                totals[k] += r[k]
            # Only a chunk that made it through extraction is recorded — a
            # failed chunk keeps its hash out of the ledger and is simply
            # re-extracted on the next sync.
            record_hashes(company_id, _LEDGER_PROVIDER, [chash])
        except Exception as e:  # noqa: BLE001 — error-isolation per chunk
            # Identifiers only — company id, channel name, error class.
            # Never message text, never a token, never channel body
            # content: this line (and the returned errors entry) are the
            # only observability this failure gets, and both must stay
            # safe to read without exposing what was said in the channel.
            logger.error(
                "slack-extract: chunk failed company=%s channel=%r "
                "doc=%r error_class=%s",
                company_id, doc.channel_name, doc_name, type(e).__name__,
            )
            errors.append(f"{doc.channel_name}: {type(e).__name__}")

    logger.info(
        "slack-extract done: company=%s channels=%d chunks=%d signals=%d "
        "errors=%d",
        company_id, totals["channels"], totals["chunks"], totals["signals"],
        len(errors),
    )
    return {**totals, "errors": errors}


# ── Catalog registration ──────────────────────────────────────────────
#
# One `document_catalog` row per SYNCED channel — unconditionally, unlike KG
# extraction above, which skips a channel with no messages. Catalog rows are
# what make a channel individually selectable, ranked and CITED; an empty
# channel is still a real, findable (if contentless) document.


def _channel_permalink(channel_id: str, team_domain: Optional[str]) -> Optional[str]:
    """A working Slack deep link, or `None` when the workspace domain could
    not be resolved. Never fabricates the undocumented `slack.com/archives`
    form — a missing link degrades to an uncited-but-named document; a wrong
    one would be worse."""
    if not team_domain:
        return None
    return f"https://{team_domain}.slack.com/archives/{channel_id}"


def _latest_message_iso(ts: str) -> Optional[str]:
    """A Slack `epoch.seq` timestamp as ISO-8601 UTC, or `None` for an empty
    or unparseable one — a channel that synced with no messages must store
    NULL, never an epoch-zero date."""
    if not ts:
        return None
    try:
        epoch = float(ts.split(".")[0])
    except (TypeError, ValueError, IndexError):
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def register_slack_catalog(
    company_id: str,
    docs: list[SlackChannelDoc],
    *,
    team_domain: Optional[str] = None,
) -> None:
    """Upsert one `document_catalog` row per channel in `docs`.

    `content_hash` is taken over the channel's FULL section text
    (`doc.text`), never the `_MAX_KG_CHARS`-truncated slice extraction uses —
    an edit past the truncation point must still re-hash, or the summary
    freezes at an old version (mirrors `pullers.confluence` / `drive_extract`).

    Error isolation matches the Confluence puller, not Drive: wrapped in
    try/except, log-and-continue. Drive's deliberate no-except exists because
    a swallowed failure there would advance `kg_file_mtime` and strand the
    file until a human edits it in Drive. Slack has no such latch — the next
    sync re-registers a failed channel for free, keyed on the same content
    hash the KG ledger already uses — so copying Drive's no-except here would
    take a whole sync down for a cataloguing failure it does not need to."""
    for doc in docs:
        try:
            document_catalog.register_document(
                company_id,
                provider=document_catalog.PROVIDER_SLACK,
                external_id=doc.channel_id,
                title=f"#{doc.channel_name}",
                source_name=_SLACK_SOURCE_NAME,
                url=_channel_permalink(doc.channel_id, team_domain),
                doc_date=_latest_message_iso(doc.latest_ts),
                content_hash=document_catalog.content_hash_for(doc.text),
                get_text=lambda text=doc.text: text,
            )
        except Exception as e:  # noqa: BLE001 — log-and-continue (Confluence precedent)
            # Identifiers only — company id, channel id, error class. Never
            # message text, never a token, never channel body content.
            logger.warning(
                "slack-catalog: registration failed company=%s channel=%s "
                "error_class=%s",
                company_id, doc.channel_id, type(e).__name__,
            )


# Per-company locks so overlapping runs (a manual re-sync racing the
# scheduled refresh) serialize instead of extracting the same channels
# twice in parallel. Mirrors drive_extract.py's identical idiom.
_extract_locks: dict[str, threading.Lock] = {}
_extract_locks_guard = threading.Lock()


def _extract_lock(company_id: str) -> threading.Lock:
    with _extract_locks_guard:
        lock = _extract_locks.get(company_id)
        if lock is None:
            lock = threading.Lock()
            _extract_locks[company_id] = lock
        return lock


def run_slack_extract(
    company_id: str,
    docs: list[SlackChannelDoc],
    *,
    team_domain: Optional[str] = None,
) -> dict:
    """Blocking extract + catalog registration. Serialized per company —
    overlapping callers can't double-extract the same channels."""
    with _extract_lock(company_id):
        facade = GraphFacade()
        result = extract_slack_channels(facade, company_id, docs)
        register_slack_catalog(company_id, docs, team_domain=team_domain)
        return result


def _run_isolated(
    company_id: str,
    docs: list[SlackChannelDoc],
    *,
    team_domain: Optional[str] = None,
) -> None:
    try:
        run_slack_extract(company_id, docs, team_domain=team_domain)
    except Exception:  # noqa: BLE001 — fully isolated
        logger.exception("slack-extract failed for %s", company_id)


def kickoff_slack_extract(
    company_id: str,
    docs: list[SlackChannelDoc],
    *,
    team_domain: Optional[str] = None,
) -> bool:
    """Fire-and-forget: extract synced Slack channels into the KG and
    register their catalog rows, in a daemon thread. Never blocks; never
    raises into the caller's sync flow."""
    if not docs:
        return False
    try:
        t = threading.Thread(
            target=_run_isolated, args=(company_id, docs),
            kwargs={"team_domain": team_domain},
            name="slack-kg-extract", daemon=True,
        )
        t.start()
        return True
    except Exception:  # noqa: BLE001 — never let a thread-spawn failure break the sync
        logger.exception(
            "slack-extract: failed to start thread for %s", company_id
        )
        return False
