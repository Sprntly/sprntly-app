import asyncio

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel

from app.auth import require_session
from app.db import (
    find_existing_evidence,
    get_brief_by_id,
    get_evidence,
    start_evidence,
)
from app.evidence_runner import generate_evidence

router = APIRouter(prefix="/v1/evidence", tags=["evidence"])


class GenerateIn(BaseModel):
    brief_id: int
    insight_index: int  # 0-based index into brief.insights
    force: bool = False  # if true, ignore any existing doc and regenerate


@router.post("/generate")
async def generate(
    body: GenerateIn,
    sprintly_session: str | None = Cookie(default=None),
):
    """Kick off Evidence Page generation as a background task.

    Returns immediately with `evidence_id` and `status` ('generating' or
    'ready'). Client should poll GET /v1/evidence/{id} until status is
    'ready' or 'failed'.

    If a ready or in-flight evidence already exists for (brief_id,
    insight_index), returns it instead — unless `force=true`.
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
        existing = find_existing_evidence(body.brief_id, body.insight_index)
        if existing:
            return {
                "evidence_id": existing["id"],
                "status": existing["status"],
                "title": existing["title"],
            }

    title = insight.get("title") or f"Insight #{body.insight_index + 1}"
    evidence_id = start_evidence(
        brief_id=body.brief_id,
        insight_index=body.insight_index,
        title=title,
    )
    asyncio.create_task(
        generate_evidence(evidence_id, body.brief_id, body.insight_index)
    )
    return {"evidence_id": evidence_id, "status": "generating", "title": title}


@router.get("/{evidence_id}")
def get(evidence_id: int, sprintly_session: str | None = Cookie(default=None)):
    """Fetch an Evidence Page by id. Includes status: 'ready' | 'generating'
    | 'failed'.

    `payload_md` is empty unless status == 'ready'.
    """
    require_session(sprintly_session)
    ev = get_evidence(evidence_id)
    if not ev:
        raise HTTPException(404, "Evidence not found")
    return ev
