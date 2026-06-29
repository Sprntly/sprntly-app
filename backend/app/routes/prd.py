"""PRD endpoints.

Trigger via:

    POST /v1/prd/generate  {"brief_id": N, "insight_index": M, "force": false}
    GET  /v1/prd/{prd_id}

The POST is fire-and-forget: it inserts a row in `generating` state,
schedules `generate_prd` in the background, and returns the prd_id
immediately. Poll the GET until status == 'ready'.

Rows live in the `prds` table. New rows are stored with variant='v2'
(the current PRD format); historical v1 rows from before the promotion
remain readable but are no longer generated. The GET is permissive —
it returns any row by id regardless of variant so old bookmarks keep
resolving.
"""
import asyncio
import logging

from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.db import (
    find_existing_prd,
    get_prd_rendered,
    start_prd,
)
from app.db.prds import (
    get_prd,
    latest_prd_for_dataset,
    list_prd_generations,
    list_prd_versions,
    restore_prd_version,
    save_prd_version,
    update_prd_content,
)
from app.deps.ownership import require_owned_brief, require_owned_dataset, require_owned_prd
from app.prd_runner import PRD_VARIANT, ensure_impl_spec, generate_prd
from app.prompts import PRD_TEMPLATE_VERSION

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/prd", tags=["prd"])


# Strong refs to in-flight background generation tasks. asyncio holds only a
# weak reference to a bare create_task result, so without this the task can be
# garbage-collected mid-run and the row would be stuck 'generating'. The
# done-callback discards each task on completion (mirrors routes/design_agent.py).
_inflight_tasks: set[asyncio.Task] = set()


def _record_prd_action(company_id: str, insight: dict) -> None:
    """Best-effort: mark the insight's theme as 'prd_created' for the lifecycle.

    Resolves the insight → theme_id from the brief payload (insights carry
    `theme_id`, set by the synthesis ranker) and records the action so the theme
    surfaces in the Backlog screen's Completed tab on the next brief. Swallows
    everything: a finding-state hiccup must never fail PRD generation."""
    try:
        theme_id = insight.get("theme_id")
        if not theme_id:
            return
        from app.db.finding_state import set_finding_action

        set_finding_action(company_id, theme_id, "prd_created")
    except Exception:  # noqa: BLE001 — lifecycle bookkeeping is non-critical
        logger.exception("failed to record prd_created finding action")


class GenerateIn(BaseModel):
    brief_id: int = Field(..., ge=1)
    insight_index: int = Field(..., ge=0)
    force: bool = False


@router.post("/generate")
async def generate(
    body: GenerateIn,
    company: CompanyContext = Depends(require_company),
):
    """Kick off PRD generation in the background.

    Returns immediately with the prd_id. If a ready/generating PRD
    already exists for (brief, insight) and `force` is false, returns
    the existing row.
    """
    # Tenant gate: the body's brief_id must belong to the caller's company
    # (404 on mismatch — no cross-tenant existence disclosure).
    brief = require_owned_brief(body.brief_id, company.company_id)
    insights = brief.get("insights") or []
    if not (0 <= body.insight_index < len(insights)):
        raise HTTPException(
            400,
            f"insight_index={body.insight_index} out of range "
            f"(0..{len(insights) - 1})",
        )

    if not body.force:
        existing = find_existing_prd(
            body.brief_id, body.insight_index, variant=PRD_VARIANT
        )
        if existing:
            return {
                "prd_id": existing["id"],
                "status": existing["status"],
                "title": existing["title"],
                "variant": PRD_VARIANT,
            }

    insight = insights[body.insight_index]
    title = insight.get("title") or f"Insight #{body.insight_index + 1}"
    prd_id = start_prd(
        brief_id=body.brief_id,
        insight_index=body.insight_index,
        title=title,
        template_version=PRD_TEMPLATE_VERSION,
        variant=PRD_VARIANT,
    )
    # Phase 2 lifecycle: record that the user created a PRD for this brief
    # finding so it lands in the Completed section once the next brief arrives.
    # Best-effort — must never break PRD creation. The insight's theme_id is
    # carried in the saved brief payload; enterprise_id == company_id (same
    # convention convergence/finding_state use).
    _record_prd_action(company.company_id, insight)

    task = asyncio.create_task(
        generate_prd(prd_id, body.brief_id, body.insight_index)
    )
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
    return {
        "prd_id": prd_id,
        "status": "generating",
        "title": title,
        "variant": PRD_VARIANT,
    }


