"""Sync explicitly-picked Google Drive files and folders into a dataset corpus.

Under the ``drive.file`` OAuth scope this app can only see what the user
explicitly picks via the Google Picker — there is still no Drive-wide listing.
The frontend Picker POSTs the picked IDs (see ``routes/connectors.py``
``POST /v1/connectors/google-drive/files``) which we store in the connection
config under ``config["files"]`` as ``{"id": "...", "name": "..."}`` entries.

FOLDERS: a picked folder id is stored in ``config["files"]`` exactly like a
file id. Every sync recursively re-walks the folder's whole subtree
(``expand_folder`` — paginated ``files.list("'<id>' in parents")``, bounded by
``_MAX_FOLDER_DEPTH`` / ``_MAX_FOLDER_FILES``) down to every descendant file,
which then flows through the same download + dedup + KG-ingest path as a
directly picked file. This mirrors the Confluence connector: store the
source, re-pull everything under it on schedule. Expanding at SYNC time (not
pick time) is the point — files added to the folder later are picked up
without re-opening the Picker.

Whether ``files.list`` returns any descendants for a freshly-picked folder
depends on the OAuth scope the connection actually holds: under the narrow
``drive.file`` scope a picked folder grants the folder OBJECT but nothing
beneath it, so the walk legitimately comes back empty (reported to the user
as an honest "no files" skip, not an error) — folder-as-a-source needs
``drive.readonly``. The frontend gates the Picker's folder-selection
affordance on the connection's granted scope for exactly this reason (see
``GoogleDrivePicker.tsx``): selecting a folder is only offered once the
connection actually holds ``drive.readonly``.
"""
from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

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

# Bound the recursive folder walk the same way the Confluence puller bounds a
# space crawl: a runaway subtree (deep nesting or a huge shared drive) must
# not turn one picked folder into an unbounded Drive scan.
_MAX_FOLDER_DEPTH = 10
_MAX_FOLDER_FILES = 500


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


def reachable_file_ids(
    picked_ids: Iterable[str], folder_contents: dict | None
) -> set[str]:
    """Every Drive file id a given set of PICKED entries covers, per the
    connection's own stored state — the picked ids themselves plus, for any
    that is a folder we have already walked, every id in its stored subtree.

    The two halves of a Drive selection are stored separately and both are
    needed: `config["files"]` is what the user clicked in the Picker, and
    `config["folder_contents"]` is what a picked FOLDER turned out to contain
    at the last sync. A file inside a picked folder has a catalog row and is
    named nowhere in `config["files"]`, so a cleanup that read only the picked
    list would leave every folder-sourced document behind — the majority of
    them, for anyone who connected a folder rather than files.

    Reads STORED state only and calls no Drive API. That is deliberate: the
    caller uses this to work out what a user's selection change removed, and
    a live enumeration would make a transient API failure look like a
    shrunken folder. A stale stored subtree can only make this set too SMALL,
    which under-cleans; a live one could make it too small too, and there the
    same shortfall would delete rows for files that are still connected.

    Sub-folders need no recursion here: `expand_folder` stores one FLAT node
    list per picked root covering its whole subtree, so a nested folder
    appears as a node under its root rather than as another key."""
    contents = folder_contents or {}
    out: set[str] = set()
    for pid in picked_ids:
        pid = str(pid or "").strip()
        if not pid:
            continue
        out.add(pid)
        for node in contents.get(pid) or []:
            if not isinstance(node, dict):
                continue
            nid = str(node.get("id") or "").strip()
            if nid:
                out.add(nid)
    return out


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
             fields="id, name, mimeType, modifiedTime, size, webViewLink, driveId",
             # supportsAllDrives so a picked folder/file living in a Shared
             # Drive resolves (its own metadata 404s without this).
             supportsAllDrives=True)
        .execute()
    )


