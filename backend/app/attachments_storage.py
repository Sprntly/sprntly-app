"""Storage for chat-turn file attachments — store the ORIGINAL uploaded file so a
reopened conversation can render it back (PDF/image inline, anything downloadable).

Distinct from routes/ask.py's extract-file, which reads a document to markdown and
throws the bytes away: here we persist the raw bytes to Supabase Storage (the
`prototypes` bucket, shared with the design-agent) under a per-workspace prefix,
and hand back a short-lived SIGNED URL the browser loads directly (the same
"Bearer-authed endpoint returns a public URL for the iframe" pattern the OAuth
start + bundle share use). Falls back to the local filesystem when no bucket is
configured (dev/test), mirroring design_agent/storage.py's dual-backend contract.

Key layout: `chat-attachments/{workspace_id}/{uuid4}.{ext}` — the workspace prefix
is the isolation primitive, enforced on every signed-url read.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# 25 MB — generous for a PDF/deck, small enough to keep a signed-URL render snappy.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

# A viewed attachment's signed URL lives 1h — long enough to open + read, short
# enough that a leaked URL expires quickly. Re-minted on every viewer open.
_SIGNED_URL_TTL_SECONDS = 3600

# Extension → media type. Covers the composer's `accept` set (docs + images).
# The media type is what the browser uses to decide inline rendering (application/
# pdf and image/* render; office types download).
_MEDIA_TYPE_BY_EXT: dict[str, str] = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain",
    "md": "text/markdown",
    "csv": "text/csv",
    "json": "application/json",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


def ext_of(filename: str) -> str:
    """Lowercased extension without the dot, or '' when the name has none."""
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def is_supported_ext(ext: str) -> bool:
    return ext in _MEDIA_TYPE_BY_EXT


def media_type_for_key(key: str) -> str:
    """Media type from a stored key's extension (the write side is the only key
    producer, so the extension is trusted). Falls back to octet-stream."""
    return _MEDIA_TYPE_BY_EXT.get(ext_of(key), "application/octet-stream")


def _bucket_name() -> str | None:
    """Supabase Storage bucket, or None → filesystem fallback (dev/test)."""
    return (os.environ.get("SUPABASE_STORAGE_BUCKET") or "").strip() or None


def _prefix(workspace_id: str) -> str:
    return f"chat-attachments/{workspace_id}/"


async def stage_attachment(*, workspace_id: str, data: bytes, ext: str) -> str:
    """Write the raw file bytes to storage; return the object key.

    Raises ValueError on a missing workspace or an unsupported extension (both
    programming errors — the route validates before calling)."""
    if not workspace_id:
        raise ValueError("stage_attachment: workspace_id is required")
    if not is_supported_ext(ext):
        raise ValueError(f"stage_attachment: unsupported extension {ext!r}")
    key = f"{_prefix(workspace_id)}{uuid.uuid4()}.{ext}"
    media_type = _MEDIA_TYPE_BY_EXT[ext]

    bucket = _bucket_name()
    if bucket:
        await asyncio.to_thread(_stage_supabase_sync, bucket, key, data, media_type)
        backend = "supabase"
    else:
        await asyncio.to_thread(_stage_filesystem_sync, key, data)
        backend = "filesystem"
    logger.info(
        "chat_attachment_uploaded workspace_present=%s key_suffix=%s size_bytes=%s backend=%s",
        bool(workspace_id), key.rsplit("/", 1)[-1], len(data), backend,
    )
    return key


def attachment_urls(*, workspace_id: str, key: str, filename: str) -> dict[str, str]:
    """Fresh signed (view + download) URLs for a stored attachment key.

    REFUSES any key outside `chat-attachments/{workspace_id}/` — a cross-workspace
    read is an isolation bug, so it raises ValueError. `filename` sets the
    download's Content-Disposition so a saved file keeps its real name (not the
    uuid key). Sync (the route is sync `def`, runs in FastAPI's threadpool)."""
    prefix = _prefix(workspace_id)
    if not workspace_id or not key.startswith(prefix):
        raise ValueError("attachment_urls: key is outside the caller's workspace prefix")
    if ".." in key.split("/"):
        raise ValueError("attachment_urls: key contains a traversal segment")

    bucket = _bucket_name()
    if not bucket:
        # Filesystem/dev fallback: a stable file:// URL that never expires.
        target = (Path(settings.storage_dir).resolve() / key)
        uri = target.as_uri()
        return {"view_url": uri, "download_url": uri}
    return _signed_urls_supabase(bucket, key, filename)


# ─── Supabase backend ────────────────────────────────────────────────────────


def _stage_supabase_sync(bucket: str, key: str, data: bytes, media_type: str) -> None:
    from app.db.client import require_client

    storage = require_client().storage.from_(bucket)
    storage.upload(
        path=key,
        file=data,
        file_options={"content-type": media_type, "upsert": "true"},
    )


def _signed_urls_supabase(bucket: str, key: str, filename: str) -> dict[str, str]:
    from app.db.client import require_client

    storage = require_client().storage.from_(bucket)
    view = _extract_signed_url(
        storage.create_signed_url(path=key, expires_in=_SIGNED_URL_TTL_SECONDS)
    )
    # Download URL carries a Content-Disposition: attachment; filename=... so the
    # saved file keeps its real name. Older supabase-py may not accept `options` —
    # fall back to the view URL rather than 500.
    try:
        download = _extract_signed_url(
            storage.create_signed_url(
                path=key,
                expires_in=_SIGNED_URL_TTL_SECONDS,
                options={"download": filename or True},
            )
        )
    except TypeError:
        download = view
    return {"view_url": view, "download_url": download or view}


def _extract_signed_url(signed: Any) -> str:
    """Pull the URL out of create_signed_url across supabase-py response shapes."""
    if isinstance(signed, dict):
        return signed.get("signedURL") or signed.get("signed_url") or signed.get("signedUrl") or ""
    return ""


# ─── Filesystem backend (dev/test) ───────────────────────────────────────────


def _stage_filesystem_sync(key: str, data: bytes) -> None:
    target = Path(settings.storage_dir).resolve() / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
