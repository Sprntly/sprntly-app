"""Background warmer for the predefined Ask prompts.

The home / Ask-Sprntly screens have a small fixed set of starter chips
(see `PREDEFINED_ASK_PROMPTS` in prompts.py). Each click sends the same
question text every time. By pre-generating responses at brief-creation
time we make those clicks render instantly — the route just returns a
cached row.
"""
import asyncio
import json
import logging

from app.corpus import load_corpus
from app.db import (
    complete_cached_ask,
    fail_cached_ask,
    find_cached_ask,
    start_cached_ask,
)
from app.llm import DEFAULT_MODEL, call_json
from app.prompts import (
    ASK_CACHE_VERSION,
    ASK_SYSTEM,
    ASK_SYSTEM_KG_ADDENDUM,
    ASK_USER_TEMPLATE_QUESTION_ONLY,
    ASK_USER_TEMPLATE_WITH_KG,
    PREDEFINED_ASK_PROMPTS,
)

logger = logging.getLogger(__name__)

# Prompt version stamped onto the Ask decision-log row so the §4d audit spine
# pins the exact Ask composition (corpus + KG bridge, #18) behind each answer.
ASK_PROMPT_VERSION = "ask-kg-v2"

# Strong refs to in-flight warm tasks. asyncio holds only a weak reference to a
# bare create_task result, so without this a fanned-out warm task can be
# garbage-collected mid-run (the warm silently dies). The done-callback discards
# each task on completion (mirrors routes/design_agent.py's _inflight_tasks).
_inflight_tasks: set[asyncio.Task] = set()


# Defined inline (and re-defined in routes/ask.py — keep in sync) so the
# warmer can run independent of the route module being imported first.
_ASK_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
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
        "confidence": {"type": "number"},
        "unanswered": {"type": "string"},
    },
    "required": ["answer", "key_points", "citations", "confidence", "unanswered"],
}


def _generate_one_sync(dataset: str, question: str) -> dict:
    """Run the same Anthropic call that /v1/ask would run for a given Q."""
    corpus = load_corpus(dataset)
    cacheable = f"Source material:\n\n{corpus.joined()}"
    user = ASK_USER_TEMPLATE_QUESTION_ONLY.format(question=question)
    # Warm/predefined asks carry only a dataset slug (no enterprise_id). The slug
    # IS the company slug (see deps.ownership), so resolve it to bind the
    # company's own Claude key when configured. Best-effort: an unresolvable slug
    # falls through to the platform key.
    from app.llm_keys import company_llm_key

    company_id = None
    try:
        from app.deps.ownership import company_id_for_dataset

        company_id = company_id_for_dataset(dataset)
    except Exception:  # noqa: BLE001 — key binding must never break warming
        company_id = None
    with company_llm_key(company_id):
        return call_json(
            system=ASK_SYSTEM,
            user=user,
            user_cacheable_prefix=cacheable,
            schema=_ASK_RESPONSE_SCHEMA,
            max_tokens=12000,
        )


def _retrieve_kg_bundle(enterprise_id: str | None, question: str) -> dict | None:
    """Best-effort KG retrieval for the Ask question (#18). Returns the bundle
    or None when there's no tenant context or the KG yields nothing / errors.

    Resilient by construction: a missing tenant, an empty KG, a fake backend
    with no pgvector, or any read failure all collapse to None so the caller
    runs the legacy corpus-only path (pre-#18 behaviour)."""
    if not enterprise_id:
        return None
    try:
        from app.graph.facade import GraphFacade
        from app.graph.retrieval import retrieve_context

        facade = GraphFacade()
        bundle = retrieve_context(facade, enterprise_id, question)
    except Exception:  # noqa: BLE001 — KG must never break Ask
        logger.exception("Ask KG retrieval failed for enterprise=%s", enterprise_id)
        return None
    if not bundle or bundle.get("empty"):
        return None
    return bundle


