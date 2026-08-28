"""Google Meet puller — meeting transcripts → RawRecords.

The credential kg_ingest hands us is the owning COMPANY ID, not an access token
(see runner.PULLERS and google_meet.token_payload_to_store). A Meet pull needs
the connected account's identity as well as the token, and `runner.token_for`
can only pass one field — so the field it passes is the company id and
`google_meet.sync_context` resolves the rest. The `uploads`, `confluence` and
`zoom` pullers use the same trick for the same reason.

WHAT WE PULL, AND WHAT WE DON'T:
    One record per conference record, whose text is the SPEAKER-ATTRIBUTED
    transcript joined from `transcripts.entries` — structured text straight from
    the Meet API, with no file to download and no format to parse. Deliberately
    excluded —
      * recordings      the MP4 lives in the organizer's Drive and is reachable
                        only through a RESTRICTED Drive scope, which would put
                        Sprntly's whole Google client through an annual paid
                        CASA assessment. Permanently out of scope; see
                        connectors/google_meet.py's header.
      * smart notes     Gemini's meeting notes are a Drive document. Same wall.
      * chat            in-meeting chat is not exposed by the Meet REST API.

THIRTY DAYS IS THE WHOLE CORPUS. Google deletes conference records and their
transcript entries 30 days after the conference ends, so there is no historical
backfill to write and no incremental cursor worth keeping: "everything that
exists" and "the last 30 days" are the same set, and a full window every run is
both correct and cheap (the runner's content-hash ledger makes a re-seen record
free — it never reaches the model twice).

COVERAGE IS ONE PERSON'S MEETINGS. `conferenceRecords.list` returns only
conferences the connected account ORGANIZED. There is no admin-wide listing, so
unlike Zoom this connector cannot be widened by asking for a bigger scope — each
teammate connects their own account. Every record says whose account it came
from (`organizer_email`) so a reader can never mistake one person's calls for
the company's.
"""
from __future__ import annotations

import logging
from typing import Any, Iterator

from app.connectors.google_meet import (
    CONFIG_LAST_SYNC_MEETINGS,
    CONFIG_LAST_SYNC_TRANSCRIPTS,
    GOOGLE_MEET_PROVIDER,
    MeetAuthExpiredError,
    MeetContext,
    MeetNotConnectedError,
    list_conference_records,
    list_participants,
    list_transcript_entries,
    list_transcripts,
    participant_display_name,
    sync_context,
)
from app.kg_ingest.types import TRANSCRIPT_CHAR_CEILING, RawRecord

logger = logging.getLogger(__name__)

#: Conferences walked per sync. One person can only organize so many meetings in
#: 30 days; 100 back-to-back calls a month is already a full calendar, and each
#: one costs three API round trips (participants + transcripts + entries), so
#: this is also the request budget — 300 calls against a 600/min per-user quota.
_MAX_CONFERENCES = 100
#: Per-record extraction ceiling — a DEFENSIVE bound, not a head-truncation
#: window. Meet is a call-shaped provider (`_CALL_PROVIDERS`), extracted ONE
#: CALL PER DOCUMENT, so this never shares a batch with another record. The
#: prior 4,000-char head bound genuinely truncated an hour-long call
#: (~50,000 characters) and a fact stated only in the transcript can sit
#: anywhere in the call — closing Meet's latent Loss-A, same finding as
#: Fireflies/Zoom. Same shared constant as the Zoom puller, deliberately:
#: two meeting connectors bounding differently would make the same call look
#: different depending which tool recorded it
#: (`app.kg_ingest.types.TRANSCRIPT_CHAR_CEILING`).
_TEXT_CHARS = TRANSCRIPT_CHAR_CEILING
#: Global safety valve. The content-hash ledger makes RE-syncs free, but the
#: FIRST sync pays the LLM for everything. Lower than Zoom's 300 because Meet's
#: coverage is one organizer's calendar rather than a whole account's hosts —
#: past this something is wrong, not busy.
_MAX_RECORDS = 150
#: Participant names held per conference. A Meet call caps around 1,000 people;
#: past this the meeting is a webinar, not a conversation, and the speaker map
#: only ever needs the people who actually spoke.
_MAX_PARTICIPANTS = 250
#: Transcript entries joined into one record's text. Each is one utterance, so
#: this is generous for an hour of conversation — and the _TEXT_CHARS truncation
#: bites long before it in practice. It exists so a pathological transcript
#: cannot hold an unbounded list in memory.
_MAX_ENTRIES = 2000

#: Google's transcript lifecycle. Only FILE_GENERATED means the transcript is
#: finished and its entries are readable; STARTED is a live meeting and ENDED is
#: the gap while Google is still assembling the file. Reading either of those
#: yields a partial transcript that would then be ledger-hashed as if it were
#: the whole thing — the meeting would look permanently half-recorded, because
#: the finished version hashes differently only if we re-read it, and we
#: wouldn't. So: skip, and pick it up on the next run.
#: https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords.transcripts
_TRANSCRIPT_READY = "FILE_GENERATED"


