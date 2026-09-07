"""Prose attached in chat, read as evidence for ONE run and written nowhere.

WHAT THIS CHANGES. A spreadsheet attached to a Goal Analysis message already
informs the plan: `recon` reads its rectangles, the gate reports what it saw,
and the weighting unit can be measured against it. A PDF did not. It was
disclosed as unread and contributed nothing — so a reader who attached ten
customer calls and asked what to build was told, accurately and uselessly,
that their document was not a spreadsheet.

This module makes that document countable, attributable, refutable and
citable, WITHOUT changing the one property that makes attaching a file safe:

    NOTHING HERE REACHES THE KNOWLEDGE GRAPH.

Not `kg_signal`, not `kg_entity`, not `kg_relationship`. The same bytes sent
through Settings -> Connectors -> Uploads are INGESTED — permanent, tenant-wide,
answering every later question, with no undo in the product. That is correct
for a company's standing sources and a one-way contamination for a file someone
is analysing once. This path reads, projects in memory, and forgets.

── FOUR DECISIONS, EACH MEASURED RATHER THAN ASSUMED ────────────────────────

**1. A DOCUMENT IS NOT AN ARTIFACT. A CONVERSATION IS.**
The refutation step asks "did all this evidence come from one conversation?"
(`pipeline._refute`'s `echo` rule). If a whole PDF is one artifact then every
finding supported only by that PDF dies there by construction. Measured on a
real ten-call transcript: per-file identity produced 3 findings from 68 claims
with 12 killed as echo; per-conversation identity produced 7, with 8 killed.

Per-conversation is also simply MORE CORRECT about the same bytes than the
graph is. The Settings-upload path chunks at
`kg_ingest.pullers.uploads._CHUNK_CHARS` (4,000), so an 11,379-character
ten-call pack becomes about three artifacts there — splitting some calls in
half and merging others. Neither "one file, one conversation" nor "one file,
N character budgets" is a fact about the document; the per-call header is.

**2. THE BOUNDARY MUST BE SELF-VERIFYING, OR IT IS NOT USED.**
The split is derived from the transcript header's own `Date:` label lines, and
CHECKED against the count the document states about itself ("10 calls" in its
own header). If those disagree — or if any segment carries no date — the whole
file is one segment under the file's own id. A silent mis-split would produce
findings whose provenance is confidently wrong, which is worse than the
per-file answer this falls back to.

**3. THE DATE COMES FROM THE SAME MARKER AS THE BOUNDARY.**
Ids per conversation with `now()` on every claim leaves decay wrong and lets a
two-year-old document outrank last week's Slack. One read of one header gives
both, or neither is used.

**4. THE WITNESS IS THE SOURCE TYPE. "UPLOAD" IS A TRANSPORT.**
`claims.AUTHORITATIVE_FOR` and `claims.DEFAULT_STRENGTH` are keyed on who is
speaking, not on how the bytes arrived. Nothing here pins a source type: the
extractor classifies it, exactly as it does for a connector's text, and on the
measured corpus it chose `customer_voice` for 62 of 68 claims unprompted — the
`no_authority` rule fired zero times. What IS applied is a CEILING: a claim
whose provenance says it came from a chat attachment cannot exceed `reported`
strength (`claims.project_signal`), so a document the reader wrote themselves
can never size a finding as measured fact.

── WHY IT REUSES `project_signals` RATHER THAN BUILDING `Claim`S ────────────

`crucible.claims` is a pure module — it imports no database client — so the
real projection runs over row-shaped dicts just as happily as over rows read
back from Postgres. Building `Claim` objects here instead would be a SECOND
classifier: a second `kind -> claim_type` table, a second authority lookup, a
second population rule, a second figure-class read, every one of them free to
drift from the one the graph's own evidence goes through. Constructing rows
and running the real function is the containment.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

# ── Identity ────────────────────────────────────────────────────────────────

#: `Signal.origin` for a run-scoped read. Matches the `source_type` `recon`
#: stamps on an uploaded TABLE (`recon.UPLOAD_SOURCE_TYPE`) so the two halves
#: of "a file the reader attached" say the same word.
UPLOAD_ORIGIN = "upload"

#: `provenance["channel"]` — THE FLAG THE STRENGTH CEILING IS KEYED ON.
#: `claims.ATTACHMENT_CHANNEL` is the reader; this is the writer. A rename
#: here without one there silently removes the cap, which is why the reader
#: side asserts against this constant in the tests rather than a literal.
ATTACHMENT_CHANNEL = "chat_attachment"

#: Root namespace for run-scoped signal ids. DELIBERATELY NOT the extractor's
#: `_NS`: that namespace keys ids on `enterprise_id|content` alone, so a
#: sentence in an attached file that also exists verbatim in `kg_signal` would
#: derive the SAME id as the stored row and the two would collide in every
#: `claims_by_id` map downstream.
_PROSE_NS = uuid.UUID("7d1f5c84-0a6b-4e2d-9c3a-5b8e1f0d2a41")

#: Namespace for the synthetic theme-entity ids a prose-only topic gets. Same
#: reasoning: a `kg_entity` id and a made-up one must never be confusable.
_PROSE_THEME_NS = uuid.UUID("7d1f5c84-0a6b-4e2d-9c3a-5b8e1f0d2a42")


def artifact_namespace(artifact_id: str) -> uuid.UUID:
    """The uuid5 namespace one CONVERSATION's claims are drawn from.

    THE ARTIFACT IS IN THE NAMESPACE, NOT IN THE KEY, and that is what makes
    two calls saying the same sentence two claims rather than one. The graph's
    own id is content-keyed on purpose — a re-sync must not duplicate a fact —
    but two DIFFERENT customers independently saying "the export is too slow"
    is corroboration, and collapsing them to one id would delete exactly the
    repetition the engine is looking for, then let `echo` refute what remained.
    """
    return uuid.uuid5(_PROSE_NS, artifact_id)


# ── What counts as prose ────────────────────────────────────────────────────

def _prose_suffixes() -> tuple[str, ...]:
    """Every format `app.ingest` has a real converter for, MINUS the tabular ones.

    DERIVED, NOT LISTED, and both halves of that matter.

    Derived from `ingest.SUPPORTED_SUFFIXES` because a hand-written list can
    contain a suffix `convert` has no converter for — and `convert` does not
    fail on one, it returns a placeholder STUB. That stub is non-empty text, so
    it would sail past every length check here and be sent to the model as if
    it were the document, producing claims about a sentence that says the file
    could not be read.

    Minus `recon.TABULAR_SUFFIXES` because a spreadsheet must not be counted
    twice: `recon` already reads its rectangles and the plan gate weighs the
    run's own unit against them, and flattening the same file into sentences
    would have one attachment both inform the method and vote on the findings.
    """
    from app.crucible.recon import TABULAR_SUFFIXES
    from app.ingest import SUPPORTED_SUFFIXES

    tabular = {s.lower() for s in TABULAR_SUFFIXES}
    return tuple(s for s in SUPPORTED_SUFFIXES if s.lower() not in tabular)


PROSE_SUFFIXES: tuple[str, ...] = _prose_suffixes()

#: Below this many characters a "document" has nothing to extract from and an
#: extraction call over it is a wasted model call with a confident-looking
#: empty answer. Named rather than inlined because it is the boundary between
#: "read it" and "say you did not".
MIN_PROSE_CHARS = 200

#: Files read as prose in one run. A bound on model spend, not on ambition:
#: `routes.crucible.MAX_RUN_ATTACHMENTS` already caps a message at 16 files,
#: and every prose file costs at least one extraction call per conversation
#: inside it.
MAX_PROSE_FILES = 8

#: Conversations extracted from ONE document. A pack of 200 calls is a
#: connector's job, not an attachment's.
MAX_SEGMENTS_PER_FILE = 40

#: Characters of one segment sent to the model. Long enough for a full call
#: transcript; short enough that a pathological single-segment document cannot
#: send an unbounded prompt.
MAX_SEGMENT_CHARS = 60_000

#: How many segment extractions may be in flight at once.
#:
#: TWO, AND THE NUMBER IS THE POINT. Measured serially: 10 calls, 133.3s,
#: 13ms of overlap between them. `app.llm`'s concurrency gate
#: (`LLM_MAX_CONCURRENCY`, default 6) is process-wide and shared with every
#: interactive chat call on the box, and `routes.crucible._POOL` already
#: admits two runs at once — so two here is up to four in flight against a
#: cap of six, and four here would be eight against six and would starve
#: chat. The same arithmetic, and the same answer, as
#: `relevance.MAX_PARALLEL`.
MAX_PARALLEL_SEGMENTS = 2


# ── Segmentation ────────────────────────────────────────────────────────────

#: The transcript header's own `Date:` label. `\s` rather than a literal space
#: because a PDF text layer separates the label from its value with a tab.
_DATE_LABEL = re.compile(r"^\s*Date:\s*(.*)$")
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

#: The document's own statement of how many conversations it holds — "10
#: calls", "· 6 interviews ·". Used to CHECK the split, never to produce it.
#:
#: `[ \t]+` AND NOT `\s+`, AND THIS IS NOT A STYLE CHOICE. `\s` matches a
#: NEWLINE, so on the real document the pattern matched across a blank line:
#:
#:     ## Page 1
#:                      <- `1\n\nCall` matched here, and `search` returns the
#:     Call Transcripts    FIRST match, so the count parsed as 1
#:     ... · 10\tcalls · ...
#:
#: A page NUMBER and the first word of a heading were read as the document's
#: own count of its conversations. The split then "disagreed" with a number
#: the document never stated (10 headers found, 1 claimed), so a correctly
#: segmented ten-call pack fell all the way back to one whole-file segment
#: dated to the run clock — worth 3 findings instead of 7 on the measured
#: corpus. The guard behaved perfectly and disclosed exactly what it did; the
#: thing it was guarding was wrong.
#:
#: THE TAB IS LOAD-BEARING IN THE CHARACTER CLASS. The separator in that PDF's
#: text layer is `\t`, not a space, so a bare `[ ]+` reintroduces the failure
#: on the exact file that exposed it.
_STATED_COUNT = re.compile(
    r"\b(\d{1,3})[ \t]+(calls?|conversations?|interviews?|meetings?|transcripts?)\b",
    re.IGNORECASE,
)

#: A page header `app.ingest`'s PDF converter emits. Never a conversation
#: title, and never a boundary: a 3-page document holding 10 calls cuts calls
#: in half at every page break — the same failure as a character budget.
_PAGE_HEADER = "## Page "

#: Dates outside this band are not a document's own dating. A `\d{4}-\d{2}-\d{2}`
#: match can be a product SKU or a future-dated plan; neither is an
#: observation date, and accepting one would make a claim look fresher than
#: any evidence in the corpus.
_EARLIEST_PLAUSIBLE = datetime(1990, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class ProseSegment:
    """One conversation inside an attached document."""
    index: int
    #: The nearest preceding non-empty, non-page-header line — the call's own
    #: title. May be empty, in which case the artifact id falls back to the
    #: FILE (see `ProseDocument.artifact_id_for`) and never to "".
    title: str
    observed_at: Optional[datetime]
    text: str


@dataclass(frozen=True)
class ProseDocument:
    """An attached document, read and segmented but NOT yet extracted.

    Produced at the plan gate, where reading is local and fast (a PDF text
    layer, no model call) and the reader is about to decide whether to approve.
    The extraction that turns these segments into claims happens after
    `/approve` — see `extract_documents`.
    """
    name: str
    sha256: str
    chars: int
    segments: tuple[ProseSegment, ...]
    #: True when the per-conversation split was BOTH found and verified. False
    #: means `segments` is a single whole-file segment.
    per_conversation: bool
    #: How many `Date:` markers were found, before any check. Kept even when
    #: the split was rejected, because "I found 9 headers in a document that
    #: says it holds 10 calls" is the disclosure — "I read it as one document"
    #: on its own is not.
    markers_found: int
    #: What the document says about itself, if it says anything.
    stated_count: Optional[int]
    #: Why the per-conversation split was not used, in prose. Empty when it was.
    fallback_reason: str = ""
    #: True when no date could be read anywhere in the file, so the claims are
    #: dated to the moment it was attached. Disclosed, never assumed away.
    dated_by_attachment_time: bool = False

    def artifact_id_for(self, segment: ProseSegment) -> str:
        """Which source DOCUMENT a claim from `segment` names.

        NEVER EMPTY. `pipeline._refute` declines the echo rule entirely unless
        EVERY claim in a cluster names an artifact, so a single unattributed
        claim switches the rule off for its whole cluster and every finding in
        it passes that check vacuously. A segment we cannot name takes the
        FILE's id, which is less precise than we wanted and still true.

        AND NEVER SHARED BETWEEN TWO CONVERSATIONS. Two calls in one pack can
        carry the SAME title — "Call with Northwind — renewal" recurring across
        a quarter is the normal case, not a corner one — and a title-keyed id
        would silently merge them into one artifact. That is precisely the
        collapse this whole scheme exists to prevent, arriving through the back
        door: the echo rule would then see one document behind a cluster built
        from two separate conversations and refute it, and the run would report
        having read ten calls while counting four.

        Disambiguated ON COLLISION ONLY, so the common case keeps a readable
        name and a citation still says which call it came from. This is the
        same shape `recon.read_uploads` already applies to two attachments
        sharing a filename, deliberately rather than by coincidence — one
        convention for "these two things are distinct and were named the same".
        Keyed on position in document order, so it is stable across runs.
        """
        if not self.per_conversation or not segment.title:
            return self.name
        seen = sum(1 for s in self.segments
                   if s.index < segment.index and s.title == segment.title)
        suffix = f" ({seen + 1})" if seen else ""
        return f"{self.name}#{segment.title}{suffix}"

    @property
    def how_it_was_read(self) -> str:
        """One sentence a reader can check the engine against.

        THE PRODUCT MAY ONLY STATE WHAT IT ACTUALLY DOES, so this is derived
        from the segmentation that ran rather than written beside it — there
        is no path on which the sentence and the split can disagree.
        """
        if self.per_conversation:
            n = len(self.segments)
            # WHAT THE SPLIT WAS CHECKED AGAINST, NAMED — and the unverified
            # case named too, which it was not before.
            #
            # This sentence is why a mis-parse of the document's own count took
            # minutes to diagnose instead of an afternoon: it said, in the
            # reader's words, exactly which two numbers disagreed. The verified
            # path has to keep that quality rather than trailing off into "read
            # as 10 conversations", which is a number nobody can check.
            #
            # AND THE THIRD CASE IS NO LONGER SILENT. A document that carries
            # per-call headers but states no count of its own is split on
            # evidence that was never corroborated — the split is used (an
            # absent count is not a disagreement), and saying so is the
            # difference between a checked claim and an unchecked one wearing
            # the same words.
            if self.stated_count == n:
                checked = f" — the document says it holds {n}, and it does"
            elif self.stated_count is None:
                checked = (" — the document states no count of its own, so "
                           "nothing corroborates that split")
            else:  # pragma: no cover — `segment` falls back before this
                checked = ""
            return (
                f"read as {n} separate conversations{checked}, split at the "
                f"per-call headers, each dated from its own header"
            )
        base = "read as one document"
        if self.fallback_reason:
            base += f", because {self.fallback_reason}"
        if self.dated_by_attachment_time:
            base += ("; it states no date, so it is dated to the moment you "
                     "attached it rather than to anything in the text")
        return base


def _plausible_date(text: str, *, now: datetime) -> Optional[datetime]:
    """The first ISO date in `text` that could be an observation date."""
    for m in _ISO_DATE.finditer(text):
        try:
            found = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                             tzinfo=timezone.utc)
        except ValueError:  # 2026-13-45 and friends
            continue
        if _EARLIEST_PLAUSIBLE <= found <= now + timedelta(days=1):
            return found
    return None


def _latest_plausible_date(text: str, *, now: datetime) -> Optional[datetime]:
    """The NEWEST date anywhere in `text`, for a document read whole.

    The newest rather than the first, because a document that discusses a
    history is observed at the end of it: dating a pack of calls to the oldest
    one it mentions would make the whole file decay from a date none of its
    evidence was actually gathered on.
    """
    best: Optional[datetime] = None
    for m in _ISO_DATE.finditer(text):
        try:
            found = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                             tzinfo=timezone.utc)
        except ValueError:
            continue
        if not (_EARLIEST_PLAUSIBLE <= found <= now + timedelta(days=1)):
            continue
        if best is None or found > best:
            best = found
    return best


def segment(text: str, *, name: str, now: datetime) -> tuple[
        tuple[ProseSegment, ...], bool, int, Optional[int], str]:
    """`(segments, per_conversation, markers_found, stated_count, fallback)`.

    THE MARKER IS THE `Date:` LABEL LINE OF EACH TRANSCRIPT HEADER, and the
    title is the nearest preceding line that is neither blank nor a page
    header. Page headers are explicitly NOT boundaries — see `_PAGE_HEADER`.

    THE SPLIT IS CHECKED BEFORE IT IS USED, against two things the document
    itself supplies: the count it states about itself, and whether every
    segment the split produced carries a date. Failing either, this returns a
    single whole-file segment and the reason, and every caller discloses it.
    """
    lines = text.split("\n")
    starts = [i for i, line in enumerate(lines) if _DATE_LABEL.match(line)]
    stated_m = _STATED_COUNT.search(text)
    stated = int(stated_m.group(1)) if stated_m else None

    found: list[ProseSegment] = []
    for n, i in enumerate(starts[:MAX_SEGMENTS_PER_FILE]):
        j = i - 1
        while j >= 0 and (not lines[j].strip()
                          or lines[j].startswith(_PAGE_HEADER)):
            j -= 1
        title = " ".join(lines[j].split()) if j >= 0 else ""

        # THE DATE COMES OFF THE SAME HEADER BLOCK THAT GAVE THE BOUNDARY.
        #
        # THE LABEL'S OWN VALUE FIRST, AND THEN A NARROW WRAP FALLBACK.
        #
        # The fallback exists for ONE reason: a PDF text layer sometimes puts
        # `Date:` and its value on separate lines. It was written as a 3-line
        # window scanned for the first date-shaped thing, which is wider than
        # that reason — it will take a date out of ANY nearby line, including
        # `Type:  discovery — agreed 2026-09-01`, and stamp the conversation
        # with it. Same defect class as `_STATED_COUNT` above, one field over:
        # a search allowed past the line that owns the value silently picks up
        # whatever is on the other side.
        #
        # So the fallback now accepts only what a WRAPPED VALUE looks like: the
        # first non-empty line after the label, and only if it BEGINS with the
        # date. Anything else stops the search and leaves the segment undated —
        # which is the honest answer, and `segment` already refuses to split a
        # document with an undated conversation rather than guessing one.
        own = _DATE_LABEL.match(lines[i])
        observed_at = _plausible_date(own.group(1) if own else "", now=now)
        if observed_at is None:
            for k in range(i + 1, min(i + 3, len(lines))):
                candidate = lines[k].strip()
                if not candidate:
                    continue
                if _ISO_DATE.match(candidate):
                    observed_at = _plausible_date(candidate, now=now)
                # THE FIRST NON-EMPTY LINE DECIDES IT, either way. Continuing
                # past a line that is not the value is what made this a search
                # of the neighbourhood instead of a read of one field.
                break

        end = len(lines)
        if n + 1 < len(starts):
            e = starts[n + 1] - 1
            while e >= 0 and (not lines[e].strip()
                              or lines[e].startswith(_PAGE_HEADER)):
                e -= 1
            end = e
        body = "\n".join(lines[max(j, 0):end]).strip()
        found.append(ProseSegment(index=n, title=title,
                                  observed_at=observed_at, text=body))

    fallback = ""
    if not found:
        fallback = "it carries no per-conversation headers to split on"
    elif stated is not None and stated != len(found):
        fallback = (f"it says it holds {stated} conversations and "
                    f"{len(found)} headers were found, so splitting it would "
                    f"be a guess")
    elif any(s.observed_at is None for s in found):
        undated = sum(1 for s in found if s.observed_at is None)
        fallback = (f"{undated} of its {len(found)} conversation headers "
                    f"carry no date, and an undated conversation cannot be "
                    f"weighed against a dated one")
    elif any(not s.text.strip() for s in found):
        fallback = "one of its conversations came back empty"

    if not fallback:
        return tuple(found), True, len(starts), stated, ""

    whole = ProseSegment(
        index=0, title="", observed_at=_latest_plausible_date(text, now=now),
        text=text,
    )
    return (whole,), False, len(starts), stated, fallback


# ── The gate-side read: local, fast, no model call ──────────────────────────


def read_prose(
    files: Sequence[tuple[str, bytes]], *, now: Optional[datetime] = None,
) -> tuple[tuple[ProseDocument, ...], tuple[Any, ...]]:
    """`(documents, unread)` for the prose among `files`.

    RUNS AT THE PLAN GATE, WHICH RETURNS IN ABOUT A SECOND. Everything here is
    local: a text-layer extraction and a regular expression. The extraction
    that costs tens of seconds per document happens after the reader approves
    (`extract_documents`), because a gate that took a minute to render is a
    gate nobody reads.

    Files whose suffix is not prose are IGNORED here, not reported: they are
    the tabular reader's business, and reporting them from both halves would
    name one attachment twice in one plan.

    `unread` items are `recon.UnreadFile` — the same type, and the same
    disclosure, that the tabular pass already produces. A second, parallel
    "files I could not read" list would be a second thing for a renderer to
    forget.
    """
    from app.crucible.recon import UnreadFile
    from app.ingest import convert, is_unparsed_stub

    now = now or datetime.now(timezone.utc)
    docs: list[ProseDocument] = []
    unread: list[UnreadFile] = []
    for name, data in files:
        if not _is_prose_name(name):
            continue
        if len(docs) >= MAX_PROSE_FILES:
            # THE BOUND IS REPORTED, NOT MERELY APPLIED — `recon.read_dir`'s
            # own posture, for the same reason: a file that stops quietly is
            # a file the reader believes was read.
            unread.append(UnreadFile(
                name=name,
                reason=(f"this run reads at most {MAX_PROSE_FILES} documents "
                        f"as prose and had already reached that before this "
                        f"file"),
            ))
            continue
        if not data:
            unread.append(UnreadFile(name=name, reason="the file arrived empty"))
            continue
        try:
            text = convert(name, data)
        except Exception:  # noqa: BLE001 — one unreadable attachment costs its
            # own claims and never the run, exactly as it costs its own table
            # on the tabular side.
            logger.warning("crucible prose: could not convert %s", name,
                           exc_info=True)
            unread.append(UnreadFile(
                name=name, reason="it could not be opened"))
            continue
        # BELT AND BRACES ON A BRANCH THAT SHOULD BE UNREACHABLE. `convert`
        # returns a placeholder stub only for a suffix it has no converter for,
        # and `PROSE_SUFFIXES` is derived from the converters — so this cannot
        # fire today. It is here because the stub is NON-EMPTY TEXT: if that
        # derivation ever loosens, the failure is not an error but a model call
        # over a sentence saying the file could not be read, and claims
        # extracted from it.
        if is_unparsed_stub(text):
            unread.append(UnreadFile(
                name=name,
                reason=("it is a binary or unrecognised format, so there was "
                        "no text in it to read"),
            ))
            continue
        if len(text.strip()) < MIN_PROSE_CHARS:
            unread.append(UnreadFile(
                name=name,
                reason=(f"it opened, but held under {MIN_PROSE_CHARS} "
                        f"characters of text — too little to read as evidence"),
            ))
            continue
        segments, per_conversation, markers, stated, fallback = segment(
            text, name=name, now=now)
        docs.append(ProseDocument(
            name=name,
            sha256=hashlib.sha256(data).hexdigest(),
            chars=len(text),
            segments=segments,
            per_conversation=per_conversation,
            markers_found=markers,
            stated_count=stated,
            fallback_reason=fallback,
            dated_by_attachment_time=(
                not per_conversation and segments[0].observed_at is None),
        ))
    return tuple(docs), tuple(unread)


def _is_prose_name(name: str) -> bool:
    lowered = str(name or "").lower()
    return any(lowered.endswith(sfx) for sfx in PROSE_SUFFIXES)


# ── The post-approve read: extraction, projection, claims ───────────────────


@dataclass(frozen=True)
class ProseEvidence:
    """Everything one run's attached prose contributed, and what it cost.

    `rows` are `kg_signal`-SHAPED DICTS and are never written anywhere. They
    exist to be handed to the real `claims.project_signals` alongside the rows
    that came out of Postgres, so authority, the strength ceiling, the
    population filter, the account canonicalisation and the figure class are
    all decided by the same code for both.
    """
    rows: tuple[dict, ...] = ()
    #: `signal id -> the extraction item it came from`, so the theme map can
    #: read the extractor's own label and relationship verb off it.
    items_by_id: Mapping[str, dict] = field(default_factory=dict)
    #: One sentence per document, for the plan and the coverage note.
    read: tuple[tuple[str, str], ...] = ()
    #: `(name, reason)` for every attached document that produced nothing.
    unread: tuple[tuple[str, str], ...] = ()
    conversations: int = 0
    #: Segments whose extraction call failed outright. Counted so a run that
    #: read four of ten calls never reports having read a document.
    failed_segments: int = 0

    @property
    def summary(self) -> dict:
        return {
            "documents": len(self.read),
            "conversations": self.conversations,
            "claims": len(self.rows),
            "failed_segments": self.failed_segments,
            "unread": len(self.unread),
        }


def _usable(items: Any, *, enterprise_id: str, doc_name: str) -> list[dict]:
    """Extraction items this projection can actually build a Signal from.

    `drop_malformed_items` removes the non-dict elements; this additionally
    requires the three keys `signals_from_items` subscripts directly
    (`kind`/`content`/`source_type`, all three declared `required` in
    `_EXTRACT_SCHEMA`). The write path can subscript them safely because a
    KeyError there aborts one ingest job; here it would abort a run the reader
    is watching, and the honest cost of a malformed item is that item.
    """
    from app.graph.extractor import drop_malformed_items

    if not isinstance(items, list):
        return []
    kept = drop_malformed_items(
        items, enterprise_id=enterprise_id, doc_name=doc_name)
    out = [i for i in kept
           if str(i.get("kind") or "").strip()
           and str(i.get("content") or "").strip()
           and str(i.get("source_type") or "").strip()]
    if len(out) != len(kept):
        logger.warning(
            "crucible prose: dropped %d extraction item(s) missing a required "
            "field for doc=%s", len(kept) - len(out), doc_name)
    return out


def signal_to_row(signal, *, created_at: datetime) -> dict:
    """A `Signal` as the `kg_signal` row shape the run reads.

    MIRRORS `routes.crucible._signal_page`'s COLUMN LIST, not
    `facade.write_signal`'s row — deliberately. `write_signal` builds what
    Postgres stores; this builds what the run actually SELECTs, so a row here
    and a row from the corpus are the same shape to every reader downstream
    and a column the run never fetches is not invented here either.

    `created_at` is the run clock. It is read by `recon.dating_reliability` to
    decide whether the corpus is dated by its own events or by ingest time —
    so a fallback-dated document, whose `valid_at` IS the run clock, correctly
    counts towards "these dates are the ingest clock" rather than pretending
    to a dating it does not have.
    """
    return {
        "id": signal.id,
        "kind": signal.kind,
        "source_type": signal.source_type,
        "content": signal.content,
        "properties": signal.properties,
        "provenance": signal.provenance,
        "valid_at": signal.valid_at.isoformat() if signal.valid_at else None,
        "created_at": created_at.isoformat(),
        "source_id": signal.source_id,
    }


class _ExtractedSegments:
    """Every segment's raw extraction result, already computed, addressable by
    `(document, segment)`.

    Holds the RAISED EXCEPTION rather than re-raising at collection time, so
    the caller's own `try` around `_usable` is what turns a failure into
    `failed += 1` — exactly where it did when the call was inline.
    """

    def __init__(self) -> None:
        self._by_seg: dict[tuple[int, int], str] = {}
        self._raw: dict[str, Any] = {}
        self._err: dict[str, BaseException] = {}

    def result_for(self, doc: "ProseDocument", seg: "ProseSegment") -> Any:
        key = self._by_seg[(id(doc), seg.index)]
        err = self._err.get(key)
        if err is not None:
            raise err
        return self._raw[key]


def _extract_all_segments(
    docs: Sequence[ProseDocument], *, enterprise_id: str,
) -> _ExtractedSegments:
    """Every segment across every document, extracted — DEDUPLICATED FIRST,
    then run `MAX_PARALLEL_SEGMENTS`-wide.

    DEDUPLICATED BEFORE THE POOL, NOT IN THE CACHE, and that is the whole
    reason this is a pre-pass rather than a bare `map`. `prose_cache` is not
    get-or-compute: two workers holding identical segment text would both
    miss, both call the model, and the last `put` would win — paying twice
    for one answer and, worse, breaking `_extract_segment`'s stated promise
    that "the same bytes read twice inside this process get the first answer
    rather than a second sample", which is the promise that keeps a draw from
    becoming two draws. Grouping identical text up front makes that promise
    hold under concurrency without touching the cache.

    KEYED ON TEXT ALONE, matching the cache's own key exactly
    (`sha256(text)` + prompt version; `artifact_id` reaches only the prompt's
    `doc_name` and never the key). The first occurrence's `artifact_id` is
    the one that makes the call, which is the one that would have made it
    serially — every later duplicate hit the cache.

    A FAILED EXTRACTION FANS OUT TO ITS DUPLICATES, and this is the one
    behavioural difference in the change. Serially, a failure on the first
    occurrence left no cache entry, so a second occurrence of the same text
    got its own attempt; here it does not, and both are counted failed.

    THE DELIBERATE SIDE, for two reasons. It is DETERMINISTIC: identical
    bytes get one outcome, so a run's failed-segment count does not depend on
    how the duplicates happened to be scheduled — the same reproducibility
    rule `cluster.py` states for worker counts. And the behaviour it gives up
    was never a designed retry, only an accidental one: re-attempting is
    precisely the duplicate model call this pre-pass exists to remove. A
    segment that genuinely warrants retrying should get one deliberately, at
    the call site, not as a side effect of the cache having missed.
    """
    out = _ExtractedSegments()
    order: list[str] = []
    first_call: dict[str, tuple[str, str]] = {}

    for doc in docs:
        for seg in doc.segments:
            text = seg.text[:MAX_SEGMENT_CHARS]
            key = hashlib.sha256(text.encode("utf-8")).hexdigest()
            out._by_seg[(id(doc), seg.index)] = key
            if key not in first_call:
                first_call[key] = (doc.artifact_id_for(seg), text)
                order.append(key)

    if not order:
        return out

    with ThreadPoolExecutor(
        max_workers=max(1, min(MAX_PARALLEL_SEGMENTS, len(order))),
        thread_name_prefix="crucible-prose",
    ) as ex:
        # `max_workers` IS the bound — at most that many calls are ever in
        # flight, whatever the submit order — so the whole list goes in at
        # once. The relevance gate submits in explicit waves only because it
        # re-checks a deadline between them; there is no deadline here, and
        # the serial loop this replaces had none either.
        futures = {key: ex.submit(
            _extract_segment, text,
            artifact_id=artifact_id, enterprise_id=enterprise_id,
        ) for key, (artifact_id, text) in first_call.items()}

        for key in order:
            try:
                out._raw[key] = futures[key].result()
            except Exception as exc:  # noqa: BLE001 — re-raised per segment
                out._err[key] = exc

    return out


def extract_documents(
    docs: Sequence[ProseDocument],
    *,
    enterprise_id: str,
    now: Optional[datetime] = None,
    unread: Sequence[Any] = (),
) -> ProseEvidence:
    """Segments -> extraction -> `kg_signal`-shaped rows. TOTAL.

    RUNS AFTER `/approve`, NEVER AT THE GATE. One model call per conversation
    is tens of seconds; the gate returns in about one. `read_prose` did the
    fast half already and the reader approved knowing what it found.

    Never raises: a document that fails costs its own claims and is counted in
    `failed_segments`, because a run that dies on an attachment is strictly
    worse for the reader than a run that reports having read nine calls of ten.
    """
    now = now or datetime.now(timezone.utc)
    rows: list[dict] = []
    items_by_id: dict[str, dict] = {}
    read: list[tuple[str, str]] = []
    conversations = 0
    failed = 0

    extracted = _extract_all_segments(docs, enterprise_id=enterprise_id)

    # THE SAME SERIAL LOOP AS BEFORE, over a list that is already ordered.
    #
    # ORDERING IS LOAD-BEARING, not cosmetic. Prose rows are appended after
    # the id-ordered graph rows with no sort, and `_cluster` preserves input
    # order, so within-group position decides four separate things
    # downstream: which claim `example`/`strongest` picks (`max` returns the
    # FIRST maximal element, and prose claims are ceilinged at `reported`, so
    # ties are the norm rather than the exception), which six ids survive
    # `claim_ids[:MAX_CLAIMS_PER_FINDING]` and therefore what the
    # recommendation model is shown, `_label`'s first-seen tie-break, and
    # `_accounts` first-seen order. `cluster.py`'s reproducibility invariant
    # names the hazard exactly: nothing may depend on how many workers
    # happened to run.
    #
    # So the fan-out above collects results into a map and this loop — the
    # counting, the per-document `read` entry, the row order — is untouched
    # and still runs in one thread, in document-then-segment order.
    for doc in docs:
        produced = 0
        for seg in doc.segments:
            artifact_id = doc.artifact_id_for(seg)
            try:
                items = _usable(
                    extracted.result_for(doc, seg),
                    enterprise_id=enterprise_id, doc_name=artifact_id)
            except Exception:  # noqa: BLE001 — see the docstring
                logger.warning(
                    "crucible prose: extraction failed for %s", artifact_id,
                    exc_info=True)
                failed += 1
                continue
            conversations += 1
            if not items:
                continue
            built = _signals_for(
                items, artifact_id=artifact_id, enterprise_id=enterprise_id,
                observed_at=seg.observed_at or now,
            )
            for item, sig in zip(items, built):
                items_by_id[sig.id] = item
                rows.append(signal_to_row(sig, created_at=now))
            produced += len(built)
        if produced or conversations:
            read.append((doc.name, doc.how_it_was_read))

    return ProseEvidence(
        rows=tuple(rows), items_by_id=items_by_id, read=tuple(read),
        unread=tuple((str(getattr(u, "name", "")), str(getattr(u, "reason", "")))
                     for u in unread),
        conversations=conversations, failed_segments=failed,
    )


def _signals_for(items, *, artifact_id: str, enterprise_id: str,
                 observed_at: datetime):
    """The extractor's own pure projection, run over one conversation.

    NOTHING IS PINNED. `force_source_type` stays None so the model's own
    witness classification stands — the same classification the graph gets for
    the same sentence — and the transport is carried in `provenance` instead,
    where `claims.project_signal` reads it to apply the strength ceiling.

    `vectors` is a list of `None`: the embedding is a network call this path
    does not make, and it does not need one. A prose claim is grouped by the
    extractor's own theme label (`theme_map_for`), not by cosine distance.
    """
    from app.graph.extractor import PROMPT_VERSION, signals_from_items

    return signals_from_items(
        enterprise_id, list(items), [None] * len(items),
        doc_name=artifact_id,
        origin=UPLOAD_ORIGIN,
        source_call_id=None,
        source_prov={},
        provenance_extra={"channel": ATTACHMENT_CHANNEL},
        resolved_skill_id=None,
        triage_category=None,
        prompt_version=PROMPT_VERSION,
        force_source_type=None,
        source_type_default=None,
        valid_at=observed_at,
        id_namespace=artifact_namespace(artifact_id),
    )


def _extract_segment(text: str, *, artifact_id: str,
                     enterprise_id: str) -> Any:
    """One extraction call, cached on `(sha256(text), PROMPT_VERSION)`.

    A MODEL CALL IS A DRAW, NOT A LOOKUP — the discipline
    `figure_class.classify_figures` states for the same reason — so the same
    bytes read twice inside this process get the first answer rather than a
    second sample.

    IN-PROCESS ONLY, AND DELIBERATELY. `prose_cache` holds the whole argument
    for why this is not a durable table; the short version is that within-run
    recovery is already solved by `routes.crucible._remember_prose` writing the
    cited rows onto the run, so a table would buy only ACROSS-run
    reproducibility — a guarantee the relevance gate does not make either, and
    not one to give attached prose alone through a cache.

    So the honest statement of what this does: a second run over the same
    document, in a fresh worker, re-extracts and may produce a slightly
    different claim set. That is the same variability the rest of the engine
    has.

    Keyed on the SEGMENT's text rather than the file's bytes, which is the
    stronger key either way: a reader who attaches the same ten-call pack with
    an eleventh call appended keeps the ten answers instead of paying for all
    eleven again, and the prompt version is in the key so a prompt change
    correctly invalidates everything.
    """
    from app.crucible import prose_cache
    from app.graph.extractor import (
        _EXTRACT_SCHEMA, PROMPT_VERSION, extract_prompt,
    )
    from app.graph.gateway import llm_call

    key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    cached = prose_cache.get(
        enterprise_id=enterprise_id, content_sha256=key,
        prompt_version=PROMPT_VERSION)
    if cached is not None:
        return cached["signals"]

    system, user = extract_prompt(doc_name=artifact_id, text=text)
    result = llm_call(
        enterprise_id=enterprise_id,
        agent="crucible",
        purpose="extract_run_scoped_prose",
        prompt_version=PROMPT_VERSION,
        system=system,
        input=user,
        json_schema=_EXTRACT_SCHEMA,
        # A DRAW WE INTEND TO KEEP. Zero does not make the API deterministic,
        # but it removes the sampling variance we can remove; the cache above
        # removes the rest for every repeat of the same bytes.
        temperature=0,
    )
    output = result.output if isinstance(result.output, dict) else {}
    items = output.get("signals", [])
    prose_cache.put(
        enterprise_id=enterprise_id, content_sha256=key,
        prompt_version=PROMPT_VERSION,
        # STORED AS THE MODEL RETURNED IT, with a normalised `signals` key so
        # a response missing it caches as an empty answer rather than as a
        # miss that costs another call on every future run over these bytes.
        output={**output, "signals": items if isinstance(items, list) else []})
    return items


# ── Grouping: the graph's themes first, for prose too ───────────────────────


def theme_map_for(
    items_by_id: Mapping[str, dict],
    graph_theme_map: Mapping[str, tuple],
) -> dict[str, tuple[str, str, Optional[str]]]:
    """Theme-map entries for prose claims, folded ONTO the graph's own themes.

    WITHOUT THIS THE FEATURE DELIVERS NOTHING, and the failure is silent. A
    prose claim's id is not in `kg_signal`, so `load_theme_map` cannot know it
    and `_load_embeddings` returns no vector for it — which lands every prose
    claim in `assign_clusters` with nothing to cluster on, one pseudo-group
    each, and the anecdote rule then drops all of them. The run would report
    having read the document and produce not one finding from it.

    So the extractor's OWN theme label — which it already wrote on every item,
    and which is what `_write_items` would have find-or-created an entity from
    on the ingest path — is matched against the theme entities the graph
    already holds, using the repo's own `normalize_label`/`same_topic` rules
    rather than a second similarity notion. A prose topic the graph has never
    seen gets a synthetic id from `_PROSE_THEME_NS`, so it can group with other
    prose claims about the same topic and can never be mistaken for a real
    `kg_entity`.

    DETERMINISTIC. Graph candidates are considered in sorted order and the
    first match wins, so the same corpus groups the same way twice — the
    property the whole engine's reproducibility claim rests on.
    """
    from app.crucible.kg_themes import (
        _SIGNAL_THEME_RELATIONS, canonicalize_themes, content_tokens,
        normalize_label, same_topic,
    )

    labels_by_id: dict[str, str] = {}
    for signal_id, item in items_by_id.items():
        label = str((item or {}).get("theme") or "").strip()
        if label:
            labels_by_id[signal_id] = label
    if not labels_by_id:
        return {}

    # 1. Fold the PROSE labels among themselves first, through the same
    #    function `load_theme_map` folds the graph's with. Two calls saying
    #    "billing" and "billing & pricing" are one topic here for exactly the
    #    reasons they are one topic there.
    prose_ids = {lab: str(uuid.uuid5(_PROSE_THEME_NS, normalize_label(lab)))
                 for lab in set(labels_by_id.values())}
    prose_labels = {eid: lab for lab, eid in prose_ids.items()}
    counts: dict[str, int] = {}
    for lab in labels_by_id.values():
        counts[prose_ids[lab]] = counts.get(prose_ids[lab], 0) + 1
    if len(prose_labels) > 1:
        folded = canonicalize_themes(prose_labels, counts) or {}
        prose_ids = {lab: folded.get(eid, eid) for lab, eid in prose_ids.items()}

    # 2. Attach each surviving prose topic to a GRAPH theme naming the same
    #    subject, if there is one.
    graph_labels: dict[str, str] = {}
    #: HOW MANY SIGNALS THE GRAPH ITSELF BOUND TO EACH THEME, derived from the
    #: map rather than fetched: `load_theme_map` returns one entry per signal,
    #: so counting entity ids over its values reproduces EXACTLY the `counts`
    #: it builds internally to feed `canonicalize_themes`. No second query, no
    #: second column, and — the part that matters — no second definition of
    #: "how much the graph leaned on this topic" that could drift from the one
    #: the fold already uses. The map arrives post-fold, so the count is the
    #: representative's total across its shards, which is the right quantity.
    graph_counts: dict[str, int] = {}
    for entry in graph_theme_map.values():
        if len(entry) >= 2 and entry[0] and entry[1]:
            graph_labels[str(entry[0])] = str(entry[1])
            graph_counts[str(entry[0])] = graph_counts.get(str(entry[0]), 0) + 1
    attach: dict[str, tuple[str, str]] = {}
    if graph_labels:
        # MOST-CITED FIRST, TIES ON THE ENTITY ID — `canonicalize_themes`'
        # `rank()`, verbatim, and adopted for its reasons rather than by
        # analogy. Most-cited is the shard the graph actually leaned on; the
        # tie-break is what makes the choice a function of the input rather
        # than of dict order or of which page a batch landed on.
        #
        # IT REPLACES ALPHABETICAL ORDER, WHICH HAD A SYSTEMATIC BIAS AND WAS
        # MEASURED. On a real tenant's 2,129 theme labels, an extractor's
        # "pricing" had 52 qualifying candidates and "tabletop exercise
        # scheduling" had 27 — so the choice was doing real work on nearly
        # every attachment rather than breaking the occasional tie — and
        # alphabetical order skews hard toward `A`. On a graph carrying
        # meeting-assistant themes, a pricing discussion in an attached
        # document attached to ANOTHER PRODUCT'S pricing theme. That is not a
        # near-miss; it is the document joining the wrong conversation, and it
        # is invisible in the output because the claim still lands in a cluster
        # and still gets cited.
        #
        # ── WHAT THIS IS STILL NOT ────────────────────────────────────────
        # BETTER, NOT CORRECT, and a reader of this code should know which.
        # "pricing" still has 52 qualifying candidates and this picks one of
        # them by popularity. That is a heuristic attaching a document to a
        # conversation: when it attaches wrongly the claim is not dropped and
        # not flagged — it is counted, cited, and filed under the wrong theme.
        #
        # What would settle it is the join the graph uses on its own ingest
        # path: embed the label and take the nearest theme entity
        # (`facade.find_candidates`). That is unavailable here by construction,
        # not by omission — it is a pgvector similarity search issued as an
        # `rpc`, and `rpc` is one of the verbs the run-scoped no-write guard
        # forbids. Lifting that would mean letting this path issue graph
        # queries, which is a bigger decision than a tie-break and belongs to
        # whoever owns the guard.
        def _rank(item: tuple[str, str]) -> tuple[int, str]:
            return (-graph_counts.get(item[0], 0), item[0])

        candidates = sorted(graph_labels.items(), key=_rank)
        by_norm = {}
        for eid, lab in candidates:
            # `setdefault` over a most-cited-first list, so the exact-match
            # tier picks the same way the overlap tier below does. Two entities
            # whose labels normalise identically is the ordinary case the fold
            # exists for, and picking the thin one there is the same defect one
            # tier up.
            by_norm.setdefault(normalize_label(lab), (eid, lab))
        for prose_eid in sorted(set(prose_ids.values())):
            label = prose_labels.get(prose_eid) or ""
            if not label:
                continue
            exact = by_norm.get(normalize_label(label))
            if exact:
                attach[prose_eid] = exact
                continue
            tokens = content_tokens(label)
            for eid, lab in candidates:
                if same_topic(tokens, content_tokens(lab)):
                    attach[prose_eid] = (eid, lab)
                    break

    out: dict[str, tuple[str, str, Optional[str]]] = {}
    for signal_id, label in labels_by_id.items():
        prose_eid = prose_ids[label]
        entity_id, shown = attach.get(
            prose_eid, (prose_eid, prose_labels.get(prose_eid, label)))
        verb = (items_by_id.get(signal_id) or {}).get("relationship")
        out[signal_id] = (
            entity_id, shown,
            verb if verb in _SIGNAL_THEME_RELATIONS else None,
        )
    return out


# ── Surviving a re-enrichment ───────────────────────────────────────────────

#: Where a run keeps its prose rows, inside `crucible_runs.prioritisation`.
#: A RUN TABLE, NOT THE GRAPH: this is the run's own record of what it read,
#: scoped to and deleted with the run, and it is never queried by anything
#: except that run's own recovery path.
META_KEY = "prose_claims"

#: Rows persisted for recovery. Bounded because `prioritisation` is one jsonb
#: blob read on every poll of a run, and an unbounded transcript in it would
#: make every status request pay for evidence only the sweep ever reads.
MAX_PERSISTED_ROWS = 300

#: Where a run keeps the ids it wanted to persist but could not fit under
#: `MAX_PERSISTED_ROWS`. SEPARATE FROM `META_KEY`, deliberately: a reader (or
#: a future resolver) checking whether a cited id is recoverable has to be
#: able to tell "dropped for space" from "never existed" — folding the two
#: into one silently-shorter list would rebuild exactly the bug this module
#: exists to close, just behind a cap instead of a missing call site.
TRUNCATED_KEY = "prose_claims_truncated"


def rows_for_recovery(
    rows: Sequence[Mapping[str, Any]], referenced: set[str],
) -> tuple[list[dict], list[str]]:
    """The prose rows a stalled-enrichment sweep will need, and no others —
    plus the ids of any that were cited but did not fit.

    THE HOLE THIS CLOSES. `routes.crucible._reenrich_stalled_run` rebuilds
    claims from `kg_signal` BY ID — and a prose claim's id is not in
    `kg_signal`, by design. So a run whose enrichment stalls and is swept would
    silently lose every prose-backed claim, and `recommend.py`'s claim-id gate
    would then drop the citations naming them: findings that still look
    prose-backed, carrying recommendations the engine can no longer ground.
    Persisting the rows here is what lets the sweep reconstitute exactly the
    claims the first pass had.

    Filtered to the ids anything the run PERSISTS actually references —
    findings and the ledger both — because that is precisely the set a
    resolver would ever ask for; carrying the rest would grow the blob
    without changing any answer. `embedding` is never carried: it is 1,536
    floats the recovery path does not read.

    The SECOND return value is the referenced ids that could not fit under
    the cap — never silently dropped. A caller that only reads the first
    value gets the exact prior behaviour; one that reads both can tell a
    cited-but-truncated id apart from one that never existed.
    """
    kept = [dict(r) for r in rows if str(r.get("id") or "") in referenced]
    truncated: list[str] = []
    if len(kept) > MAX_PERSISTED_ROWS:
        logger.warning(
            "crucible prose: %d prose rows referenced by findings/ledger, "
            "keeping %d for recovery — a sweep of this run would ground "
            "fewer citations than the first pass did",
            len(kept), MAX_PERSISTED_ROWS)
        truncated = sorted(
            str(r.get("id")) for r in kept[MAX_PERSISTED_ROWS:])
        kept = kept[:MAX_PERSISTED_ROWS]
    return kept, truncated


def rows_from_meta(meta: Mapping[str, Any]) -> list[dict]:
    """The persisted prose rows on a run, defensively.

    A blob written by an older build has no such key and reads back as no
    prose, which is exactly what a run with no attachment had.
    """
    stored = (meta or {}).get(META_KEY)
    if not isinstance(stored, list):
        return []
    return [r for r in stored if isinstance(r, dict) and r.get("id")]


def truncated_ids_from_meta(meta: Mapping[str, Any]) -> list[str]:
    """The ids a run wanted to persist but could not fit under the cap.

    Distinguishes "cited and once known, but dropped for space" from "never
    existed" for any reader checking whether an id ought to be recoverable.
    """
    stored = (meta or {}).get(TRUNCATED_KEY)
    if not isinstance(stored, list):
        return []
    return [str(i) for i in stored if i]
