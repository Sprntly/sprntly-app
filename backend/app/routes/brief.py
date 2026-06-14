import asyncio
import logging

from fastapi import Depends, APIRouter, HTTPException

from app.auth import CompanyContext, require_company
from app.brief_runner import get_status, set_status, warm_synthesis_drilldowns
from app.db import get_current_brief
from app.db.companies import display_name_for_slug
from app.deps.ownership import require_owned_brief, require_owned_dataset
from app.synthesis.agent import EmptyKnowledgeGraphError
from app.synthesis_brief import generate_brief_for

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/brief", tags=["brief"])

# Strong refs to in-flight background brief-generation tasks (see the note in
# routes/design_agent.py): without this, the bare create_task result can be
# garbage-collected mid-run and the regenerate would silently die.
_inflight_tasks: set[asyncio.Task] = set()


def _track(task: asyncio.Task) -> asyncio.Task:
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
    return task


def _with_company_name(brief: dict) -> dict:
    """Attach the company's display name to a brief payload.

    The `dataset` slug is an internal key (db / infra-api only) — the UI must
    never have to render it. `company_name` is None for legacy demo datasets
    that have no companies row; the frontend falls back to the slug then.
    """
    return {**brief, "company_name": display_name_for_slug(brief.get("dataset") or "")}


async def _synthesis_generate_bg(dataset: str) -> None:
    """Background body for /regenerate under the synthesis engine.

    Seed-if-empty + run_synthesis runs off the event loop (it makes blocking
    LLM/Supabase calls); failures are
    logged, never raised — the service keeps serving the prior cached brief.
    run_synthesis save_brief()s the new brief, so /current picks it up.
    """
    set_status(dataset, "generating")
    try:
        await asyncio.to_thread(generate_brief_for, dataset)
        set_status(dataset, "ready")
        logger.info("Synthesis brief generated for %s", dataset)
    except EmptyKnowledgeGraphError:
        # Benign: new company with no data yet. Mark failed with a helpful
        # message so the frontend can tell the user what to do.
        set_status(dataset, "failed",
                   error="No data to generate a brief from yet — upload files "
                         "or connect a data source, then regenerate.")
        logger.info("Synthesis brief skipped for %s — KG empty after seeding", dataset)
        return
    except Exception:  # noqa: BLE001 — fire-and-forget; prior brief stays
        set_status(dataset, "failed",
                   error="Brief generation failed — check server logs.")
        logger.exception("Synthesis brief generation failed for %s", dataset)
        return
    # Warm the per-insight drill-downs so the first click is instant.
    # Error-isolated inside the
    # helper, so it can never undo the brief we just generated.
    warm_synthesis_drilldowns(dataset)


@router.get("/current")
def current(
    dataset: str,
    company: CompanyContext = Depends(require_company),
):
    """Return the latest cached brief for a dataset.

    If none exists yet, returns 404 with a body that includes the current
    auto-generation status (`empty | generating | failed`). Frontend can
    poll `/v1/brief/status` while this is anything other than `ready`.
    """
    # Tenant gate: the dataset slug must resolve to the caller's company.
    require_owned_dataset(dataset, company.company_id)
    brief = get_current_brief(dataset)
    if brief:
        return _with_company_name(brief)
    raise HTTPException(404, {"message": "No brief generated yet", **get_status(dataset)})


@router.get("/status")
def status(
    dataset: str,
    company: CompanyContext = Depends(require_company),
):
    """Lightweight poll endpoint for the frontend.

    Status values:
      - "ready": brief is in the cache, fetch it via /v1/brief/current
      - "generating": auto-gen is in flight; retry in a few seconds
      - "failed": last attempt failed (see `error`); will retry on service restart
      - "empty": nothing has been attempted yet
    """
    require_owned_dataset(dataset, company.company_id)
    return {"dataset": dataset, **get_status(dataset)}


@router.post("/regenerate")
async def regenerate(
    dataset: str,
    company: CompanyContext = Depends(require_company),
):
    """Force a fresh brief generation in the background. Returns immediately.

    Use case: API key was just fixed, want to retry without restarting the
    service. Existing cached brief (if any) stays in place until the new
    generation completes successfully.

    Runs the KG seed-if-empty → run_synthesis path in the background.
    """
    require_owned_dataset(dataset, company.company_id)
    _track(asyncio.create_task(_synthesis_generate_bg(dataset)))
    return {"started": True, "dataset": dataset}


@router.get("/{brief_id}")
def by_id(
    brief_id: int,
    company: CompanyContext = Depends(require_company),
):
    # require_owned_brief resolves brief → dataset → company and 404s on
    # mismatch (or a missing brief), returning the brief row on success.
    return _with_company_name(require_owned_brief(brief_id, company.company_id))


@router.post("/generate")
def generate(
    dataset: str,
    company: CompanyContext = Depends(require_company),
):
    """Synchronously generate a fresh brief and return it.

    Note: invokes Claude. Costs tokens. Blocks until done (~30s). Use
    /v1/brief/regenerate for fire-and-forget behavior instead.

    Runs the KG seed-if-empty → run_synthesis path (which save_brief()s the
    result); we then read it back to preserve the {brief_id, **payload}
    response shape.
    """
    require_owned_dataset(dataset, company.company_id)
    try:
        payload = generate_brief_for(dataset)
    except ValueError as e:
        # Unknown dataset/company or an empty KG even after seeding.
        raise HTTPException(409, str(e)) from e
    saved = get_current_brief(dataset)
    brief_id = saved.get("id") if saved else None
    return _with_company_name({"brief_id": brief_id, "dataset": dataset, **payload})