def _speaker_map(ctx: MeetContext, conference_name: str) -> dict[str, str]:
    """participant resource name → display name, for one conference.

    A transcript entry names its speaker as
    `conferenceRecords/{c}/participants/{p}`, never as text, so this listing is
    the only way the words get attributed to a person. Failure is tolerated and
    degrades to an unattributed transcript: losing the names costs detail, while
    dropping the meeting would lose what was actually said.
    """
    try:
        participants = list_participants(ctx.access_token, conference_name)
    except MeetAuthExpiredError:
        raise  # never swallow a reconnect signal
    except Exception as e:  # noqa: BLE001 — attribution is worth less than the words
        logger.info(
            "google_meet: could not list participants for %s: %s", conference_name, e
        )
        return {}
    out: dict[str, str] = {}
    for p in participants[:_MAX_PARTICIPANTS]:
        name = p.get("name")
        display = participant_display_name(p)
        if name and display:
            out[str(name)] = display
    return out


def join_entries(
    entries: list[dict[str, Any]], speakers: dict[str, str]
) -> tuple[str, list[str]]:
    """Transcript entries → `(speaker-attributed text, speakers in order)`.

    Consecutive entries from the SAME speaker are merged into one paragraph.
    Google emits an entry per utterance, so an unmerged transcript is hundreds
    of one-line fragments — which reads to an extractor as hundreds of
    disconnected statements rather than one person making one argument. (The
    Zoom puller merges WebVTT cues for exactly this reason; the shape of the
    problem is the same even though the input format is not.)

    An entry whose speaker is unknown — a participant who left before the
    listing, or a listing that failed — keeps its words and loses only its
    label. A wrong speaker label is asserted misinformation; a missing one is
    only less detail.
    """
    blocks: list[tuple[str | None, list[str]]] = []
    ordered: list[str] = []
    for entry in entries:
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        who = speakers.get(str(entry.get("participant") or "")) or None
        if who and who not in ordered:
            ordered.append(who)
        if blocks and blocks[-1][0] == who:
            blocks[-1][1].append(text)
        else:
            blocks.append((who, [text]))
    rendered = "\n".join(
        f"{who}: {' '.join(said)}" if who else " ".join(said)
        for who, said in blocks
    )
    return rendered, ordered


def _transcript_text(
    ctx: MeetContext, conference_name: str, speakers: dict[str, str]
) -> tuple[str, list[str], bool]:
    """`(text, speakers, had_a_ready_transcript)` for one conference.

    Every FILE_GENERATED transcript on the conference is read and concatenated,
    not just the first: transcription stopped and restarted mid-call produces
    two, and taking `[0]` would silently drop the second half of the meeting.

    DELIBERATELY DOES NOT SWALLOW A READ FAILURE. It is tempting to catch here
    and fall through to the no-transcript record, and that is wrong: that record
    states in words that the meeting was probably never set to transcribe, which
    is a claim about the customer's own Google Meet settings. A 500 from Google
    or a dropped connection is not evidence of that, and asserting it would put
    a confident false conclusion in the knowledge graph — the exact failure
    class the honest-degradation record exists to prevent. So a read that FAILED
    propagates, `pull` skips that one conference, and the next sweep picks it up;
    only a read that SUCCEEDED and found nothing ready produces the record that
    explains itself.
    """
    transcripts = list_transcripts(ctx.access_token, conference_name)
    ready = [t for t in transcripts if str(t.get("state") or "") == _TRANSCRIPT_READY]
    if not ready:
        return "", [], False

    entries: list[dict[str, Any]] = []
    for transcript in ready:
        name = transcript.get("name")
        if not name:
            continue
        entries.extend(list_transcript_entries(ctx.access_token, str(name)))
        if len(entries) >= _MAX_ENTRIES:
            entries = entries[:_MAX_ENTRIES]
            break
    text, speaker_names = join_entries(entries, speakers)
    return text, speaker_names, True


#: What a record says when the meeting happened but nobody turned transcription
#: on. Spelled out in words rather than left as an empty text, because the
#: commonest cause is a Google Meet setting the customer can change and a
#: silently-skipped meeting presents a half-empty corpus as a complete one, with
#: nothing anywhere to explain the gap. Same reasoning, and deliberately the
#: same shape, as the Zoom puller's no-transcript record.
_NO_TRANSCRIPT_TEXT = (
    "No transcript available for this Google Meet call. The meeting took place "
    "but no finished transcript was found — the commonest cause is that "
    "\"Record the transcript\" was never switched on for the meeting (Google "
    "does not transcribe a call retroactively). This is a meeting we could not "
    "read, NOT an empty meeting."
)


