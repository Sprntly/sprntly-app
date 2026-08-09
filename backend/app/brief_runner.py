"""Brief generation status + drill-down warming helpers.

After a synthesis brief is generated and saved (by the brief route, the
scheduler, or the startup pass), `warm_synthesis_drilldowns` warms the
per-insight evidence/Ask drill-downs so the first user click renders instantly.

PRDs are NOT pre-warmed: a PRD is the most expensive drill-down (a large
2-part LLM generation, minutes each), so warming one per insight floods the
warm queue and a user's "Generate PRD" click ends up stuck behind the warm
backlog. PRDs are generated strictly on-demand instead — the on-demand path
(`routes/prd.py` → `prd_runner.generate_prd`) runs unthrottled (it does not
acquire `_WARM_SEMA`), so a click runs immediately.
"""
import asyncio
import logging
from weakref import WeakKeyDictionary

from app.db import (
    find_existing_evidence,
    get_current_brief,
    start_evidence,
)
from app.ask_runner import warm_brief_dynamic_asks, warm_predefined_asks
from app.evidence_runner import generate_evidence
from app.prd_runner import warm_prds_for_brief
from app.prompts import EVIDENCE_TEMPLATE_VERSION, EVIDENCE_VARIANT

logger = logging.getLogger(__name__)

# In-memory transient state. Single uvicorn worker → a dict is fine.
_status: dict[str, str] = {}
_errors: dict[str, str] = {}

# Bound how many drill-down warming tasks run at once. Anthropic handles
# parallel calls, but firing several in a burst on every restart competes for
# rate limit and bandwidth — each call ends up slower than in isolation,
# and a user clicking "View evidence" during the burst waits behind the
# queue. 3 lets all 3 evidence calls for a brief run concurrently so no
# insight queues behind another. PRDs are NOT warmed (they run on-demand,
# unthrottled), so this only caps evidence + Ask warming.
_WARM_CONCURRENCY = 3

# Per-event-loop warm semaphore. A module-level Semaphore is bound to the loop
# active when it is first awaited; warm_synthesis_drilldowns runs the fan-out via
# a FRESH `asyncio.run(...)` loop on every scheduler/startup pass (the no-running-
# loop path), so a single shared Semaphore raised "bound to a different event
# loop" and every cached-Ask warm failed. Keyed weakly by loop so closed loops
# are GC'd (no leak across the many short-lived asyncio.run loops).
_warm_semas: "WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    WeakKeyDictionary()
)


def _warm_sema() -> asyncio.Semaphore:
    """The warm-concurrency semaphore bound to the CURRENT running loop.

    Must be called from within a running loop (every warming entry point runs on
    one — either the request loop or the per-pass `asyncio.run` loop)."""
    loop = asyncio.get_running_loop()
    sema = _warm_semas.get(loop)
    if sema is None:
        sema = asyncio.Semaphore(_WARM_CONCURRENCY)
        _warm_semas[loop] = sema
    return sema

# Strong refs to in-flight warm tasks. asyncio holds only a weak reference to a
# bare create_task result, so without this a fanned-out evidence warm task
# can be garbage-collected mid-run. The done-callback discards each on
# completion (mirrors routes/design_agent.py's _inflight_tasks). The drain in
# _warm_drilldowns_to_completion still works — it gathers asyncio.all_tasks().
_inflight_tasks: set[asyncio.Task] = set()


def _track(task: asyncio.Task) -> asyncio.Task:
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
    return task


def get_status(dataset: str) -> dict:
    """Return one of: ready, generating, failed, empty (+ error message if any).

    When a brief is already cached, `status` stays "ready" (so the frontend keeps
    the current brief on screen), but a regeneration running *over* that cached
    brief is surfaced with an additive `regenerating: True` flag. Without this the
    in-flight regen is invisible — `_status[dataset]` is "generating" but the
    cached brief short-circuits us to "ready" — so the home surface can't show a
    "refreshing your brief" indicator while a fresh brief is being built.
    """
    if get_current_brief(dataset):
        out: dict = {"status": "ready"}
        if _status.get(dataset) == "generating":
            out["regenerating"] = True
        return out
    s = _status.get(dataset, "empty")
    out = {"status": s}
    if s == "failed" and dataset in _errors:
        out["error"] = _errors[dataset]
    return out


def set_status(dataset: str, status: str, *, error: str | None = None) -> None:
    """Update the in-memory brief generation status for a dataset.

    Used by the synthesis brief generation path so the frontend poll endpoint
    (/v1/brief/status) always reflects progress.
    """
    _status[dataset] = status
    if error is not None:
        _errors[dataset] = error[:300]
    elif dataset in _errors:
        _errors.pop(dataset, None)


