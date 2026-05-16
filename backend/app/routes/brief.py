import asyncio

from fastapi import APIRouter, Cookie, HTTPException

from app.auth import require_session
from app.brief_runner import auto_generate_brief, get_status
from app.corpus import load_corpus
from app.db import get_brief_by_id, get_current_brief, save_brief
from app.llm import call_json
from app.prompts import BRIEF_SCHEMA_VERSION, BRIEF_SYSTEM, BRIEF_USER_TEMPLATE

router = APIRouter(prefix="/v1/brief", tags=["brief"])


@router.get("/current")
def current(
    dataset: str,
    sprintly_session: str | None = Cookie(default=None),
):
    """Return the latest cached brief for a dataset.

    If none exists yet, returns 404 with a body that includes the current
    auto-generation status (`empty | generating | failed`). Frontend can
    poll `/v1/brief/status` while this is anything other than `ready`.
    """
    require_session(sprintly_session)
    brief = get_current_brief(dataset)
    if brief:
        return brief
    raise HTTPException(404, {"message": "No brief generated yet", **get_status(dataset)})


@router.get("/status")
def status(
    dataset: str,
    sprintly_session: str | None = Cookie(default=None),
):
    """Lightweight poll endpoint for the frontend.

    Status values:
      - "ready": brief is in the cache, fetch it via /v1/brief/current
      - "generating": auto-gen is in flight; retry in a few seconds
      - "failed": last attempt failed (see `error`); will retry on service restart
      - "empty": nothing has been attempted yet
    """
    require_session(sprintly_session)
    return {"dataset": dataset, **get_status(dataset)}


@router.post("/regenerate")
async def regenerate(
    dataset: str,
    sprintly_session: str | None = Cookie(default=None),
):
    """Force a fresh brief generation in the background. Returns immediately.

    Use case: API key was just fixed, want to retry without restarting the
    service. Existing cached brief (if any) stays in place until the new
    generation completes successfully.
    """
    require_session(sprintly_session)
    asyncio.create_task(auto_generate_brief(dataset))
    return {"started": True, "dataset": dataset}


@router.get("/{brief_id}")
def by_id(
    brief_id: int,
    sprintly_session: str | None = Cookie(default=None),
):
    require_session(sprintly_session)
    brief = get_brief_by_id(brief_id)
    if not brief:
        raise HTTPException(404, "Brief not found")
    return brief


@router.post("/generate")
def generate(
    dataset: str,
    sprintly_session: str | None = Cookie(default=None),
):
    """Synchronously generate a fresh brief and return it.

    Note: invokes Claude. Costs tokens. Blocks until done (~30s). Use
    /v1/brief/regenerate for fire-and-forget behavior instead.
    """
    require_session(sprintly_session)
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
