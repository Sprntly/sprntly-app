"""Evidence Page endpoints.

Trigger via:

    POST /v1/evidence/generate  {"brief_id": N, "insight_index": M, "force": false}
    GET  /v1/evidence/{evidence_id}

The POST is fire-and-forget: it inserts a row in `generating` state,
schedules `generate_evidence` in the background, and returns the
evidence_id immediately. Poll the GET until status == 'ready'.

Rows live in the `evidences` table. New rows are stored with
variant='v2' (the current evidence format); historical v1 rows from
before the promotion remain readable but are no longer generated. The
GET is permissive — it returns any row by id regardless of variant so
old bookmarks keep resolving.
"""
import asyncio

from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.auth import require_session
from app.config import settings
from app.db import (
    find_existing_evidence,
    get_brief_by_id,
    get_evidence,
    start_evidence,
)
from app.evidence_kg import generate_evidence_kg
from app.evidence_runner import generate_evidence
from app.prompts import EVIDENCE_TEMPLATE_VERSION

router = APIRouter(prefix="/v1/evidence", tags=["evidence"])

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
    """Kick off evidence generation in the background.

    Returns immediately with the evidence_id. If a ready/generating doc
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
        template_version=EVIDENCE_TEMPLATE_VERSION,
        variant=_VARIANT,
    )
    # Engine selection (BRIEF_ENGINE): "synthesis" (default) grounds evidence
    # in the knowledge graph — the provenance trail (SUPPORTS signals + theme
    # convergence) behind the insight. generate_evidence_kg itself falls back
    # to the legacy corpus path when the KG has no backing for the insight, so
    # this never hard-fails. "legacy" keeps the corpus-only runner.
    runner = (
        generate_evidence_kg
        if settings.brief_engine == "synthesis"
        else generate_evidence
    )
    asyncio.create_task(
        runner(evidence_id, body.brief_id, body.insight_index)
    )
    return {
        "evidence_id": evidence_id,
        "status": "generating",
        "title": title,
        "variant": _VARIANT,
    }


@router.get("/{evidence_id}")
def get(
    evidence_id: int,
    _session: dict = Depends(require_session),
):
    """Fetch an evidence row by id.

    Permissive on variant — historical v1 rows still resolve so old
    bookmarks don't 409. The `variant` field on the response identifies
    which format the row was generated under.
    """
    row = get_evidence(evidence_id)
    if not row:
        raise HTTPException(404, "Evidence not found")
    return row
