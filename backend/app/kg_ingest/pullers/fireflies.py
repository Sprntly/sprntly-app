"""Fireflies puller — meeting transcripts → RawRecords (+ on-demand digest fetch).

GraphQL API (api.fireflies.ai), API-key auth (per #106).

TWO surfaces, deliberately separated by what they persist:

  • pull()        — the KG-ingest path. Pulls the DISTILLED layer only
                    (summary overview + action items + keywords), never raw
                    sentences, and yields RawRecords the runner extracts into
                    the KG. This is the no-raw-dump §6 contract — unchanged
                    except that it now accepts an optional date window/limit so
                    a sync can be scoped to "what landed recently".

  • fetch_calls() — the on-demand call-digest path. Pulls the same distilled
                    layer PLUS a bounded sample of verbatim sentences so the
                    voice-of-customer-report skill has real, sourced quotes.
                    These quotes are TRANSIENT: returned to the digest runner,
                    rendered into the skill's input corpus, and never written
                    to the KG. Nothing here persists raw transcript.

Raw-audio ingestion (transcribe an uploaded recording with Whisper, then
extract) is a separate path in app/kg_ingest/audio_ingest.py — untouched.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

import requests

from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

URL = "https://api.fireflies.ai/graphql"
_TIMEOUT = 30
# THE FIRST SYNC's history ceiling. Was 25 with no pagination — "pilot-scale",
# and the reason a workspace with years of Fireflies history answered "no
# signals in synced data" for every week older than about three days: at ~10
# meetings a day the newest 25 transcripts ARE three days, the ledger deduped
# all of them on every later cycle, and nothing older ever entered the graph
# (reported 2026-08-15). 500 matches `call_index._SYNC_LIMIT`, so the two
# Fireflies surfaces cover the same history rather than disagreeing about how
# much of it exists.
_LIMIT = 500
# How far back the FIRST sync reaches. A ceiling on cost, not on interest: each
# fresh record costs one extraction call, so this bounds what a new connection
# can spend at once. Later syncs are incremental and ignore it.
_HISTORY_DAYS = 365
_PAGE_SIZE = 50        # Fireflies API max per transcripts query — paginate past it
# Re-fetch overlap on an incremental sync, matching `call_index`'s: a meeting
# whose transcript lands late must not fall in the gap between two cursors.
# Costs nothing — the ledger dedups by content hash.
_INCREMENTAL_OVERLAP_DAYS = 1
#: Connection-config key holding the instant the last KG pull completed. Read
#: and advanced exactly like Zoom's `CONFIG_LAST_SYNCED_UNTIL`; absent means
#: "never synced", which is what triggers the one-time history backfill.
CONFIG_KG_SYNCED_UNTIL = "kg_last_synced_until"
# On-demand digest cap — the safety ceiling across ALL pages, not a page size.
# A busy quarter is ~150 calls; 300 leaves headroom while bounding a runaway
# window. The digest runner discloses when a window hits this cap.
_DIGEST_LIMIT = 300
# Per-call verbatim-sentence cap for the digest. Bounds the transient corpus
# (a long call can be 1000+ sentences); the skill only needs raw material to
# pick 2–3 strong quotes per theme, not the whole transcript.
_QUOTES_PER_CALL = 60

# Distilled-only query (KG-ingest path) — no `sentences`, per §6. `skip` is
# what lets a first sync walk past the API's 50-per-query ceiling; without it
# this path could never see more than one page however high the cap was set.
_QUERY = """
query Transcripts($limit: Int, $skip: Int, $fromDate: DateTime, $toDate: DateTime) {
  transcripts(limit: $limit, skip: $skip, fromDate: $fromDate, toDate: $toDate) {
    id
    title
    date
    participants
    summary { overview action_items keywords }
  }
}
"""

# Digest query (on-demand path) — adds `sentences` for transient quotes and
# `skip` so windows holding more than one API page (50) can be fetched in full.
_QUERY_WITH_SENTENCES = """
query Transcripts($limit: Int, $skip: Int, $fromDate: DateTime, $toDate: DateTime) {
  transcripts(limit: $limit, skip: $skip, fromDate: $fromDate, toDate: $toDate) {
    id
    title
    date
    participants
    summary { overview action_items keywords }
    sentences { speaker_name text }
  }
}
"""


#: The source a CallTranscript came from when nothing says otherwise. A DEFAULT
#: rather than a required field so every existing construction site — this
#: puller, connector_lookup/fireflies — is untouched, exactly as
#: `call_index.CallRow.provider` was introduced for the same two sources.
PROVIDER = "fireflies"


@dataclass
class CallTranscript:
    """One recorded customer call, distilled + a bounded sample of verbatim quotes.

    Fireflies-shaped by history, PROVIDER-AGNOSTIC by contract: the on-demand
    call digest (app/call_digest.py) fills this same record from Zoom cloud
    recordings and merges both sources into one corpus, so nothing here may
    assume a field only Fireflies can populate. It still lives in this module
    because this is where it is populated most, and moving it would churn three
    import sites for no behaviour change.

    Lives only for the duration of a digest request — never persisted. The
    `quotes` are the transient verbatim material the VoC skill mines; everything
    else mirrors the distilled layer pull() already ingests.
    """
    external_id: str
    title: str
    date: str                                   # ISO 8601 (or "" if unknown)
    participants: list[str] = field(default_factory=list)
    overview: str = ""
    action_items: str = ""
    keywords: list[str] = field(default_factory=list)
    quotes: list[dict] = field(default_factory=list)  # [{"speaker", "text"}]
    #: Which source issued `external_id` — meaningful only to that provider.
    #: Defaulted and placed last so every existing call site stays as it was.
    provider: str = PROVIDER
    #: An honest caveat about THIS call, rendered ahead of its content: a Zoom
    #: recording whose account has audio transcription switched off still enters
    #: the corpus saying so, rather than being dropped and leaving a gap nothing
    #: explains. Empty for a call that needs no caveat — which is every
    #: Fireflies call, since Fireflies only returns transcribed meetings.
    note: str = ""

    def render(self, max_quotes: Optional[int] = None) -> str:
        """Render one call into the skill's input corpus — header, distilled
        summary, action items, then the verbatim quote block (speaker-attributed
        so the skill can source each quote). `max_quotes` trims the quote block
        (0 = summary only) so the digest runner can fit every call in a big
        window into its corpus budget instead of dropping whole calls."""
        who = ", ".join(self.participants) if self.participants else "unknown"
        head = f"date: {self.date or 'unknown'} · participants: {who}"
        if self.provider and self.provider != PROVIDER:
            # Only a NON-Fireflies call carries a source tag, so a corpus built
            # from Fireflies alone renders byte-identically to before the digest
            # learned about a second source — and a mixed corpus can still
            # attribute every theme to where it was heard.
            head += f" · source: {self.provider}"
        parts = [f"## Call: {self.title or '(untitled)'}", head]
        if self.note:
            parts.append(f"note: {self.note}")
        if self.overview:
            parts.append(f"summary: {self.overview}")
        if self.action_items:
            parts.append(f"action items: {self.action_items}")
        if self.keywords:
            parts.append(f"keywords: {', '.join(self.keywords)}")
        quotes = self.quotes if max_quotes is None else self.quotes[:max_quotes]
        if quotes:
            parts.append("verbatim quotes:")
            parts.extend(f'  - {q["speaker"]}: "{q["text"]}"' for q in quotes)
        return "\n".join(parts)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    """Render a datetime as a UTC ISO 8601 string for the GraphQL DateTime args.
    Naive datetimes are assumed UTC. None passes through (no bound)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _normalize_date(raw) -> str:
    """Fireflies returns `date` as epoch milliseconds (Float). Render it as ISO
    for display; pass through a string untouched; "" when absent/unparseable."""
    if raw in (None, ""):
        return ""
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw / 1000, tz=timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return ""
    return str(raw)


