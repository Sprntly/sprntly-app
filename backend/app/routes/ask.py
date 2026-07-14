import asyncio
import json
import logging
import random
import sys
import time

from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.ask_job_runner import run_ask_job
from app.auth import CompanyContext, require_company
from app.db import (
    complete_ask_job,
    find_cached_ask,
    get_ask_job,
    start_ask_job,
)
from app.deps.ownership import require_owned_dataset
from app.skill_router import list_available_skills

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ask", tags=["ask"])


# Strong refs to in-flight background Ask tasks. asyncio holds only a weak
# reference to a bare create_task result, so without this the task can be
# garbage-collected mid-run and the row would be stuck 'generating'. The
# done-callback discards each task on completion (mirrors routes/prd.py).
_inflight_tasks: set[asyncio.Task] = set()


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
    # Optional multi-turn: when set, prior turns of this conversation are
    # loaded (ownership-checked) and fed to the router + answer for follow-ups.
    conversation_id: int | None = None
    # Optional: skip routing and force this skill — used when a confirm-gate
    # follow-up has already chosen the skill.
    pinned_skill: str | None = None


def _strip_citations(payload: dict) -> dict:
    """Citations stay in the LLM's grounding (so answers remain evidence-bound)
    but are not surfaced to the UI — the citation cards clutter the Ask reply.
    Always pass the response through this before returning to the client.
    """
    payload["citations"] = []
    return payload


