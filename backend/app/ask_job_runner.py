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

from app import ask_runner, qa_agent
from app.ask_stream import AnswerFieldExtractor
from app.db import complete_ask_job, fail_ask_job, is_ask_cancelled
from app.db.asks import (
    ORPHAN_ASK_JOB_HEARTBEAT_SECONDS,
    set_ask_job_route,
    touch_ask_job,
)
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
    user_id: str | None = None,
    workspace_id: str | None = None,
    project_id: int | None = None,
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
    # `qa_agent.answer()` has no conversation_id/user_id parameter — and
    # keeping it that way is the point (see app.ask_runner.set_active_conversation
    # for the full rationale: threading either through answer() ->
    # _answer_single_shot -> its document_grounding call would be four edits
    # inside qa_agent.py, the file this fix stays out of). Both ride a
    # request-scoped ContextVar pair instead, set here immediately before the
    # call and ALWAYS cleared in the finally below — even when answer() raises.
    #
    # The question embedding rides the same route, for the same reason. Topical
    # document selection fuses a lexical and a semantic channel; the semantic
    # one needs a vector, and the only call site that had one was
    # `compose_ask_answer`. The skill-routed path reaches document grounding
    # through `qa_agent._answer_single_shot`, which calls it positionally, so
    # that path ran with no semantic channel at all and its ranking fell back
    # to whatever the lexical channel could separate — which, for a catalog
    # whose documents share the workspace's own name, is nothing. Ranking was
    # then decided by recency and still reported as a topic match.
    #
    # Computed HERE, once, before `answer()` picks a path, so both paths get
    # the same vector and the ask pays for exactly one embedding:
    # `compose_ask_answer` reuses this instead of computing its own.
    #
    # Computed BEFORE either setter, deliberately: this is the only step here
    # that does real work (an HTTP call), and nothing between a `set_` and the
    # `try` is covered by the `finally`. This worker runs on a pooled thread,
    # and a ContextVar left set outlives the request into whatever ask reuses
    # that thread next — a stale conversation id would then scope another
    # user's document lookup. `_question_embedding` swallows its own failures
    # and returns `(None, True)` rather than raising, so today that window is
    # already closed; ordering it this way is what keeps it closed if that
    # ever stops being true.
    embedding, embedding_degraded = ask_runner._question_embedding(
        enterprise_id, question
    )
    context_token = ask_runner.set_active_conversation(conversation_id, user_id)
    embedding_token = ask_runner.set_active_question_embedding(
        embedding, embedding_degraded
    )
    # The prior turns, by the same route and for the same reason: document
    # RESOLUTION ("what does it say about pricing?") cannot work out what "it"
    # is without them, and the skill-routed path reaches document grounding
    # through `qa_agent._answer_single_shot`, which calls it positionally.
    # `history` is already loaded and already this function's parameter —
    # `routes.ask._load_history` fetched it, ownership-checked, before the job
    # started — so this publishes what is in hand rather than reading again.
    history_token = ask_runner.set_active_history(history)
    # Project-scoped ask (individual project chat): let qa_agent skip the
    # connector-lookup interceptors so the folded, authoritative project-context
    # block answers project-meta questions instead of a "connect a connector"
    # deflection. None for every non-project ask (interceptors unchanged there).
    project_id_token = ask_runner.set_active_project_id(project_id)

    def _single_shot() -> dict:
        return qa_agent.answer(
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
            # The routed skill goes onto the job row the INSTANT the router resolves
            # — seconds into a run that can last minutes — so GET /v1/ask/{id} can
            # name the running skill while the job is still `generating`. Two
            # different transports on purpose: the skill is durable state the poll
            # must still find after a reload, whereas a phase is an ephemeral "which
            # leg is live right now" that only means anything to a client currently
            # attached to the stream.
            on_route=lambda skill_id, action: set_ask_job_route(ask_id, skill_id, action),
            on_phase=token_stream.phase_sink(loop, ask_channel(ask_id)),
        )

    try:
        if project_id is not None:
            # Project-scoped individual chat: a bounded tool-loop responder with
            # the project read tools (depth), answer-executor only — PRD edits
            # are classified client-side and never reach this loop (see
            # project_individual_agent.py's module docstring). It returns the
            # same payload shape as the single-shot path, so the strip/complete/
            # capture/log calls below run UNCHANGED, and degrades to `_single_shot`
            # on any failure. Non-project asks (`project_id is None`) skip this
            # branch entirely — byte-identical to before this change.
            from app.project_individual_agent import respond_individual

            payload = respond_individual(
                project_id=project_id,
                dataset=dataset,
                company_id=enterprise_id,
                question=question,
                history=history,
                single_shot=_single_shot,
            )
        else:
            payload = _single_shot()
    finally:
        ask_runner.reset_active_conversation(context_token)
        ask_runner.reset_active_question_embedding(embedding_token)
        ask_runner.reset_active_history(history_token)
        ask_runner.reset_active_project_id(project_id_token)
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
    # Individual project chat (build spec §5.3): after the answer is the
    # authoritative stored reply, best-effort promote a durable insight into
    # project memory — gated on a project-scoped ask, so a non-project ask is
    # byte-for-byte unaffected. Self-swallowing (maybe_promote_turn never
    # raises), so it can only ADD a memory entry, never delay/break the
    # answer. Reuses the existing group-chat promotion writer verbatim (no
    # re-implementation) — it already schedules its own summary regen, so
    # nothing else is added here.
    if project_id is not None and conversation_id is not None:
        from app.project_memory import maybe_promote_turn

        transcript = f"{question}\n\nSprntly: {payload.get('answer', '')}"
        maybe_promote_turn(project_id, conversation_id, transcript)


async def run_ask_job(
    ask_id: int,
    enterprise_id: str,
    question: str,
    dataset: str,
    history: list[dict] | None = None,
    pinned_skill: str | None = None,
    prd_id: int | None = None,
    conversation_id: int | None = None,
    user_id: str | None = None,
    workspace_id: str | None = None,
    project_id: int | None = None,
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
            user_id,
            workspace_id,
            project_id,
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
