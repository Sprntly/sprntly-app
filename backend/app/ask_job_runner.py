
from app.timing import timed_def
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
import uuid
from dataclasses import dataclass, field
from typing import Callable, Literal

from app import ask_runner, qa_agent
from app.ask_stream import AnswerFieldExtractor
from app.db import complete_ask_job, fail_ask_job, is_ask_cancelled
from app.db.asks import (
    ORPHAN_ASK_JOB_HEARTBEAT_SECONDS,
    set_ask_job_route,
    touch_ask_job,
)
from app.context_assembler import AssembleRequest, resolve_context_scope
from app.db.conversations import post_individual_turn
from app.graph import token_stream
from app.qa_agent import AskCancelled
from app.report_capture import capture_report
from app.surface_scope import PROJECT_TOOL_NUDGE, Surface, SurfaceScope

logger = logging.getLogger(__name__)

# The private ("My chat with Sprntly") individual thread's system-prompt
# addendum — RELOCATED verbatim from the deleted `project_individual_agent.
# _SYSTEM` (the standalone bounded-loop responder this collapse replaces).
# Carried on `SurfaceScope.system_addendum` — read by BOTH the sixth ladder
# branch (`qa_agent._try_scoped_tool_answer`, as the tool loop's system
# prompt) AND, on the gate's decline path, folded into `history` ahead of
# the composer (`qa_agent.answer`'s fall-through seam) — which is how
# `PROJECT_TOOL_NUDGE` (appended below) reaches a plain-Q&A turn.
_PRIVATE_SCOPE_SYSTEM = (
    "You are Sprntly, the user's private project assistant in their one-on-one "
    "chat. Answer the user's question about THIS project directly and concisely. "
    "You have tools to read the project's shared memory, its artifact list, a "
    "specific artifact's content, and its task ledger — call them when the answer "
    "depends on project data rather than guessing. When the user asks for the "
    "whole picture — e.g. 'give me the entire context on this project', 'catch me "
    "up', or 'what's the why and goal here' — first read the project's shared "
    "memory (and its artifacts/ledger as needed), then synthesize the why, the "
    "goal, the current state, who's assigned to what, and prior work — grounded in "
    "what you read, never generic. When the user asks you to change the PRD, the "
    "edit is applied to the document in place and a new version is saved "
    "automatically so the change is undoable — it is NOT queued for approval and "
    "does not need a teammate to manually accept it before it takes effect. Never "
    "describe your role as merely advisory, or claim you cannot edit the PRD, or "
    "say edits must be accepted before they apply. You also have a delegate_task "
    "tool: when the user asks you to hand a specific task to a project teammate "
    "(by name, @handle, or role — resolve them against the roster below), call "
    "it. Do not call it for a plain question, an FYI, or a request aimed at you. "
    "Once you call delegate_task, the handoff has happened — you are done. Do "
    "NOT then do the task yourself, write the deliverable you just handed off, "
    "or say the teammate has replied, finished, or done anything at all — they "
    "have not. Confirm the handoff plainly (\"I've asked <name> to <task> — "
    "I'll bring their answer back here once it's in.\") and stop there; never "
    "end on a fabricated result. Everything you can read is "
    "scoped to this one project; never assume data from another project or "
    "company.\n\n" + PROJECT_TOOL_NUDGE
)


def _private_roster_block(roster: list[dict]) -> str:
    """"PROJECT ROSTER:\n- {first} — {job_role}" — RELOCATED verbatim from
    the deleted `project_individual_agent._roster_prompt_block`, so the
    private surface resolves a free-text assignee ("the designer") against
    the real names/roles on the project."""
    lines = []
    for m in roster:
        name = m.get("name") or "(unnamed)"
        first = name.split()[0] if name != "(unnamed)" else name
        role = m.get("job_role") or "no role set"
        lines.append(f"- {first} — {role}")
    return "PROJECT ROSTER:\n" + ("\n".join(lines) if lines else "(no other members yet)")