def _load_history(
    conversation_id: int | None, company_id: str, user_id: str
) -> list[dict]:
    """Fetch prior turns [{role, content}] for an owned conversation, oldest
    first. Chats are per-user: the conversation must belong to the CALLER, not
    just their company — otherwise a teammate's conversation_id would replay
    that teammate's private turns into the model context. Best-effort: no id,
    foreign/unowned conversation, or any read error → []."""
    if not conversation_id:
        return []
    try:
        from app.db.client import require_client

        c = require_client()
        owned = (
            c.table("conversations")
            .select("id")
            .eq("id", conversation_id)
            .eq("company_id", company_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not owned.data:
            return []
        turns = (
            c.table("conversation_turns")
            .select("role,content")
            .eq("conversation_id", conversation_id)
            .order("created_at")
            .execute()
        )
        return turns.data or []
    except Exception:  # noqa: BLE001 — history must never break the answer
        return []


def _resolve_cache_hit(dataset: str, question: str) -> dict | None:
    """Resolve the pre-warm cache for this question, applying the same waiting +
    synthetic-delay behavior the old synchronous endpoint did. Returns the
    decoded (un-stripped) cached payload on a ready hit, else None. Blocking —
    called via `asyncio.to_thread` so the synthetic delay / generating-poll
    never blocks the event loop."""
    cached = find_cached_ask(dataset, question)
    # If a warm task is still in flight (typical post-restart while the warming
    # semaphore drains), wait for it instead of firing a parallel LLM call — the
    # user perceives real generation time, so we skip the synthetic delay.
    waited_on_generation = False
    if cached and cached.get("status") == "generating":
        deadline = time.monotonic() + GENERATING_POLL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(GENERATING_POLL_INTERVAL_SECONDS)
            cached = find_cached_ask(dataset, question)
            if not cached or cached.get("status") != "generating":
                waited_on_generation = True
                break
    if cached and cached.get("status") == "ready":
        try:
            payload = json.loads(cached["response_json"])
        except (TypeError, ValueError):
            # Corrupt cache row — caller falls through and regenerates.
            return None
        if not waited_on_generation:
            time.sleep(
                random.uniform(
                    CACHE_HIT_DELAY_MIN_SECONDS, CACHE_HIT_DELAY_MAX_SECONDS
                )
            )
        return payload
    return None


@router.post("")
async def ask(
    body: AskIn,
    company: CompanyContext = Depends(require_company),
):
    """Kick off (or short-circuit) an Ask, returning `{ask_id, status}`.

    Fire-and-forget — mirrors PRD/evidence so a backgrounded or remounted tab
    keeps the answer generating server-side and re-attaches by polling
    `GET /v1/ask/{ask_id}`. The actual answer body is fetched from the status
    endpoint, which returns the SAME citation-stripped shape the old
    synchronous POST returned (so downstream rendering/citation handling is
    unchanged).
    """
    # 0) Tenant gate: the dataset slug must resolve to the caller's company.
    # Without this, an arbitrary client slug would seed a FOREIGN company's
    # corpus into the LLM answer (cross-tenant corpus leak). require_company
    # scopes the KG half; this scopes the corpus/dataset half. 404 on mismatch.
    require_owned_dataset(body.dataset, company.company_id)
    enterprise_id = company.company_id

    # 1) Cache hit short-circuit — the home + Ask Sprntly starter chips send
    # deterministic prompts pre-warmed at brief-generation time. We persist the
    # cached answer onto an immediately-`ready` ask job (rather than returning it
    # inline) so the POST contract is uniform — the client always gets an ask_id
    # and reads the body from the status endpoint, cached or generated. The
    # user-visible result is identical (same payload, same synthetic delay).
    cached_payload = await asyncio.to_thread(
        _resolve_cache_hit, body.dataset, body.question
    )
    if cached_payload is not None:
        ask_id = start_ask_job(
            company_id=enterprise_id,
            dataset=body.dataset,
            question=body.question,
            conversation_id=body.conversation_id,
            pinned_skill=body.pinned_skill,
        )
        complete_ask_job(ask_id, _strip_citations(cached_payload))
        return {"ask_id": ask_id, "status": "ready"}

    # 2) Cache miss → persist a generating job and kick the SAME qa_agent
    # pipeline in the background. The worker writes the result/citations onto
    # the job row; the client polls GET /v1/ask/{ask_id} until ready.
    history = _load_history(body.conversation_id, enterprise_id, company.user_id)
    ask_id = start_ask_job(
        company_id=enterprise_id,
        dataset=body.dataset,
        question=body.question,
        conversation_id=body.conversation_id,
        pinned_skill=body.pinned_skill,
    )
    if "pytest" in sys.modules:
        # The TestClient does not keep the app's event loop alive between
        # requests, so a fire-and-forget create_task would never run and the
        # client's status-poll would spin forever. Run the worker inline under
        # pytest for deterministic results (mirrors decision_log's test-mode
        # handling). Production keeps the non-blocking create_task path below.
        await run_ask_job(
            ask_id=ask_id,
            enterprise_id=enterprise_id,
            question=body.question,
            dataset=body.dataset,
            history=history,
            pinned_skill=body.pinned_skill,
        )
        row = get_ask_job(ask_id)
        return {"ask_id": ask_id, "status": (row or {}).get("status", "ready")}

    task = asyncio.create_task(
        run_ask_job(
            ask_id=ask_id,
            enterprise_id=enterprise_id,
            question=body.question,
            dataset=body.dataset,
            history=history,
            pinned_skill=body.pinned_skill,
        )
    )
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
    return {"ask_id": ask_id, "status": "generating"}


@router.get("/skills")
def get_skills():
    """Return the list of available skills for the chat composer UI."""
    return {"skills": list_available_skills()}


@router.get("/usage")
def get_usage(company: CompanyContext = Depends(require_company)):
    """Per-enterprise Q&A usage: calls, cost, tokens (total + by agent)."""
    from app.qa_usage import fetch_qa_usage

    return fetch_qa_usage(company.company_id)


# Declared AFTER the static /skills + /usage routes so they aren't shadowed by
# this dynamic int param (FastAPI matches in declaration order).
@router.get("/{ask_id}")
def get_ask(
    ask_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Status + result for an Ask job.

    Returns `{status, answer, key_points, citations, confidence, unanswered, error}`.
    Once `status == 'ready'` the answer/key_points/citations/etc. fields carry
    the SAME citation-stripped shape the old synchronous POST returned, so
    downstream rendering is unchanged. 404 if the job doesn't belong to the
    caller's company (no cross-tenant existence disclosure)."""
    row = get_ask_job(ask_id)
    if not row or row.get("company_id") != company.company_id:
        raise HTTPException(404, "Ask not found")
    status = row.get("status") or "generating"
    payload = row.get("response") or {}
    return {
        "status": status,
        "error": row.get("error"),
        "answer": payload.get("answer", ""),
        "key_points": payload.get("key_points", []),
        "citations": payload.get("citations", []),
        "confidence": payload.get("confidence", 0),
        "unanswered": payload.get("unanswered", ""),
        # Pass through any extra fields the qa_agent attaches (e.g. confirm-gate
        # metadata, routed skill) so the contract stays a superset of the old body.
        **{
            k: v
            for k, v in payload.items()
            if k
            not in {
                "answer",
                "key_points",
                "citations",
                "confidence",
                "unanswered",
            }
        },
    }
