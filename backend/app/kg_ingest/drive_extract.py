"""Google Drive → KG extraction — picked-file docs into connector-origin signals.

Drive has no token-based puller (its records come from the connection's
picked-file config, not a bare API token), so it bypasses the PULLERS
registry: ``google_drive_sync.sync_google_drive`` downloads + converts each
changed file and hands the markdown here as ``DriveDoc``s. Each file is
extracted as its own document (chunked by char budget) with
``origin="connector"`` and a Drive-specific source hint, and gets a
``kg_source`` row (``source_type="google_drive"``) recording file-level
provenance (Drive file id, modifiedTime, link).

Retry-safe by design: a file's ``kg_file_mtime`` entry on the connection
config is advanced ONLY after every chunk of that file extracted successfully,
so a crashed thread (deploy restart) or a failed batch is re-attempted on the
next scheduled/manual sync. Extraction itself is content-keyed idempotent, so
retries can't duplicate signals.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass

from app import db
from app.graph.extractor import extract_document
from app.graph.facade import GraphFacade
from app.graph.types import Source

logger = logging.getLogger(__name__)

GOOGLE_DRIVE_PROVIDER = "google_drive"

_NS = uuid.UUID("c0ffee00-0000-4000-8000-00000000d21e")

# Matches the runner's per-extraction char budget — one extract_document call
# per ~6k chars keeps the LLM focused and the output schema within caps.
_CHUNK_CHARS = 6000
# Hard per-file cap: a 20MB PDF can convert to megabytes of markdown; beyond
# this the tail is dropped (logged) rather than burning unbounded LLM calls.
_MAX_KG_CHARS = 60_000

_DRIVE_SOURCE_HINT = (
    "documents (Google Drive product docs — PRDs, specs, research notes, "
    "meeting docs, strategy/plans: extract customer facts, metrics, "
    "decisions, risks, and feature requests)"
)


@dataclass
class DriveDoc:
    """One changed Drive file, converted to markdown, ready for extraction."""
    file_id: str
    name: str
    modified: str
    text: str
    mime: str = ""
    link: str = ""


def _chunks(text: str) -> list[str]:
    """Split on line boundaries into ~_CHUNK_CHARS pieces (a single overlong
    line becomes its own chunk rather than being dropped)."""
    out: list[str] = []
    buf: list[str] = []
    size = 0
    for line in text.splitlines(keepends=True):
        if buf and size + len(line) > _CHUNK_CHARS:
            out.append("".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line)
    if buf:
        out.append("".join(buf))
    return out


def extract_drive_docs(
    facade: GraphFacade, company_id: str, docs: list[DriveDoc]
) -> dict:
    """Extract each DriveDoc into the KG. Error-isolated per file: one bad
    file is logged + reported, the rest proceed. Returns totals plus
    ``ok`` ({file_id: modifiedTime} for fully-extracted files) and ``errors``."""
    totals = {"files": 0, "signals": 0, "themes": 0, "skipped": 0}
    ok: dict[str, str] = {}
    errors: list[str] = []
    for doc in docs:
        try:
            text = doc.text[:_MAX_KG_CHARS]
            if len(doc.text) > _MAX_KG_CHARS:
                logger.info(
                    "drive-extract: %r truncated %d -> %d chars for company %s",
                    doc.name, len(doc.text), _MAX_KG_CHARS, company_id,
                )
            parts = _chunks(text)
            for i, part in enumerate(parts):
                doc_name = (
                    doc.name if len(parts) == 1
                    else f"{doc.name} (part {i + 1}/{len(parts)})"
                )
                r = extract_document(
                    facade, company_id,
                    doc_name=doc_name, text=part,
                    agent="ingest:google_drive",
                    source_hint=_DRIVE_SOURCE_HINT,
                    origin="connector",
                )
                for k in ("signals", "themes", "skipped"):
                    totals[k] += r[k]
            # File-level provenance in the source registry — upserted on a
            # stable per-file id, so re-extracting a new version updates the
            # same row instead of accumulating one row per edit.
            facade.create_source(company_id, Source(
                id=str(uuid.uuid5(_NS, f"gdrive-file|{company_id}|{doc.file_id}")),
                enterprise_id=company_id,
                source_type=GOOGLE_DRIVE_PROVIDER,
                label=doc.name[:200],
                config={
                    "file_id": doc.file_id,
                    "modified": doc.modified,
                    "mime": doc.mime,
                    "link": doc.link,
                },
            ))
            ok[doc.file_id] = doc.modified
            totals["files"] += 1
        except Exception as e:  # noqa: BLE001 — error-isolation per file
            logger.exception(
                "drive-extract: failed for %r (company %s)", doc.name, company_id
            )
            errors.append(f"{doc.name}: {e}")
    return {**totals, "ok": ok, "errors": errors}


def _record_kg_result(company_id: str, ok: dict[str, str], errors: list[str]) -> None:
    """Advance kg_file_mtime for fully-extracted files; stamp an error only
    when extraction failed (never clobber the corpus sync's own stamp)."""
    try:
        row = db.get_connection(company_id, GOOGLE_DRIVE_PROVIDER)
        if not row:
            return
        if ok:
            try:
                config = json.loads(row.get("config_json") or "{}")
            except (TypeError, ValueError):
                config = {}
            mtimes = dict(config.get("kg_file_mtime") or {})
            mtimes.update(ok)
            config["kg_file_mtime"] = mtimes
            db.patch_connection_config(company_id, GOOGLE_DRIVE_PROVIDER, config)
        if errors:
            db.update_connection_sync(
                company_id, GOOGLE_DRIVE_PROVIDER,
                last_sync_error=f"KG extraction: {len(errors)} file(s) failed"[:500],
            )
    except Exception:  # noqa: BLE001 — bookkeeping must never raise
        logger.exception(
            "drive-extract: could not record result for %s", company_id
        )


def run_drive_extract(company_id: str, docs: list[DriveDoc]) -> dict:
    """Blocking extract + bookkeeping (the inline path used by the brief's
    first-time seed)."""
    facade = GraphFacade()
    result = extract_drive_docs(facade, company_id, docs)
    _record_kg_result(company_id, result["ok"], result["errors"])
    return result


# Per-company locks so overlapping kickoffs (manual sync racing the scheduled
# refresh) serialize instead of extracting the same files twice in parallel.
_extract_locks: dict[str, threading.Lock] = {}
_extract_locks_guard = threading.Lock()


def _extract_lock(company_id: str) -> threading.Lock:
    with _extract_locks_guard:
        lock = _extract_locks.get(company_id)
        if lock is None:
            lock = threading.Lock()
            _extract_locks[company_id] = lock
        return lock


def _run_locked(company_id: str, docs: list[DriveDoc]) -> None:
    with _extract_lock(company_id):
        try:
            r = run_drive_extract(company_id, docs)
            logger.info(
                "drive-extract done: %s files=%s signals=%s errors=%s",
                company_id, r["files"], r["signals"], len(r["errors"]),
            )
        except Exception:  # noqa: BLE001 — fully isolated
            logger.exception("drive-extract failed for %s", company_id)


def kickoff_drive_extract(company_id: str, docs: list[DriveDoc]) -> bool:
    """Fire-and-forget: extract changed Drive docs into the KG in a daemon
    thread. Never blocks; never raises into the caller's sync flow."""
    if not docs:
        return False
    try:
        t = threading.Thread(
            target=_run_locked, args=(company_id, docs),
            name="drive-kg-extract", daemon=True,
        )
        t.start()
        return True
    except Exception:  # noqa: BLE001 — never let a thread-spawn failure break the sync
        logger.exception(
            "drive-extract: failed to start thread for %s", company_id
        )
        return False