@dataclass
class ExecutionOutcome:
    """Contract A — the one result shape every execution surface (main,
    private) hands back from its `body` closure to
    `run_execution_job`. `response` is the citation-stripped answer payload
    that becomes the job row's stored `response`; `error`/`error_class` are
    populated ONLY on the failure path (by the primitive, from the raised
    exception) and `error` is an internal debug string that is NEVER
    broadcast or exposed on any read; `side_effects` is an advisory list of
    tool side-effects the body performed (unused by the primitive itself —
    the retry side-effect gate derives its own truth from the delegation
    ledger, not from this list)."""

    status: Literal["ready", "error", "cancelled"]
    response: dict
    error: str | None = None
    error_class: str | None = None
    side_effects: list[str] = field(default_factory=list)


def _classify_error(exc: BaseException) -> str:
    """Map a raised exception to a typed, user-safe category — never the
    message itself. Order matters only where types could overlap (they do
    not here): a PROVIDER refusal is classified by `app.llm_errors`; a
    transport/asyncio/httpx timeout is `timeout`; a LOCAL FastAPI gate raised
    before/around the model (priority/auth) is `local_gate`; anything else is
    a generic `app` fault.

    THE PROVIDER ARM USED TO BE `billing` FOR EVERY `APIStatusError`, which was
    both too broad and too vague: a malformed request read as a billing
    problem, and a genuinely exhausted account got a label no surface could
    turn into a sentence. `llm_errors` splits it into `provider_limit` /
    `provider_unavailable` / `provider_error`, each with copy the client shows
    — see that module for why an out-of-credits refusal arrives as a 400 and
    cannot be recognised by status code alone. Nothing branched on `billing`
    (it was stored and passed through, never compared), so narrowing it costs
    no consumer.
    """
    import anthropic
    from fastapi import HTTPException

    from app.llm_errors import classify_provider_error

    provider_code = classify_provider_error(exc)
    if provider_code is not None:
        return provider_code
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, anthropic.APITimeoutError)):
        return "timeout"
    try:
        import httpx

        if isinstance(exc, httpx.TimeoutException):
            return "timeout"
    except Exception:  # noqa: BLE001 — httpx always present; guard is defensive
        pass
    if isinstance(exc, HTTPException):
        return "local_gate"
    return "app"


async def _run_heartbeat(heartbeat: Callable[[], bool]) -> None:
    """The shared liveness loop (relocated from `run_ask_job`'s inline
    `beat`): every ORPHAN_ASK_JOB_HEARTBEAT_SECONDS call `heartbeat()` on a
    worker thread; it returns whether the row is still `generating`, so a
    `False` means the row went terminal and there is nothing left to keep
    alive — stop. A blip that raises exits the loop rather than crashing the
    run (a lost beat can only cost the reaper's grace window, never the
    answer). See `_heartbeat`'s docstring for the staging incident this
    prevents."""
    try:
        while True:
            await asyncio.sleep(ORPHAN_ASK_JOB_HEARTBEAT_SECONDS)
            if not await asyncio.to_thread(heartbeat):
                return          # no longer generating — nothing left to keep alive
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a heartbeat failure must never fail the run
        logger.exception("execution heartbeat loop failed")


def _commit_body(
    job_id: int,
    body: Callable[[], "ExecutionOutcome"],
    on_committed: "Callable[[ExecutionOutcome], None] | None",
) -> "ExecutionOutcome":
    """Run the surface `body` on THIS worker thread, then the ONE guarded
    terminal success write (`complete_ask_job`, `.eq('status','generating')`)
    and the post-terminal side effects (`on_committed`) — in that exact
    order, so main/private's `complete → capture_report → promote → ingest`
    ordering (AC2) is preserved and everything stays on the same threadpool
    thread it ran on before the extraction. A raise from `body` propagates
    (the caller classifies it and fails the row); `complete_ask_job` /
    `on_committed` are reached only on the success path."""
    outcome = body()
    complete_ask_job(job_id, outcome.response)
    if on_committed is not None:
        on_committed(outcome)
    return outcome