async def _warm_evidence(brief_id: int, insight_index: int, title: str) -> None:
    """Generate an evidence doc for (brief_id, insight_index) unless one is
    already cached. Errors are swallowed — drill-down warming is a perf
    optimization, not a correctness requirement.
    """
    if find_existing_evidence(brief_id, insight_index, variant=EVIDENCE_VARIANT):
        logger.info(
            "Evidence already cached brief_id=%s insight_index=%s, skipping warm",
            brief_id,
            insight_index,
        )
        return
    ev_id = start_evidence(
        brief_id=brief_id,
        insight_index=insight_index,
        title=title,
        template_version=EVIDENCE_TEMPLATE_VERSION,
        variant=EVIDENCE_VARIANT,
    )
    logger.info(
        "Warming evidence ev_id=%s brief_id=%s insight_index=%s (waiting on sema)",
        ev_id,
        brief_id,
        insight_index,
    )
    async with _warm_sema():
        # background=True: warming rides the LLM gate's low-priority lane so a
        # user's own generation (tickets, PRD, evidence click) never queues
        # behind the post-brief warm storm.
        await generate_evidence(ev_id, brief_id, insight_index, background=True)


def _warm_drilldowns(brief: dict, dataset: str | None = None) -> None:
    """Fan out evidence generation for every insight in this brief, plus
    warming for predefined Ask Sprntly starter prompts.

    Warm tasks share a semaphore (_WARM_SEMA) so at most a few run at once;
    cached entries return immediately (dedupe lives in _warm_evidence /
    ask_runner._warm_one).

    PRDs warm too, but through a different throttle: warm_prds_for_brief fans
    out the top-N insights' human PRDs concurrently in the LLM gate's BACKGROUND
    lane (app.llm._PriorityGate) — which still bounds in-flight warm model-calls
    (bg_cap) and lets every interactive caller (including a user's "Generate PRD"
    click) jump ahead. That removes the old reason PRDs were on-demand only:
    warming can no longer stall a click behind the backlog. Each warm PRD is
    run_id-stamped so the multi-agent click path dedupes against it.
    Ask warming runs in parallel with evidence, hitting the same throughput cap.
    """
    brief_id = brief.get("id")
    if not brief_id:
        return
    insights = brief.get("insights") or []
    # All evidence — these get the early semaphore slots.
    for i, ins in enumerate(insights):
        title = (ins or {}).get("title") or f"Insight #{i + 1}"
        _track(asyncio.create_task(_warm_evidence(brief_id, i, title)))
    # Predefined Ask Sprntly starter prompts. The home + Ask chips
    # send a fixed set of questions; pre-generating responses means the
    # demo's first click renders instantly.
    if dataset:
        warm_predefined_asks(dataset, _warm_sema())
        # Pass 4: per-insight Ask prompts. Clicking "Ask Sprntly" on a
        # finding card in the BriefScreen fires "Tell me more about: <title>"
        # — those titles are known at brief-gen time, so we warm them too.
        warm_brief_dynamic_asks(dataset, brief, _warm_sema())
    # PRDs for the top insights — background-lane only (see docstring), so
    # this never competes with evidence/Ask warming or a user click.
    _track(asyncio.create_task(warm_prds_for_brief(brief)))


async def _warm_drilldowns_to_completion(brief: dict, dataset: str | None = None) -> None:
    """Run the same warming fan-out as `_warm_drilldowns`, but awaited to
    completion rather than fire-and-forget.

    Used when warming is invoked from a no-loop (startup worker thread) context
    via `asyncio.run`: the loop closes as soon as this returns, so the warm
    tasks must finish before then rather than being scheduled and abandoned.
    """
    _warm_drilldowns(brief, dataset=dataset)
    # Drain every warm task scheduled on this loop (evidence/Ask) so they
    # complete before the loop is torn down. Their own runners are error-
    # isolated, so failures here are swallowed (return_exceptions).
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def warm_synthesis_drilldowns(dataset: str) -> None:
    """Warm evidence/Ask drill-downs for the synthesis brief of `dataset`.

    After a synthesis brief is generated+saved, pre-generate the per-insight
    drill-downs so the first user click renders instantly. Reads the
    freshly-saved brief back (so it carries the DB id +
    insight titles _warm_drilldowns needs) and fans out the same warming.

    Works from either context: when a loop is already running (the synthesis
    background/scheduler coroutines) it schedules the warm tasks on it
    (fire-and-forget, unchanged); when invoked from a no-loop context (the
    startup pass runs on a worker thread via `asyncio.to_thread`, which has no
    event loop) it spins up a loop with `asyncio.run` and drains the fan-out to
    completion — so startup warming actually runs instead of throwing
    "no running event loop".

    Error-isolated: warming is a perf optimization, never a correctness
    requirement, so a failure here must not break brief generation.
    """
    try:
        brief = get_current_brief(dataset)
        if brief is None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (startup worker thread) — run the fan-out to
            # completion on a fresh loop.
            asyncio.run(_warm_drilldowns_to_completion(brief, dataset=dataset))
        else:
            # A loop is already running — schedule tasks on it as before.
            _warm_drilldowns(brief, dataset=dataset)
    except Exception:  # noqa: BLE001 — warming is best-effort; brief must survive
        logger.exception("Synthesis drill-down warming failed for %s", dataset)

