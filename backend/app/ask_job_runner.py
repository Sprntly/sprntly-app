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
from app.ask_stream import AnswerFieldExtractor
from app.db import complete_ask_job, fail_ask_job, is_ask_cancelled
from app.db.asks import ORPHAN_ASK_JOB_HEARTBEAT_SECONDS, touch_ask_job
from app.graph import token_stream
from app.qa_agent import AskCancelled
from app.report_capture import capture_report

logger = logging.getLogger(__name__)


async def _heartbeat(ask_id: int) -> None:
    """Bump the job row's `updated_at` while this worker is alive.

    The orphan sweep fails any `ask_jobs` row that has sat in `generating`
    longer than ORPHAN_ASK_JOB_AFTER_MINUTES, because a row carries no owner
    column and age is the only available "the worker died" signal. Without a
    beat that window is a ceiling on how long an answer may TAKE: the
    competitive-intelligence review runs ~20 minutes, so on staging its row was
    failed at 15 minutes while the worker was still running, and the answer it
    finally produced was then dropped by complete_ask_job's
    `status == 'generating'` guard. Every attempt was lost, and every attempt
    had already been paid for.

    Beating turns the age gate back into what it was meant to be — a liveness
    check. Cancelled early when the row leaves `generating`, so a finished or
    stopped job stops being touched immediately.
    """
    try:
        while True:
            await asyncio.sleep(ORPHAN_ASK_JOB_HEARTBEAT_SECONDS)
            if not await asyncio.to_thread(touch_ask_job, ask_id):
                return          # no longer generating — nothing left to keep alive
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a heartbeat failure must never fail the ask
        logger.exception("ask heartbeat loop failed ask_id=%s", ask_id)


def ask_channel(ask_id: int) -> str:
    """The token_stream channel a running Ask publishes its answer text on
    (subscribed by GET /v1/ask/{id}/stream)."""
    return f"ask:{ask_id}"


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
    prd_id: int | None,
    loop: asyncio.AbstractEventLoop,
    conversation_id: int | None = None,
    workspace_id: str | None = None,
) -> None:
    # Token-stream the answer text as it generates: the structured answer call
    # forwards its partial-JSON fragments to this extractor, which decodes just
    # the `answer` field and publishes it on the ask's SSE channel (subscribed
    # by GET /v1/ask/{id}/stream). Display only — the persisted job row the
    # client polls stays the authoritative answer, so the non-streamable paths
    # (reports, tool loops) simply publish nothing.
    extractor = AnswerFieldExtractor(
        token_stream.delta_sink(loop, ask_channel(ask_id))
    )
    payload = qa_agent.answer(
        enterprise_id=enterprise_id,
        question=question,
        dataset=dataset,
        history=history,
        pinned_skill=pinned_skill,
        prd_id=prd_id,
        # Cooperative cancellation: the user's Stop flips the job row to
        # `cancelled` (POST /v1/ask/{id}/cancel); qa_agent polls this between LLM
        # steps and raises AskCancelled to abort before the expensive answer call.
        is_cancelled=lambda: is_ask_cancelled(ask_id),
        on_delta=extractor,
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
    # A report skill answers with a self-contained HTML document rather than
    # markdown. Capture it as a durable `reports` artifact so it survives the
    # chat turn — listable on /artifacts, downloadable, shareable. Attached to
    # the chat room and/or PRD this ask ran in (whichever the ask carried).
    #
    # AFTER complete_ask_job, and self-swallowing: the reply is already the
    # authoritative stored answer, so capture can only add a library entry and
    # can never delay or break the turn. A no-op for every markdown answer.
    #
    # Only the generation path captures. The cache-hit short-circuit in
    # routes/ask.py deliberately does not: those are pre-warmed starter-chip
    # answers (markdown), and re-serving one per user would mint a duplicate row
    # on every hit.
    capture_report(
        payload,
        company_id=enterprise_id,
        question=question,
        workspace_id=workspace_id,
        ask_id=ask_id,
        conversation_id=conversation_id,
        prd_id=prd_id,
        is_cancelled=lambda: is_ask_cancelled(ask_id),
    )


async def run_ask_job(
    ask_id: int,
    enterprise_id: str,
    question: str,
    dataset: str,
    history: list[dict] | None = None,
    pinned_skill: str | None = None,
    prd_id: int | None = None,
    conversation_id: int | None = None,
    workspace_id: str | None = None,
) -> None:
    """Run the Ask pipeline in a worker thread; update the job row with the
    result. A failure marks the row `error` and is swallowed — the worker never
    crashes the event loop."""
    logger.info("Ask job starting ask_id=%s dataset=%s", ask_id, dataset)
    loop = asyncio.get_running_loop()
    channel = ask_channel(ask_id)
    # Keep the row's liveness fresh for as long as this worker runs, so the
    # orphan sweep can't fail a long-but-healthy answer out from under us.
    beat = asyncio.create_task(_heartbeat(ask_id))
    try:
        await asyncio.to_thread(
            _run_sync,
            ask_id,
            enterprise_id,
            question,
            dataset,
            history or [],
            pinned_skill,
            prd_id,
            loop,
            conversation_id,
            workspace_id,
        )
        logger.info("Ask job succeeded ask_id=%s", ask_id)
        # Terminal SSE frame AFTER complete_ask_job (inside _run_sync) so a
        # client woken by `done` reads a `ready` row on its next poll.
        token_stream.close(channel, kind="done")
    except AskCancelled:
        # The user stopped the ask; the row is already `cancelled` (set by the
        # cancel endpoint). Leave it as-is — this is NOT a failure, so must not
        # be marked `error`. The worker just abandons the answer.
        logger.info("Ask job cancelled ask_id=%s", ask_id)
        token_stream.close(channel, kind="done")
    except Exception as exc:  # noqa: BLE001 — best-effort; never crash the worker
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("Ask job failed ask_id=%s", ask_id)
        try:
            fail_ask_job(ask_id, msg)
        except Exception:  # noqa: BLE001 — even the fail-marking is best-effort
            logger.exception("fail_ask_job failed ask_id=%s", ask_id)
        token_stream.close(channel, kind="error")
    finally:
        beat.cancel()
