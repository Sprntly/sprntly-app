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
    user = BRIEF_USER_TEMPLATE.format(dataset=dataset, corpus=corpus.joined())
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
    if find_existing_evidence(brief_id, insight_index):
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
    """Generate a PRD for (brief_id, insight_index) unless one is already
    cached. Errors are swallowed for the same reason as _warm_evidence.
    """
    if find_existing_prd(brief_id, insight_index):
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
    )
    logger.info(
        "Warming PRD prd_id=%s brief_id=%s insight_index=%s (waiting on sema)",
        prd_id,
        brief_id,
        insight_index,
    )
    async with _WARM_SEMA:
        await generate_prd(prd_id, brief_id, insight_index)


def _warm_drilldowns(brief: dict) -> None:
    """Fan out evidence + PRD generation for every insight in this brief.
    Warm tasks share a semaphore (_WARM_SEMA) so at most 2 run at once;
    cached entries return immediately (dedupe lives in _warm_evidence /
    _warm_prd).

    Evidence is scheduled before PRD because users click "View evidence"
    first — they shouldn't queue behind PRD warming.
    """
    brief_id = brief.get("id")
    if not brief_id:
        return
    insights = brief.get("insights") or []
    # Pass 1: all evidence — these get the early semaphore slots.
    for i, ins in enumerate(insights):
        title = (ins or {}).get("title") or f"Insight #{i + 1}"
        asyncio.create_task(_warm_evidence(brief_id, i, title))
    # Pass 2: all PRDs — they wait behind evidence for sema slots.
    for i, ins in enumerate(insights):
        title = (ins or {}).get("title") or f"Insight #{i + 1}"
        asyncio.create_task(_warm_prd(brief_id, i, title))


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
    _warm_drilldowns(brief)


# Datasets the service will auto-generate briefs for on startup.
AUTO_DATASETS: tuple[str, ...] = ("asurion",)


async def auto_generate_all() -> None:
    for dataset in AUTO_DATASETS:
        await auto_generate_brief(dataset)
