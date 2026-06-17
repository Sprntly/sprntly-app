import asyncio
import logging

from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.brief_runner import get_status, set_status, warm_synthesis_drilldowns
from app.db import get_current_brief
from app.db.companies import display_name_for_slug
from app.db.finding_state import set_finding_action
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


class DismissIn(BaseModel):
    """Identify the brief finding to dismiss, either directly by theme_id or by
    (brief_id, insight_index) which we resolve to a theme_id via the brief
    payload. Exactly one form is required."""

    theme_id: str | None = None
    brief_id: int | None = Field(default=None, ge=1)
    insight_index: int | None = Field(default=None, ge=0)


@router.post("/dismiss")
def dismiss(
    body: DismissIn,
    company: CompanyContext = Depends(require_company),
):
    """Record that the user dismissed a brief finding (action='dismissed').

    Phase 2 lifecycle: a dismissed finding is NOT 'completed' (completed =
    prd_created | done) — it simply won't reappear as a fresh brief finding via
    the de-dup memory it shares. The frontend keeps its localStorage dismiss UX;
    this server record makes the action durable + theme-keyed.

    Accepts either a raw `theme_id` or a (`brief_id`, `insight_index`) pair which
    is resolved to the theme_id from the owned brief's payload."""
    theme_id = body.theme_id
    if not theme_id:
        if body.brief_id is None or body.insight_index is None:
            raise HTTPException(
                400, "Provide either theme_id or (brief_id and insight_index)"
            )
        # Tenant gate + resolve insight → theme_id from the brief payload.
        brief = require_owned_brief(body.brief_id, company.company_id)
        insights = brief.get("insights") or []
        if not (0 <= body.insight_index < len(insights)):
            raise HTTPException(
                400,
                f"insight_index={body.insight_index} out of range "
                f"(0..{len(insights) - 1})",
            )
        theme_id = insights[body.insight_index].get("theme_id")
        if not theme_id:
            raise HTTPException(400, "Insight carries no theme_id; cannot dismiss")

    set_finding_action(company.company_id, theme_id, "dismissed")
    return {"dismissed": True, "theme_id": theme_id}


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
