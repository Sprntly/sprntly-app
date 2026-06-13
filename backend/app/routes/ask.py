import json
import random
import time

from fastapi import Depends, APIRouter
from pydantic import BaseModel, Field

from app.ask_runner import compose_ask_answer
from app.auth import CompanyContext, require_company
from app.db import find_cached_ask, log_ask
from app.deps.ownership import require_owned_dataset
from app.prompts import ASK_SYSTEM
from app.skill_router import detect_intent, list_available_skills

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
    company: CompanyContext = Depends(require_company),
):
    # 0) Tenant gate: the dataset slug must resolve to the caller's company.
    # Without this, an arbitrary client slug would seed a FOREIGN company's
    # corpus into the LLM answer (cross-tenant corpus leak). require_company
    # scopes the KG half; this scopes the corpus/dataset half. 404 on mismatch.
    require_owned_dataset(body.dataset, company.company_id)

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

    # 2) Skill routing: detect if the question maps to a specialized skill
    intent = detect_intent(body.question)

    # 3) Cache miss → compose the answer. If a skill matched, include the skill
    # context so the LLM uses the specialized method. Otherwise fall back to
    # general Ask (corpus + KG).
    enterprise_id = company.company_id

    if intent and intent.confidence >= 0.75:
        # Skill-routed answer: bind the matched skill's method onto the gateway
        # call (it prepends SKILL.md into the cacheable prefix + logs the call).
        try:
            from app.graph.gateway import llm_call

            result = llm_call(
                enterprise_id=enterprise_id,
                agent="ask",
                purpose="skill_answer",
                system=(
                    ASK_SYSTEM
                    + f"\n\nThe user's question maps to the '{intent.skill_id}' "
                    "skill. Follow that skill's method to produce a structured, "
                    "actionable answer."
                ),
                input=body.question,
                prompt_version="ask-skill-v1",
                json_schema=ASK_RESPONSE_SCHEMA,
                skill=intent.skill_id,
                max_tokens=12000,
            )
            payload = (
                result.output
                if isinstance(result.output, dict)
                else {
                    "answer": str(result.output),
                    "key_points": [],
                    "citations": [],
                    "confidence": intent.confidence,
                    "unanswered": "",
                }
            )
            # Tag the response with the matched skill for frontend rendering
            payload["_skill"] = intent.skill_id
            payload["_skill_action"] = intent.action
            payload["_skill_confidence"] = intent.confidence
        except Exception:
            # Skill call failed — fall back to general Ask
            payload = compose_ask_answer(
                body.dataset, body.question, enterprise_id=enterprise_id
            )
    else:
        payload = compose_ask_answer(
            body.dataset, body.question, enterprise_id=enterprise_id
        )
        # Tag with detected intent (if any, even low confidence) for UI hints
        if intent:
            payload["_skill_hint"] = intent.skill_id
            payload["_skill_hint_action"] = intent.action

    log_ask(
        question=body.question,
        answer=payload.get("answer", ""),
        citations=payload.get("citations", []),
    )
    return _strip_citations(payload)


@router.get("/skills")
def get_skills():
    """Return the list of available skills for the chat composer UI."""
    return {"skills": list_available_skills()}
