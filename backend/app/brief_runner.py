"""Background brief generation. Kicked off on app startup; ensures a brief
exists for each configured dataset, and also warms the per-insight
evidence + PRD drill-downs so the first user click renders instantly.
"""
import asyncio
import logging

from app.corpus import load_corpus
from app.db import (
    find_existing_evidence,
    find_existing_prd,
    get_current_brief,
    save_brief,
    start_evidence,
    start_prd,
)
from app.ask_runner import warm_brief_dynamic_asks, warm_predefined_asks
from app.evidence_runner import generate_evidence
from app.llm import call_json
from app.prd_runner import generate_prd
from app.prompts import (
    BRIEF_SCHEMA_VERSION,
    BRIEF_SYSTEM,
    BRIEF_USER_TEMPLATE,
    EVIDENCE_TEMPLATE_VERSION,
    PRD_TEMPLATE_VERSION,
)

logger = logging.getLogger(__name__)

# In-memory transient state. Single uvicorn worker → a dict is fine.
_status: dict[str, str] = {}
_errors: dict[str, str] = {}

# Bound how many drill-down warming tasks run at once. Anthropic handles
# parallel calls, but firing 6+ in a burst on every restart competes for
# rate limit and bandwidth — each call ends up slower than in isolation,
# and a user clicking "View evidence" during the burst waits behind the
# queue. 3 lets all 3 evidence calls for a brief run concurrently so no
# insight queues behind another; PRD warming fills slots as they free.
_WARM_SEMA = asyncio.Semaphore(3)

# Strong refs to in-flight warm tasks. asyncio holds only a weak reference to a
# bare create_task result, so without this a fanned-out evidence/PRD warm task
# can be garbage-collected mid-run. The done-callback discards each on
# completion (mirrors routes/design_agent.py's _inflight_tasks). The drain in
# _warm_drilldowns_to_completion still works — it gathers asyncio.all_tasks().
_inflight_tasks: set[asyncio.Task] = set()


def _track(task: asyncio.Task) -> asyncio.Task:
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
    return task


def get_status(dataset: str) -> dict:
    """Return one of: ready, generating, failed, empty (+ error message if any)."""
    if get_current_brief(dataset):
        return {"status": "ready"}
    s = _status.get(dataset, "empty")
    out: dict = {"status": s}
    if s == "failed" and dataset in _errors:
        out["error"] = _errors[dataset]
    return out


def _run_sync(dataset: str) -> None:
    corpus = load_corpus(dataset)

    # Signal fusion: rank sources by freshness, confidence, and diversity
    try:
        from app.signal_fusion import fuse_signals
        signal_context = fuse_signals(dataset)
    except Exception:
        signal_context = ""

    user = BRIEF_USER_TEMPLATE.format(
        dataset=dataset,
        signal_context=signal_context,
        corpus=corpus.joined(),
    )
    payload = call_json(system=BRIEF_SYSTEM, user=user)
    save_brief(
        dataset,
        payload.get("week_label", ""),
        payload,
        schema_version=BRIEF_SCHEMA_VERSION,
    )


async def _warm_evidence(brief_id: int, insight_index: int, title: str) -> None:
    """Generate an evidence doc for (brief_id, insight_index) unless one is
    already cached. Errors are swallowed — drill-down warming is a perf
    optimization, not a correctness requirement.
    """
    if find_existing_evidence(brief_id, insight_index, variant="v2"):
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
        variant="v2",
    )
    logger.info(
        "Warming evidence ev_id=%s brief_id=%s insight_index=%s (waiting on sema)",
        ev_id,
        brief_id,
        insight_index,
    )
    async with _WARM_SEMA:
        await generate_evidence(ev_id, brief_id, insight_index)


async def _warm_prd(brief_id: int, insight_index: int, title: str) -> None:
    """Generate a v2 PRD for (brief_id, insight_index) unless one is
    already cached. Errors are swallowed for the same reason as
    _warm_evidence.

    Variant must be pinned to "v2" everywhere: without it, find_existing_prd
    defaults to variant="v1" and matches legacy v1 rows left over from
    before the v2 promotion — the warmer concludes "cached, skip" while
    the prd route (which writes/reads v2) still has nothing to dedupe
    against, and the user pays the full LLM cost on first click.
    """
    if find_existing_prd(brief_id, insight_index, variant="v2"):
        logger.info(
            "PRD already cached brief_id=%s insight_index=%s, skipping warm",
            brief_id,
            insight_index,
        )
        return
    prd_id = start_prd(
        brief_id=brief_id,
        insight_index=insight_index,
        title=title,
        template_version=PRD_TEMPLATE_VERSION,
        variant="v2",
    )
    logger.info(
        "Warming PRD prd_id=%s brief_id=%s insight_index=%s (waiting on sema)",
        prd_id,
        brief_id,
        insight_index,
    )
    async with _WARM_SEMA:
        await generate_prd(prd_id, brief_id, insight_index)


