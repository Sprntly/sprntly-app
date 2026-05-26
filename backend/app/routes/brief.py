import asyncio
import logging

from fastapi import Depends, APIRouter, HTTPException

from app.auth import require_session
from app.brief.comprehensive import run_brief_comprehensive
from app.brief_runner import auto_generate_brief, get_status
from app.corpus import load_corpus
from app.db import get_brief_by_id, get_current_brief, save_brief
from app.graph import GraphFacade
from app.llm import call_json
from app.prompts import BRIEF_SCHEMA_VERSION, BRIEF_SYSTEM, BRIEF_USER_TEMPLATE
from app.synthesis.brief_assembly import assemble_brief

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/brief", tags=["brief"])


def _get_graph_facade() -> GraphFacade:
    """Per-request facade. The from_env() path picks up GRAPH_BACKEND
    (sqlite by default) and is safe to invoke repeatedly — no network
    state."""
    facade = GraphFacade.from_env()
    facade.initialize()
    return facade


@router.get("/current")
def current(
    dataset: str,
    _session: dict = Depends(require_session),
):
    """Return the latest cached brief for a dataset.

    If none exists yet, returns 404 with a body that includes the current
    auto-generation status (`empty | generating | failed`). Frontend can
    poll `/v1/brief/status` while this is anything other than `ready`.
    """
    brief = get_current_brief(dataset)
    if brief:
        return brief
    raise HTTPException(404, {"message": "No brief generated yet", **get_status(dataset)})


@router.get("/status")
def status(
    dataset: str,
    _session: dict = Depends(require_session),
):
    """Lightweight poll endpoint for the frontend.

    Status values:
      - "ready": brief is in the cache, fetch it via /v1/brief/current
      - "generating": auto-gen is in flight; retry in a few seconds
      - "failed": last attempt failed (see `error`); will retry on service restart
      - "empty": nothing has been attempted yet
    """
    return {"dataset": dataset, **get_status(dataset)}


@router.post("/regenerate")
async def regenerate(
    dataset: str,
    _session: dict = Depends(require_session),
):
    """Run the Synthesis Agent's scheduled-mode 11-step Brief Assembly
    pipeline against the KG for `dataset` (treated as the workspace_id
    until full multi-tenant routing lands).

    Spec source: Synthesis_Agent_Spec §3.2. Replaces the monolithic
    `brief_runner.auto_generate_brief` flow that called Claude in one
    shot with no structured cross-source reasoning. The legacy runner
    remains importable (other callers may still depend on it during
    the migration window) but is no longer wired to this endpoint.

    Returns 200 + the generated Brief synchronously. The new pipeline
    completes in seconds — it does ONE LLM call, not five — so we
    don't need the legacy fire-and-forget shape.
    """
    workspace_id = dataset  # transitional — slug doubles as workspace_id
    graph = _get_graph_facade()
    try:
        brief = await asyncio.to_thread(
            assemble_brief,
            workspace_id,
            None,  # no DS Agent output in this transitional path
            graph,
            call_json,
        )
    except Exception as exc:  # pragma: no cover — bubble up for observability
        logger.exception("Brief regenerate failed for dataset=%s", dataset)
        raise HTTPException(502, f"Brief regeneration failed: {exc}") from exc
    return {"started": True, "dataset": dataset, "brief": brief.model_dump(mode="json")}


@router.post("/comprehensive/regenerate")
async def regenerate_comprehensive(
    dataset: str,
    _session: dict = Depends(require_session),
):
    """Manual trigger for the Monday Comprehensive flow.

    Spec source: Master PRD §4.2. Runs DS Comprehensive + Synthesis 11-step
    assembly and persists the result. Subsequent calls within the same
    ISO-week hit the `cached_briefs` row this call writes (the Monday
    scheduled run overwrites it). Use `/v1/brief/regenerate` for the
    Synthesis-only path (faster, signal-only, no DS).
    """
    workspace_id = dataset  # transitional — slug doubles as workspace_id
    graph = _get_graph_facade()
    try:
        brief = await asyncio.to_thread(
            run_brief_comprehensive,
            workspace_id,
            dataset,
            graph,
            call_json,
            None,  # ds_runner — default lazy-imports ds_agent
        )
    except Exception as exc:  # pragma: no cover — observability bubble-up
        logger.exception(
            "Comprehensive Brief regenerate failed for dataset=%s", dataset
        )
        raise HTTPException(
            502, f"Comprehensive Brief regeneration failed: {exc}"
        ) from exc
    return {
        "started": True,
        "dataset": dataset,
        "tier": "comprehensive",
        "brief": brief.model_dump(mode="json"),
    }


@router.post("/regenerate-legacy")
async def regenerate_legacy(
    dataset: str,
    _session: dict = Depends(require_session),
):
    """Legacy: fire-and-forget regeneration through the monolithic
    `brief_runner`. Kept for migration so the existing UI button
    doesn't break while we cut over to the 11-step pipeline."""
    asyncio.create_task(auto_generate_brief(dataset))
    return {"started": True, "dataset": dataset}


@router.get("/{brief_id}")
def by_id(
    brief_id: int,
    _session: dict = Depends(require_session),
):
    brief = get_brief_by_id(brief_id)
    if not brief:
        raise HTTPException(404, "Brief not found")
    return brief


@router.post("/generate")
def generate(
    dataset: str,
    _session: dict = Depends(require_session),
):
    """Synchronously generate a fresh brief and return it.

    Note: invokes Claude. Costs tokens. Blocks until done (~30s). Use
    /v1/brief/regenerate for fire-and-forget behavior instead.
    """
    corpus = load_corpus(dataset)
    user = BRIEF_USER_TEMPLATE.format(dataset=dataset, corpus=corpus.joined())
    payload = call_json(system=BRIEF_SYSTEM, user=user)
    week_label = payload.get("week_label", "")
    brief_id = save_brief(
        dataset=dataset,
        week_label=week_label,
        payload=payload,
        schema_version=BRIEF_SCHEMA_VERSION,
    )
    return {"brief_id": brief_id, **payload}
