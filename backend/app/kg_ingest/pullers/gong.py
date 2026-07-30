"""Gong puller — distilled call intelligence → RawRecords (voice of customer).

REST API (api.gong.io/v2), Basic auth with a workspace Access Key + Secret
(see app/connectors/gong.py — the runner hands us the precomputed basic
token). One endpoint does everything: POST /v2/calls/extensive lists calls
in a date window WITH the content we ask for via `contentSelector`, cursor-
paginated.

Same no-raw-dump contract as the Fireflies puller (§6): the KG-ingest path
pulls the DISTILLED layer only — Gong's own call brief, key points,
highlights, topics and trackers — never transcript sentences. Those fields
are Gong-generated summaries of what the customer said, which is exactly the
voice-of-customer evidence the brief synthesizes; raw transcripts stay in
Gong. (Workspaces without Gong's AI summaries simply yield thinner records —
title, participants, topics — never a transcript fallback.)

Rate limits: Gong defaults to 3 calls/sec + 10k/day; a sync here is one
request per 100 calls, far under both.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional

import requests

from app.connectors.gong import API_BASE
from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

_TIMEOUT = 30
_LIMIT = 50            # KG-ingest cap per sync — newest calls win, pilot-scale
_PAGE_SIZE = 100       # Gong's max page size for /calls/extensive
_WINDOW_DAYS = 90      # default lookback when the runner passes no window
_TEXT_CAP = 3000       # per-record extraction budget (mirrors fireflies)

# Ask Gong for the distilled layer only — no media, no transcript structure.
_CONTENT_SELECTOR = {
    "exposedFields": {
        "parties": True,
        "content": {
            "brief": True,
            "keyPoints": True,
            "highlights": True,
            "topics": True,
            "trackers": True,
        },
    }
}


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _post_extensive(token: str, body: dict[str, Any]) -> dict[str, Any]:
    """One /v2/calls/extensive page. Raises on transport/API error (the
    runner isolates per-provider failures). Gong signals 'no calls in this
    window' as HTTP 404 with a requestId body — normalized to an empty page
    rather than an error."""
    r = requests.post(
        f"{API_BASE}/calls/extensive",
        json=body,
        headers={"Authorization": f"Basic {token}",
                 "Content-Type": "application/json"},
        timeout=_TIMEOUT,
    )
    if r.status_code == 404:
        return {"calls": []}
    r.raise_for_status()
    return r.json() or {}


def _party_names(call: dict[str, Any]) -> list[str]:
    """Participant names, external (customer-side) first — they are the
    voice the evidence belongs to."""
    parties = call.get("parties") or []
    external = [p.get("name") for p in parties
                if p.get("affiliation") == "External" and p.get("name")]
    internal = [p.get("name") for p in parties
                if p.get("affiliation") != "External" and p.get("name")]
    return external + internal


def _distill(call: dict[str, Any]) -> str:
    """Render Gong's own distilled layer into the extraction text. Never
    includes transcript sentences (§6)."""
    content = call.get("content") or {}
    parts: list[str] = []
    if content.get("brief"):
        parts.append(f"brief: {content['brief']}")
    key_points = [
        kp.get("text") for kp in (content.get("keyPoints") or []) if kp.get("text")
    ]
    if key_points:
        parts.append("key points: " + "; ".join(key_points))
    for hl in content.get("highlights") or []:
        items = [i.get("text") for i in (hl.get("items") or []) if i.get("text")]
        if items:
            title = hl.get("title") or "highlights"
            parts.append(f"{title}: " + "; ".join(items))
    topics = [
        t.get("name") for t in (content.get("topics") or []) if t.get("name")
    ]
    if topics:
        parts.append("topics: " + ", ".join(topics))
    trackers = [
        f"{t['name']} ×{t.get('count', 0)}"
        for t in (content.get("trackers") or [])
        if t.get("name") and t.get("count")
    ]
    if trackers:
        parts.append("trackers: " + ", ".join(trackers))
    return "\n".join(parts)


def pull(
    token: str,
    *,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = _LIMIT,
) -> Iterator[RawRecord]:
    """KG-ingest pull: distilled call intelligence → RawRecords.

    Defaults to the last _WINDOW_DAYS ending now (Gong requires an explicit
    date window). Pages newest-window content up to `limit` calls; repeated
    syncs are cheap downstream — the runner's content-hash ledger skips
    already-extracted records before any LLM call."""
    now = datetime.now(timezone.utc)
    filter_body: dict[str, Any] = {
        "fromDateTime": _iso(since or now - timedelta(days=_WINDOW_DAYS)),
        "toDateTime": _iso(until or now),
    }

    yielded = 0
    cursor: Optional[str] = None
    while yielded < limit:
        body: dict[str, Any] = {
            "filter": filter_body,
            "contentSelector": _CONTENT_SELECTOR,
        }
        if cursor:
            body["cursor"] = cursor
        page = _post_extensive(token, body)

        calls = page.get("calls") or []
        for call in calls:
            if yielded >= limit:
                break
            meta = call.get("metaData") or {}
            call_id = str(meta.get("id") or "")
            if not call_id:
                continue
            participants = _party_names(call)
            duration = meta.get("duration")
            yield RawRecord(
                provider="gong",
                kind="call",
                external_id=call_id,
                title=meta.get("title") or "(untitled call)",
                text=_distill(call)[:_TEXT_CAP],
                properties={
                    "participants": participants,
                    "duration_seconds": duration or "",
                    "direction": meta.get("direction") or "",
                },
                timestamp=meta.get("started") or None,
            )
            yielded += 1

        cursor = (page.get("records") or {}).get("cursor")
        if not cursor or not calls:
            break
