"""HTTP layer for dataset onboarding.

  GET    /v1/datasets                       -> list
  POST   /v1/datasets                        -> create {slug, display_name}
  GET    /v1/datasets/{slug}/files           -> list source files for a dataset
  POST   /v1/datasets/{slug}/files           -> multipart upload (one or more files)
  DELETE /v1/datasets/{slug}/files/{filename}-> remove a single source file
  POST   /v1/datasets/{slug}/generate        -> kick brief generation (async)
  DELETE /v1/datasets/{slug}                  -> remove DB row (files left in place)

All routes require the demo session cookie.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app import datasets
from app.auth import CompanyContext, require_company
from app.brief_runner import auto_generate_brief
from app.config import settings
from app.db.companies import slug_for_company_id
from app.deps.ownership import require_owned_dataset
from app.ingest import UnsupportedFileType, md_filename

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/datasets", tags=["datasets"])

# Strong refs to in-flight background generation tasks (see routes/design_agent.py):
# without this, the bare create_task result can be garbage-collected mid-run.
_inflight_tasks: set[asyncio.Task] = set()

# 20 MB hard cap per file. Docx/xlsx/pdf at this size already strain the LLM
# context window once converted; bigger files are almost always wrong-format.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _ensure_owned_dataset(slug: str, company_id: str) -> None:
    """Tenant gate + lazy registration. `require_owned_dataset` first proves the
    slug maps to the CALLER'S OWN company (404 otherwise — no cross-tenant
    disclosure). Onboarding creates the company but not always its `datasets`
    row, so if the row is missing for this verified-own slug we register it
    (idempotent insert). This never creates a dataset for a slug the caller
    doesn't own — the ownership check runs first and 404s on any mismatch.
    """
    require_owned_dataset(slug, company_id)
    from app import db
    if not db.dataset_exists(slug):
        db.insert_dataset(slug, slug)


class CreateDatasetIn(BaseModel):
    slug: str
    display_name: str


@router.get("")
def list_all(company: CompanyContext = Depends(require_company)):
    """List ONLY the caller's company's dataset(s).

    A dataset slug IS a company slug, so the caller's company maps to exactly
    one owned slug. We filter the full table down to rows the caller owns rather
    than returning every tenant's datasets (the pre-fix behaviour leaked the
    full tenant roster). Returns [] when the company has no dataset row yet.
    """
    owned_slug = slug_for_company_id(company.company_id)
    rows = datasets.list_datasets() if owned_slug else []
    mine = [d for d in rows if d.get("slug") == owned_slug]
    return {"datasets": mine}


@router.post("")
def create(
    body: CreateDatasetIn,
    company: CompanyContext = Depends(require_company),
):
    # Format first (422), then the tenant gate: a dataset slug IS a company
    # slug, and onboarding (not this route) is what creates companies. Only
    # the caller's own slug may be registered here — otherwise any signed-in
    # user could mint dataset rows for arbitrary slugs (including ones a
    # future tenant would claim).
    try:
        slug = datasets.validate_slug(body.slug)
    except datasets.InvalidSlug as e:
        raise HTTPException(422, str(e))
    if slug != slug_for_company_id(company.company_id):
        raise HTTPException(
            403, "Datasets can only be created for your own company"
        )
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
    company: CompanyContext = Depends(require_company),
):
    """Accept one or more files; convert each to markdown; persist both.

    Partial success is acceptable: if 4 of 5 files convert and 1 fails, the
    response includes a per-file result so the frontend can show ✓/✗ on each.
    """
    # Tenant gate (404 if not the caller's company) + lazily register the
    # caller's own dataset row if onboarding hasn't created it yet.
    _ensure_owned_dataset(slug, company.company_id)
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


@router.get("/{slug}/files")
def list_files(
    slug: str,
    company: CompanyContext = Depends(require_company),
):
    """List source files (raw originals) for a dataset, newest first."""
    # Tenant gate (404 if not the caller's company) + lazily register the
    # caller's own dataset row if onboarding hasn't created it yet, so the
    # Sources page shows an empty list rather than "dataset does not exist".
    _ensure_owned_dataset(slug, company.company_id)

    raw_dir = datasets.raw_path(slug)
    base_dir = datasets.dataset_path(slug)
    files: list[dict] = []
    if raw_dir.exists():
        for p in raw_dir.iterdir():
            if not p.is_file():
                continue
            # Compute md_chars by checking the converted .md sibling. The
            # ingest path uses md_filename(original) as the base name and
            # appends .1, .2, … on collisions. We can't know which numbered
            # sibling belongs to which raw upload (the mapping isn't stored),
            # so we report the canonical name first, then fall back to
            # numbered variants. Practically there's one .md per upload.
            md_chars = 0
            md_base = md_filename(p.name)
            md_candidates = [base_dir / md_base]
            stem = Path(md_base).stem
            for n in range(1, 11):
                md_candidates.append(base_dir / f"{stem}.{n}.md")
            for md_path in md_candidates:
                if md_path.exists():
                    try:
                        md_chars = len(md_path.read_text())
                    except Exception:  # pragma: no cover — unreadable .md
                        md_chars = 0
                    break

            stat = p.stat()
            files.append({
                "filename": p.name,
                "kind": p.suffix.lower().lstrip("."),
                "size_bytes": stat.st_size,
                "md_chars": md_chars,
                "added_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            })

    files.sort(key=lambda f: f["added_at"], reverse=True)
    return {"slug": slug, "files": files}


@router.delete("/{slug}/files/{filename}")
def delete_file(
    slug: str,
    filename: str,
    company: CompanyContext = Depends(require_company),
):
    """Remove one source file: the raw original plus any matching .md siblings."""
    require_owned_dataset(slug, company.company_id)
    # Defense in depth — reject anything that isn't a plain basename. FastAPI
    # already prevents path segments in {filename}, but a bare ".." or a
    # dotfile would slip through path validation.
    if filename != Path(filename).name or filename.startswith("."):
        raise HTTPException(422, "filename must be a plain basename")

    from app import db
    if not db.dataset_exists(slug):
        raise HTTPException(404, f"Dataset {slug!r} does not exist")

    raw_target = datasets.raw_path(slug) / filename
    if not raw_target.exists() or not raw_target.is_file():
        raise HTTPException(404, f"File {filename!r} not found in dataset {slug!r}")

    raw_target.unlink()
    raw_removed = True

    base_dir = datasets.dataset_path(slug)
    md_base = md_filename(filename)
    md_removed = False
    md_candidates = [base_dir / md_base]
    stem = Path(md_base).stem
    for n in range(1, 11):
        md_candidates.append(base_dir / f"{stem}.{n}.md")
    for md_path in md_candidates:
        if md_path.exists() and md_path.is_file():
            md_path.unlink()
            md_removed = True

    return {
        "slug": slug,
        "filename": filename,
        "removed": {"raw": raw_removed, "md": md_removed},
    }


@router.post("/{slug}/generate")
async def generate(
    slug: str,
    company: CompanyContext = Depends(require_company),
):
    """Fire-and-forget brief generation. Frontend polls /v1/brief/status?dataset=slug.

    Honors BRIEF_ENGINE so a newly-created dataset produces the SAME engine's
    brief as /regenerate + the scheduler: "synthesis" (default) runs the KG
    seed-if-empty → run_synthesis path (+ drill-down warming); "legacy" keeps
    the corpus→Claude auto_generate_brief.
    """
    require_owned_dataset(slug, company.company_id)
    from app import db
    if not db.dataset_exists(slug):
        raise HTTPException(404, f"Dataset {slug!r} does not exist")
    if settings.brief_engine == "synthesis":
        # Reuse the brief route's synthesis background body (run_synthesis +
        # warm-drilldowns, error-isolated) so both write paths stay identical.
        from app.routes.brief import _synthesis_generate_bg
        task = asyncio.create_task(_synthesis_generate_bg(slug))
    else:
        task = asyncio.create_task(auto_generate_brief(slug))
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
    return {"started": True, "dataset": slug}


@router.delete("/{slug}")
def delete(
    slug: str,
    company: CompanyContext = Depends(require_company),
):
    require_owned_dataset(slug, company.company_id)
    from app import db
    if not db.delete_dataset(slug):
        raise HTTPException(404, f"Dataset {slug!r} does not exist")
    return {"deleted": True, "slug": slug}
