"""Second-pass read for uploads the converter couldn't parse.

The upload routes never block on a model. `app.ingest.convert` runs inline and
stores a placeholder for anything it can't parse; this module comes along
afterwards, finds those placeholders, and asks the model to read the original
bytes (see app.llm_file_read). Files that become readable are rewritten in
place, so the knowledge graph picks them up on the next sync with no
re-upload — the deferred half of the "file it first, read it second" pattern
app.llm_context already uses for context import.

Two storage paths, both handled:

  * document sources (the `uploads` connector) — text lives in
    `document_source_file.extracted_text`, the original in `raw_b64`;
  * the dataset corpus (connector-category upload strips) — the converted
    markdown is a `.md` in the dataset directory, the original in `raw/`.

Fully isolated and idempotent: every file is independent, failures are logged
and skipped, and a file that still can't be read keeps its placeholder and
stays eligible next time. Bounded per run so one pathological upload batch
can't spend unbounded model budget.
"""
from __future__ import annotations

import logging
import threading

from app.ingest import is_unparsed_stub, md_filename
from app.llm_file_read import needs_model_read, read_file

logger = logging.getLogger(__name__)

#: Files read per run, per path. A cost ceiling, not a coverage policy — the
#: pass is idempotent, so a bigger batch is simply finished by the next run
#: (each upload kicks one off).
MAX_FILES_PER_RUN = 25


def enrich_document_sources(company_id: str) -> dict:
    """Model-read the unparsed files in this company's document sources."""
    from app.document_sources import (
        get_file_raw_bytes,
        list_document_sources,
        list_source_files,
        set_file_extracted_text,
    )

    totals = {"read": 0, "failed": 0, "skipped": 0}
    for source in list_document_sources(company_id):
        for f in list_source_files(company_id, source.id):
            if totals["read"] + totals["failed"] >= MAX_FILES_PER_RUN:
                return totals
            if not is_unparsed_stub(f.extracted_text) and f.extracted_text.strip():
                # Already readable — the PDF-scan case is caught below via
                # needs_model_read, which sees the raw bytes.
                if not f.filename.lower().endswith(".pdf"):
                    totals["skipped"] += 1
                    continue
            data = get_file_raw_bytes(company_id, f.id)
            if not data or not needs_model_read(f.filename, data, f.extracted_text):
                totals["skipped"] += 1
                continue
            text = read_file(company_id, f.filename, data)
            if not text:
                totals["failed"] += 1
                continue
            if set_file_extracted_text(company_id, f.id, text):
                totals["read"] += 1
                logger.info("file-read: recovered %s (%d chars)", f.filename, len(text))
            else:
                totals["failed"] += 1
    return totals


def enrich_dataset_corpus(company_id: str, slug: str) -> dict:
    """Model-read the unparsed files in a dataset's corpus.

    The corpus holds converted `.md` files; the originals live in `raw/`. A
    stub `.md` is rewritten with the model's transcription, which the corpus
    seed then extracts normally — and because the seed never RECORDED the stub
    (see synthesis_brief), the recovered file is picked up as new.
    """
    from app.datasets import dataset_path, raw_path

    totals = {"read": 0, "failed": 0, "skipped": 0}
    try:
        base = dataset_path(slug)
        raw_dir = raw_path(slug)
        if not base.exists() or not raw_dir.exists():
            return totals
        # Index raw originals by their converted name so a stub .md can find
        # the bytes it came from (collision-suffixed names included).
        raw_by_md: dict[str, object] = {}
        for original in raw_dir.iterdir():
            if original.is_file():
                raw_by_md.setdefault(md_filename(original.name), original)

        for md in sorted(base.glob("*.md")):
            if md.name.startswith("_"):
                continue
            if totals["read"] + totals["failed"] >= MAX_FILES_PER_RUN:
                break
            try:
                current = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                totals["failed"] += 1
                continue
            original = raw_by_md.get(md.name)
            if original is None:
                totals["skipped"] += 1
                continue
            try:
                data = original.read_bytes()  # type: ignore[union-attr]
            except OSError:
                totals["failed"] += 1
                continue
            if not needs_model_read(original.name, data, current):  # type: ignore[union-attr]
                totals["skipped"] += 1
                continue
            text = read_file(company_id, original.name, data)  # type: ignore[union-attr]
            if not text:
                totals["failed"] += 1
                continue
            try:
                md.write_text(text, encoding="utf-8")
            except OSError:
                totals["failed"] += 1
                continue
            totals["read"] += 1
            logger.info("file-read: recovered %s (%d chars)", md.name, len(text))
    except Exception:  # noqa: BLE001 — fully isolated
        logger.exception("file-read: dataset enrichment failed for %s", slug)
    return totals


def _run(company_id: str, slug: str | None) -> None:
    """Blocking body — runs inside the daemon thread. Never raises."""
    try:
        doc_totals = enrich_document_sources(company_id)
        corpus_totals = (
            enrich_dataset_corpus(company_id, slug) if slug else
            {"read": 0, "failed": 0, "skipped": 0}
        )
        read = doc_totals["read"] + corpus_totals["read"]
        if read:
            # Newly-readable text only reaches the KG on the next sync/seed,
            # so kick one now rather than waiting for the scheduler.
            from app.kg_ingest.auto_sync import kickoff_corpus_seed, kickoff_sync

            kickoff_sync(company_id, "uploads")
            if slug:
                kickoff_corpus_seed(company_id, slug)
        logger.info(
            "file-read done: %s sources=%s corpus=%s", company_id, doc_totals,
            corpus_totals,
        )
    except Exception:  # noqa: BLE001 — fully isolated
        logger.exception("file-read: enrichment failed for %s", company_id)


def kickoff_upload_enrichment(company_id: str, slug: str | None = None) -> bool:
    """Fire-and-forget: model-read this company's unparsed uploads.

    Called right after an upload so a file the converter couldn't parse is
    readable within a minute, without making the user wait on a model call.
    Never blocks; never raises into the request.
    """
    try:
        threading.Thread(
            target=_run, args=(company_id, slug),
            name="file-read", daemon=True,
        ).start()
        return True
    except Exception:  # noqa: BLE001 — a spawn failure must not break the upload
        logger.exception("file-read: could not start enrichment for %s", company_id)
        return False
