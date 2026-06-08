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
from app.kg_ingest.transcription import transcribe_audio
from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

# Mirrors the runner's fireflies hint — meeting audio is voice-of-customer /
# communication, the same source class the Fireflies puller feeds.
_SOURCE_HINT = ("customer_voice / communication (meeting transcript transcribed "
                "from raw audio)")


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
    # idempotent end-to-end (the extractor is content-keyed below this).
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    doc_name = f"{source}-audio-{content_hash}"

    # Wrap as the generic envelope so composition metadata (duration, filename,
    # detected language) rides through render() into the extraction text — the
    # same shape every other connector emits.
    record = RawRecord(
        provider=source,
        kind="meeting_transcript",
        external_id=content_hash,
        title=filename,
        text=text,
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
    return {
        "signals": extracted["signals"],
        "themes": extracted["themes"],
        "skipped": extracted["skipped"],
        "duration": duration,
    }
