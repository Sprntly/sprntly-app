"""PRD endpoints.

Trigger via:

    POST /v1/prd/generate  {"brief_id": N, "insight_index": M, "force": false}
    GET  /v1/prd/{prd_id}

The POST is fire-and-forget: it inserts a row in `generating` state,
schedules `generate_prd` in the background, and returns the prd_id
immediately. Poll the GET until status == 'ready'.

Rows live in the `prds` table. New rows are stored with variant='v2'
(the current PRD format); historical v1 rows from before the promotion
remain readable but are no longer generated. The GET is permissive —
it returns any row by id regardless of variant so old bookmarks keep
resolving.
"""
import asyncio

from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.auth import require_session
from app.db import (
    find_existing_prd,
    get_brief_by_id,
    get_prd_rendered,
    start_prd,
)
from app.db.prds import (
    get_prd,
    list_prd_versions,
    restore_prd_version,
    save_prd_version,
    update_prd_content,
)
from app.prd_runner import generate_prd
from app.prompts import PRD_TEMPLATE_VERSION

router = APIRouter(prefix="/v1/prd", tags=["prd"])

_VARIANT = "v2"


class GenerateIn(BaseModel):
    brief_id: int = Field(..., ge=1)
    insight_index: int = Field(..., ge=0)
    force: bool = False


@router.post("/generate")
async def generate(
    body: GenerateIn,
    _session: dict = Depends(require_session),
):
    """Kick off PRD generation in the background.

    Returns immediately with the prd_id. If a ready/generating PRD
    already exists for (brief, insight) and `force` is false, returns
    the existing row.
    """
    brief = get_brief_by_id(body.brief_id)
    if not brief:
        raise HTTPException(404, f"brief_id={body.brief_id} not found")
    insights = brief.get("insights") or []
    if not (0 <= body.insight_index < len(insights)):
        raise HTTPException(
            400,
            f"insight_index={body.insight_index} out of range "
            f"(0..{len(insights) - 1})",
        )

    if not body.force:
        existing = find_existing_prd(
            body.brief_id, body.insight_index, variant=_VARIANT
        )
        if existing:
            return {
                "prd_id": existing["id"],
                "status": existing["status"],
                "title": existing["title"],
                "variant": _VARIANT,
            }

    insight = insights[body.insight_index]
    title = insight.get("title") or f"Insight #{body.insight_index + 1}"
    prd_id = start_prd(
        brief_id=body.brief_id,
        insight_index=body.insight_index,
        title=title,
        template_version=PRD_TEMPLATE_VERSION,
        variant=_VARIANT,
    )
    asyncio.create_task(generate_prd(prd_id, body.brief_id, body.insight_index))
    return {
        "prd_id": prd_id,
        "status": "generating",
        "title": title,
        "variant": _VARIANT,
    }


@router.get("/{prd_id}")
def get(
    prd_id: int,
    _session: dict = Depends(require_session),
):
    """Fetch a PRD row by id."""
    row = get_prd_rendered(prd_id)
    if not row:
        raise HTTPException(404, "PRD not found")
    return row


# ── PRD editing + version control ──────────────────────────────────────


class PrdUpdateIn(BaseModel):
    title: str = Field(..., min_length=1)
    payload_md: str = Field(...)


@router.put("/{prd_id}")
def update(
    prd_id: int,
    body: PrdUpdateIn,
    _session: dict = Depends(require_session),
):
    """Save PRD edits to Supabase. Auto-creates a version snapshot."""
    row = get_prd(prd_id)
    if not row:
        raise HTTPException(404, "PRD not found")
    # Save current content as a version before overwriting
    try:
        save_prd_version(prd_id, row.get("title", ""), row.get("payload_md", ""), saved_by="auto")
    except Exception:
        pass  # version table may not exist yet — don't block the save
    updated = update_prd_content(prd_id, body.title, body.payload_md)
    return updated


class PrdVersionSaveIn(BaseModel):
    title: str = Field(..., min_length=1)
    payload_md: str = Field(...)
    label: str = Field("Manual save")


@router.post("/{prd_id}/versions")
def create_version(
    prd_id: int,
    body: PrdVersionSaveIn,
    _session: dict = Depends(require_session),
):
    """Explicitly save a named version of the PRD."""
    row = get_prd(prd_id)
    if not row:
        raise HTTPException(404, "PRD not found")
    version = save_prd_version(prd_id, body.title, body.payload_md, saved_by=body.label)
    return version


@router.get("/{prd_id}/versions")
def get_versions(
    prd_id: int,
    _session: dict = Depends(require_session),
):
    """List all versions of a PRD, newest first."""
    return list_prd_versions(prd_id)


@router.post("/{prd_id}/versions/{version_id}/restore")
def restore_version(
    prd_id: int,
    version_id: int,
    _session: dict = Depends(require_session),
):
    """Restore a PRD to a specific version."""
    result = restore_prd_version(prd_id, version_id)
    if not result:
        raise HTTPException(404, "Version not found")
    return result
