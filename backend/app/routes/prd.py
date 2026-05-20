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

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel, Field

from app.auth import require_session
from app.db import (
    find_existing_prd,
    get_brief_by_id,
    get_prd,
    start_prd,
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
    sprintly_session: str | None = Cookie(default=None),
):
    """Kick off PRD generation in the background.

    Returns immediately with the prd_id. If a ready/generating PRD
    already exists for (brief, insight) and `force` is false, returns
    the existing row.
    """
    require_session(sprintly_session)

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
    sprintly_session: str | None = Cookie(default=None),
):
    """Fetch a PRD row by id.

    Permissive on variant — historical v1 rows still resolve so old
    bookmarks don't 409. The `variant` field on the response identifies
    which format the row was generated under.
    """
    require_session(sprintly_session)
    row = get_prd(prd_id)
    if not row:
        raise HTTPException(404, "PRD not found")
    return row
