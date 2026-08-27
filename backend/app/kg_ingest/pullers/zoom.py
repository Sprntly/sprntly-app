"""Zoom puller — cloud-recorded meeting transcripts → RawRecords.

The credential kg_ingest hands us is the owning COMPANY ID, not an access token
(see runner.PULLERS and zoom_oauth.token_payload_to_store). A Zoom pull needs
the picked hosts and the incremental cursor as well as the token, and
`runner.token_for` can only pass one field — so the field it passes is the
company id and `zoom_oauth.sync_context` resolves the rest off the connection
row. The `uploads` and `confluence` pullers use the same trick for the same
reason.

WHAT WE PULL, AND WHAT WE DON'T:
    One record per cloud recording, whose text is the SPEAKER-ATTRIBUTED
    transcript parsed out of the meeting's WebVTT file. Deliberately excluded —
      * audio/video      binary, and Sprntly already has an audio path
                         (kg_ingest/audio_ingest.py) for files a user uploads
                         deliberately. Pulling every recorded call's media is a
                         different product decision.
      * chat files       Zoom's in-meeting chat is a separate recording_file
                         type; it is mostly links and "can you hear me?".
      * CC captions      duplicates TRANSCRIPT when both exist — see
                         zoom_oauth.transcript_file_from.
      * participants     /past_meetings/{id}/participants is a per-meeting call,
                         i.e. an N+1 across the whole window. Speaker names are
                         taken from the transcript instead, which costs nothing
                         and is the same information for a recorded call.

WINDOWING IS NOT OPTIONAL. Zoom caps a recordings query at a ONE-MONTH span and
does not error past it — a wider `from`/`to` is silently clamped, so a naive
"last 90 days" request returns a month and looks like a quiet quarter. Every
window here goes through `zoom_oauth.window_bounds`, which enforces the cap in
one place, and a longer reach is explicitly several windows.

FIRST SYNC BACKFILLS THREE MONTHS, newest window first, so a new connection has
a quarter of calls in the graph rather than whatever happened this week. After
that `CONFIG_LAST_SYNCED_UNTIL` advances and each run walks only the gap since
the last completed sync. Without that cursor, a 6-hourly schedule would re-walk
the entire backfill forever: three windows per host, four times a day.

COVERAGE IS THE ACCOUNT'S COVERAGE, bounded by the host picker. An empty
selection means every licensed host (the backwards-compatible default). A host
that 404s mid-sync is skipped, not raised: a recording deleted or moved to trash
between the listing and the read is a normal race on a live account.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, Iterator

from app.connectors.zoom_oauth import (
    CONFIG_LAST_SYNC_MEETINGS,
    CONFIG_LAST_SYNC_TRANSCRIPTS,
    CONFIG_LAST_SYNCED_UNTIL,
    ZOOM_PROVIDER,
    ZoomAuthExpiredError,
    ZoomContext,
    ZoomNotConnectedError,
    fetch_transcript_text,
    get_meeting_recordings,
    list_user_recordings,
    list_users,
    sync_context,
    sync_windows,
    transcript_file_from,
)
from app.kg_ingest.types import TRANSCRIPT_CHAR_CEILING, RawRecord

logger = logging.getLogger(__name__)

#: Hosts touched per sync. Deliberately the SAME number as the picker's
#: `_ZOOM_MAX_SYNC_USERS` in routes/connectors.py: a selection the route accepts
#: must be one the puller can honor in full, or the cap silently drops half of
#: what an admin explicitly chose. When no selection exists this bounds the
#: "every host" default on a large account.
_MAX_HOSTS = 100
#: Recordings per host per window. Zoom's page_size ceiling is 300; a host with
#: more than 50 recordings in one month is in back-to-back calls all day, and
#: taking the newest 50 of those is a better answer than a multi-page sweep on
#: every one of the 6-hourly runs.
_MAX_RECORDINGS_PER_HOST = 50
#: Windows walked in one run. Three x 30 days IS the 3-month backfill; it also
#: bounds the catch-up after a long outage, so a connector paused for a year
#: resumes with the last quarter rather than twelve windows per host.
_MAX_WINDOWS = 3
#: Per-record extraction ceiling — a DEFENSIVE bound, not a head-truncation
#: window. Zoom is a call-shaped provider (`_CALL_PROVIDERS`), extracted ONE
#: CALL PER DOCUMENT, so this never shares a batch with another record. The
#: prior 4,000-char head bound genuinely truncated an hour-long call
#: (~50,000 characters) — and a fact stated only in the transcript can sit
#: anywhere in the call, not just the first few minutes, so a small head
#: window silently loses exactly the facts transcript-read exists to
#: capture (closes Zoom's latent Loss-A, same finding as Fireflies).
#: Shared with Fireflies/Meet (`app.kg_ingest.types.TRANSCRIPT_CHAR_CEILING`)
#: so the three providers can't drift apart on it.
_TEXT_CHARS = TRANSCRIPT_CHAR_CEILING
#: Global safety valve. The content-hash ledger makes RE-syncs free, but the
#: FIRST sync pays the LLM for everything: 100 hosts x 50 recordings x 3 windows
#: is 15,000 calls, i.e. thousands of extraction batches on day one. Lower than
#: Confluence's 500 because a transcript record is dense — every one of these is
#: a full _TEXT_CHARS of conversation, where most wiki pages are far shorter.
_MAX_RECORDS = 300

#: A WebVTT cue's timing line. Recognised by the arrow rather than by parsing
#: the timestamps, because Zoom emits both `HH:MM:SS.mmm` and `MM:SS.mmm`.
_VTT_TIMING = re.compile(r"-->")
#: A cue index: a bare integer on its own line, above the timing line.
_VTT_INDEX = re.compile(r"^\d+$")
#: `Speaker Name: what they said`. Non-greedy so the FIRST colon wins — a line
#: like "Sam Lee: the problem is this: nobody can log in" attributes to Sam Lee
#: and keeps the rest as speech.
_VTT_SPEAKER = re.compile(r"^([^:]{1,60}?):\s+(.*)$")
#: Punctuation that no display name carries but ordinary prose does.
_NOT_IN_A_NAME = re.compile(r"[,;.!?]")


def _looks_like_speaker(candidate: str) -> bool:
    """True when the text before a colon is plausibly a Zoom display name.

    A length bound alone is not enough, and that is not hypothetical: "the
    problem is this: nobody in the enterprise tier can log in" has only
    nineteen characters before the colon, so a purely length-based rule
    attributes a customer's actual complaint to a speaker named "the problem is
    this" — and then merges every later cue from that "speaker" into it.

    Three signals together, all cheap:
      * at most four words — display names are "Sam", "Sam Lee", "Sam Lee (Acme)"
      * no sentence punctuation — prose has commas and full stops, names do not
      * starts with a capital, or is an address — Zoom writes either a display
        name (capitalised) or an email

    A lowercase display name is missed and its cue is kept as unattributed
    text, which loses attribution but never loses the words. That is the right
    way round: a wrong speaker label is asserted misinformation, a missing one
    is only less detail.
    """
    words = candidate.split()
    if not words or len(words) > 4:
        return False
    # An email label is a single token and legitimately carries dots, so it is
    # decided before the punctuation rule rather than tripping over it.
    if len(words) == 1 and "@" in candidate:
        return True
    if _NOT_IN_A_NAME.search(candidate):
        return False
    return candidate[:1].isupper()


def parse_vtt(raw: str) -> tuple[str, list[str]]:
    """Parse WebVTT into `(speaker-attributed text, speakers)`.

    Hand-rolled rather than pulled from a library on purpose: this is thirty
    lines against a format we only ever read one dialect of, and
    `backend/requirements.txt` pins are load-bearing enough that adding a
    dependency is its own PR with its own justification.

    Consecutive cues from the SAME speaker are merged into one paragraph. Zoom
    emits a cue every few seconds, so an unmerged transcript is hundreds of
    two-line fragments — which reads to an extractor as hundreds of disconnected
    utterances rather than one person making one argument.

    Cues with no `Speaker:` prefix are kept, not dropped: some Zoom accounts
    record without speaker attribution, and discarding those would turn a
    perfectly good transcript into an empty one.
    """
    blocks: list[tuple[str | None, list[str]]] = []
    speakers: list[str] = []

    for chunk in re.split(r"\r?\n\s*\r?\n", raw or ""):
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        # Drop the file header, NOTE comments, cue indices and timing lines —
        # everything that is not somebody talking.
        payload = [
            ln for ln in lines
            if not _VTT_TIMING.search(ln)
            and not _VTT_INDEX.match(ln)
            and not ln.upper().startswith("WEBVTT")
            and not ln.upper().startswith("NOTE")
        ]
        if not payload:
            continue
        text = " ".join(payload).strip()
        if not text:
            continue
        speaker: str | None = None
        match = _VTT_SPEAKER.match(text)
        if match and _looks_like_speaker(match.group(1).strip()):
            candidate, said = match.group(1).strip(), match.group(2).strip()
            if not said:
                continue
            speaker, text = candidate, said
            if speaker not in speakers:
                speakers.append(speaker)
        if blocks and blocks[-1][0] == speaker:
            blocks[-1][1].append(text)
        else:
            blocks.append((speaker, [text]))

    rendered = "\n".join(
        f"{who}: {' '.join(said)}" if who else " ".join(said)
        for who, said in blocks
    )
    return rendered, speakers


def _windows(cursor: str | None, today: date | None = None) -> list[tuple[str, str]]:
    """The `(from, to)` date pairs this run should walk, NEWEST FIRST.

    Delegates to `zoom_oauth.sync_windows` so the KG puller and the call index
    obey ONE windowing rule — Zoom's silent clamping past a month is subtle
    enough that two copies would eventually disagree, and the one that drifted
    would look like a quiet quarter rather than like a bug.
    """
    return sync_windows(cursor, today=today, max_windows=_MAX_WINDOWS)


def _hosts(ctx: ZoomContext) -> list[dict[str, Any]]:
    """The hosts this sync walks, as `[{id, email, display_name}]`.

    A non-empty selection is used verbatim — including ids the account listing
    would not return, because a selected host who has since been deactivated
    still OWNS their old recordings and dropping them would silently shrink the
    corpus. An EMPTY selection means every licensed host: unlicensed (Basic)
    accounts cannot record to the cloud at all, so listing them would spend one
    request per host to learn nothing.
    """
    if ctx.user_ids:
        return [
            {"id": uid, "email": ctx.user_names.get(uid, ""),
             "display_name": ctx.user_names.get(uid, uid)}
            for uid in ctx.user_ids[:_MAX_HOSTS]
        ]
    found, capped = list_users(ctx.access_token)
    if capped:
        logger.info(
            "zoom: the account has more hosts than one listing pass returns — "
            "syncing the first %d. Pick hosts in Settings to choose which.",
            len(found),
        )
    return [u for u in found if u.get("licensed")][:_MAX_HOSTS]


def _transcript_for(ctx: ZoomContext, meeting: dict[str, Any]) -> dict[str, Any] | None:
    """The meeting's transcript file entry, or None when it has none.

    The recordings LISTING already carries `recording_files`, so the common case
    costs no extra request. `get_meeting_recordings` is the fallback for when it
    doesn't — deliberately not the primary path, because one call per meeting
    across a 3-month backfill is the N+1 the Confluence puller's comment header
    warns about (there, 250 pages per space became a 250x request multiplier).
    """
    found = transcript_file_from(meeting)
    if found:
        return found
    if not meeting.get("recording_files"):
        detail = get_meeting_recordings(ctx.access_token, str(meeting.get("uuid") or ""))
        if detail:
            return transcript_file_from(detail)
    return None


def _to_record(
    ctx: ZoomContext, host: dict[str, Any], meeting: dict[str, Any]
) -> RawRecord | None:
    """One recording → RawRecord, or None when there is no usable identity.

    A meeting WITHOUT a transcript still yields a record, and its text says so
    in words. That is deliberate and it is the difference between "we found
    nothing" and "we could not look": the commonest cause is audio transcription
    being switched off in the customer's Zoom account, which is a setting they
    can change — and silently skipping those meetings would present a half-empty
    corpus as a complete one, with nothing anywhere to explain the gap.
    """
    uuid = meeting.get("uuid") or meeting.get("id")
    if not uuid:
        return None

    topic = str(meeting.get("topic") or "").strip()
    speakers: list[str] = []
    text = ""
    entry = _transcript_for(ctx, meeting)
    if entry:
        # The download_url is a credential-bearing link to customer
        # conversation. It is passed to the fetcher and never logged, never
        # stored in properties, and never rendered into the record.
        raw = fetch_transcript_text(ctx.access_token, entry.get("download_url") or "")
        if raw:
            text, speakers = parse_vtt(raw)
    if not text:
        text = (
            "No transcript available for this recording. The meeting was "
            "recorded to the Zoom cloud but no readable transcript file was "
            "found — the commonest cause is audio transcription being turned "
            "off for the account."
        )

    duration = meeting.get("duration")
    return RawRecord(
        provider=ZOOM_PROVIDER,
        kind="meeting",
        external_id=str(uuid),
        title=topic or "(untitled Zoom meeting)",
        text=text[:_TEXT_CHARS],
        properties={
            "host_email": meeting.get("host_email") or host.get("email") or "",
            "duration_min": duration if isinstance(duration, int) else None,
            "start_time": meeting.get("start_time"),
            # From the transcript, so no extra request — see the module header.
            "speakers": speakers,
            # Carried so a reader can tell an empty call apart from a call we
            # could not read, without re-deriving it from the text.
            "has_transcript": bool(speakers or (entry is not None and text)),
        },
        timestamp=meeting.get("start_time"),
    )


def _stamp_counters(company_id: str, *, meetings: int, transcripts: int,
                    until: str | None) -> None:
    """Persist the run's counters and advance the cursor.

    Via `patch_connection_config` — a MERGE, never a wholesale write. The
    connection config also holds `sync_user_ids`, and replacing it here would
    silently widen a narrowed selection back to every host, which is the same
    regression the OAuth callback had to be fixed for.

    Best-effort: a sync that produced records must not be reported as failed
    because the bookkeeping write did not land.
    """
    patch: dict[str, Any] = {
        CONFIG_LAST_SYNC_MEETINGS: meetings,
        CONFIG_LAST_SYNC_TRANSCRIPTS: transcripts,
    }
    if until:
        patch[CONFIG_LAST_SYNCED_UNTIL] = until
    try:
        from app import db

        db.patch_connection_config(company_id, ZOOM_PROVIDER, patch)
    except Exception:  # noqa: BLE001 — bookkeeping must not fail a good sync
        logger.warning(
            "zoom: could not stamp sync counters for %s", company_id, exc_info=True,
        )


def pull(company_id: str) -> Iterator[RawRecord]:
    """Yield a RawRecord per cloud recording across the company's synced hosts.

    Error-isolated per host (an unreadable one is logged and skipped) — but if
    EVERY host failed and nothing was yielded, the last error is re-raised.
    Otherwise a revoked grant would report a cheerful zero-record sync, which
    looks identical to "nobody recorded anything" on the connection row.

    The cursor only advances when every host was walked cleanly. A partial run
    that moved the watermark would skip the failed host's window permanently —
    those calls would never be picked up by any later sync, and nothing would
    ever say so.
    """
    try:
        ctx = sync_context(company_id)
    except ZoomNotConnectedError as e:
        logger.warning("zoom puller: %s — nothing to pull", e)
        return

    hosts = _hosts(ctx)
    if not hosts:
        logger.info("zoom puller: no licensed hosts to sync for %s", company_id)
        return

    windows = _windows(ctx.last_synced_until)
    # The newest window's upper bound is where the cursor lands on success.
    synced_until = windows[0][1] if windows else None

    emitted = 0
    meetings_seen = 0
    transcripts_seen = 0
    yielded = False
    capped = False
    last_error: Exception | None = None
    seen_ids: set[str] = set()

    for host in hosts:
        if capped:
            break
        try:
            for frm, to in windows:
                meetings = list_user_recordings(
                    ctx.access_token, str(host["id"]), frm=frm, to=to,
                    page_size=_MAX_RECORDINGS_PER_HOST, max_pages=1,
                )
                for meeting in meetings[:_MAX_RECORDINGS_PER_HOST]:
                    if emitted >= _MAX_RECORDS:
                        logger.warning(
                            "zoom: hit the %d-record cap for %s — pick specific "
                            "hosts in Settings to cover the rest",
                            _MAX_RECORDS, company_id,
                        )
                        capped = True
                        break
                    record = _to_record(ctx, host, meeting)
                    if record is None:
                        continue
                    # A recurring meeting can appear in two adjacent windows;
                    # the ledger would dedupe the LLM cost but the counters
                    # would still double-count, which is what the web layer
                    # reads to decide whether transcription is switched off.
                    if record.external_id in seen_ids:
                        continue
                    seen_ids.add(record.external_id)
                    meetings_seen += 1
                    if record.properties.get("has_transcript"):
                        transcripts_seen += 1
                    emitted += 1
                    yielded = True
                    yield record
                if capped:
                    break
        except ZoomAuthExpiredError:
            raise  # never swallow a reconnect signal behind per-host isolation
        except Exception as e:  # noqa: BLE001 — one bad host must not end the sync
            logger.info(
                "zoom: skipping host %s: %s", host.get("email") or host.get("id"), e
            )
            last_error = e

    if not yielded and last_error is not None:
        raise last_error

    _stamp_counters(
        company_id,
        meetings=meetings_seen,
        transcripts=transcripts_seen,
        # A run that skipped a host, or stopped at the valve, has NOT covered
        # its window — moving the watermark would drop the remainder forever.
        until=synced_until if (last_error is None and not capped) else None,
    )