@router.get("/latest")
def latest(
    dataset: str,
    company: CompanyContext = Depends(require_company),
):
    """Return the most recent ready PRD for a dataset (company slug).

    Used by the PRD screen to auto-load the last generated PRD on refresh
    instead of showing an empty pane.
    """
    require_owned_dataset(dataset, company.company_id)
    row = latest_prd_for_dataset(dataset)
    if not row:
        raise HTTPException(404, "No PRD found for this workspace")
    rendered = get_prd_rendered(row["id"])
    return rendered or row


@router.get("/{prd_id}")
def get(
    prd_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Fetch a PRD row by id (only if it belongs to the caller's company)."""
    require_owned_prd(prd_id, company.company_id)
    row = get_prd_rendered(prd_id)
    if not row:
        raise HTTPException(404, "PRD not found")
    return row


# ── Send to Claude Code: on-demand Implementation Spec ─────────────────


@router.post("/{prd_id}/impl-spec")
def generate_impl_spec(
    prd_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Produce the machine-readable Implementation Spec for a PRD on demand.

    Backs the PRD's "Send to Claude Code" action. The spec is generated the first
    time (via the `implementation-spec` skill, fed the finished human PRD) and
    cached in the PRD row; re-sends reuse the cache until the human PRD changes.
    Synchronous: the caller shows a loading state and pastes the returned
    `llm_part` into Claude Code. Returns {"llm_part": ..., "cached": ...}.
    """
    require_owned_prd(prd_id, company.company_id)
    try:
        return ensure_impl_spec(prd_id)
    except RuntimeError as exc:
        # Missing row / empty human PRD — nothing to build a spec from.
        raise HTTPException(404, str(exc))


# ── PRD editing + version control ──────────────────────────────────────


class PrdUpdateIn(BaseModel):
    title: str = Field(..., min_length=1)
    payload_md: str = Field(...)


@router.put("/{prd_id}")
def update(
    prd_id: int,
    body: PrdUpdateIn,
    company: CompanyContext = Depends(require_company),
):
    """Save PRD edits to Supabase. Auto-creates a version snapshot."""
    row = require_owned_prd(prd_id, company.company_id)
    # Save current content as a version before overwriting
    try:
        save_prd_version(prd_id, row.get("title", ""), row.get("payload_md", ""), saved_by="auto")
    except Exception:
        # Non-blocking: a failed snapshot must not fail the save. But don't
        # swallow it silently — a lost auto-version is the user's undo point
        # vanishing, so surface it in the logs (e.g. version table missing).
        logger.warning(
            "auto-version snapshot failed for prd_id=%s — proceeding with save "
            "(undo point not captured)", prd_id, exc_info=True,
        )
    updated = update_prd_content(prd_id, body.title, body.payload_md)
    return updated


class PrdVersionSaveIn(BaseModel):
    title: str = Field(..., min_length=1)
    payload_md: str = Field(...)
    label: str = Field("Manual save")


@router.post("/{prd_id}/versions")
def create_version(
    prd_id: int,
    body: PrdVersionSaveIn,
    company: CompanyContext = Depends(require_company),
):
    """Explicitly save a named version of the PRD."""
    require_owned_prd(prd_id, company.company_id)
    version = save_prd_version(prd_id, body.title, body.payload_md, saved_by=body.label)
    return version


@router.get("/{prd_id}/versions")
def get_versions(
    prd_id: int,
    company: CompanyContext = Depends(require_company),
):
    """List all versions of a PRD, newest first."""
    require_owned_prd(prd_id, company.company_id)
    return list_prd_versions(prd_id)


@router.get("/{prd_id}/generations")
def get_generations(
    prd_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Prior generations of this PRD (other prds rows sharing the same
    brief+insight), newest first — the regeneration history surfaced in the
    PRD's Version History dropdown."""
    require_owned_prd(prd_id, company.company_id)
    return {"generations": list_prd_generations(prd_id)}


@router.post("/{prd_id}/versions/{version_id}/restore")
def restore_version(
    prd_id: int,
    version_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Restore a PRD to a specific version."""
    require_owned_prd(prd_id, company.company_id)
    result = restore_prd_version(prd_id, version_id)
    if not result:
        raise HTTPException(404, "Version not found")
    return result