def compose_ask_answer(
    dataset: str,
    question: str,
    *,
    enterprise_id: str | None = None,
    prd_context: str = "",
) -> dict:
    """Generate an Ask answer from BOTH the legacy corpus AND the knowledge
    graph (#18 — chat answers from the brain, not only the markdown corpus).

    Flow:
      - Always load the dataset corpus (cacheable prefix; unchanged grounding).
      - If a tenant (`enterprise_id`) is resolvable AND its KG has relevant
        signals/entities, retrieve a ranked, budget-capped context bundle and
        inject it as a "LIVE CONTEXT FROM CONNECTED SOURCES" section, with the KG-aware
        system addendum. Otherwise fall back to corpus-only — identical to the
        pre-#18 path, including the cache warmer's prompt.
      - Decision-log the ask (agent="ask", decision_type="answer") with
        kg_refs = the signal/entity ids that fed the answer.

    Returns the raw response payload (answer/key_points/citations/...); the
    caller strips citations + logs to ask_log as before."""
    corpus = load_corpus(dataset)
    cacheable = f"Source material:\n\n{corpus.joined()}" if corpus.docs else None

    bundle = _retrieve_kg_bundle(enterprise_id, question)

    if bundle:
        from app.graph.retrieval import render_context_section

        system = ASK_SYSTEM + ASK_SYSTEM_KG_ADDENDUM
        user = ASK_USER_TEMPLATE_WITH_KG.format(
            kg_context=render_context_section(bundle), question=question
        )
    else:
        system = ASK_SYSTEM
        user = ASK_USER_TEMPLATE_QUESTION_ONLY.format(question=question)

    # PRD-tab chat: the open PRD (+ insight/evidence/tickets/prototype) rides
    # above the question so "this PRD" asks see the document. Kept OUT of the
    # cacheable corpus prefix — it varies per PRD (and per applied patch), so
    # folding it in would fragment the corpus prompt-cache for plain asks.
    if prd_context:
        from app.prompts import ASK_SYSTEM_PRD_ADDENDUM

        system = system + ASK_SYSTEM_PRD_ADDENDUM
        user = f"{prd_context}\n\n---\n\n{user}"

    # Bind the tenant's own Claude key (when configured) for this direct
    # (non-gateway) answer call. See app.llm_keys.
    from app.llm_keys import company_llm_key

    with company_llm_key(enterprise_id):
        payload = call_json(
            system=system,
            user=user,
            user_cacheable_prefix=cacheable,
            schema=_ASK_RESPONSE_SCHEMA,
            max_tokens=12000,
        )

    # Decision-log the ask onto the §4d audit spine. Best-effort + tenant-
    # scoped — only when a tenant resolved (legacy cookie sessions have none).
    if enterprise_id:
        try:
            from app.graph.decision_log import log_agent_decision

            log_agent_decision(
                enterprise_id=enterprise_id,
                agent="ask",
                decision_type="answer",
                factors={
                    "dataset": dataset,
                    "question": question,
                    "kg_used": bool(bundle),
                    "prd_grounded": bool(prd_context),
                    "kg_signals": len(bundle["signals"]) if bundle else 0,
                    "kg_themes": len(bundle["themes"]) if bundle else 0,
                },
                output={
                    "key_points": payload.get("key_points", []),
                    "unanswered": payload.get("unanswered", ""),
                },
                model=DEFAULT_MODEL,
                prompt_version=ASK_PROMPT_VERSION,
                confidence=payload.get("confidence"),
                kg_refs=(bundle or {}).get("kg_refs") or [],
            )
        except Exception:  # noqa: BLE001 — audit write must not block the answer
            logger.exception("Ask decision-log write failed for enterprise=%s", enterprise_id)

    return payload


async def _warm_one(dataset: str, question: str, sema: asyncio.Semaphore) -> None:
    """Generate + cache the response for a single predefined prompt.

    No-op if a ready/generating row already exists for (dataset, question).
    Errors are logged + stored on the cache row; do not propagate.
    """
    if find_cached_ask(dataset, question):
        logger.info("Cached Ask already exists for %s · %s", dataset, question[:60])
        return
    cache_id = start_cached_ask(
        dataset=dataset,
        question=question,
        cache_version=ASK_CACHE_VERSION,
    )
    logger.info(
        "Warming cached Ask id=%s dataset=%s q=%r",
        cache_id,
        dataset,
        question[:80],
    )
    try:
        async with sema:
            payload = await asyncio.to_thread(_generate_one_sync, dataset, question)
        complete_cached_ask(cache_id, json.dumps(payload))
        logger.info("Cached Ask ready id=%s", cache_id)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("Cached Ask warming failed id=%s", cache_id)
        fail_cached_ask(cache_id, msg)


def warm_predefined_asks(dataset: str, sema: asyncio.Semaphore) -> None:
    """Fan out warm tasks for every predefined prompt. Returns immediately;
    each warm task runs concurrently under the shared semaphore so we don't
    burst-fire Anthropic on top of brief / evidence / PRD warming.
    """
    for prompt in PREDEFINED_ASK_PROMPTS:
        task = asyncio.create_task(_warm_one(dataset, prompt, sema))
        _inflight_tasks.add(task)
        task.add_done_callback(_inflight_tasks.discard)


def warm_brief_dynamic_asks(
    dataset: str, brief: dict, sema: asyncio.Semaphore
) -> None:
    """Warm the per-insight Ask prompts that the BriefScreen fires when the
    user clicks "Ask Sprntly" on a finding card.

    Frontend pattern (web/app/lib/brief-adapter.ts):
        askQuestion: `Tell me more about: ${insight.title}`

    For each insight in the brief, we precompute the same text and warm a
    cache row so the click renders instantly.
    """
    for insight in brief.get("insights") or []:
        title = (insight or {}).get("title")
        if not title:
            continue
        prompt = f"Tell me more about: {title}"
        task = asyncio.create_task(_warm_one(dataset, prompt, sema))
        _inflight_tasks.add(task)
        task.add_done_callback(_inflight_tasks.discard)
