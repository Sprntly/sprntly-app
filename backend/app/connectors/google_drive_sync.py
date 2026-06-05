"""Sync files from a Google Drive folder into a dataset corpus."""
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
_FOLDER_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,}$")

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
    folder_id: str
    synced: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "dataset": self.dataset,
            "folder_id": self.folder_id,
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


def parse_folder_id(value: str) -> str:
    """Accept a raw folder ID or a drive.google.com folder URL."""
    raw = (value or "").strip()
    if not raw:
        raise SyncConfigError("folder_id is required")
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", raw)
    if m:
        raw = m.group(1)
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", raw)
    if m:
        raw = m.group(1)
    if not _FOLDER_ID_RE.match(raw):
        raise SyncConfigError(
            "folder_id must be a Drive folder ID or a folder URL from Google Drive"
        )
    return raw


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


def browse_folders(company_id: str, parent_id: str | None = None) -> dict:
    """List child folders under parent_id (default: user's Drive root)."""
    row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER)
    if not row:
        raise SyncConfigError("Google Drive is not connected")

    pid = (parent_id or "root").strip() or "root"
    if pid != "root":
        parse_folder_id(pid)  # validate format

    service = build_drive_service(row)
    current_name = "My Drive"
    parent_meta = None

    if pid != "root":
        try:
            meta = (
                service.files()
                .get(fileId=pid, fields="id, name, parents")
                .execute()
            )
            current_name = meta.get("name") or pid
            parents = meta.get("parents") or []
            if parents:
                try:
                    p = (
                        service.files()
                        .get(fileId=parents[0], fields="id, name")
                        .execute()
                    )
                    parent_meta = {"id": p["id"], "name": p.get("name") or parents[0]}
                except HttpError:
                    parent_meta = {"id": parents[0], "name": "My Drive"}
            else:
                parent_meta = {"id": "root", "name": "My Drive"}
        except HttpError as e:
            raise SyncConfigError(drive_http_error_message(e)) from e

    try:
        children = _list_child_folders(service, pid)
    except HttpError as e:
        raise SyncConfigError(drive_http_error_message(e)) from e

    return {
        "current": {"id": pid, "name": current_name},
        "parent": parent_meta,
        "folders": children,
    }


def _list_child_folders(service: Resource, parent_id: str) -> list[dict]:
    folders: list[dict] = []
    page_token = None
    q = (
        f"'{parent_id}' in parents and trashed = false "
        f"and mimeType = '{GOOGLE_FOLDER}'"
    )
    while True:
        resp = (
            service.files()
            .list(
                q=q,
                fields="nextPageToken, files(id, name)",
                orderBy="name",
                pageSize=100,
                pageToken=page_token,
                spaces="drive",
            )
            .execute()
        )
        for f in resp.get("files") or []:
            folders.append({"id": f["id"], "name": f.get("name") or "Untitled"})
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return folders


def list_folder_files(service: Resource, folder_id: str) -> list[dict]:
    files: list[dict] = []
    page_token = None
    q = (
        f"'{folder_id}' in parents and trashed = false "
        f"and mimeType != '{GOOGLE_FOLDER}'"
    )
    while True:
        resp = (
            service.files()
            .list(
                q=q,
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                pageSize=100,
                pageToken=page_token,
            )
            .execute()
        )
        files.extend(resp.get("files") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


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
    folder_id: str | None = None,
) -> SyncResult:
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

    fid = folder_id or config.get("folder_id")
    if not fid:
        raise SyncConfigError(
            "folder_id is not configured — set it via POST /v1/connectors/google-drive/config"
        )
    fid = parse_folder_id(fid)

    if folder_id or dataset:
        merge_config(row, {"folder_id": fid, "dataset": slug})
        row = db.get_connection(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER) or row

    config = load_config(row)
    mtime_map: dict[str, str] = dict(config.get("file_mtime") or {})

    result = SyncResult(dataset=slug, folder_id=fid)
    try:
        service = build_drive_service(row)
        files = list_folder_files(service, fid)
    except HttpError as e:
        msg = f"Drive API error: {e}"
        db.update_connection_sync(company_id, google_oauth.GOOGLE_DRIVE_PROVIDER, last_sync_error=msg)
        raise SyncConfigError(msg) from e

    for meta in files:
        file_id = meta["id"]
        name = meta.get("name") or file_id
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

    merge_config(row, {"file_mtime": mtime_map, "folder_id": fid, "dataset": slug})
    err = None
    if result.errors:
        err = f"{len(result.errors)} file(s) failed"
    db.update_connection_sync(
        company_id,
        google_oauth.GOOGLE_DRIVE_PROVIDER,
        last_sync_error=err,
    )
    return result
