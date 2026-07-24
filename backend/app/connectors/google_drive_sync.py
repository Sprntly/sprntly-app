"""Sync explicitly-picked Google Drive files into a dataset corpus.

Under the ``drive.file`` OAuth scope this app can only see files the user
explicitly picks via the Google Picker — there is no Drive-wide listing or
folder browsing. The frontend Picker POSTs the picked file IDs (see
``routes/connectors.py`` ``POST /v1/connectors/google-drive/files``) which we
store in the connection config under ``config["files"]`` as a list of
``{"id": "...", "name": "..."}`` entries. ``sync_google_drive`` iterates those
IDs, fetches each file's metadata, downloads/exports it, and ingests it.
"""
from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from app import datasets, db
from app.connectors import google_oauth
from app.connectors.google_oauth import credentials_from_token_json
from app.connectors.tokens import decrypt_token_json, encrypt_token_json
from app.ingest import SUPPORTED_SUFFIXES, UnsupportedFileType, convert

logger = logging.getLogger(__name__)

MAX_SYNC_BYTES = 20 * 1024 * 1024
_FILE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,}$")

GOOGLE_FOLDER = "application/vnd.google-apps.folder"
GOOGLE_DOC = "application/vnd.google-apps.document"
GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDES = "application/vnd.google-apps.presentation"

