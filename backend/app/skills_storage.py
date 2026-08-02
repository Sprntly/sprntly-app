"""Storage for custom-skill upload files — keep the ORIGINAL uploaded .md/.zip so
the skill library can offer a view/download of exactly what the uploader sent.

The PARSED skill content (the markdown that gets injected into prompts) lives in
the `custom_skills` table (db/custom_skills.py); this module only persists the
raw upload bytes to Supabase Storage (the `prototypes` bucket, shared with the
design-agent and chat attachments) under a per-company prefix, and hands back
short-lived SIGNED URLs. Falls back to the local filesystem when no bucket is
configured (dev/test), mirroring attachments_storage.py's dual-backend contract.

Key layout: `custom-skills/{company_id}/{uuid4}.{ext}` — the company prefix is
the isolation primitive, enforced on every signed-url read. Company-level (not
workspace-level) because custom skills are company-scoped for now — all
workspaces in a company share one skill library (see the custom_skills
migration).
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

# 20 MB — the PRD 1854 hard cap for any skill upload (.md or .zip), enforced
# before processing. Deliberately tighter than attachments_storage's 25 MB.
MAX_SKILL_UPLOAD_BYTES = 20 * 1024 * 1024

# A signed URL lives 1h — long enough to open + read, short enough that a
# leaked URL expires quickly. Re-minted on every viewer open.
_SIGNED_URL_TTL_SECONDS = 3600

# The only accepted upload formats (PRD R1/R6). Extension → media type.
_MEDIA_TYPE_BY_EXT: dict[str, str] = {
    "md": "text/markdown",
    "zip": "application/zip",
}


def ext_of(filename: str) -> str:
    """Lowercased extension without the dot, or '' when the name has none."""
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def is_supported_ext(ext: str) -> bool:
    return ext in _MEDIA_TYPE_BY_EXT


def _bucket_name() -> str | None:
    """Supabase Storage bucket, or None → filesystem fallback (dev/test)."""
    return (os.environ.get("SUPABASE_STORAGE_BUCKET") or "").strip() or None


def _prefix(company_id: str) -> str:
    return f"custom-skills/{company_id}/"


async def stage_skill_file(*, company_id: str, data: bytes, ext: str) -> str:
    """Write the raw upload bytes to storage; return the object key.

    Raises ValueError on a missing company or an unsupported extension (both
    programming errors — the route validates before calling)."""
    if not company_id:
        raise ValueError("stage_skill_file: company_id is required")
    if not is_supported_ext(ext):
        raise ValueError(f"stage_skill_file: unsupported extension {ext!r}")
    key = f"{_prefix(company_id)}{uuid.uuid4()}.{ext}"
    media_type = _MEDIA_TYPE_BY_EXT[ext]

    bucket = _bucket_name()
    if bucket:
        await asyncio.to_thread(_stage_supabase_sync, bucket, key, data, media_type)
        backend = "supabase"
    else:
        await asyncio.to_thread(_stage_filesystem_sync, key, data)
        backend = "filesystem"
    logger.info(
        "custom_skill_file_uploaded company_present=%s key_suffix=%s size_bytes=%s backend=%s",
        bool(company_id), key.rsplit("/", 1)[-1], len(data), backend,
    )
    return key


async def delete_skill_file(*, company_id: str, key: str) -> None:
    """Best-effort removal of a staged upload (insert-failure rollback now; the
    delete-skill ticket reuses it later). Refuses cross-company keys; never
    raises on backend errors — an orphaned object is logged, not fatal."""
    prefix = _prefix(company_id)
    if not company_id or not key.startswith(prefix) or ".." in key.split("/"):
        raise ValueError("delete_skill_file: key is outside the caller's company prefix")
    bucket = _bucket_name()
    try:
        if bucket:
            await asyncio.to_thread(_delete_supabase_sync, bucket, key)
        else:
            await asyncio.to_thread(_delete_filesystem_sync, key)
    except Exception:  # noqa: BLE001 — best-effort cleanup
        logger.warning("delete_skill_file failed for key_suffix=%s; object orphaned",
                       key.rsplit("/", 1)[-1], exc_info=True)


def skill_file_urls(*, company_id: str, key: str, filename: str) -> dict[str, str]:
    """Fresh signed (view + download) URLs for a stored upload key.

    REFUSES any key outside `custom-skills/{company_id}/` — a cross-company
    read is an isolation bug, so it raises ValueError. `filename` sets the
    download's Content-Disposition so a saved file keeps its real name (not the
    uuid key). Sync (the route is sync `def`, runs in FastAPI's threadpool)."""
    prefix = _prefix(company_id)
    if not company_id or not key.startswith(prefix):
        raise ValueError("skill_file_urls: key is outside the caller's company prefix")
    if ".." in key.split("/"):
        raise ValueError("skill_file_urls: key contains a traversal segment")

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


def _delete_supabase_sync(bucket: str, key: str) -> None:
    from app.db.client import require_client

    require_client().storage.from_(bucket).remove([key])


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


def _delete_filesystem_sync(key: str) -> None:
    (Path(settings.storage_dir).resolve() / key).unlink(missing_ok=True)