def _list_folder_children(service: Resource, folder_id: str) -> list[dict]:
    """One folder's DIRECT children — files AND sub-folders — paginated.

    ``files.list(q="'<id>' in parents and trashed = false")`` is the standard
    Drive subtree step (the same query the Confluence puller's per-space page
    fetch is the analogue of). Returns raw child metadata; the caller decides
    which are folders to recurse into and which are files to ingest."""
    children: list[dict] = []
    page_token = None
    q = f"'{folder_id}' in parents and trashed = false"
    while True:
        resp = (
            service.files()
            .list(
                q=q,
                fields=(
                    "nextPageToken, "
                    "files(id, name, mimeType, modifiedTime, size, "
                    "webViewLink, driveId)"
                ),
                pageSize=100,
                pageToken=page_token,
                # Without these two, files.list only searches My Drive, so a
                # folder that lives in a Shared Drive returns 0 children
                # regardless of OAuth scope. Shared Drives are the "one shared
                # team folder" use case, so this is required, not optional.
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        children.extend(resp.get("files") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return children


def expand_folder(
    service: Resource, folder_id: str, folder_name: str
) -> tuple[list[dict], list[dict]]:
    """Recursively walk a picked folder. Returns ``(files, tree_nodes)``.

    ``files`` is the flat list of every descendant FILE's metadata — the
    ingest targets. ``tree_nodes`` additionally describes the SHAPE of the
    subtree so the connector UI can render it as a nested tree instead of one
    opaque row: every child encountered (sub-folder AND file) is emitted as
    ``{"id", "name", "mimeType", "parentId"}`` where ``parentId`` is the id of
    the folder it was listed under (the picked root's own id for a direct
    child). A node with a folder ``mimeType`` is a sub-folder; anything else
    is a file. The picked root itself is not a node — it is the dict key the
    caller stores this under.

    Bounded by ``_MAX_FOLDER_DEPTH`` and ``_MAX_FOLDER_FILES``. Dedupe of a
    file reachable by more than one path is the caller's job.
    """
    files: list[dict] = []
    tree_nodes: list[dict] = []
    # (folder_id, display_name, depth); iterative to keep the depth bound cheap.
    stack: list[tuple[str, str, int]] = [(folder_id, folder_name, 0)]
    visited: set[str] = set()

    while stack:
        fid, fname, depth = stack.pop()
        if fid in visited:
            continue
        visited.add(fid)

        try:
            children = _list_folder_children(service, fid)
        except HttpError as e:
            logger.warning(
                "drive folder-walk: list failed for folder %s at depth %d: %s",
                fid, depth, drive_http_error_message(e),
            )
            continue

        for child in children:
            if len(files) >= _MAX_FOLDER_FILES:
                logger.warning(
                    "drive folder-walk: %r hit _MAX_FOLDER_FILES=%d; truncating",
                    folder_name, _MAX_FOLDER_FILES,
                )
                return files, tree_nodes
            # Every child becomes a tree node, parented to the folder it was
            # listed under, so the UI can reconstruct the subtree from a flat
            # list without extra fetches.
            tree_nodes.append({
                "id": child.get("id"),
                "name": child.get("name"),
                "mimeType": child.get("mimeType"),
                "parentId": fid,
            })
            if (child.get("mimeType") or "") == GOOGLE_FOLDER:
                if depth + 1 <= _MAX_FOLDER_DEPTH:
                    stack.append(
                        (child["id"], child.get("name") or "Untitled", depth + 1)
                    )
                else:
                    logger.warning(
                        "drive folder-walk: %r hit _MAX_FOLDER_DEPTH=%d; not "
                        "descending into %r",
                        folder_name, _MAX_FOLDER_DEPTH, child.get("name"),
                    )
                continue
            files.append(child)

    return files, tree_nodes


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

    # supportsAllDrives so a binary file living in a Shared Drive downloads
    # (get_media 404s on a Shared-Drive file without it). files.export has no
    # such parameter — native Google Docs export is unaffected.
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
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
    service: Resource | None = None,
    entries: list[dict] | None = None,
) -> SyncResult:
    """Download + ingest the explicitly-picked Drive files stored in the
    connection config (``config["files"]``). Pass ``files`` to overwrite the
    stored picked-file list first (used by the save-picked-files endpoint);
    otherwise the existing config is used. An empty picked-file list is a
    graceful no-op — not an error.

    ``service`` and ``entries`` let a caller inject an already-authenticated
    Drive client and its own enumerated item set into this SAME walk /
    download / ingest / KG path, instead of resolving OAuth credentials off
    the connection row and reading ``config["files"]`` — this is how
    service-account mode (``google_service_account.sync_service_account``)
    reuses this function unchanged. When ``service`` is given it is used
    as-is and ``build_drive_service`` is never called. When ``entries`` is
    given it replaces the stored picked-file list as the item set to
    resolve — each entry may be a file or a FOLDER, and a folder entry is
    recursively expanded via ``expand_folder`` exactly like a picked folder
    is. Both default to ``None``, in which case behaviour is byte-identical
    to the original OAuth-only path.

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

    picked = (
        normalize_picked_files(entries)
        if entries is not None
        else normalize_picked_files(config.get("files"))
    )
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

    if service is None:
        try:
            service = build_drive_service(row)
        except HttpError as e:
            msg = f"Drive API error: {e}"
            db.update_connection_sync(
                company_id, google_oauth.GOOGLE_DRIVE_PROVIDER, last_sync_error=msg
            )
            raise SyncConfigError(msg) from e

    # ── Resolve the picked entries to FILES ──────────────────────────────────
    #
    # A picked entry is a file or a folder, and only its metadata says which.
    # Folders are expanded to the files beneath them here, so everything below
    # this block deals in files only and folder support costs the ingest loop
    # nothing. Expanding at SYNC time rather than at pick time is the whole
    # value of picking a folder: files added to it later are picked up by the
    # next sync without the user touching the Picker again.
    targets: list[dict] = []
    seen_ids: set[str] = set()
    # folder id -> the files it expanded to, so the UI can show what connecting
    # a folder actually brought in. Rebuilt from scratch every sync rather than
    # merged, so a folder the user disconnects takes its listing with it and a
    # folder that shrank in Drive shrinks here too.
    folder_contents: dict[str, list[dict]] = {}

    def _add_target(meta: dict) -> None:
        fid = meta.get("id")
        # A file reachable from two picked folders, or picked directly AND
        # inside a picked folder, must ingest once — not once per path to it.
        if fid and fid not in seen_ids:
            seen_ids.add(fid)
            targets.append(meta)

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

        if (meta.get("mimeType") or "") != GOOGLE_FOLDER:
            _add_target(meta)
            continue

        # A FOLDER. Store-the-folder + recursive-pull: the folder id is what's
        # persisted in config["files"], and every sync re-walks its whole
        # subtree here so files added to it later are ingested without the
        # user re-picking — the same contract as the Confluence connector
        # re-walking a stored space. Bounded by _MAX_FOLDER_DEPTH /
        # _MAX_FOLDER_FILES.
        try:
            folder_files, folder_nodes = expand_folder(service, file_id, name)
        except Exception as e:  # noqa: BLE001 — one bad folder must not fail the sync
            result.errors.append(
                {"name": name, "error": f"folder walk failed: {e}"}
            )
            folder_contents[file_id] = []
            continue

        # Store the SUBTREE SHAPE (sub-folders + files, each with parentId),
        # not a flat leaf list, so the connector UI can render the folder as a
        # tree instead of hoisting every descendant to the root.
        folder_contents[file_id] = folder_nodes
        logger.info(
            "drive sync: picked folder %r expanded to %d descendant file(s)",
            name, len(folder_files),
        )
        if not folder_files:
            # Empty is a real, reportable outcome — most likely the drive.file
            # no-cascade case (a picked folder grants the folder object but
            # nothing beneath it). Surface it rather than looking silently
            # connected-but-inert.
            result.skipped.append({
                "name": name,
                "reason": (
                    "folder is connected but returned no files — under the "
                    "current Drive scope only items picked directly may be "
                    "readable; pick the files inside, or grant broader Drive "
                    "access"
                ),
            })
        for m in folder_files:
            _add_target(m)

    for meta in targets:
        file_id = meta["id"]
        name = meta.get("name") or file_id

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
            result.errors.append(
                {
                    "name": name,
                    "error": (
                        f"Unsupported file type ({meta.get('mimeType')}) — "
                        "Sprntly reads documents, spreadsheets, slides and PDFs."
                    ),
                }
            )
            continue

        filename, data = downloaded
        if len(data) > MAX_SYNC_BYTES:
            result.errors.append(
                {
                    "name": name,
                    "error": "File is larger than the 20MB sync limit.",
                }
            )
            continue

        md_text = ""
        # Where this file's converted markdown landed, when this pass is the
        # one that writes it. Empty on the KG-only refresh below: that branch
        # converts in memory and writes nothing, so it has no name to report
        # and the extractor keeps whatever an earlier sync recorded.
        md_path = ""
        if not corpus_fresh:
            try:
                ingested = datasets.ingest_file(slug, filename, data)
            except UnsupportedFileType:
                result.errors.append({
                    "name": name,
                    "error": "Sprntly couldn't convert this file after downloading it.",
                })
                continue
            except Exception as e:
                result.errors.append({"name": name, "error": str(e)})
                continue

            md_path = ingested.md_path
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
                result.errors.append({
                    "name": name,
                    "error": "Sprntly couldn't convert this file after downloading it.",
                })
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
                # The corpus location this sync just wrote. Carried through so
                # the extractor can record it on the file's provenance row:
                # the converted name is normalised and collision-suffixed, so
                # this is the only moment it is knowable.
                dataset=slug,
                md_file=md_path,
            ))

    patch: dict = {
        "file_mtime": mtime_map,
        "dataset": slug,
        # Whole-value replace, not a merge — see where this is built.
        "folder_contents": folder_contents,
    }
    if grandfathered:
        # Persist the adopted ledger so the next sync doesn't grandfather
        # again over a by-then-updated file_mtime. Safe from clobbering the
        # extraction thread's own patch: the KG kick below hasn't run yet.
        patch["kg_file_mtime"] = kg_mtime_map
    merge_config(row, patch)
    err = None
    if result.errors:
        first = result.errors[0]
        err = f"{first['name']}: {first['error']}"
        if len(result.errors) > 1:
            err = f"{err} (+{len(result.errors) - 1} more)"
        err = err[:500]
    db.update_connection_sync(
        company_id,
        google_oauth.GOOGLE_DRIVE_PROVIDER,
        last_sync_error=err,
    )

    if kg_docs:
        result.kg_queued = [d.name for d in kg_docs]
        started = False
        try:
            if kg_inline:
                extract = run_drive_extract(company_id, kg_docs)
                result.kg_signals = extract.get("signals", 0)
                started = True
            else:
                started = kickoff_drive_extract(company_id, kg_docs)
        except Exception:  # noqa: BLE001 — extraction must never fail the sync
            logger.exception(
                "drive sync: KG extraction kick failed for %s", company_id
            )
        if not started:
            msg = "Files synced, but knowledge-graph extraction didn't start. Re-run the sync."
            result.errors.append({"name": "knowledge graph", "error": msg})
            db.update_connection_sync(
                company_id, google_oauth.GOOGLE_DRIVE_PROVIDER, last_sync_error=msg[:500],
            )
    return result
