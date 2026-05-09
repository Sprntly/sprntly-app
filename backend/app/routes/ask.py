from fastapi import APIRouter, Cookie
from pydantic import BaseModel, Field

from app.auth import require_session
from app.corpus import load_corpus
from app.db import log_ask
from app.llm import call_json
from app.prompts import ASK_SYSTEM, ASK_USER_TEMPLATE

router = APIRouter(prefix="/v1/ask", tags=["ask"])


class AskIn(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)
    dataset: str = "asurion"


@router.post("")
def ask(
    body: AskIn,
    sprintly_session: str | None = Cookie(default=None),
):
    require_session(sprintly_session)
    corpus = load_corpus(body.dataset)
    user = ASK_USER_TEMPLATE.format(question=body.question, corpus=corpus.joined())
    payload = call_json(system=ASK_SYSTEM, user=user)
    log_ask(
        question=body.question,
        answer=payload.get("answer", ""),
        citations=payload.get("citations", []),
    )
    return payload
