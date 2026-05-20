"""Sample-build endpoints for the v2 evidence format.

Runs side-by-side with /v1/evidence/* (v1 stays untouched). Rows live in
the same `evidences` table, distinguished by the `variant` column. Trigger
via:

    POST /v1/evidence/v2/generate  {"brief_id": N, "insight_index": M, "force": false}
    GET  /v1/evidence/v2/{evidence_id}

The POST is fire-and-forget: it inserts a row in `generating` state,
schedules `generate_evidence_v2` in the background, and returns the
evidence_id immediately. Poll the GET until status == 'ready'.
"""
import asyncio

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel, Field

from app.auth import require_session
from app.db import (
    find_existing_evidence,
    get_brief_by_id,
    get_evidence,
    start_evidence,
)
from app.evidence_runner import generate_evidence_v2
from app.prompts import EVIDENCE_V2_TEMPLATE_VERSION

router = APIRouter(prefix="/v1/evidence/v2", tags=["evidence-v2"])

_VARIANT = "v2"


class GenerateV2In(BaseModel):
    brief_id: int = Field(..., ge=1)
    insight_index: int = Field(..., ge=0)
    force: bool = False


@router.post("/generate")
async def generate_v2(
    body: GenerateV2In,
    sprintly_session: str | None = Cookie(default=None),
):
    """Kick off v2 evidence generation in the background.

    Returns immediately with the evidence_id. If a ready/generating v2
    doc already exists for (brief, insight) and `force` is false, returns
    the existing row — same dedupe semantics as v1.
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
        existing = find_existing_evidence(
            body.brief_id, body.insight_index, variant=_VARIANT
        )
        if existing:
            return {
                "evidence_id": existing["id"],
                "status": existing["status"],
                "title": existing["title"],
                "variant": _VARIANT,
            }

    insight = insights[body.insight_index]
    title = insight.get("title") or f"Insight #{body.insight_index + 1}"
    evidence_id = start_evidence(
        brief_id=body.brief_id,
        insight_index=body.insight_index,
        title=title,
        template_version=EVIDENCE_V2_TEMPLATE_VERSION,
        variant=_VARIANT,
    )
    asyncio.create_task(
        generate_evidence_v2(evidence_id, body.brief_id, body.insight_index)
    )
    return {
        "evidence_id": evidence_id,
        "status": "generating",
        "title": title,
        "variant": _VARIANT,
    }


@router.get("/{evidence_id}")
def get_v2(
    evidence_id: int,
    sprintly_session: str | None = Cookie(default=None),
):
    """Fetch a v2 evidence row. 404 if missing; 409 if the id belongs to v1.

    The v1 endpoint (GET /v1/evidence/{id}) can also read v2 rows since
    they share a table, but this v2 endpoint enforces the variant so a
    caller that asks for v2 by id doesn't accidentally get a v1 doc.
    """
    require_session(sprintly_session)
    row = get_evidence(evidence_id)
    if not row:
        raise HTTPException(404, "Evidence not found")
    if row.get("variant") != _VARIANT:
        raise HTTPException(
            409,
            f"evidence_id={evidence_id} is variant={row.get('variant')!r}, "
            f"not {_VARIANT!r}",
        )
    return row
