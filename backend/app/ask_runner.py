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
from app.llm import call_json
from app.prompts import (
    ASK_CACHE_VERSION,
    ASK_SYSTEM,
    ASK_USER_TEMPLATE_QUESTION_ONLY,
    PREDEFINED_ASK_PROMPTS,
)

logger = logging.getLogger(__name__)


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
    cacheable = f"Corpus:\n\n{corpus.joined()}"
    user = ASK_USER_TEMPLATE_QUESTION_ONLY.format(question=question)
    return call_json(
        system=ASK_SYSTEM,
        user=user,
        user_cacheable_prefix=cacheable,
        schema=_ASK_RESPONSE_SCHEMA,
        max_tokens=12000,
    )


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
        asyncio.create_task(_warm_one(dataset, prompt, sema))
