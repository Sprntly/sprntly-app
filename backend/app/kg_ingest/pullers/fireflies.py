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
from datetime import datetime, timezone
from typing import Iterator, Optional

import requests

from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

URL = "https://api.fireflies.ai/graphql"
_TIMEOUT = 30
_LIMIT = 25            # KG-ingest cap — most recent meetings, pilot-scale
_PAGE_SIZE = 50        # Fireflies API max per transcripts query — paginate past it
# On-demand digest cap — the safety ceiling across ALL pages, not a page size.
# A busy quarter is ~150 calls; 300 leaves headroom while bounding a runaway
# window. The digest runner discloses when a window hits this cap.
_DIGEST_LIMIT = 300
# Per-call verbatim-sentence cap for the digest. Bounds the transient corpus
# (a long call can be 1000+ sentences); the skill only needs raw material to
# pick 2–3 strong quotes per theme, not the whole transcript.
_QUOTES_PER_CALL = 60

# Distilled-only query (KG-ingest path) — no `sentences`, per §6.
_QUERY = """
query Transcripts($limit: Int, $fromDate: DateTime, $toDate: DateTime) {
  transcripts(limit: $limit, fromDate: $fromDate, toDate: $toDate) {
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


@dataclass
class CallTranscript:
    """One Fireflies meeting, distilled + a bounded sample of verbatim quotes.

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

    def render(self, max_quotes: Optional[int] = None) -> str:
        """Render one call into the skill's input corpus — header, distilled
        summary, action items, then the verbatim quote block (speaker-attributed
        so the skill can source each quote). `max_quotes` trims the quote block
        (0 = summary only) so the digest runner can fit every call in a big
        window into its corpus budget instead of dropping whole calls."""
        who = ", ".join(self.participants) if self.participants else "unknown"
        parts = [
            f"## Call: {self.title or '(untitled)'}",
            f"date: {self.date or 'unknown'} · participants: {who}",
        ]
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


def pull(
    api_key: str,
    *,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = _LIMIT,
) -> Iterator[RawRecord]:
    """KG-ingest pull: distilled summaries → RawRecords (no raw sentences, §6).

    `since`/`until` scope the window (omit for "most recent `limit`"); the
    weekly sync passes none and gets the historical default behaviour."""
    for t in _post(api_key, _QUERY, {
        "limit": limit, "fromDate": _iso(since), "toDate": _iso(until),
    }):
        s = t.get("summary") or {}
        text_parts = []
        if s.get("overview"):
            text_parts.append(f"summary: {s['overview']}")
        if s.get("action_items"):
            text_parts.append(f"action items: {s['action_items']}")
        yield RawRecord(
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
