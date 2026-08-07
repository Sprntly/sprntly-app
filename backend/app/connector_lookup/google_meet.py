"""Google Meet adapter — search and read meeting transcripts live from chat.

Mirrors the Zoom adapter's search-then-read shape (connector_lookup/zoom.py)
and reuses the KG puller's own transcript resolution
(kg_ingest/pullers/google_meet.py) so a live chat read and the periodic sync
never disagree about which transcript is "the" transcript or how its entries
are joined.

WHY THIS ADAPTER CANNOT WORK LIKE ZOOM'S, AND WHAT THAT COSTS. A Zoom recording
carries a TOPIC, so Zoom's search filters metadata and only reads a transcript
once the model has picked one. A Meet conference record carries no subject
line at all — the API exposes `{name, startTime, endTime, space}` and nothing
else, and the meeting's calendar title is not available. So there is no
metadata to keyword-match against: **searching Meet means reading transcripts**,
and reading a transcript is three round trips (participants, transcripts,
entries).

That makes an unbounded search the exact failure mode `sweep.py`'s docstring
refuses for Google Drive — a leg that, once abandoned at the sweep's budget,
keeps a worker thread hammering an upstream for minutes. So the scan carries
its OWN wall-clock deadline (`SCAN_BUDGET_S`) and its own conference cap
(`_MAX_SCAN_CONFERENCES`), both checked between conferences. The leg therefore
self-terminates whether or not anyone is still waiting for it, and the worst
case after abandonment is the one HTTP call already in flight, bounded by
`google_meet._TIMEOUT`.

`SCAN_BUDGET_S` is 6.0 against `sweep.BUDGET_S` of 8.0. THE TWO ARE NOT
MEASURED FROM THE SAME INSTANT, and an earlier version of this docstring
claimed they were, which a review correctly called out as a false guarantee:
`BUDGET_S` starts at the top of `sweep.sweep()`, while this deadline is
anchored when `_search` is ENTERED. Everything in between — the local legs on
the calling thread, `connected_providers`, the credential-freshness read, and
`open_session` itself, all DB round trips against a shared Supabase — is
charged to the sweep's budget and not to this one. So under real latency the
scan's deadline can land AFTER the sweep has already given up.

What that costs is bounded and honest, which is why the anchor is left where it
is rather than plumbed through `_AdapterLeg`: the sweep reports the leg as
`STATUS_TIMEOUT` (unread, named, not persisted) and the abandoned thread stops
itself within `SCAN_BUDGET_S` of its own start. The guarantee this file
actually makes is therefore the useful one — **an abandoned Meet leg
terminates in bounded time** — not the stronger one it used to claim.

A partial scan says how far it got, in the rendered result and in the not-found
copy, because "I read your 6 most recent Meet calls and none mention this" and
"you have no Meet call about this" are different sentences.

DEPTH IS UNBOUNDED, BREADTH IS NOT. `google_meet_get_transcript` reads ONE
named conference in full and is not under the scan budget — the model reaches
it after a search has said which call matters, exactly as Zoom's
`zoom_get_recording` works.

COVERAGE IS ONE PERSON'S CALENDAR, AND 30 DAYS OF IT. Google's
`conferenceRecords.list` returns only conferences the connected account
ORGANIZED, and deletes them 30 days after the call. Both limits are Google's,
neither can be widened by a bigger scope, and both are stated to the model.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.connector_lookup.base import LookupSession, cap_items
from app.connectors.google_meet import (
    GOOGLE_MEET_PROVIDER,
    MeetAuthExpiredError,
    MeetContext,
    MeetDeadlineExceeded,
    MeetNotConnectedError,
    RETENTION_DAYS,
    list_conference_records,
    read_deadline,
    sync_context,
)
from app.kg_ingest.pullers.google_meet import (
    _NO_TRANSCRIPT_TEXT,
    _speaker_map,
    _title_for,
    _transcript_text,
)

if TYPE_CHECKING:
    from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

DISPLAY_NAME = "Google Meet"

#: Wall-clock one `google_meet_search_transcripts` scan may spend, measured
#: from when the scan STARTS (not from when the sweep started — see the module
#: docstring, which no longer claims otherwise).
#:
#: Kept under `connector_lookup.sweep.BUDGET_S` (8.0) so that in the common
#: case the scan finishes on its own terms and its partial results still land.
#: The property that holds unconditionally, and the one that matters, is that
#: an ABANDONED leg terminates within this many seconds of its own start
#: instead of running on against Google with nobody listening.
SCAN_BUDGET_S = 6.0

#: Conferences one scan will read transcripts for, newest first. The second
#: bound, and the one that binds on a fast connection where the deadline never
#: fires: three round trips per conference means eight is already ~24 requests.
_MAX_SCAN_CONFERENCES = 8

#: Conferences named in one search result.
#:
#: Currently equal to `_MAX_SCAN_CONFERENCES`, so `cap_items` can never
#: actually truncate: hits are a subset of the conferences read, and the scan
#: reads at most that many. The cap is kept as a backstop for the day the scan
#: cap is raised, and a test pins the relationship — so raising
#: `_MAX_SCAN_CONFERENCES` past this number becomes a deliberate decision about
#: how many calls to NAME, rather than a silent one.
_MAX_HITS = 8

#: Characters of a matching transcript quoted per hit in the SEARCH result.
#: The full transcript is one `google_meet_get_transcript` call away; a search
#: that returned every matching call in full would be the answer rather than
#: the index to it.
_SNIPPET_CHARS = 400

#: Characters of transcript carried into a persisted record. Same figure as
#: `kg_ingest.pullers.google_meet._TEXT_CHARS`, deliberately: the same call
#: read two ways must not produce two different texts.
_TEXT_CHARS = 4000

SYSTEM = (
    "Tools:\n"
    "- google_meet_search_transcripts: find Google Meet calls whose TRANSCRIPT "
    "mentions `keywords`. Returns one line per call with its id and a short "
    "quoted snippet around the match.\n"
    "- google_meet_get_transcript: one call's transcript in full by the id "
    "from a search result, speaker-attributed.\n\n"
    "READ THE TRANSCRIPT BEFORE ANSWERING ABOUT WHAT WAS SAID. The search "
    "returns a short snippet, never the conversation; if the question is about "
    "what was discussed, decided or promised on a call, call "
    "google_meet_get_transcript for the matching call(s) in the SAME turn and "
    "answer from the transcript rather than from the snippet.\n\n"
    "Honest limits you MUST respect, all of them Google's and none of them "
    "wideable:\n"
    "- A Meet call has NO TITLE. There is nothing to match but the transcript "
    "itself, so the search reads transcripts and can only cover a bounded "
    "number of the most recent calls per question — the result says how many "
    "it read. An empty result means \"not in the calls I read\", not \"never "
    "discussed\".\n"
    f"- Google keeps conference records for {RETENTION_DAYS} DAYS and then "
    "deletes them. There is no older history to search, for anyone.\n"
    "- Coverage is the connected account's OWN calls: Google only exposes "
    "meetings that account ORGANIZED, not ones they attended. Say so rather "
    "than implying the whole company's meetings were searched.\n"
    "- A call only has a transcript if somebody switched \"Record the "
    "transcript\" on for it, and Google will not transcribe retroactively. A "
    "call with no transcript is a call we could not read, NOT an empty "
    "meeting — say which."
)

SEARCH_TOOL = {
    "name": "google_meet_search_transcripts",
    "description": (
        "Find Google Meet calls whose transcript mentions `keywords`. Reads "
        "the transcripts of the most recent calls (bounded — the result says "
        "how many) and returns each match with its id and a short quoted "
        "snippet. Follow with google_meet_get_transcript for whichever call "
        "the question is actually about."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "string",
                "description": "Words to look for in what was said on the call.",
            },
        },
        "required": ["keywords"],
    },
}

GET_TRANSCRIPT_TOOL = {
    "name": "google_meet_get_transcript",
    "description": (
        "Read one Google Meet call's transcript in full by the "
        "`conference_id` from a search result. Call this before answering any "
        "question about what was said, decided or agreed on a call — a search "
        "snippet is not enough to answer it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "conference_id": {
                "type": "string",
                "description": "The conference id from a search result.",
            },
        },
        "required": ["conference_id"],
    },
}

TOOLS = [SEARCH_TOOL, GET_TRANSCRIPT_TOOL]

NOT_CONNECTED = (
    "I can read your Google Meet call transcripts live — but Google Meet "
    "isn't connected yet (or its access needs refreshing). Connect **Google "
    "Meet** in Settings → Connectors and ask me again."
)


@dataclass
class MeetHandle:
    """A Meet context plus a per-lookup cache of everything already read, so a
    search followed by a full read doesn't pay for the same transcript twice."""

    # repr suppressed: ctx carries a live access token.
    ctx: MeetContext = field(repr=False)
    #: conference resource name -> the conference record dict.
    conferences: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    #: conference resource name -> (text, speaker names, had_a_ready_transcript).
    transcripts: dict[str, tuple[str, list[str], bool]] = field(
        default_factory=dict, repr=False
    )
    #: conference resource name -> participant map, as the puller builds it.
    speakers: dict[str, dict[str, str]] = field(default_factory=dict, repr=False)


