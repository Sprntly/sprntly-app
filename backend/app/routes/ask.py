import json
import random
import time

from fastapi import APIRouter, Cookie
from pydantic import BaseModel, Field

from app.auth import require_session
from app.corpus import load_corpus
from app.db import find_cached_ask, log_ask
from app.llm import call_json
from app.prompts import ASK_SYSTEM, ASK_USER_TEMPLATE_QUESTION_ONLY

router = APIRouter(prefix="/v1/ask", tags=["ask"])


# Pre-warmed cache hits return in <100ms — instantaneous responses break the
# demo illusion that the LLM is generating the answer in real time. A short
# random synthetic delay keeps the cached responses feeling generated. The
# frontend's "Thinking…" loader bridges this gap.
CACHE_HIT_DELAY_MIN_SECONDS = 5.0
CACHE_HIT_DELAY_MAX_SECONDS = 7.0


# Tool-use schema for the Ask endpoint. Defined here (not in prompts.py)
# because it's how we extract the response, not part of the prompt text.
# Letting the Anthropic SDK validate structured input avoids the
# JSON-string-escaping failures that happen when the LLM hand-writes JSON
# with markdown tables, quoted text, and pipes inside the answer field.
ASK_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "answer": {
            "type": "string",
            "description": "Markdown-formatted answer. Follow the formatting rules in the system prompt.",
        },
        "key_points": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short bullet summary of the answer.",
        },
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["source", "evidence"],
            },
        },
        "confidence": {"type": "number", "description": "0..1"},
        "unanswered": {
            "type": "string",
            "description": "Empty string if fully answered, else what data is missing.",
        },
    },
    "required": ["answer", "key_points", "citations", "confidence", "unanswered"],
}


class AskIn(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)
    dataset: str = "asurion"


@router.post("")
def ask(
    body: AskIn,
    sprintly_session: str | None = Cookie(default=None),
):
    require_session(sprintly_session)
    # 1) Cache hit short-circuit — the home + Ask Sprntly starter chips send
    # deterministic prompts pre-warmed at brief-generation time. Returns
    # without an LLM call, with a small random delay so the response
    # doesn't appear suspiciously instant.
    cached = find_cached_ask(body.dataset, body.question)
    if cached and cached.get("status") == "ready":
        try:
            payload = json.loads(cached["response_json"])
        except (TypeError, ValueError):
            # Corrupt cache row — fall through and regenerate.
            pass
        else:
            time.sleep(
                random.uniform(CACHE_HIT_DELAY_MIN_SECONDS, CACHE_HIT_DELAY_MAX_SECONDS)
            )
            return payload

    # 2) Cache miss → standard LLM call.
    corpus = load_corpus(body.dataset)
    # The corpus is constant per dataset, so we pass it as a cacheable prefix
    # — repeat /v1/ask calls within the cache TTL skip re-encoding it.
    cacheable = f"Corpus:\n\n{corpus.joined()}"
    user = ASK_USER_TEMPLATE_QUESTION_ONLY.format(question=body.question)
    # Lower than the call_json default (16k) — Ask answers run on the home
    # surface and load time matters more than max answer length. Brief and
    # PRD generation keep the default headroom.
    payload = call_json(
        system=ASK_SYSTEM,
        user=user,
        user_cacheable_prefix=cacheable,
        schema=ASK_RESPONSE_SCHEMA,
        max_tokens=12000,
    )
    log_ask(
        question=body.question,
        answer=payload.get("answer", ""),
        citations=payload.get("citations", []),
    )
    return payload
