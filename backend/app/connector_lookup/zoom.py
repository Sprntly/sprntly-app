"""Zoom adapter — find and read individual cloud-recording transcripts live.

Mirrors the Fireflies adapter's search-then-read shape (app/connector_lookup/
fireflies.py): a bounded-window search returns short hits with an id, and a
follow-up read fetches one recording's transcript in full. This reuses the
SAME host-selection and transcript-resolution logic as the KG puller
(kg_ingest/pullers/zoom.py) so live chat and the periodic 6-hourly sync never
disagree about which hosts count or which file is "the transcript".

Honest limits stated to the model: Zoom's own API only returns recordings
from a bounded window (default 30 days, max 90 here — Zoom silently clamps
anything wider per call, see zoom_oauth.sync_windows), and a search only
covers the hosts this company has chosen to sync (or every licensed host, if
none are chosen) — not every Zoom user who might have attended.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.connector_lookup.base import LookupSession, cap_items
from app.connectors.zoom_oauth import (
    ZoomAuthExpiredError,
    ZoomContext,
    fetch_transcript_text,
    list_user_recordings,
    sync_context,
    sync_windows,
)
from app.kg_ingest.pullers.zoom import _hosts, _transcript_for, parse_vtt

logger = logging.getLogger(__name__)

DISPLAY_NAME = "Zoom"

#: Default window for a search, and the hard bound on how far back one lookup
#: reaches — kept smaller than Fireflies' (180d) because Zoom's own recording
#: retention and this connector's 3-month backfill make older history sparse.
_DEFAULT_DAYS = 30
_MAX_DAYS = 90
_MAX_HITS = 10
#: Recordings requested per host per (<=1-month) window — a live lookup reads
#: fewer than the background puller's page size because it must return inside
#: one chat turn, across however many hosts are configured.
_PAGE_SIZE = 30

SYSTEM = (
    "Tools:\n"
    "- zoom_search_recordings: find cloud-recorded Zoom meetings in a time "
    "window, optionally filtered by `keywords` matched against the meeting "
    "topic and host email. Returns one line per meeting with its id.\n"
    "- zoom_get_recording: one meeting's transcript in full by the id from a "
    "search result, speaker-attributed where Zoom generated one.\n\n"
    "Honest limits you MUST respect: the search only covers the hosts this "
    "company has chosen to sync (or every licensed host, if none are chosen) "
    "and only a bounded recent window (default 30 days, max 90) — an empty "
    "result means \"not in the window/hosts I read\", say which window that "
    "was. Some meetings have no transcript (audio transcription may be off "
    "for the account, or Zoom is still processing it) — say so rather than "
    "inventing content."
)

SEARCH_TOOL = {
    "name": "zoom_search_recordings",
    "description": (
        "Find cloud-recorded Zoom meetings. `days` sets how far back to look "
        "(default 30, max 90); `keywords` narrows to meetings whose topic or "
        "host email mention them. Returns id, topic, host, start time and "
        "duration per meeting."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "keywords": {"type": "string", "description": "Words to look for in the meeting topic or host."},
            "days": {"type": "integer", "description": "How many days back to search (default 30)."},
        },
    },
}

GET_RECORDING_TOOL = {
    "name": "zoom_get_recording",
    "description": (
        "Read one Zoom meeting's transcript in full by the `meeting_id` from "
        "zoom_search_recordings."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string", "description": "The meeting id (uuid) from a search result."},
        },
        "required": ["meeting_id"],
    },
}

TOOLS = [SEARCH_TOOL, GET_RECORDING_TOOL]


@dataclass
class ZoomHandle:
    """A refreshed Zoom context plus a per-lookup cache of meetings already
    fetched (and the host each belongs to), so search-then-read doesn't pay
    for the window twice."""

    # repr suppressed: ctx carries a live access token.
    ctx: ZoomContext = field(repr=False)
    meetings: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    meeting_hosts: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)


def _load_context(company_id: str) -> ZoomContext | None:
    """A refreshed ZoomContext for a company, or None when Zoom isn't
    connected / the token can't be refreshed. Mirrors
    fireflies.load_api_key — company-scoped, single chokepoint, never raises."""
    try:
        return sync_context(company_id)
    except ZoomAuthExpiredError:
        logger.info(
            "zoom-lookup: stored token rejected for %s — reconnect needed",
            company_id,
        )
        return None
    except Exception:  # noqa: BLE001 — a live-lookup open must never raise
        logger.exception("zoom-lookup: could not open a session for %s", company_id)
        return None


def _matches(meeting: dict[str, Any], host: dict[str, Any], needle: str) -> bool:
    haystack = " ".join([
        str(meeting.get("topic") or ""),
        str(meeting.get("host_email") or host.get("email") or ""),
    ]).lower()
    return needle.lower() in haystack


def _render_hit(meeting: dict[str, Any], host: dict[str, Any]) -> str:
    uuid = meeting.get("uuid") or meeting.get("id") or ""
    topic = meeting.get("topic") or "(untitled Zoom meeting)"
    start = meeting.get("start_time") or "date unknown"
    duration = meeting.get("duration")
    host_email = meeting.get("host_email") or host.get("email") or ""
    dur = f" · {duration} min" if isinstance(duration, int) else ""
    return f"- {uuid}: {topic} · {start}{dur}" + (f" · {host_email}" if host_email else "")


class ZoomProvider:
    """LookupProvider over app/connectors/zoom_oauth.py, reusing the KG
    puller's host/transcript resolution (kg_ingest/pullers/zoom.py)."""

    provider = "zoom"
    display_name = DISPLAY_NAME
    keywords = ("zoom", "recording", "transcript")

    def open_session(self, enterprise_id: str) -> LookupSession | None:
        ctx = _load_context(enterprise_id)
        if ctx is None:
            return None
        return LookupSession(provider=self.provider, handle=ZoomHandle(ctx=ctx))

    def tools(self) -> list[dict]:
        return TOOLS

    def system_block(self) -> str:
        return SYSTEM

    def dispatch(self, session: LookupSession, name: str, inp: dict) -> str:
        handle: ZoomHandle = session.handle
        if name == "zoom_search_recordings":
            return self._search(handle, inp)
        if name == "zoom_get_recording":
            return self._get_recording(handle, inp)
        return f"(unknown tool {name})"

    def _fetch_window(self, handle: ZoomHandle, days: int) -> list[tuple[dict, dict]]:
        """[(meeting, host), ...] across every configured host, within `days`
        back. Caches each meeting (and its host) on the handle by uuid."""
        max_windows = max(1, -(-days // 30))  # ceil(days / 30)
        cursor = (date.today() - timedelta(days=days)).isoformat()
        windows = sync_windows(cursor, max_windows=max_windows)
        hosts = _hosts(handle.ctx)
        out: list[tuple[dict, dict]] = []
        for host in hosts:
            for frm, to in windows:
                try:
                    meetings = list_user_recordings(
                        handle.ctx.access_token, host["id"], frm=frm, to=to,
                        page_size=_PAGE_SIZE,
                    )
                except ZoomAuthExpiredError:
                    logger.info(
                        "zoom-lookup: token rejected mid-search for %s",
                        handle.ctx.company_id,
                    )
                    return out
                for meeting in meetings:
                    uuid = str(meeting.get("uuid") or meeting.get("id") or "")
                    if not uuid:
                        continue
                    handle.meetings[uuid] = meeting
                    handle.meeting_hosts[uuid] = host
                    out.append((meeting, host))
        return out

    def _search(self, handle: ZoomHandle, inp: dict) -> str:
        try:
            days = int(inp.get("days") or _DEFAULT_DAYS)
        except (TypeError, ValueError):
            days = _DEFAULT_DAYS
        days = max(1, min(days, _MAX_DAYS))
        hits_all = self._fetch_window(handle, days)
        needle = (inp.get("keywords") or "").strip()
        hits = [(m, h) for m, h in hits_all if not needle or _matches(m, h, needle)]
        if not hits:
            scope = f"the last {days} days"
            extra = f" mentioning {needle!r}" if needle else ""
            return (
                f"(no Zoom cloud recordings in {scope}{extra} — {len(hits_all)} "
                "recordings were read across the synced hosts. Say which "
                "window you searched.)"
            )
        kept, marker = cap_items(hits, _MAX_HITS)
        lines = [_render_hit(m, h) for m, h in kept]
        head = (
            f"{len(hits)} recording(s) in the last {days} days"
            + (f" mentioning {needle!r}" if needle else "")
            + f" (of {len(hits_all)} read):"
        )
        return "\n".join(p for p in [head, "\n".join(lines), marker] if p)

    def _get_recording(self, handle: ZoomHandle, inp: dict) -> str:
        meeting_id = (inp.get("meeting_id") or "").strip()
        if not meeting_id:
            return "(zoom_get_recording: 'meeting_id' is required)"
        meeting = handle.meetings.get(meeting_id)
        if meeting is None:
            # Not in what this lookup has already read — scan the default
            # window once (search is windowed, not id-addressable).
            self._fetch_window(handle, _DEFAULT_DAYS)
            meeting = handle.meetings.get(meeting_id)
        if meeting is None:
            return (
                f"(no Zoom recording with id {meeting_id} in the last "
                f"{_DEFAULT_DAYS} days across the synced hosts — search a "
                "wider window first)"
            )
        host = handle.meeting_hosts.get(meeting_id, {})
        entry = _transcript_for(handle.ctx, meeting)
        text = ""
        speakers: list[str] = []
        if entry:
            # The download_url is a credential-bearing link to customer
            # conversation. Passed to the fetcher and never logged or rendered.
            raw = fetch_transcript_text(
                handle.ctx.access_token, entry.get("download_url") or ""
            )
            if raw:
                text, speakers = parse_vtt(raw)

        topic = meeting.get("topic") or "(untitled Zoom meeting)"
        start = meeting.get("start_time") or "date unknown"
        host_email = meeting.get("host_email") or host.get("email") or ""
        header = f"{topic} · {start}" + (f" · host {host_email}" if host_email else "")

        if not text:
            return (
                f"{header}\n\nNo transcript available for this recording. The "
                "meeting was recorded to the Zoom cloud but no readable "
                "transcript file was found — the commonest cause is audio "
                "transcription being turned off for the account, or Zoom is "
                "still processing the recording."
            )
        body = f"speakers: {', '.join(speakers)}\n\n{text}" if speakers else text
        return f"{header}\n\n{body}"


PROVIDER = ZoomProvider()
