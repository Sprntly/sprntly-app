import asyncio

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel

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


class GenerateIn(BaseModel):
    brief_id: int
    insight_index: int  # 0-based index into brief.insights
    force: bool = False  # if true, ignore any existing PRD and generate a fresh one


@router.post("/generate")
async def generate(
    body: GenerateIn,
    sprintly_session: str | None = Cookie(default=None),
):
    """Kick off PRD generation as a background task.

    Returns immediately with `prd_id` and `status` ('generating' or 'ready').
    Client should poll GET /v1/prd/{id} until status is 'ready' or 'failed'.

    If a ready or in-flight PRD already exists for (brief_id, insight_index),
    returns it instead — unless `force=true`.
    """
    require_session(sprintly_session)

    brief = get_brief_by_id(body.brief_id)
    if not brief:
        raise HTTPException(404, "Brief not found")
    insights = brief.get("insights") or []
    if not (0 <= body.insight_index < len(insights)):
        raise HTTPException(400, "insight_index out of range")
    insight = insights[body.insight_index]

    if not body.force:
        existing = find_existing_prd(body.brief_id, body.insight_index)
        if existing:
            return {
                "prd_id": existing["id"],
                "status": existing["status"],
                "title": existing["title"],
            }

    title = insight.get("title") or f"Insight #{body.insight_index + 1}"
    prd_id = start_prd(
        brief_id=body.brief_id,
        insight_index=body.insight_index,
        title=title,
        template_version=PRD_TEMPLATE_VERSION,
    )
    asyncio.create_task(generate_prd(prd_id, body.brief_id, body.insight_index))
    return {"prd_id": prd_id, "status": "generating", "title": title}


@router.get("/{prd_id}")
def get(prd_id: int, sprintly_session: str | None = Cookie(default=None)):
    """Fetch a PRD by id. Includes status: 'ready' | 'generating' | 'failed'.

    `payload_md` is empty unless status == 'ready'.
    """
    require_session(sprintly_session)
    prd = get_prd(prd_id)
    if not prd:
        raise HTTPException(404, "PRD not found")
    return prd