def _post(api_key: str, query: str, variables: dict) -> list[dict]:
    """Run a transcripts query and return the raw transcript dicts. Raises on
    transport error or a GraphQL `errors` array (caller isolates)."""
    r = requests.post(
        URL,
        json={"query": query, "variables": variables},
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(f"Fireflies GraphQL error: {body['errors'][:1]}")
    return (body.get("data") or {}).get("transcripts", []) or []


def _record_from(t: dict) -> RawRecord:
    """One transcript's distilled layer as a RawRecord. No sentences, per §6."""
    s = t.get("summary") or {}
    text_parts = []
    if s.get("overview"):
        text_parts.append(f"summary: {s['overview']}")
    if s.get("action_items"):
        text_parts.append(f"action items: {s['action_items']}")
    return RawRecord(
        provider="fireflies",
        kind="meeting",
        external_id=str(t["id"]),
        title=t.get("title", ""),
        text="\n".join(text_parts)[:3000],
        properties={
            "participants": t.get("participants") or [],
            "keywords": s.get("keywords") or [],
        },
        timestamp=_normalize_date(t.get("date")),
    )


def _kg_cursor(enterprise_id: Optional[str]) -> Optional[datetime]:
    """When this company's KG pull last completed, or None if it never has.

    Best-effort: an unreadable connection row reads as "never synced", which
    costs one wide backfill rather than silently skipping history."""
    if not enterprise_id:
        return None
    try:
        from app import db

        row = db.get_connection(enterprise_id, PROVIDER) or {}
        raw = (row.get("config") or {}).get(CONFIG_KG_SYNCED_UNTIL)
        if not raw:
            return None
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001 — unknown cursor → full backfill, never a skip
        logger.warning("fireflies: could not read KG cursor for %s",
                       enterprise_id, exc_info=True)
        return None


def _stamp_kg_cursor(enterprise_id: Optional[str], when: datetime) -> None:
    """Advance the cursor after a completed pull. A MERGE via
    `patch_connection_config`, never a wholesale write — the same config holds
    the api key payload's neighbours, and replacing it would take them with it.

    Best-effort: a pull that yielded records must not be reported as failed
    because the bookkeeping did not land."""
    if not enterprise_id:
        return
    try:
        from app import db

        db.patch_connection_config(
            enterprise_id, PROVIDER, {CONFIG_KG_SYNCED_UNTIL: _iso(when)}
        )
    except Exception:  # noqa: BLE001 — bookkeeping must not fail a good sync
        logger.warning("fireflies: could not stamp KG cursor for %s",
                       enterprise_id, exc_info=True)


def pull(
    api_key: str,
    *,
    enterprise_id: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = _LIMIT,
) -> Iterator[RawRecord]:
    """KG-ingest pull: distilled summaries → RawRecords (no raw sentences, §6).

    PAGINATED, and BOUNDED BY A CURSOR — both new on 2026-08-16, and the two
    halves of the same fix:

      * Paginated, because the API caps a transcripts query at 50 and this
        path never passed `skip`. However high `limit` went, one page was all
        it could ever see, so a year of history was unreachable by
        construction.
      * Cursor-bounded, because the naive way to reach that history — ask for
        a year on every sync — is what exhausted a tenant's daily Fireflies
        quota through `call_index` the day before (429 `too_many_requests`
        until the next UTC midnight, taking every other Fireflies read down
        with it). The FIRST sync for a connection walks up to `limit`
        transcripts back `_HISTORY_DAYS`; every later sync asks only for what
        landed since the last one, which is one page and one request.

    `since`/`until` still override explicitly, for a caller that wants a
    specific window (and a backfill script that wants a wider one).
    """
    explicit_window = since is not None or until is not None
    cursor = None if explicit_window else _kg_cursor(enterprise_id)
    started = datetime.now(timezone.utc)

    if not explicit_window:
        if cursor is not None:
            since = cursor - timedelta(days=_INCREMENTAL_OVERLAP_DAYS)
        else:
            # First sync for this connection: the one-time history backfill.
            since = started - timedelta(days=_HISTORY_DAYS)
            logger.info(
                "fireflies: first KG sync for %s — backfilling up to %d "
                "transcripts over the last %d days",
                enterprise_id, limit, _HISTORY_DAYS,
            )

    fetched = 0
    skip = 0
    while fetched < limit:
        page_size = min(_PAGE_SIZE, limit - fetched)
        page = _post(api_key, _QUERY, {
            "limit": page_size, "skip": skip,
            "fromDate": _iso(since), "toDate": _iso(until),
        })
        for t in page:
            yield _record_from(t)
        fetched += len(page)
        if len(page) < page_size:   # short page → window exhausted
            break
        skip += len(page)

    if fetched >= limit:
        # Say so rather than implying the window is covered — the next sync
        # resumes from the cursor, so the OLDEST tail is what stays unread.
        logger.info(
            "fireflies: KG pull for %s hit the %d-transcript cap; older "
            "history in this window was not fetched",
            enterprise_id, limit,
        )

    # Only after a clean walk. An exception above propagates and leaves the
    # cursor where it was, so the next run retries the same window instead of
    # stepping over meetings nothing would ever come back for.
    if not explicit_window:
        _stamp_kg_cursor(enterprise_id, started)


def fetch_calls(
    api_key: str,
    *,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = _DIGEST_LIMIT,
) -> list[CallTranscript]:
    """On-demand digest fetch: distilled summary + a bounded sample of verbatim
    quotes per call, for the window. The quotes are transient (never persisted)
    — they exist only to give voice-of-customer-report real, sourced material.

    Pages through the API (Fireflies caps a transcripts query at 50) until the
    window is exhausted or `limit` calls are collected, so "the last 30 days"
    means every call in those 30 days — not the newest page. Returns calls
    newest-first as the API yields them. Raises on API failure so the digest
    runner can tell the user "couldn't reach Fireflies" rather than silently
    produce an empty report."""
    calls: list[CallTranscript] = []
    skip = 0
    while len(calls) < limit:
        page_size = min(_PAGE_SIZE, limit - len(calls))
        page = _post(api_key, _QUERY_WITH_SENTENCES, {
            "limit": page_size, "skip": skip,
            "fromDate": _iso(since), "toDate": _iso(until),
        })
        for t in page[:page_size]:
            s = t.get("summary") or {}
            quotes: list[dict] = []
            for sent in (t.get("sentences") or [])[:_QUOTES_PER_CALL]:
                text = (sent.get("text") or "").strip()
                if not text:
                    continue
                quotes.append({"speaker": sent.get("speaker_name") or "?", "text": text})
            calls.append(CallTranscript(
                external_id=str(t["id"]),
                title=t.get("title", ""),
                date=_normalize_date(t.get("date")),
                participants=t.get("participants") or [],
                overview=s.get("overview") or "",
                action_items=s.get("action_items") or "",
                keywords=s.get("keywords") or [],
                quotes=quotes,
            ))
        if len(page) < page_size:  # short page → window exhausted
            break
        skip += page_size
    return calls