_EXPORT = {
    GOOGLE_DOC: ("text/plain", ".txt"),
    GOOGLE_SHEET: (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    GOOGLE_SLIDES: ("application/pdf", ".pdf"),
}

_NATIVE_SUFFIXES = {s.lower() for s in SUPPORTED_SUFFIXES}


@dataclass
class SyncResult:
    dataset: str
    synced: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    # Files handed to the KG extractor this run (names). Extraction itself is
    # async unless kg_inline — kg_signals is only populated on the inline path.
    kg_queued: list[str] = field(default_factory=list)
    kg_signals: int = 0

    def to_dict(self) -> dict:
        return {
            "dataset": self.dataset,
            "synced": self.synced,
            "skipped": self.skipped,
            "errors": self.errors,
            "kg_queued": self.kg_queued,
            "kg_signals": self.kg_signals,
        }


class SyncConfigError(ValueError):
    pass


def drive_http_error_message(err: HttpError) -> str:
    """Turn a Drive API HttpError into a short, user-facing message."""
    try:
        payload = json.loads(err.content.decode())
        err_obj = payload.get("error") or {}
        msg = err_obj.get("message") or str(err)
        reasons = err_obj.get("errors") or []
        reason = reasons[0].get("reason") if reasons else ""
    except (TypeError, ValueError, AttributeError, IndexError, KeyError):
        msg = str(err)
        reason = ""

    if reason == "accessNotConfigured":
        return (
            "Google Drive API is not enabled for this OAuth app — enable "
            "“Google Drive API” in Google Cloud Console, then disconnect and "
            "reconnect Drive."
        )
    if err.resp is not None and err.resp.status in (401, 403):
        return (
            f"Google Drive access denied ({msg}). Disconnect and reconnect "
            "Google Drive to refresh permissions."
        )
    return f"Google Drive API error: {msg}"


def normalize_picked_files(files: list[dict] | None) -> list[dict]:
    """Validate + dedupe the picked-file list the Picker frontend sends.

    Each entry must carry an ``id``; ``name`` is optional (used to name the
    ingested doc — falls back to the live Drive metadata name at sync time).
    Returns a clean ``[{"id": str, "name": str|None}, ...]`` list (last write
    wins per id). Raises SyncConfigError on a malformed id."""
    out: dict[str, dict] = {}
    for entry in files or []:
        if not isinstance(entry, dict):
            raise SyncConfigError("each picked file must be an object with an id")
        fid = (entry.get("id") or "").strip()
        if not fid:
            raise SyncConfigError("each picked file must have an id")
        if not _FILE_ID_RE.match(fid):
            raise SyncConfigError(f"invalid Drive file id: {fid!r}")
        name = entry.get("name")
        out[fid] = {"id": fid, "name": (name.strip() if isinstance(name, str) and name.strip() else None)}
    return list(out.values())


def load_config(row: dict) -> dict:
    try:
        return json.loads(row.get("config_json") or "{}")
    except (TypeError, ValueError):
        return {}


def merge_config(row: dict, patch: dict) -> dict:
    # company_id comes off the row itself — the row IS the per-company
    # connection; threading it as a separate arg would duplicate the truth.
    config = load_config(row)
    config.update(patch)
    updated_row = db.patch_connection_config(
        row["company_id"], google_oauth.GOOGLE_DRIVE_PROVIDER, config
    )
    return load_config(updated_row) if updated_row else config


def _refresh_credentials(row: dict):
    creds = credentials_from_token_json(
        decrypt_token_json(row["token_json_encrypted"])
    )
    if creds.expired:
        if not creds.refresh_token:
            raise SyncConfigError(
                "Google Drive session expired — disconnect and connect again."
            )
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise SyncConfigError(
                "Google Drive authorization expired — disconnect and connect again."
            ) from e
        db.update_connection_tokens(
            row["company_id"],
            google_oauth.GOOGLE_DRIVE_PROVIDER,
            encrypt_token_json(creds.to_json()),
        )
    return creds


def build_drive_service(row: dict) -> Resource:
    from googleapiclient.discovery import build

    creds = _refresh_credentials(row)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def get_file_metadata(service: Resource, file_id: str) -> dict:
    """Fetch the metadata fields download_file_content needs for one picked
    file. Under drive.file this succeeds only for files the user picked /
    granted this app access to."""
    return (
        service.files()
        .get(fileId=file_id,
             fields="id, name, mimeType, modifiedTime, size, webViewLink")
        .execute()
    )


def _download_bytes(request) -> bytes:
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def download_file_content(service: Resource, meta: dict) -> tuple[str, bytes] | None:
    mime = meta.get("mimeType") or ""
    name = meta.get("name") or "untitled"
    file_id = meta["id"]

    if mime in _EXPORT:
        export_mime, ext = _EXPORT[mime]
        if not name.lower().endswith(ext):
            name = f"{Path(name).stem}{ext}"
        request = service.files().export_media(fileId=file_id, mimeType=export_mime)
        data = _download_bytes(request)
        return name, data

    suffix = Path(name).suffix.lower()
    if suffix not in _NATIVE_SUFFIXES:
        return None

    size = int(meta.get("size") or 0)
    if size > MAX_SYNC_BYTES:
        raise ValueError(f"File exceeds {MAX_SYNC_BYTES // (1024 * 1024)}MB limit")

    request = service.files().get_media(fileId=file_id)
    data = _download_bytes(request)
    return name, data


def _mark_corpus_doc(company_id: str, doc_name: str, md_text: str) -> None:
    """Best-effort corpus-doc ledger mark (see
    ``synthesis_brief.mark_corpus_doc_ingested``). A failed mark only risks
    the corpus seed double-extracting this doc as origin="upload" — extraction
    is content-keyed idempotent, so that costs an LLM call, not correctness."""
    try:
        from app.graph.facade import GraphFacade
        from app.synthesis_brief import mark_corpus_doc_ingested

        mark_corpus_doc_ingested(GraphFacade(), company_id, doc_name, md_text)
    except Exception:  # noqa: BLE001
        logger.warning("drive sync: corpus-doc ledger mark failed for %r",
                       doc_name, exc_info=True)


def sync_google_drive(
    *,
    company_id: str,
    dataset: str | None = None,
    files: list[dict] | None = None,
    kg_inline: bool = False,
) -> SyncResult:
    """Download + ingest the explicitly-picked Drive files stored in the
    connection config (``config["files"]``). Pass ``files`` to overwrite the
    stored picked-file list first (used by the save-picked-files endpoint);
    otherwise the existing config is used. An empty picked-file list is a
    graceful no-op — not an error.

    Two freshness ledgers per file: ``file_mtime`` (corpus copy) and
    ``kg_file_mtime`` (KG extraction). A file changed against either gets
    re-downloaded; the corpus copy re-ingests only when corpus-stale, and
    KG-stale files are handed to the connector-origin extractor
    (``kg_ingest.drive_extract``) — in a background thread by default, or
    synchronously with ``kg_inline=True`` (the brief's first-time seed).
    ``kg_file_mtime`` advances only after successful extraction, so a lost
    background thread is retried on the next scheduled/manual sync."""
    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    if not row:
        raise SyncConfigError("Google Drive is not connected")

    config = load_config(row)
    slug = (dataset or config.get("dataset") or "").strip()
    if not slug:
        raise SyncConfigError(
            "dataset is required — pass ?dataset= on authorize or in sync body"
        )
    if not db.dataset_exists(slug):
        raise SyncConfigError(f"Dataset {slug!r} does not exist")

    if files is not None or dataset:
        patch: dict = {"dataset": slug}
        if files is not None:
            patch["files"] = normalize_picked_files(files)
        merge_config(row, patch)
        row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER) or row
        config = load_config(row)

    picked = normalize_picked_files(config.get("files"))
    result = SyncResult(dataset=slug)

    # No picked files yet (fresh connect, or the Picker hasn't run) — no-op.
    if not picked:
        db.update_connection_sync(
            company_id, google_oauth.GOOGLE_DRIVE_PROVIDER, last_sync_error=None
        )
        return result

    mtime_map: dict[str, str] = dict(config.get("file_mtime") or {})
    kg_mtime_map: dict[str, str] = dict(config.get("kg_file_mtime") or {})
    # Grandfather pre-existing connections: on the very first KG-aware sync
    # (no kg_file_mtime key in config, ever) the already-synced files were
    # extracted long ago by the corpus seed — adopt their corpus mtimes
    # instead of re-extracting the same bytes into near-duplicate signals.
    # New and edited files still extract normally. The key is then persisted
    # below even when empty, so this fires exactly once per connection — a
    # later sync where extraction is still pending/failed must NOT
    # re-grandfather away the retry.
    grandfathered = "kg_file_mtime" not in config
    if grandfathered:
        kg_mtime_map = dict(mtime_map)

    from app.kg_ingest.drive_extract import (  # lazy — keeps graph/LLM deps off module load
        DriveDoc,
        kickoff_drive_extract,
        run_drive_extract,
    )

    kg_docs: list[DriveDoc] = []

    try:
        service = build_drive_service(row)
    except HttpError as e:
        msg = f"Drive API error: {e}"
        db.update_connection_sync(
            company_id, google_oauth.GOOGLE_DRIVE_PROVIDER, last_sync_error=msg
        )
        raise SyncConfigError(msg) from e

    for entry in picked:
        file_id = entry["id"]
        picked_name = entry.get("name") or file_id

        try:
            meta = get_file_metadata(service, file_id)
        except HttpError as e:
            result.errors.append(
                {"name": picked_name, "error": drive_http_error_message(e)}
            )
            continue
        except Exception as e:
            result.errors.append({"name": picked_name, "error": str(e)})
            continue

        name = meta.get("name") or picked_name
        modified = meta.get("modifiedTime") or ""
        corpus_fresh = mtime_map.get(file_id) == modified
        kg_fresh = kg_mtime_map.get(file_id) == modified
        if corpus_fresh and kg_fresh:
            result.skipped.append({"name": name, "reason": "unchanged"})
            continue

        try:
            downloaded = download_file_content(service, meta)
        except Exception as e:
            result.errors.append({"name": name, "error": str(e)})
            continue

        if downloaded is None:
            result.skipped.append(
                {
                    "name": name,
                    "reason": f"unsupported type ({meta.get('mimeType')})",
                }
            )
            continue

        filename, data = downloaded
        if len(data) > MAX_SYNC_BYTES:
            result.skipped.append(
                {
                    "name": name,
                    "reason": f"exceeds {MAX_SYNC_BYTES // (1024 * 1024)}MB limit",
                }
            )
            continue

        md_text = ""
        if not corpus_fresh:
            try:
                ingested = datasets.ingest_file(slug, filename, data)
            except UnsupportedFileType:
                result.skipped.append({"name": name, "reason": "unsupported after export"})
                continue
            except Exception as e:
                result.errors.append({"name": name, "error": str(e)})
                continue

            try:
                md_text = Path(ingested.md_path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                logger.warning("drive sync: could not re-read md for %r", filename,
                               exc_info=True)
            # Mark the corpus-doc ledger so the brief's corpus seed doesn't
            # re-extract this same content as origin="upload" — Drive content
            # reaches the KG through its own connector-origin path below.
            if md_text:
                _mark_corpus_doc(company_id, Path(ingested.md_path).stem, md_text)
            mtime_map[file_id] = modified
            result.synced.append(
                {
                    "filename": ingested.original_filename,
                    "md_path": ingested.md_path,
                    "md_chars": ingested.md_chars,
                }
            )
        else:
            # KG-only refresh (first pass after KG ingest shipped, or a prior
            # extraction that never completed) — convert in memory, no
            # duplicate corpus write.
            try:
                md_text = convert(filename, data)
            except UnsupportedFileType:
                result.skipped.append({"name": name, "reason": "unsupported after export"})
                continue
            except Exception as e:
                result.errors.append({"name": name, "error": str(e)})
                continue

        if not kg_fresh and md_text.strip():
            kg_docs.append(DriveDoc(
                file_id=file_id,
                name=Path(filename).stem,
                modified=modified,
                text=md_text,
                mime=meta.get("mimeType") or "",
                link=meta.get("webViewLink") or "",
            ))

    patch: dict = {"file_mtime": mtime_map, "dataset": slug}
    if grandfathered:
        # Persist the adopted ledger so the next sync doesn't grandfather
        # again over a by-then-updated file_mtime. Safe from clobbering the
        # extraction thread's own patch: the KG kick below hasn't run yet.
        patch["kg_file_mtime"] = kg_mtime_map
    merge_config(row, patch)
    err = None
    if result.errors:
        err = f"{len(result.errors)} file(s) failed"
    db.update_connection_sync(
        company_id,
        google_oauth.GOOGLE_DRIVE_PROVIDER,
        last_sync_error=err,
    )

    if kg_docs:
        result.kg_queued = [d.name for d in kg_docs]
        try:
            if kg_inline:
                extract = run_drive_extract(company_id, kg_docs)
                result.kg_signals = extract.get("signals", 0)
            else:
                kickoff_drive_extract(company_id, kg_docs)
        except Exception:  # noqa: BLE001 — extraction must never fail the sync
            logger.exception(
                "drive sync: KG extraction kick failed for %s", company_id
            )
    return result
