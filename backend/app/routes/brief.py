import asyncio
import logging

from fastapi import Depends, APIRouter, HTTPException

from app.auth import require_session
from app.brief_runner import auto_generate_brief, get_status
from app.config import settings
from app.corpus import load_corpus
from app.db import get_brief_by_id, get_current_brief, save_brief
from app.llm import call_json
from app.prompts import BRIEF_SCHEMA_VERSION, BRIEF_SYSTEM, BRIEF_USER_TEMPLATE
from app.synthesis_brief import generate_brief_for

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/brief", tags=["brief"])


async def _synthesis_generate_bg(dataset: str) -> None:
    """Background body for /regenerate under the synthesis engine.

    Mirrors auto_generate_brief's posture: seed-if-empty + run_synthesis runs
    off the event loop (it makes blocking LLM/Supabase calls); failures are
    logged, never raised — the service keeps serving the prior cached brief.
    run_synthesis save_brief()s the new brief, so /current picks it up.
    """
    try:
        await asyncio.to_thread(generate_brief_for, dataset)
        logger.info("Synthesis brief generated for %s", dataset)
    except Exception:  # noqa: BLE001 — fire-and-forget; prior brief stays
        logger.exception("Synthesis brief generation failed for %s", dataset)


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
    """Force a fresh brief generation in the background. Returns immediately.

    Use case: API key was just fixed, want to retry without restarting the
    service. Existing cached brief (if any) stays in place until the new
    generation completes successfully.

    Engine selection (BRIEF_ENGINE): "synthesis" (default) runs the KG
    seed-if-empty → run_synthesis path; "legacy" keeps the placeholder
    corpus→Claude path. Response contract is identical either way.
    """
    if settings.brief_engine == "synthesis":
        asyncio.create_task(_synthesis_generate_bg(dataset))
    else:
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

    Engine selection (BRIEF_ENGINE): "synthesis" (default) runs the KG
    seed-if-empty → run_synthesis path (which save_brief()s the result); we
    then read it back to preserve the {brief_id, **payload} response shape.
    "legacy" keeps the placeholder corpus→Claude path.
    """
    if settings.brief_engine == "synthesis":
        try:
            payload = generate_brief_for(dataset)
        except ValueError as e:
            # Unknown dataset/company or an empty KG even after seeding.
            raise HTTPException(409, str(e)) from e
        saved = get_current_brief(dataset)
        brief_id = saved.get("id") if saved else None
        return {"brief_id": brief_id, **payload}

    corpus = load_corpus(dataset)
    try:
        from app.signal_fusion import fuse_signals
        signal_context = fuse_signals(dataset)
    except Exception:
        signal_context = ""
    user = BRIEF_USER_TEMPLATE.format(
        dataset=dataset, signal_context=signal_context, corpus=corpus.joined(),
    )
    payload = call_json(system=BRIEF_SYSTEM, user=user)
    week_label = payload.get("week_label", "")
    brief_id = save_brief(
        dataset=dataset,
        week_label=week_label,
        payload=payload,
        schema_version=BRIEF_SCHEMA_VERSION,
    )
    return {"brief_id": brief_id, **payload}
