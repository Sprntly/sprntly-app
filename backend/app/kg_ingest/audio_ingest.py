"""Raw-audio ingestion (task #25): an audio file → transcript → KG.

This is the raw-recording path that complements the Fireflies puller. The
puller pulls already-distilled summaries + action items via GraphQL; here we
take an actual audio file (e.g. a Fireflies meeting recording the user uploads)
and run it through Whisper first, then route the transcript through the same
generic extractor every other connector uses.

Idempotency: the transcript is content-hashed and folded into the doc name, so
re-ingesting the identical recording produces the identical signal ids
(extract_document is itself content-keyed at the signal level) → no duplicates.

Data minimization (§6): we deliberately do NOT persist the full verbatim
transcript anywhere in the KG — only the distilled signals the extractor
produces. The transcript exists only transiently in memory for this call. The
duration/filename we keep are composition metadata, not content.
"""
from __future__ import annotations

import hashlib
import logging

from app.graph.extractor import extract_document
from app.graph.facade import GraphFacade
from app.kg_ingest.runner import _BATCH_CHAR_BUDGET
from app.kg_ingest.transcription import transcribe_audio
from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

# Mirrors the runner's fireflies hint — meeting audio is voice-of-customer /
# communication, the same source class the Fireflies puller feeds.
_SOURCE_HINT = ("customer_voice / communication (meeting transcript transcribed "
                "from raw audio)")

# Cap each LLM extraction call to the same char budget the runner batches
# pullers to. A long meeting recording → a transcript far past this; without a
# cap the whole thing rides into a single extract_document call (token blowup /
# cost / timeout). We split on this budget and extract each chunk.
_TRANSCRIPT_CHAR_BUDGET = _BATCH_CHAR_BUDGET


def _chunk_transcript(text: str, budget: int = _TRANSCRIPT_CHAR_BUDGET) -> list[str]:
    """Split a transcript into <=budget-char chunks, preferring to break on
    whitespace near the boundary so we don't slice mid-word/mid-sentence. Always
    returns at least one chunk (the whole text when it's within budget)."""
    if len(text) <= budget:
        return [text]
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + budget, n)
        if end < n:
            # Back up to the last whitespace in the window so we cut on a word
            # boundary; fall back to the hard cut if there's no whitespace.
            split = text.rfind(" ", start, end)
            if split <= start:
                split = text.rfind("\n", start, end)
            if split > start:
                end = split
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end if end > start else end + 1
    return chunks or [text[:budget]]


def ingest_audio(
    facade: GraphFacade,
    enterprise_id: str,
    *,
    audio_bytes: bytes,
    filename: str,
    source: str = "fireflies",
) -> dict:
    """Transcribe an audio file and route the transcript into the KG.

    Returns {"signals", "themes", "skipped", "duration"}. `duration` is the
    audio length in seconds when Whisper reports it (None otherwise).
    """
    result = transcribe_audio(audio_bytes, filename)
    text = result.get("text") or ""
    duration = result.get("duration")

    if not text.strip():
        logger.info("audio %r transcribed to empty text — nothing to extract", filename)
        return {"signals": 0, "themes": 0, "skipped": 0, "duration": duration}

    # Content hash drives a stable doc name so re-ingesting the same recording is
    # idempotent end-to-end (the extractor is content-keyed below this). The hash
    # is over the FULL transcript so it stays stable regardless of how the text
    # chunks below — re-ingesting the identical recording reuses the same names.
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    # Cap each extraction call: split the transcript into char-budgeted chunks
    # (a long meeting blows past the budget) and extract each chunk separately,
    # so no single LLM call gets an unbounded transcript. Signal idempotency is
    # content-keyed in the extractor, so chunking can't duplicate.
    chunks = _chunk_transcript(text)
    if len(chunks) > 1:
        logger.info(
            "audio %r transcript is %d chars — splitting into %d chunks "
            "(<=%d chars each) before extraction",
            filename, len(text), len(chunks), _TRANSCRIPT_CHAR_BUDGET,
        )

    totals = {"signals": 0, "themes": 0, "skipped": 0}
    for i, chunk in enumerate(chunks):
        # Stable per-chunk doc name (single-chunk recordings keep the original
        # name; multi-chunk get a deterministic suffix).
        doc_name = (f"{source}-audio-{content_hash}" if len(chunks) == 1
                    else f"{source}-audio-{content_hash}-{i}")

        # Wrap as the generic envelope so composition metadata (duration,
        # filename, detected language) rides through render() into the
        # extraction text — the same shape every other connector emits.
        record = RawRecord(
            provider=source,
            kind="meeting_transcript",
            external_id=(content_hash if len(chunks) == 1 else f"{content_hash}-{i}"),
            title=filename,
            text=chunk,
            properties={
                "duration": duration,
                "filename": filename,
                "language": result.get("language"),
            },
        )

        extracted = extract_document(
            facade,
            enterprise_id,
            doc_name=doc_name,
            text=record.render(),
            agent=f"ingest:{source}:audio",
            source_hint=_SOURCE_HINT,
        )
        for k in totals:
            totals[k] += extracted[k]

    return {**totals, "duration": duration}
