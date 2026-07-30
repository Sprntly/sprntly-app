"""Fireflies adapter — find and read individual meeting transcripts.

Division of labour with the call digest (app/call_digest.py), which owns the
window-shaped question: `is_call_digest` fires FIRST in qa_agent.answer, so
"summarize the customer calls from last week" still goes to the digest (a
deterministic full-window fetch feeding the VoC skill). This adapter owns the
NEEDLE question — "find the call where the SSO deadline came up", "what did Ada
say about pricing on the Acme call" — which the digest can't answer without
burning a whole window's budget.

Both read through the same puller (kg_ingest/pullers/fireflies.fetch_calls), so
there is one Fireflies client, one auth path, one query shape.

Honest limit stated to the model: the API is windowed, not id-addressable for us,
so a call is fetched by scanning a bounded window (the search results are cached
on the session, so reading a call the search just found costs nothing extra).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.connector_lookup.base import LookupSession, cap_items
from app.connectors.tokens import TokenEncryptionError, decrypt_token_json
from app.kg_ingest.pullers.fireflies import CallTranscript, fetch_calls

logger = logging.getLogger(__name__)

DISPLAY_NAME = "Fireflies"

#: Default window for a search, and the hard bound on calls pulled per lookup.
_DEFAULT_DAYS = 30
_MAX_DAYS = 180
_FETCH_LIMIT = 50
_MAX_HITS = 10
#: Quotes per call: none in a search listing (summaries only), a bounded sample
#: when the model asks for one call in full.
_QUOTES_IN_CALL = 25

SYSTEM = (
    "Tools:\n"
    "- fireflies_search_calls: find meetings in a time window, optionally "
    "filtered by `keywords` matched against title, participants, summary and "
    "the transcript's own keywords. Returns one line per call with its id.\n"
    "- fireflies_get_call: one meeting in full by id — summary, action items and "
    "a bounded sample of verbatim, speaker-attributed quotes.\n\n"
    "Honest limits you MUST respect: the search scans a bounded window (default "
    "30 days, 180 max) of the most recent meetings, so an empty result means "
    "\"not in the window I read\" — say which window that was. Quotes are a "
    "SAMPLE of each call, not the whole transcript: attribute a quote to its "
    "speaker and never present a paraphrase as a verbatim quote.\n"
    "For a whole-period summary of every call (a voice-of-customer report), the "
    "digest path handles that — here, answer the specific question asked."
)

SEARCH_TOOL = {
    "name": "fireflies_search_calls",
    "description": (
        "Find recorded meetings. `days` sets how far back to look (default 30, "
        "max 180); `keywords` narrows to calls whose title, participants, "
        "summary or keywords mention them. Returns id, title, date, participants "
        "and a one-line summary per call."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "keywords": {"type": "string", "description": "Words to look for in the call."},
            "days": {"type": "integer", "description": "How many days back to search (default 30)."},
        },
    },
}

GET_CALL_TOOL = {
    "name": "fireflies_get_call",
    "description": (
        "Read one meeting in full by the `call_id` from fireflies_search_calls: "
        "summary, action items, keywords and speaker-attributed verbatim quotes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "call_id": {"type": "string", "description": "The call id from a search result."},
        },
        "required": ["call_id"],
    },
}

TOOLS = [SEARCH_TOOL, GET_CALL_TOOL]


@dataclass
class FirefliesHandle:
    """API key plus a per-lookup cache of the calls already fetched, so
    search-then-read doesn't pay for the window twice."""

    # repr suppressed: a live API key must not reach a log line or a test dump.
    api_key: str = field(repr=False)
    calls: dict[str, CallTranscript] = field(default_factory=dict, repr=False)


def load_api_key(company_id: str) -> str | None:
    """Decrypt the stored Fireflies API key for a company, or None when the
    source isn't connected / the credential can't be read. Mirrors
    call_digest._load_api_key — company-scoped, single chokepoint."""
    from app import db

    row = db.get_connection(company_id, "fireflies")
    if not row:
        return None
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, ValueError, KeyError, TypeError):
        logger.warning(
            "fireflies-lookup: could not decrypt fireflies token for %s", company_id
        )
        return None
    return token_json.get("api_key") or None


