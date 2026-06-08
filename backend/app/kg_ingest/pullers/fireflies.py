"""Fireflies puller — meeting transcripts → RawRecords.

GraphQL API (api.fireflies.ai), API-key auth (per #106). We pull the meeting
summary + action items rather than full sentence-level transcripts — the
distilled layer is what the brain ingests (no raw-dump, §6).

Raw-audio ingestion (transcribe an uploaded recording with Whisper, then
extract) is a separate path in app/kg_ingest/audio_ingest.py — this puller is
left untouched.
"""
from __future__ import annotations

import logging
from typing import Iterator

import requests

from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

URL = "https://api.fireflies.ai/graphql"
_TIMEOUT = 30
_LIMIT = 25  # most recent meetings — pilot-scale cap

_QUERY = """
query Transcripts($limit: Int) {
  transcripts(limit: $limit) {
    id
    title
    date
    participants
    summary { overview action_items keywords }
  }
}
"""


def pull(api_key: str) -> Iterator[RawRecord]:
    r = requests.post(
        URL,
        json={"query": _QUERY, "variables": {"limit": _LIMIT}},
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(f"Fireflies GraphQL error: {body['errors'][:1]}")
    for t in (body.get("data") or {}).get("transcripts", []) or []:
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
            timestamp=str(t.get("date") or ""),
        )