def _load_context(company_id: str) -> MeetContext | None:
    """A live MeetContext for a company, or None when Meet isn't connected /
    the token can't be refreshed. Mirrors `zoom._load_context` — company-scoped,
    single chokepoint, never raises."""
    try:
        return sync_context(company_id)
    except MeetNotConnectedError:
        logger.info("google-meet-lookup: not connected for %s", company_id)
        return None
    except MeetAuthExpiredError:
        logger.info(
            "google-meet-lookup: stored token rejected for %s — reconnect needed",
            company_id,
        )
        return None
    except Exception:  # noqa: BLE001 — a live-lookup open must never raise
        logger.exception(
            "google-meet-lookup: could not open a session for %s", company_id
        )
        return None


def _read_transcript(
    handle: MeetHandle, conference_name: str
) -> tuple[str, list[str], bool]:
    """`(text, speakers, had_a_ready_transcript)` for one conference, cached.

    Delegates to the puller's `_transcript_text`, which deliberately does NOT
    swallow a read failure — a 500 from Google must not be rendered as "this
    meeting was never transcribed", which is a claim about the customer's own
    settings. Failures propagate to the caller, which decides whether to skip
    the conference (search) or report it (single read).
    """
    cached = handle.transcripts.get(conference_name)
    if cached is not None:
        return cached
    speakers = handle.speakers.get(conference_name)
    if speakers is None:
        speakers = _speaker_map(handle.ctx, conference_name)
        handle.speakers[conference_name] = speakers
    result = _transcript_text(handle.ctx, conference_name, speakers)
    handle.transcripts[conference_name] = result
    return result


