"""HTTP layer for dataset onboarding.

  GET    /v1/datasets               -> list
  POST   /v1/datasets                -> create {slug, display_name}
  POST   /v1/datasets/{slug}/files   -> multipart upload (one or more files)
  POST   /v1/datasets/{slug}/generate -> kick brief generation (async)
  DELETE /v1/datasets/{slug}          -> remove DB row (files left in place)

All routes require the demo session cookie.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Cookie, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app import datasets
from app.auth import require_session
from app.brief_runner import auto_generate_brief
from app.ingest import UnsupportedFileType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/datasets", tags=["datasets"])

# 20 MB hard cap per file. Docx/xlsx/pdf at this size already strain the LLM
# context window once converted; bigger files are almost always wrong-format.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class CreateDatasetIn(BaseModel):
    slug: str
    display_name: str


@router.get("")
def list_all(sprintly_session: Annotated[str | None, Cookie()] = None):
    require_session(sprintly_session)
    return {"datasets": datasets.list_datasets()}


@router.post("")
def create(
    body: CreateDatasetIn,
    sprintly_session: Annotated[str | None, Cookie()] = None,
):
    require_session(sprintly_session)
    try:
        out = datasets.create_dataset(slug=body.slug, display_name=body.display_name)
    except datasets.DatasetAlreadyExists as e:
        raise HTTPException(409, str(e))
    except datasets.InvalidSlug as e:
        raise HTTPException(422, str(e))
    except datasets.DatasetError as e:
        raise HTTPException(400, str(e))
    return out


@router.post("/{slug}/files")
async def upload_files(
    slug: str,
    files: Annotated[list[UploadFile], File(description="Source files to ingest")],
    sprintly_session: Annotated[str | None, Cookie()] = None,
):
    """Accept one or more files; convert each to markdown; persist both.

    Partial success is acceptable: if 4 of 5 files convert and 1 fails, the
    response includes a per-file result so the frontend can show ✓/✗ on each.
    """
    require_session(sprintly_session)
    if not datasets.dataset_path(slug).exists():
        # Hit the DB too — folder might exist from a stale dir without a row.
        try:
            datasets.validate_slug(slug)
        except datasets.InvalidSlug as e:
            raise HTTPException(422, str(e))
    if not files:
        raise HTTPException(400, "No files uploaded")

    results: list[dict] = []
    errors: list[dict] = []
    for upload in files:
        filename = upload.filename or "untitled"
        data = await upload.read()
        if len(data) > MAX_UPLOAD_BYTES:
            errors.append({
                "filename": filename,
                "error": f"File exceeds {MAX_UPLOAD_BYTES // (1024*1024)}MB limit",
            })
            continue
        try:
            ingested = datasets.ingest_file(slug, filename, data)
        except datasets.DatasetNotFound as e:
            raise HTTPException(404, str(e))
        except UnsupportedFileType as e:
            errors.append({"filename": filename, "error": str(e)})
            continue
        except Exception as e:  # pragma: no cover — surfaced to the user
            logger.exception("Ingest failed for %s/%s", slug, filename)
            errors.append({"filename": filename, "error": f"Conversion failed: {e}"})
            continue
        results.append({
            "filename": ingested.original_filename,
            "md_path": ingested.md_path,
            "md_chars": ingested.md_chars,
        })
    return {"slug": slug, "ingested": results, "errors": errors}


@router.post("/{slug}/generate")
async def generate(
    slug: str,
    sprintly_session: Annotated[str | None, Cookie()] = None,
):
    """Fire-and-forget brief generation. Frontend polls /v1/brief/status?dataset=slug."""
    require_session(sprintly_session)
    from app import db
    if not db.dataset_exists(slug):
        raise HTTPException(404, f"Dataset {slug!r} does not exist")
    asyncio.create_task(auto_generate_brief(slug))
    return {"started": True, "dataset": slug}


@router.delete("/{slug}")
def delete(
    slug: str,
    sprintly_session: Annotated[str | None, Cookie()] = None,
):
    require_session(sprintly_session)
    from app import db
    if not db.delete_dataset(slug):
        raise HTTPException(404, f"Dataset {slug!r} does not exist")
    return {"deleted": True, "slug": slug}
