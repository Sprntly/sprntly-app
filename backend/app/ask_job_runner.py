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
import json
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
    # Keyword-only with defaults so the suite's positional direct calls keep
    # working; the one production caller (run_ask_job) passes them by name.
    *,
    evidence_id: int | None = None,
    ticket_set_id: int | None = None,
) -> None:
    # Token-stream the answer text as it generates: the structured answer call
    # forwards its partial-JSON fragments to this extractor, which decodes just
    # the `answer` field and publishes it on the ask's SSE channel (subscribed
    # by GET /v1/ask/{id}/stream). Display only — the persisted job row the
    # client polls stays the authoritative answer, so the non-streamable paths
    # (reports, tool loops) simply publish nothing.
    #
    # The extractor's `on_restart` is the sink's own `reset`: when the gateway
    # retries mid-stream the answer is re-emitted from zero, and rewinding only
    # the JSON parse state would leave attempt 1's markdown sitting in the
    # channel's replay buffer and in the browser's accumulator for attempt 2 to
    # be appended to. Chat answers are plain markdown, so the frontend's
    # `<!doctype` restart heuristic — which covers the HTML generations — never
    # fires for them; this is the explicit signal that does.
    sink = token_stream.delta_sink(loop, ask_channel(ask_id))
    extractor = AnswerFieldExtractor(sink, on_restart=getattr(sink, "reset", None))
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
    # SCOPED here, once, before `answer()` picks a path, so that whichever path
    # runs shares one vector and the ask pays for at most one embedding.
    #
    # Scoped rather than COMPUTED: the vector is only ever read by Stage T of
    # document grounding and by KG retrieval, and there are two common shapes
    # where neither runs. A workspace with no documents returns from
    # `document_grounding` before Stage T, and a PRD-grounded ask skips KG
    # retrieval entirely — both used to pay a full embedding round trip
    # (measured at 2.8s) for a vector nothing then read. Deferring it to first
    # use keeps the exactly-once guarantee (the resolver memoises back into
    # this slot) while dropping that cost to zero on the paths that need
    # nothing. See `ask_runner._EMBED_PENDING` for the three slot states.
    #
    # This also closes, rather than merely orders around, the hazard the eager
    # call had to be careful about: nothing between a `set_` and the `try` is
    # covered by the `finally`, and this worker runs on a POOLED thread where a
    # ContextVar left set outlives the request into whatever ask reuses that
    # thread next — a stale conversation id would then scope another user's
    # document lookup. The eager embed was ordered before both setters to keep
    # that window shut; scoping does no I/O at all, so there is no window.
    #
    # THE PLANNER RUNS FIRST, before `answer()` picks a path. It ran here
    # originally to get ahead of the eager embedding call — a live HTTP round
    # trip the plan did not depend on. That call is gone (the embedding is now
    # resolved lazily by whichever consumer needs it, and on a planned turn that
    # may be none at all), so the ordering is no longer load-bearing for
    # latency; what keeps the planner here is the separation of concerns below.
    #
    # It runs HERE rather than inside `answer()` so that function stays a pure
    # executor of a plan it is handed, rather than something that both decides
    # and executes. `plan_for_answer` returns None on any planner failure, and
    # `answer(plan=None)` is byte-identical to the pre-planner behaviour, so an
    # outage degrades this path rather than breaking it.
    #
    # A pinned turn is never planned: the user already named the skill, so there
    # is nothing to decide and no reason to bill them for a decision.
    ask_plan = None
    if not pinned_skill:
        from app import ask_planner

        ask_plan = ask_planner.plan_for_answer(
            enterprise_id=enterprise_id, question=question, history=history
        )

    context_token = ask_runner.set_active_conversation(conversation_id, user_id)
    embedding_token = ask_runner.set_active_question_embedding_pending()
    # The prior turns, by the same route and for the same reason: document
    # RESOLUTION ("what does it say about pricing?") cannot work out what "it"
    # is without them, and the skill-routed path reaches document grounding
    # through `qa_agent._answer_single_shot`, which calls it positionally.
    # `history` is already loaded and already this function's parameter —
    # `routes.ask._load_history` fetched it, ownership-checked, before the job
    # started — so this publishes what is in hand rather than reading again.
    # The documents the plan named, on the same request-scoped route and with
    # the same finally-cleared discipline. Empty when nothing planned one, which
    # is the normal outcome and leaves document selection exactly as it was.
    planned_docs_token = ask_runner.set_active_planned_documents(
        getattr(ask_plan, "documents", None) if ask_plan is not None else None
    )
    history_token = ask_runner.set_active_history(history)
    try:
        payload = qa_agent.answer(
            plan=ask_plan,
            enterprise_id=enterprise_id,
            question=question,
            dataset=dataset,
            history=history,
            pinned_skill=pinned_skill,
            prd_id=prd_id,
            evidence_id=evidence_id,
            ticket_set_id=ticket_set_id,
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
    finally:
        ask_runner.reset_active_conversation(context_token)
        ask_runner.reset_active_question_embedding(embedding_token)
        ask_runner.reset_active_history(history_token)
        ask_runner.reset_active_planned_documents(planned_docs_token)
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
    # The other half of the planner-first shadow (backend/docs/ASK_PLANNER.md):
    # the plan was logged at the TOP of qa_agent.answer, before anything
    # decided; this line records what the ladder ACTUALLY did, read off the
    # finished payload (`_skill`/`_skill_action` are set by whichever
    # interceptor, pipeline or skill answered — both None on the direct path).
    # The two lines join on `question`. Logged here because this is the one
    # exit point where the outcome is known — answer() returns from a dozen
    # places, and instrumenting each was the invasive option.
    #
    # Same flag as the shadow itself, checked cheaply: this whole function is
    # already on a background worker, and `shadow_enabled` fails closed, so an
    # unenrolled company logs nothing and a broken flag read costs one line.
    try:
        from app import ask_planner

        if ask_planner.shadow_enabled(enterprise_id):
            logger.info(
                "ask-planner actual: %s",
                json.dumps(
                    {
                        "enterprise_id": enterprise_id,
                        "question": ask_planner._clamp_for_log(question),
                        "skill": payload.get("_skill"),
                        "action": payload.get("_skill_action"),
                    },
                    sort_keys=True,
                    default=str,
                ),
            )
    except Exception:  # noqa: BLE001 — shadow telemetry must never fail the answer
        logger.exception("ask-planner actual line failed for ask_id=%s", ask_id)
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
    evidence_id: int | None = None,
    ticket_set_id: int | None = None,
    conversation_id: int | None = None,
    user_id: str | None = None,
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
            user_id,
            workspace_id,
            evidence_id=evidence_id,
            ticket_set_id=ticket_set_id,
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