async def run_execution_job(
    *,
    job_id: int,
    run_id: str,
    is_cancelled: Callable[[], bool],
    heartbeat: Callable[[], bool],
    body: Callable[[], "ExecutionOutcome"],
    on_committed: "Callable[[ExecutionOutcome], None] | None" = None,
) -> "ExecutionOutcome":
    """The shared execution-lifecycle primitive (Contract A) that both main
    and private run through — so every surface *inherits* the lifecycle by
    using the SAME code, not a status-column wrapper. Owns exactly:

    * the async heartbeat loop (so a long-but-live run is never reaped);
    * running the surface `body` on a worker thread and, on success, the ONE
      terminal transition (`complete_ask_job`, guarded on
      `status='generating'`) + the post-terminal `on_committed` side effects;
    * `AskCancelled` → leave the row `cancelled` (the /cancel endpoint already
      wrote it) — NOT a failure;
    * any other exception → classify `error_class` and write the ONE guarded
      terminal fail (`fail_ask_job`, also `.eq('status','generating')`), never
      broadcasting the raw message;
    * cancelling the beat in `finally`.

    Terminal-once is keyed to the run by REUSING the existing guarded writes:
    a late `fail_orphan_generating_ask_jobs` reaper and this worker can never
    both finalize, because whichever writes first flips `status` out of
    `generating` and the other's guarded update no-ops. NO new/unguarded
    UPDATE is introduced here.

    `is_cancelled` is part of Contract A and is wired by the caller into the
    answer call inside `body` (so the body raises `AskCancelled` at a
    checkpoint); the primitive accepts it for surface symmetry. `run_id` is the durable
    execution identity carried for logging/retry correlation. Returns the
    resolved `ExecutionOutcome` so the caller can emit its own surface-specific
    terminal signal (e.g. main/private's `token_stream.close` frame) keyed to
    the outcome — the primitive itself emits none."""
    beat = asyncio.create_task(_run_heartbeat(heartbeat))
    try:
        return await asyncio.to_thread(_commit_body, job_id, body, on_committed)
    except AskCancelled:
        # The row is already `cancelled` (set by the cancel endpoint); leave
        # it. NOT a failure — must never be marked `error`.
        logger.info("execution job cancelled job_id=%s run_id=%s", job_id, run_id)
        return ExecutionOutcome(status="cancelled", response={})
    except Exception as exc:  # noqa: BLE001 — best-effort; never crash the worker
        error_class = _classify_error(exc)
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("execution job failed job_id=%s run_id=%s", job_id, run_id)
        try:
            fail_ask_job(job_id, msg, error_class)
        except Exception:  # noqa: BLE001 — even the fail-marking is best-effort
            logger.exception("fail_ask_job failed job_id=%s", job_id)
        return ExecutionOutcome(
            status="error", response={}, error=msg, error_class=error_class
        )
    finally:
        beat.cancel()


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

    The loop body now lives in the shared `_run_heartbeat` primitive
    (both main and private beat through it); this thin wrapper keeps the
    ask-id-shaped `touch_ask_job` binding and the direct unit coverage in
    `test_ask_job_heartbeat.py`.
    """
    await _run_heartbeat(lambda: touch_ask_job(ask_id))


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


@timed_def("worker:ask")
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
    project_id: int | None = None,
    evidence_id: int | None = None,
    ticket_set_id: int | None = None,
    context_source: dict | None = None,
) -> "ExecutionOutcome":
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
    # The workspace, by the same route: a project list scopes to `(company,
    # workspace, my memberships)`, and this function is the only place on the
    # answer path that holds all three. Cleared in the same `finally` — a
    # pooled thread holding the last ask's workspace would scope the next
    # caller's list to someone else's.
    workspace_token = ask_runner.set_active_workspace_id(workspace_id)
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

    # Pluggable context seam: resolve any caller-supplied `context_source`
    # (`{"kind": str, "params": dict}`) to a `ContextScope` through the
    # assembler registry. The registry is EMPTY in this phase, so
    # `resolve_context_scope` returns None for every ask — `qa_agent.answer()`
    # runs the exact current (unscoped) main path, byte-identical to before.
    scope = resolve_context_scope(
        context_source,
        AssembleRequest(
            user_id=user_id,
            company_id=enterprise_id,
            dataset=dataset,
            conversation_id=conversation_id,
            question=question,
            workspace_id=workspace_id,
            params=(context_source or {}).get("params") or {},
        ),
    )

    # Conv-bind (ported from `b09801dd^:routes/ask.py`): when a project scope
    # resolves, point this conversation at the project — first-write-wins and
    # best-effort (mirrors `bind_conversation_to_prd`), so navigating away
    # mid-generation can't orphan the conversation↔project link. The membership
    # gate already ran inside the assembler (raising before we get here) on the
    # SAME `(company, workspace, member)` facts, so a bind only ever fires for a
    # caller the gate admitted. Never blocks the answer.
    if (
        scope is not None
        and conversation_id is not None
        and context_source
        and context_source.get("kind") == "project"
    ):
        _bind_project_id = ((context_source.get("params") or {}).get("project_id"))
        if _bind_project_id is not None:
            try:
                from app.db.conversations import bind_conversation_to_project

                bind_conversation_to_project(
                    conversation_id, int(_bind_project_id), enterprise_id, user_id
                )
            except Exception:  # noqa: BLE001 — best-effort, never blocks the answer
                logger.warning(
                    "bind_conversation_to_project failed conversation_id=%s "
                    "project_id=%s",
                    conversation_id, _bind_project_id, exc_info=True,
                )

    def _single_shot() -> dict:
        return qa_agent.answer(
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
            scope=scope,
        )

    try:
        payload = _single_shot()
    finally:
        ask_runner.reset_active_conversation(context_token)
        ask_runner.reset_active_workspace_id(workspace_token)
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
    # The terminal SUCCESS write (`complete_ask_job`) and the post-terminal
    # side effects (`capture_report → maybe_promote_turn → maybe_ingest_status`)
    # have moved OUT of this body: `run_execution_job` performs the ONE guarded
    # terminal write, then hands this outcome to `run_ask_job`'s `on_committed`
    # closure (`_run_committed_side_effects`), which runs those three in the
    # SAME order and under the SAME `project_id is not None` gate as before.
    # This body now returns the citation-stripped answer payload; the observable
    # success flow — the stored payload and the post-terminal ordering — is
    # unchanged (AC1/AC2).
    return ExecutionOutcome(status="ready", response=_strip_citations(payload))


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
    project_id: int | None = None,
    context_source: dict | None = None,
) -> None:
    """Run the Ask pipeline in a worker thread; update the job row with the
    result. A failure marks the row `error` and is swallowed — the worker never
    crashes the event loop.

    A thin caller over the shared `run_execution_job` primitive: it mints a
    `run_id`, builds the sync `body` (the `_run_sync` answer work) and the
    `on_committed` post-terminal side effects (`capture_report →
    maybe_promote_turn → maybe_ingest_status`), and lets the primitive own the
    heartbeat + the ONE guarded terminal transition. Behaviour-identical for
    main/private (AC1/AC2): the SSE `close` frame is still emitted here, keyed
    to the resolved outcome (`error → 'error'`, success/cancel → 'done')."""
    logger.info("Ask job starting ask_id=%s dataset=%s", ask_id, dataset)
    loop = asyncio.get_running_loop()
    channel = ask_channel(ask_id)
    run_id = str(uuid.uuid4())

    def _body() -> ExecutionOutcome:
        return _run_sync(
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
            project_id=project_id,
            evidence_id=evidence_id,
            ticket_set_id=ticket_set_id,
            context_source=context_source,
        )

    def _on_committed(outcome: ExecutionOutcome) -> None:
        # Post-terminal side effects, RELOCATED verbatim from `_run_sync`'s
        # tail — run AFTER the guarded `complete_ask_job`, in the same order
        # and under the same `project_id is not None` gate (AC2). Each is
        # self-swallowing, so it can only ever ADD a durable artifact/memory
        # entry, never delay or break the already-stored answer.
        payload = outcome.response
        # A report skill answers with a self-contained HTML document; capture
        # it as a durable `reports` artifact (no-op for a markdown answer).
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
        # Private project chat: promote a durable insight into project
        # memory + ingest inbound task-status — gated on a PROJECT-scoped ask
        # (the assembler resolved a project `SurfaceScope` for this turn, which
        # is exactly the `context_source["kind"] == "project"` condition; a scope
        # that failed to resolve would have failed the answer and never reached
        # this post-terminal `on_committed`). A project chat carries its project
        # on `context_source`, NOT on the top-level `project_id` (which it never
        # sends), so the gate reads the id from `context_source["params"]` — the
        # SAME source the conv-bind in `_run_sync` uses. Best-effort: both
        # `maybe_promote_turn` and `maybe_ingest_status` are self-swallowing
        # (never raise, AD-P7) and are wrapped here besides, so a promotion
        # failure can only fail to ADD a memory entry — it can never delay or
        # break the answer, which is already durably stored by `complete_ask_job`
        # above. Ported from `b09801dd^:ask_job_runner.py`'s `_on_committed`,
        # re-keyed off `context_source` instead of the top-level `project_id`.
        if (
            context_source
            and context_source.get("kind") == "project"
            and conversation_id is not None
        ):
            _promo_project_id = (context_source.get("params") or {}).get("project_id")
            if _promo_project_id is not None:
                try:
                    from app.project_memory import maybe_promote_turn

                    transcript = f"{question}\n\nSprntly: {payload.get('answer', '')}"
                    maybe_promote_turn(
                        int(_promo_project_id), conversation_id, transcript
                    )
                except Exception:  # noqa: BLE001 — best-effort, never fail the answer
                    logger.warning(
                        "maybe_promote_turn failed ask_id=%s project_id=%s",
                        ask_id, _promo_project_id, exc_info=True,
                    )
                try:
                    from app.delegation_status_ingest import maybe_ingest_status

                    maybe_ingest_status(
                        int(_promo_project_id), conversation_id, user_id, question
                    )
                except Exception:  # noqa: BLE001 — best-effort, never fail the answer
                    logger.warning(
                        "maybe_ingest_status failed ask_id=%s project_id=%s",
                        ask_id, _promo_project_id, exc_info=True,
                    )

    outcome = await run_execution_job(
        job_id=ask_id,
        run_id=run_id,
        is_cancelled=lambda: is_ask_cancelled(ask_id),
        heartbeat=lambda: touch_ask_job(ask_id),
        body=_body,
        on_committed=_on_committed,
    )
    if outcome.status == "error":
        logger.info("Ask job failed ask_id=%s", ask_id)
        token_stream.close(channel, kind="error")
    elif outcome.status == "cancelled":
        logger.info("Ask job cancelled ask_id=%s", ask_id)
        # NOT a failure — the row is already `cancelled`; `done` so a client
        # woken by the frame reads the terminal row on its next poll.
        token_stream.close(channel, kind="done")
    else:
        logger.info("Ask job succeeded ask_id=%s", ask_id)
        # Terminal SSE frame AFTER complete_ask_job (inside the primitive) so a
        # client woken by `done` reads a `ready` row on its next poll.
        token_stream.close(channel, kind="done")

