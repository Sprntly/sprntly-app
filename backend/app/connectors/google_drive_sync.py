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
from app.ingest import SUPPORTED_SUFFIXES, UnsupportedFileType

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

    def to_dict(self) -> dict:
        return {
            "dataset": self.dataset,
            "synced": self.synced,
            "skipped": self.skipped,
            "errors": self.errors,
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
        .get(fileId=file_id, fields="id, name, mimeType, modifiedTime, size")
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


def sync_google_drive(
    *,
    company_id: str,
    dataset: str | None = None,
    files: list[dict] | None = None,
) -> SyncResult:
    """Download + ingest the explicitly-picked Drive files stored in the
    connection config (``config["files"]``). Pass ``files`` to overwrite the
    stored picked-file list first (used by the save-picked-files endpoint);
    otherwise the existing config is used. An empty picked-file list is a
    graceful no-op — not an error."""
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
        if mtime_map.get(file_id) == modified:
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

        try:
            ingested = datasets.ingest_file(slug, filename, data)
        except UnsupportedFileType:
            result.skipped.append({"name": name, "reason": "unsupported after export"})
            continue
        except Exception as e:
            result.errors.append({"name": name, "error": str(e)})
            continue

        mtime_map[file_id] = modified
        result.synced.append(
            {
                "filename": ingested.original_filename,
                "md_path": ingested.md_path,
                "md_chars": ingested.md_chars,
            }
        )

    merge_config(row, {"file_mtime": mtime_map, "dataset": slug})
    err = None
    if result.errors:
        err = f"{len(result.errors)} file(s) failed"
    db.update_connection_sync(
        company_id,
        google_oauth.GOOGLE_DRIVE_PROVIDER,
        last_sync_error=err,
    )
    return result
