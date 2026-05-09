from fastapi import APIRouter, Cookie, HTTPException

from app.auth import require_session
from app.corpus import load_corpus
from app.db import get_brief_by_id, get_current_brief, save_brief
from app.llm import call_json
from app.prompts import BRIEF_SYSTEM, BRIEF_USER_TEMPLATE

router = APIRouter(prefix="/v1/brief", tags=["brief"])


@router.get("/current")
def current(
    dataset: str = "asurion",
    sprintly_session: str | None = Cookie(default=None),
):
    require_session(sprintly_session)
    brief = get_current_brief(dataset)
    if not brief:
        raise HTTPException(404, "No brief generated yet")
    return brief


@router.get("/{brief_id}")
def by_id(
    brief_id: int,
    sprintly_session: str | None = Cookie(default=None),
):
    require_session(sprintly_session)
    brief = get_brief_by_id(brief_id)
    if not brief:
        raise HTTPException(404, "Brief not found")
    return brief


@router.post("/generate")
def generate(
    dataset: str = "asurion",
    sprintly_session: str | None = Cookie(default=None),
):
    """Pre-compute a fresh brief for a dataset. Caches the result.

    Note: invokes Claude. Costs tokens. Intended for ops, not normal user flow.
    """
    require_session(sprintly_session)
    corpus = load_corpus(dataset)
    user = BRIEF_USER_TEMPLATE.format(dataset=dataset, corpus=corpus.joined())
    payload = call_json(system=BRIEF_SYSTEM, user=user)
    week_label = payload.get("week_label", "")
    brief_id = save_brief(dataset=dataset, week_label=week_label, payload=payload)
    return {"brief_id": brief_id, **payload}
