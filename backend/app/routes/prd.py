import json

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel

from app.auth import require_session
from app.corpus import load_corpus, load_prd_template
from app.db import get_brief_by_id, get_prd, save_prd
from app.llm import call_md
from app.prompts import PRD_SYSTEM, PRD_USER_TEMPLATE

router = APIRouter(prefix="/v1/prd", tags=["prd"])


class GenerateIn(BaseModel):
    brief_id: int
    insight_index: int  # 0-based index into brief.insights


@router.post("/generate")
def generate(
    body: GenerateIn,
    sprintly_session: str | None = Cookie(default=None),
):
    require_session(sprintly_session)
    brief = get_brief_by_id(body.brief_id)
    if not brief:
        raise HTTPException(404, "Brief not found")
    insights = brief.get("insights") or []
    if not (0 <= body.insight_index < len(insights)):
        raise HTTPException(400, "insight_index out of range")
    insight = insights[body.insight_index]

    corpus = load_corpus(brief.get("dataset", "asurion"))
    template = load_prd_template()
    user = PRD_USER_TEMPLATE.format(
        insight_json=json.dumps(insight, indent=2),
        corpus=corpus.joined(),
        template=template,
    )
    md = call_md(system=PRD_SYSTEM, user=user)
    title = insight.get("title") or f"Insight #{body.insight_index + 1}"
    prd_id = save_prd(
        brief_id=body.brief_id,
        insight_index=body.insight_index,
        title=title,
        md=md,
    )
    return {"prd_id": prd_id, "title": title, "markdown": md}


@router.get("/{prd_id}")
def get(prd_id: int, sprintly_session: str | None = Cookie(default=None)):
    require_session(sprintly_session)
    prd = get_prd(prd_id)
    if not prd:
        raise HTTPException(404, "PRD not found")
    return prd