def _matches(call: CallTranscript, needle: str) -> bool:
    haystack = " ".join([
        call.title or "", call.overview or "", call.action_items or "",
        " ".join(call.keywords or []), " ".join(call.participants or []),
        " ".join(q.get("text", "") for q in (call.quotes or [])),
    ]).lower()
    return needle.lower() in haystack


class FirefliesProvider:
    """LookupProvider over kg_ingest/pullers/fireflies.py."""

    provider = "fireflies"
    display_name = DISPLAY_NAME
    keywords = ("fireflies", "call", "meeting")

    def open_session(self, enterprise_id: str) -> LookupSession | None:
        api_key = load_api_key(enterprise_id)
        if not api_key:
            return None
        return LookupSession(
            provider=self.provider, handle=FirefliesHandle(api_key=api_key)
        )

    def tools(self) -> list[dict]:
        return TOOLS

    def system_block(self) -> str:
        return SYSTEM

    def dispatch(self, session: LookupSession, name: str, inp: dict) -> str:
        handle: FirefliesHandle = session.handle
        if name == "fireflies_search_calls":
            return self._search(handle, inp)
        if name == "fireflies_get_call":
            return self._get_call(handle, inp)
        return f"(unknown tool {name})"

    def _fetch_window(self, handle: FirefliesHandle, days: int) -> list[CallTranscript]:
        until = datetime.now(timezone.utc)
        since = until - timedelta(days=days)
        calls = fetch_calls(handle.api_key, since=since, until=until, limit=_FETCH_LIMIT)
        for call in calls:
            handle.calls[str(call.external_id)] = call
        return calls

    def _search(self, handle: FirefliesHandle, inp: dict) -> str:
        try:
            days = int(inp.get("days") or _DEFAULT_DAYS)
        except (TypeError, ValueError):
            days = _DEFAULT_DAYS
        days = max(1, min(days, _MAX_DAYS))
        calls = self._fetch_window(handle, days)
        needle = (inp.get("keywords") or "").strip()
        hits = [c for c in calls if not needle or _matches(c, needle)]
        if not hits:
            scope = f"the last {days} days"
            extra = f" mentioning {needle!r}" if needle else ""
            return (
                f"(no Fireflies calls in {scope}{extra} — {len(calls)} calls were "
                "read. Say which window you searched.)"
            )
        kept, marker = cap_items(hits, _MAX_HITS)
        lines = [
            f"- {c.external_id}: {c.title or '(untitled)'} · {c.date or 'date unknown'}"
            + (f" · {', '.join(c.participants)}" if c.participants else "")
            + (f"\n    summary: {(c.overview or '')[:400]}" if c.overview else "")
            for c in kept
        ]
        footer = marker or ""
        head = (
            f"{len(hits)} call(s) in the last {days} days"
            + (f" mentioning {needle!r}" if needle else "")
            + f" (of {len(calls)} read):"
        )
        return "\n".join(p for p in [head, "\n".join(lines), footer] if p)

    def _get_call(self, handle: FirefliesHandle, inp: dict) -> str:
        call_id = (inp.get("call_id") or "").strip()
        if not call_id:
            return "(fireflies_get_call: 'call_id' is required)"
        call = handle.calls.get(call_id)
        if call is None:
            # Not in what this lookup has already read — scan the default window
            # once (the API is windowed for us, not id-addressable).
            self._fetch_window(handle, _DEFAULT_DAYS)
            call = handle.calls.get(call_id)
        if call is None:
            return (
                f"(no Fireflies call with id {call_id} in the last "
                f"{_DEFAULT_DAYS} days — search a wider window first)"
            )
        return call.render(max_quotes=_QUOTES_IN_CALL)


PROVIDER = FirefliesProvider()
