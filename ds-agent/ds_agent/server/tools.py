"""File ingestion: upload one or many files to Anthropic Files API,
extracting zips along the way.

The chat agent has no application-defined tools — it runs Python in
Anthropic's sandbox. This module just exists so file admin (upload,
zip extraction, validation, deletion) happens once at ingest time
rather than per chat turn.
"""

from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass
from pathlib import Path

from anthropic import Anthropic


# Total payload cap across one upload request.
MAX_TOTAL_BYTES = 100 * 1024 * 1024  # 100 MB
# Per-file cap (the Files API enforces 500MB, but we keep things tight).
MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB
# Max files per session.
MAX_FILES = 20

# File types Claude's sandbox can usefully consume.
ALLOWED_EXTENSIONS = {
    ".csv", ".tsv", ".json", ".jsonl", ".ndjson",
    ".parquet", ".feather",
    ".xlsx", ".xls",
    ".txt", ".md", ".log",
    ".pdf",
    ".html", ".htm",
}

# Maps extension → content-type for the Files API upload tuple.
_CONTENT_TYPES = {
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".ndjson": "application/x-ndjson",
    ".parquet": "application/octet-stream",
    ".feather": "application/octet-stream",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".log": "text/plain",
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
}


class IngestError(ValueError):
    """User-visible — content goes straight to the /api/upload 400 detail."""


@dataclass
class StagedFile:
    """An extracted file ready to upload to Anthropic."""
    local_path: Path
    label: str  # filename inside the sandbox (zip-path-flattened)
    size_bytes: int


@dataclass
class UploadedFile:
    """A file we successfully pushed to the Files API."""
    local_path: Path
    label: str
    size_bytes: int
    anthropic_file_id: str


_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")
        _client = Anthropic(api_key=key)
    return _client


# ─────────────────────── public ───────────────────────

def stage_uploads(
    sources: list[tuple[Path, str]],
    workdir: Path,
    existing_count: int = 0,
) -> list[StagedFile]:
    """Validate + extract + flatten a list of (local_path, original_filename) pairs.

    Zips are extracted into `workdir`. Non-zip files are validated in
    place. Raises `IngestError` on policy violations (forbidden type,
    too big, too many files, zip path traversal).
    """
    staged: list[StagedFile] = []
    total_bytes = 0

    for src_path, original_name in sources:
        if not src_path.exists():
            raise IngestError(f"missing_file:{original_name}")

        if original_name.lower().endswith(".zip"):
            for sf in _extract_zip(src_path, original_name, workdir):
                staged.append(sf)
                total_bytes += sf.size_bytes
        else:
            ext = Path(original_name).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise IngestError(f"unsupported_type:{original_name}")
            size = src_path.stat().st_size
            if size > MAX_FILE_BYTES:
                raise IngestError(f"file_too_large:{original_name}")
            total_bytes += size
            staged.append(
                StagedFile(local_path=src_path, label=original_name, size_bytes=size)
            )

        if total_bytes > MAX_TOTAL_BYTES:
            raise IngestError(f"total_size_exceeds_{MAX_TOTAL_BYTES}_bytes")
        if existing_count + len(staged) > MAX_FILES:
            raise IngestError(f"too_many_files_max_{MAX_FILES}")

    return staged


def upload_staged(staged: list[StagedFile]) -> list[UploadedFile]:
    """Push staged files to Anthropic's Files API. Returns the uploaded list."""
    client = _get_client()
    out: list[UploadedFile] = []
    for sf in staged:
        ext = Path(sf.label).suffix.lower()
        content_type = _CONTENT_TYPES.get(ext, "application/octet-stream")
        with sf.local_path.open("rb") as fh:
            uploaded = client.beta.files.upload(file=(sf.label, fh, content_type))
        out.append(
            UploadedFile(
                local_path=sf.local_path,
                label=sf.label,
                size_bytes=sf.size_bytes,
                anthropic_file_id=uploaded.id,
            )
        )
    return out


def delete_file(file_id: str) -> None:
    """Best-effort cleanup."""
    try:
        _get_client().beta.files.delete(file_id)
    except Exception:
        pass


# ─────────────────────── internal ───────────────────────


def _extract_zip(zip_path: Path, original_name: str, workdir: Path) -> list[StagedFile]:
    """Extract a zip into workdir, flattening nested paths and rejecting traversal."""
    out: list[StagedFile] = []
    workdir.mkdir(parents=True, exist_ok=True)
    zip_label_prefix = Path(original_name).stem  # "archive.zip" → "archive"

    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                # Reject path traversal.
                if info.filename.startswith("/") or ".." in Path(info.filename).parts:
                    raise IngestError(f"zip_path_traversal:{info.filename}")
                if info.file_size > MAX_FILE_BYTES:
                    raise IngestError(f"file_too_large_in_zip:{info.filename}")

                ext = Path(info.filename).suffix.lower()
                if ext not in ALLOWED_EXTENSIONS:
                    # Skip junk (e.g. macOS .DS_Store, __MACOSX) silently.
                    if info.filename.startswith("__MACOSX/") or Path(info.filename).name.startswith("."):
                        continue
                    raise IngestError(f"unsupported_type_in_zip:{info.filename}")

                # Flatten: "data/users.csv" → "<zipname>__data__users.csv".
                rel = info.filename.replace("\\", "/").lstrip("/")
                flat = rel.replace("/", "__")
                # Namespace by zip filename so multi-zip sessions don't collide.
                label = f"{zip_label_prefix}__{flat}" if zip_label_prefix else flat
                # Sanitize filesystem-unsafe chars in the host-side path.
                host_safe = label.replace(" ", "_").replace(":", "_")
                target = workdir / host_safe
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    # Bound the copy in case info.file_size lied (zip bomb).
                    written = 0
                    while True:
                        chunk = src.read(64 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_FILE_BYTES:
                            raise IngestError(f"zip_bomb_suspected:{info.filename}")
                        dst.write(chunk)
                out.append(
                    StagedFile(
                        local_path=target,
                        label=label,
                        size_bytes=target.stat().st_size,
                    )
                )
    except zipfile.BadZipFile as exc:
        raise IngestError(f"bad_zip:{original_name}") from exc

    if not out:
        raise IngestError(f"empty_zip:{original_name}")
    return out