def _title_for(
    record: dict[str, Any], speakers: dict[str, str], start: str | None
) -> str:
    """A label a person can recognise the call by.

    The Meet API gives a conference no subject line — `space` is an opaque
    resource name and the meeting's calendar title is not exposed — so the
    honest recognisable handle is WHO WAS THERE plus WHEN. Built from the
    participant map we already fetched for speaker attribution, so it costs no
    extra request; deliberately not `spaces.get`, which would buy a meeting code
    like "abc-defg-hij" for one more round trip per conference and be less
    recognisable than the names.
    """
    names = [n for n in speakers.values() if n][:3]
    day = (start or "")[:10]
    if names:
        who = ", ".join(names)
        if len(speakers) > len(names):
            who += f" +{len(speakers) - len(names)}"
        return f"Google Meet call with {who}" + (f" on {day}" if day else "")
    return f"Google Meet call on {day}" if day else "Google Meet call"


def _to_record(ctx: MeetContext, record: dict[str, Any]) -> RawRecord | None:
    """One conference record → RawRecord, or None when there is no identity.

    A conference WITHOUT a finished transcript still yields a record, and its
    text says so in words — see _NO_TRANSCRIPT_TEXT.
    """
    name = record.get("name")
    if not name:
        return None

    start = record.get("startTime")
    speakers = _speaker_map(ctx, str(name))
    text, speaker_names, has_transcript = _transcript_text(ctx, str(name), speakers)
    if not text:
        text = _NO_TRANSCRIPT_TEXT
        has_transcript = False

    return RawRecord(
        provider=GOOGLE_MEET_PROVIDER,
        kind="meeting",
        external_id=str(name),
        title=_title_for(record, speakers, start),
        text=text[:_TEXT_CHARS],
        properties={
            # Everyone in the room, not just whoever spoke — an attendee who
            # said nothing is still evidence of who the meeting was with.
            "participants": sorted(set(speakers.values())),
            "speakers": speaker_names,
            "start_time": start,
            "end_time": record.get("endTime"),
            # Carried so a reader can tell a call with nothing in it apart from
            # a call we could not read, without re-deriving it from the text.
            "has_transcript": has_transcript,
            # Coverage is organizer-only, so this is not bookkeeping: it is the
            # scope statement for the record. Whoever reads this signal later
            # can see it is one person's meeting, not the company's.
            "organizer_email": ctx.account_email,
        },
        timestamp=start,
    )


def _stamp_counters(company_id: str, *, meetings: int, transcripts: int) -> None:
    """Persist the run's counters.

    Via `patch_connection_config` — a MERGE, never a wholesale write. The
    connection config also holds the cached identity, and replacing it here
    would drop the account email that every record's `organizer_email` is built
    from. (The OAuth callback had to be fixed for the same class of mistake on
    Zoom.)

    Best-effort: a sync that produced records must not be reported as failed
    because the bookkeeping write did not land.
    """
    try:
        from app import db

        db.patch_connection_config(
            company_id,
            GOOGLE_MEET_PROVIDER,
            {
                CONFIG_LAST_SYNC_MEETINGS: meetings,
                CONFIG_LAST_SYNC_TRANSCRIPTS: transcripts,
            },
        )
    except Exception:  # noqa: BLE001 — bookkeeping must not fail a good sync
        logger.warning(
            "google_meet: could not stamp sync counters for %s",
            company_id, exc_info=True,
        )


def pull(company_id: str) -> Iterator[RawRecord]:
    """Yield a RawRecord per Google Meet conference in the last 30 days.

    Error-isolated per conference (an unreadable one is logged and skipped) —
    but if EVERY conference failed and nothing was yielded, the last error is
    re-raised. Otherwise a revoked grant would report a cheerful zero-record
    sync, which looks identical to "nobody had any meetings" on the connection
    row.
    """
    try:
        ctx = sync_context(company_id)
    except MeetNotConnectedError as e:
        logger.warning("google_meet puller: %s — nothing to pull", e)
        return

    records = list_conference_records(ctx.access_token)
    if not records:
        logger.info(
            "google_meet puller: no conferences in the last 30 days for %s "
            "(coverage is meetings this account ORGANIZED)", company_id,
        )
        _stamp_counters(company_id, meetings=0, transcripts=0)
        return

    emitted = 0
    transcripts_seen = 0
    yielded = False
    last_error: Exception | None = None
    seen_ids: set[str] = set()

    for record in records[:_MAX_CONFERENCES]:
        if emitted >= _MAX_RECORDS:
            logger.warning(
                "google_meet: hit the %d-record cap for %s — the newest calls "
                "are covered and the oldest of the 30-day window are not",
                _MAX_RECORDS, company_id,
            )
            break
        try:
            row = _to_record(ctx, record)
        except MeetAuthExpiredError:
            raise  # never swallow a reconnect signal behind per-record isolation
        except Exception as e:  # noqa: BLE001 — one bad call must not end the sync
            logger.info(
                "google_meet: skipping conference %s: %s", record.get("name"), e
            )
            last_error = e
            continue
        if row is None or row.external_id in seen_ids:
            continue
        seen_ids.add(row.external_id)
        if row.properties.get("has_transcript"):
            transcripts_seen += 1
        emitted += 1
        yielded = True
        yield row

    if not yielded and last_error is not None:
        raise last_error

    _stamp_counters(company_id, meetings=emitted, transcripts=transcripts_seen)
