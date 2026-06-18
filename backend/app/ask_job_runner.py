"""Background worker for the blur-safe chat Ask flow.

`POST /v1/ask` persists a `generating` row in `ask_jobs` and schedules
`run_ask_job` as a fire-and-forget task; this module runs the SAME
`qa_agent.answer(...)` pipeline the old synchronous endpoint ran, strips
citations the same way, and writes the result onto the job row (status →
`ready`). A backgrounded / remounted tab keeps the answer generating
server-side and re-attaches by polling `GET /v1/ask/{id}`.

Mirrors evidence_runner / prd_runner: a worker-thread call wrapped so a
failure marks the row `error` and never crashes the task (the asyncio loop
holds a strong ref via routes/ask.py's `_inflight_tasks`).
"""
import asyncio
import logging

from app import qa_agent
from app.db import complete_ask_job, fail_ask_job

logger = logging.getLogger(__name__)


def _strip_citations(payload: dict) -> dict:
    """Citations stay in the LLM's grounding (so answers remain evidence-bound)
    but are not surfaced to the UI. Identical to routes.ask._strip_citations —
    kept here too so the worker is self-contained and the stored payload always
    matches what the old synchronous endpoint returned."""
    payload["citations"] = []
    return payload


def _run_sync(
    ask_id: int,
    enterprise_id: str,
    question: str,
    dataset: str,
    history: list[dict],
    pinned_skill: str | None,
) -> None:
    payload = qa_agent.answer(
        enterprise_id=enterprise_id,
        question=question,
        dataset=dataset,
        history=history,
        pinned_skill=pinned_skill,
    )
    # Append-only analytics log, same as the old inline path.
    try:
        from app.db import log_ask

        log_ask(
            question=question,
            answer=payload.get("answer", ""),
            citations=payload.get("citations", []),
        )
    except Exception:  # noqa: BLE001 — analytics logging must never fail the answer
        logger.exception("log_ask failed for ask_id=%s", ask_id)
    complete_ask_job(ask_id, _strip_citations(payload))


async def run_ask_job(
    ask_id: int,
    enterprise_id: str,
    question: str,
    dataset: str,
    history: list[dict] | None = None,
    pinned_skill: str | None = None,
) -> None:
    """Run the Ask pipeline in a worker thread; update the job row with the
    result. A failure marks the row `error` and is swallowed — the worker never
    crashes the event loop."""
    logger.info("Ask job starting ask_id=%s dataset=%s", ask_id, dataset)
    try:
        await asyncio.to_thread(
            _run_sync,
            ask_id,
            enterprise_id,
            question,
            dataset,
            history or [],
            pinned_skill,
        )
        logger.info("Ask job succeeded ask_id=%s", ask_id)
    except Exception as exc:  # noqa: BLE001 — best-effort; never crash the worker
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("Ask job failed ask_id=%s", ask_id)
        try:
            fail_ask_job(ask_id, msg)
        except Exception:  # noqa: BLE001 — even the fail-marking is best-effort
            logger.exception("fail_ask_job failed ask_id=%s", ask_id)