def _warm_drilldowns(brief: dict, dataset: str | None = None) -> None:
    """Fan out evidence + PRD generation for every insight in this brief,
    plus warming for predefined Ask Sprntly starter prompts.

    Warm tasks share a semaphore (_WARM_SEMA) so at most a few run at once;
    cached entries return immediately (dedupe lives in _warm_evidence /
    _warm_prd / ask_runner._warm_one).

    Evidence is scheduled before PRD because users click "View evidence"
    first — they shouldn't queue behind PRD warming. Ask warming runs in
    parallel with both, hitting the same throughput cap.
    """
    brief_id = brief.get("id")
    if not brief_id:
        return
    insights = brief.get("insights") or []
    # Pass 1: all evidence — these get the early semaphore slots.
    for i, ins in enumerate(insights):
        title = (ins or {}).get("title") or f"Insight #{i + 1}"
        _track(asyncio.create_task(_warm_evidence(brief_id, i, title)))
    # Pass 2: all PRDs — they wait behind evidence for sema slots.
    for i, ins in enumerate(insights):
        title = (ins or {}).get("title") or f"Insight #{i + 1}"
        _track(asyncio.create_task(_warm_prd(brief_id, i, title)))
    # Pass 3: predefined Ask Sprntly starter prompts. The home + Ask chips
    # send a fixed set of questions; pre-generating responses means the
    # demo's first click renders instantly.
    if dataset:
        warm_predefined_asks(dataset, _WARM_SEMA)
        # Pass 4: per-insight Ask prompts. Clicking "Ask Sprntly" on a
        # finding card in the BriefScreen fires "Tell me more about: <title>"
        # — those titles are known at brief-gen time, so we warm them too.
        warm_brief_dynamic_asks(dataset, brief, _WARM_SEMA)


async def _warm_drilldowns_to_completion(brief: dict, dataset: str | None = None) -> None:
    """Run the same warming fan-out as `_warm_drilldowns`, but awaited to
    completion rather than fire-and-forget.

    Used when warming is invoked from a no-loop (startup worker thread) context
    via `asyncio.run`: the loop closes as soon as this returns, so the warm
    tasks must finish before then rather than being scheduled and abandoned.
    """
    _warm_drilldowns(brief, dataset=dataset)
    # Drain every warm task scheduled on this loop (evidence/PRD/Ask) so they
    # complete before the loop is torn down. Their own runners are error-
    # isolated, so failures here are swallowed (return_exceptions).
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def warm_synthesis_drilldowns(dataset: str) -> None:
    """Warm evidence/PRD/Ask drill-downs for the synthesis brief of `dataset`.

    Parity with the legacy path: after a synthesis brief is generated+saved,
    pre-generate the per-insight drill-downs so the first user click renders
    instantly. Reads the freshly-saved brief back (so it carries the DB id +
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


async def auto_generate_brief(dataset: str) -> None:
    """Generate a brief for `dataset` if one doesn't already exist, then warm
    the per-insight drill-downs (evidence + PRD).

    Errors during brief generation are logged and stored on `_errors[dataset]`;
    the service keeps serving. Drill-down warming failures are logged inside
    the runners and don't block subsequent work.
    """
    brief = get_current_brief(dataset)
    if brief is None:
        _status[dataset] = "generating"
        _errors.pop(dataset, None)
        logger.info("Auto-generating brief for %s ...", dataset)
        try:
            await asyncio.to_thread(_run_sync, dataset)
            _status[dataset] = "ready"
            logger.info("Brief generated for %s", dataset)
        except Exception as exc:
            _status[dataset] = "failed"
            _errors[dataset] = f"{type(exc).__name__}: {exc}"[:300]
            logger.exception("Brief generation failed for %s", dataset)
            return
        brief = get_current_brief(dataset)
    else:
        logger.info("Brief already cached for %s, skipping auto-generate", dataset)
    if brief is None:
        return
    _warm_drilldowns(brief, dataset=dataset)


async def auto_generate_all() -> None:
    """Generate briefs for every dataset registered in the DB.

    Replaces the previous hardcoded `AUTO_DATASETS = ("asurion",)` tuple.
    A startup hook in main.py seeds the table from disk first, so existing
    on-disk corpora are picked up automatically.
    """
    from app.db import list_dataset_slugs
    for dataset in list_dataset_slugs():
        await auto_generate_brief(dataset)
