"""Uploads puller — the user's own documents → RawRecords.

The one puller with no outbound HTTP: the "credential" kg_ingest hands us is
the owning company id (see app/connectors/uploads.py), and the data lives in
our own `document_source` / `document_source_file` tables. Everything after
this point is identical to Fireflies or Jira — the runner batches the records
and routes them through the generic extractor into the KG.

Every record carries the USER-SUPPLIED source name and description in its
title/properties. That context is the whole point of the named-source flow: it
tells the extractor that "NPS_verbatims_Q3.csv" is "Q3 NPS survey — free-text
answers from churned enterprise accounts", which it could never infer from the
filename alone.

Long documents are chunked so one 200k-char PDF becomes several records rather
than a single record the batcher can't split — same reason the other pullers
cap their per-record text. `external_id` is stable per (file, chunk), so the
content-keyed signal idempotency upstream dedups re-syncs exactly as it does
for every other provider.
"""
from __future__ import annotations

import logging
from typing import Iterator

from app.document_sources import list_document_sources, list_source_files
from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

#: Chars per emitted record. Sized just under the runner's 6000-char batch
#: budget so a chunk always fits in one extraction batch.
_CHUNK_CHARS = 4000

#: Pilot-scale ceiling per file, mirroring the other pullers' page caps.
_MAX_CHUNKS_PER_FILE = 25


def _chunks(text: str) -> list[str]:
    """Split on the chunk budget. Returns [] for whitespace-only text."""
    body = (text or "").strip()
    if not body:
        return []
    return [
        body[i:i + _CHUNK_CHARS]
        for i in range(0, len(body), _CHUNK_CHARS)
    ][:_MAX_CHUNKS_PER_FILE]


def pull(company_id: str) -> Iterator[RawRecord]:
    """Yield one RawRecord per chunk of every file in every document source.

    Error-isolated per source (a source whose files can't be read is logged and
    skipped) so one bad row never kills the sync — same philosophy as the
    HubSpot / Sprinklr sub-pullers.
    """
    for source in list_document_sources(company_id):
        try:
            files = list_source_files(company_id, source.id)
        except Exception:  # noqa: BLE001 — error-isolation per source
            logger.exception("uploads: could not read files for source %s", source.id)
            continue
        for f in files:
            parts = _chunks(f.extracted_text)
            if not parts:
                # Binary/unparseable upload: app.ingest.convert stored a stub,
                # or extraction degraded to empty. Nothing to extract from.
                continue
            for i, chunk in enumerate(parts):
                suffix = f" (part {i + 1}/{len(parts)})" if len(parts) > 1 else ""
                yield RawRecord(
                    provider="uploads",
                    kind="document",
                    external_id=f"{f.id}:{i}",
                    title=f"{source.name} — {f.filename}{suffix}",
                    text=chunk,
                    properties={
                        # The user's own words about what this corpus is — the
                        # extractor reads these alongside the text.
                        "source_name": source.name,
                        "source_description": source.description or None,
                        "filename": f.filename,
                        "content_type": f.content_type,
                    },
                    timestamp=f.uploaded_at,
                )
