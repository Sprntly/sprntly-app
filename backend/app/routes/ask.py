import json
import random
import time

from fastapi import Depends, APIRouter
from pydantic import BaseModel, Field

from app.ask_runner import compose_ask_answer
from app.auth import CompanyContext, require_session, resolve_company_optional
from app.db import find_cached_ask, log_ask

router = APIRouter(prefix="/v1/ask", tags=["ask"])


# Pre-warmed cache hits return in <100ms — instantaneous responses break the
# demo illusion that the LLM is generating the answer in real time. A short
# random synthetic delay keeps the cached responses feeling generated. The
# frontend's "Thinking…" loader bridges this gap.
CACHE_HIT_DELAY_MIN_SECONDS = 5.0
CACHE_HIT_DELAY_MAX_SECONDS = 7.0

# Briefly wait on a still-warming cache row before falling through to a
# parallel LLM call. After a backend restart the warming semaphore can be
# draining for ~30-60s; an early click would otherwise pay full generation
# cost and race the warm task.
GENERATING_POLL_TIMEOUT_SECONDS = 25.0
GENERATING_POLL_INTERVAL_SECONDS = 0.5


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
    dataset: str


def _strip_citations(payload: dict) -> dict:
    """Citations stay in the LLM's grounding (so answers remain evidence-bound)
    but are not surfaced to the UI — the citation cards clutter the Ask reply.
    Always pass the response through this before returning to the client.
    """
    payload["citations"] = []
    return payload


@router.post("")
def ask(
    body: AskIn,
    _session: dict = Depends(require_session),
    company: CompanyContext | None = Depends(resolve_company_optional),
):
    # 1) Cache hit short-circuit — the home + Ask Sprntly starter chips send
    # deterministic prompts pre-warmed at brief-generation time. Returns
    # without an LLM call, with a small random delay so the response
    # doesn't appear suspiciously instant.
    cached = find_cached_ask(body.dataset, body.question)
    # If a warm task is still in flight (typical post-restart while the
    # warming semaphore drains), wait for it instead of firing a parallel
    # LLM call — the user perceives real generation time, so we skip the
    # synthetic delay on the way out.
    waited_on_generation = False
    if cached and cached.get("status") == "generating":
        deadline = time.monotonic() + GENERATING_POLL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(GENERATING_POLL_INTERVAL_SECONDS)
            cached = find_cached_ask(body.dataset, body.question)
            if not cached or cached.get("status") != "generating":
                waited_on_generation = True
                break
    if cached and cached.get("status") == "ready":
        try:
            payload = json.loads(cached["response_json"])
        except (TypeError, ValueError):
            # Corrupt cache row — fall through and regenerate.
            pass
        else:
            if not waited_on_generation:
                time.sleep(
                    random.uniform(
                        CACHE_HIT_DELAY_MIN_SECONDS, CACHE_HIT_DELAY_MAX_SECONDS
                    )
                )
            return _strip_citations(payload)

    # 2) Cache miss → compose the answer from the legacy corpus AND the
    # knowledge graph (#18). compose_ask_answer loads the corpus as a cacheable
    # prefix, retrieves a ranked KG context bundle for the resolved tenant (if
    # any), injects it as a "KNOWLEDGE GRAPH CONTEXT" section, runs the LLM, and
    # decision-logs the ask with kg_refs. When no company resolves (legacy cookie
    # session) or the KG is empty, it falls back to corpus-only — the pre-#18
    # behaviour.
    enterprise_id = company.company_id if company else None
    payload = compose_ask_answer(
        body.dataset, body.question, enterprise_id=enterprise_id
    )
    log_ask(
        question=body.question,
        answer=payload.get("answer", ""),
        citations=payload.get("citations", []),
    )
    return _strip_citations(payload)