def _snippet(text: str, needles: list[str]) -> str:
    """A short quote around the FIRST matching term, or "" when none match."""
    lowered = text.lower()
    for needle in needles:
        at = lowered.find(needle.lower())
        if at < 0:
            continue
        start = max(0, at - _SNIPPET_CHARS // 3)
        chunk = text[start:start + _SNIPPET_CHARS].replace("\n", " ").strip()
        prefix = "…" if start > 0 else ""
        suffix = "…" if start + _SNIPPET_CHARS < len(text) else ""
        return f"{prefix}{chunk}{suffix}"
    return ""


def _label(handle: MeetHandle, conference: dict[str, Any]) -> str:
    name = str(conference.get("name") or "")
    return _title_for(
        conference, handle.speakers.get(name, {}), conference.get("startTime")
    )


def _to_record(handle: MeetHandle, conference: dict[str, Any]) -> "RawRecord":
    """One conference the search actually read → a `RawRecord`.

    Built field-for-field like `kg_ingest.pullers.google_meet._to_record`, from
    the same joined transcript and the same participant map, because a Meet
    conference read live and read by the scheduled pull genuinely IS the same
    thing: the transcript is immutable once Google has generated the file, so
    unlike ClickUp or Asana there is no leaner "search hit" shape here — the
    search had to read the whole transcript to match on it at all.

    Two ways the pair can still differ, stated rather than claimed away:
    a conference read here BEFORE its transcript finished generating carries
    `_NO_TRANSCRIPT_TEXT` and will not equal the pull's later record for the
    same call; and `properties["speakers"]` follows the participant listing,
    which can lose someone who left before it ran.
    """
    from app.kg_ingest.types import RawRecord

    name = str(conference.get("name") or "")
    text, speaker_names, has_transcript = handle.transcripts.get(name, ("", [], False))
    if not text:
        text = _NO_TRANSCRIPT_TEXT
        has_transcript = False
    return RawRecord(
        provider=GOOGLE_MEET_PROVIDER,
        kind="meeting",
        external_id=name,
        title=_label(handle, conference),
        text=text[:_TEXT_CHARS],
        properties={
            "speakers": speaker_names,
            "organizer_email": handle.ctx.account_email,
            "has_transcript": has_transcript,
            "start_time": conference.get("startTime"),
            "end_time": conference.get("endTime"),
        },
        timestamp=conference.get("startTime"),
    )


class GoogleMeetProvider:
    """LookupProvider over app/connectors/google_meet.py, reusing the KG
    puller's transcript resolution (kg_ingest/pullers/google_meet.py)."""

    provider = GOOGLE_MEET_PROVIDER
    display_name = DISPLAY_NAME
    keywords = ("google meet", "gmeet", "meet call", "meet transcript")

    def open_session(self, enterprise_id: str) -> LookupSession | None:
        ctx = _load_context(enterprise_id)
        if ctx is None:
            return None
        return LookupSession(provider=self.provider, handle=MeetHandle(ctx=ctx))

    def tools(self) -> list[dict]:
        return TOOLS

    def system_block(self) -> str:
        return SYSTEM

    def dispatch(self, session: LookupSession, name: str, inp: dict) -> str:
        if name == "google_meet_search_transcripts":
            text, _hits = self._search(session.handle, inp)
            return text
        if name == "google_meet_get_transcript":
            return self._get_transcript(session.handle, inp)
        return f"(unknown tool {name})"

    def dispatch_records(self, session: LookupSession, name: str, inp: dict):
        """`(text, records)` for `google_meet_search_transcripts`, `None` for
        anything else. Runs `_search` — the SAME single pass `dispatch` runs —
        so `text` is byte-identical to `dispatch`'s output by construction, and
        the records are built from transcripts already in the handle's cache,
        with no new HTTP call."""
        if name != "google_meet_search_transcripts":
            return None
        handle: MeetHandle = session.handle
        text, hits = self._search(handle, inp)
        return text, ([_to_record(handle, c) for c in hits] if hits else None)

    def _conferences(self, handle: MeetHandle) -> list[dict[str, Any]]:
        """The retention window's conferences, newest first, cached per lookup."""
        records = list_conference_records(handle.ctx.access_token)
        for record in records:
            name = record.get("name")
            if name:
                handle.conferences[str(name)] = record
        return records

    def _search(
        self, handle: MeetHandle, inp: dict
    ) -> tuple[str, list[dict[str, Any]]]:
        """`(rendered text, conferences that matched)`.

        Bounded twice over — see the module docstring. Both bounds are
        reported: `read` is how many transcripts were actually opened and
        `stopped_early` is why the scan ended, so a partial scan can never be
        rendered as a complete search.

        A FAILED LISTING RAISES; IT DOES NOT RETURN PROSE. That distinction is
        the whole of a HIGH review finding against the first version of this
        file, and the failure mode is worth stating because it is not obvious
        from either side alone:

          `sweep._run_live` promotes ANY non-empty text to `STATUS_OK`. So a
          carefully-worded "(Google Meet could not be listed — NOT an absence
          of calls)" was, to the sweep, a source that had been READ. It
          rendered under `### Google Meet` inside the "read from the connected
          sources just now" block instead of the not-covered list;
          `covered_providers()` reported Meet as covered, which deleted it from
          `registry.answer_for_hints`'s `unavailable_names`; and worst,
          `sweep_persist.kickoff_sweep_persist` takes `list(result.read)`, so
          the error STRING was handed to `extract_document` and written into
          the tenant's knowledge graph on the shared prod Supabase — where it
          would later resurface as evidence, indistinguishable from something a
          customer actually said on a call.

        Raising is the fix and it costs nothing, because both callers already
        do the right thing with an exception: the sweep maps it to
        `STATUS_ERROR` (unread, named, never persisted) and `answer.py`'s
        dispatch wrapper renders it as a sentence for the model. Prose is only
        ever returned for outcomes that genuinely ARE reads — including the
        legitimate empty result, which is a real answer about a real search.
        """
        needles = [w for w in (inp.get("keywords") or "").split() if w.strip()]
        if not needles:
            return "(google_meet_search_transcripts: 'keywords' is required)", []

        deadline = time.monotonic() + SCAN_BUDGET_S
        hits: list[dict[str, Any]] = []
        read = 0
        total = 0
        stopped_early = ""
        # EVERY read inside this block is bound to the same deadline, down to
        # the HTTP timeout and the 429 backoff sleep — see
        # `connectors.google_meet.read_deadline`. Checking the clock only
        # between conferences would leave one rate-limited call free to sleep
        # ~30s twice, which is the whole failure this leg has to be immune to.
        with read_deadline(deadline):
            # No `except` here at all: MeetAuthExpiredError, MeetDeadlineExceeded
            # and any transport failure all propagate, for the reason in the
            # docstring above. A listing we could not read is NOT a search.
            conferences = self._conferences(handle)

            total = len(conferences)
            for conference in conferences:
                name = str(conference.get("name") or "")
                if not name:
                    continue
                if read >= _MAX_SCAN_CONFERENCES:
                    stopped_early = (
                        f"stopped after the {_MAX_SCAN_CONFERENCES} most recent "
                        "calls"
                    )
                    break
                if time.monotonic() >= deadline:
                    stopped_early = "stopped at this lookup's time budget"
                    break
                try:
                    text, _speakers, _ready = _read_transcript(handle, name)
                except MeetAuthExpiredError:
                    raise
                except MeetDeadlineExceeded:
                    stopped_early = "stopped at this lookup's time budget"
                    break
                except Exception:  # noqa: BLE001 — one unreadable call, not the scan
                    logger.info(
                        "google-meet-lookup: transcript read failed for %s", name,
                        exc_info=True,
                    )
                    continue
                read += 1
                if text and _snippet(text, needles):
                    hits.append(conference)

        if total and not read:
            # The smaller form of the same defect. "0 of 12 calls had their
            # transcript read" is honest PROSE, but it is not a SEARCH — and
            # non-empty text is exactly what `sweep._run_live` turns into
            # STATUS_OK and `sweep_persist` writes to the graph. A scan that
            # opened nothing is a failure to read, so it raises like one.
            raise MeetDeadlineExceeded(
                "google_meet: no transcript could be opened before the scan "
                f"budget expired ({total} call(s) were listed)"
            )

        kept, marker = cap_items(hits, _MAX_HITS)
        return self._render_search(
            handle, kept, needles, read=read, total=total,
            stopped_early=stopped_early, truncation=marker,
        ), kept

    def _render_search(
        self,
        handle: MeetHandle,
        hits: list[dict[str, Any]],
        needles: list[str],
        *,
        read: int,
        total: int,
        stopped_early: str,
        truncation: str = "",
    ) -> str:
        scope = (
            f"{read} of {total} Google Meet call(s) in the last "
            f"{RETENTION_DAYS} days had their transcript read"
        )
        if stopped_early:
            scope += f" ({stopped_early})"
        if not hits:
            return (
                f"(no Google Meet transcript mentions these terms — {scope}. "
                "Calls not read here, and calls with transcription switched "
                "off, are not covered by that.)"
            )
        lines = []
        for conference in hits:
            name = str(conference.get("name") or "")
            text, _speakers, _ready = handle.transcripts.get(name, ("", [], False))
            lines.append(
                f"- {name}: {_label(handle, conference)}\n  "
                + (_snippet(text, needles) or "(match in transcript)")
            )
        head = f"{len(hits)} Google Meet call(s) mention these terms — {scope}:"
        return "\n".join(p for p in [head, "\n".join(lines), truncation] if p)

    def _get_transcript(self, handle: MeetHandle, inp: dict) -> str:
        conference_id = (inp.get("conference_id") or "").strip()
        if not conference_id:
            return "(google_meet_get_transcript: 'conference_id' is required)"
        conference = handle.conferences.get(conference_id)
        if conference is None:
            # Not in what this lookup has already listed — the listing is
            # windowed, not id-addressable, so read the window once.
            try:
                self._conferences(handle)
            except Exception:  # noqa: BLE001 — reported as not-found below
                logger.info(
                    "google-meet-lookup: conference listing failed while "
                    "resolving %s", conference_id, exc_info=True,
                )
            conference = handle.conferences.get(conference_id)
        if conference is None:
            return (
                f"(no Google Meet call with id {conference_id} in the last "
                f"{RETENTION_DAYS} days for the connected account — Google "
                "deletes conference records after that, and only exposes "
                "calls this account organized)"
            )
        header = _label(handle, conference)
        try:
            text, speakers, _ready = _read_transcript(handle, conference_id)
        except Exception:  # noqa: BLE001 — a read failure is not an absence
            logger.info(
                "google-meet-lookup: transcript read failed for %s", conference_id,
                exc_info=True,
            )
            return (
                f"{header}\n\nThis call's transcript could not be read just "
                "now. That is a failure to reach Google, NOT evidence that the "
                "meeting was never transcribed — say so rather than concluding "
                "either way."
            )
        if not text:
            return f"{header}\n\n{_NO_TRANSCRIPT_TEXT}"
        body = f"speakers: {', '.join(speakers)}\n\n{text}" if speakers else text
        return f"{header}\n\n{body}"


PROVIDER = GoogleMeetProvider()
