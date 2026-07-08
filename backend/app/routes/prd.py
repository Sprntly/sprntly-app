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
from app.db.backlog import get_backlog_item
from app.db.briefs import get_current_brief
from app.db.companies import slug_for_company_id
from app.db.prd_input_questions import (
    answer_question,
    get_question,
    list_questions,
)
from app.db.prds import (
    find_existing_prd_for_theme,
    get_prd,
    latest_prd_for_dataset,
    list_prd_generations,
    list_prd_versions,
    restore_prd_version,
    save_prd_version,
    update_prd_content,
)
from app.deps.ownership import require_owned_brief, require_owned_dataset, require_owned_prd
from app.prd_runner import (
    PRD_VARIANT, ensure_impl_spec, generate_prd, generate_prd_and_warm,
)
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
        generate_prd_and_warm(
            prd_id, body.brief_id, body.insight_index,
            author=company.user_name,
        )
    )
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
    return {
        "prd_id": prd_id,
        "status": "generating",
        "title": title,
        "variant": PRD_VARIANT,
    }


# Sentinel insight_index for backlog PRDs. The theme isn't in brief.insights, so
# there is no real index; backlog PRDs dedupe + group by (brief_id, theme_id)
# instead (see db.prds.find_existing_prd_for_theme / list_prd_generations).
_BACKLOG_INSIGHT_INDEX = 0


class BacklogGenerateIn(BaseModel):
    backlog_item_id: str = Field(..., min_length=1)
    force: bool = False


@router.post("/generate-from-backlog")
async def generate_from_backlog(
    body: BacklogGenerateIn,
    company: CompanyContext = Depends(require_company),
):
    """Kick off PRD generation for a BACKLOG item (a theme ranked ≥ 4 that never
    made the weekly brief's top-3).

    Backlog themes aren't in brief.insights, so we synthesize an insight from the
    backlog row ({theme_id, title, summary}) — the same shape the KG evidence
    trail and PRD prompt consume — and attach the PRD to the company's CURRENT
    brief for tenant/dataset grounding. Dedup + version history key on
    (brief_id, theme_id). Fire-and-forget like /generate: returns the prd_id
    immediately; poll GET /v1/prd/{prd_id} until status == 'ready'.
    """
    # Tenant gate: the item must belong to the caller's company (404 otherwise).
    item = get_backlog_item(company.company_id, body.backlog_item_id)
    if item is None:
        raise HTTPException(404, "Backlog item not found")

    # Backlog PRDs anchor to the company's current brief (dataset grounding).
    # No brief ⇒ no analysis ⇒ nothing to ground a PRD on.
    slug = slug_for_company_id(company.company_id)
    brief = get_current_brief(slug) if slug else None
    if not brief:
        raise HTTPException(
            409, "No current brief for this company — generate a brief first."
        )
    brief_id = brief["id"]

    theme_id = item.get("theme_id")
    title = item.get("title") or "Backlog item"
    # Synthetic insight — mirrors a brief insight so the KG trail (theme_id) and
    # the PRD prompt (title/summary) resolve identically to the brief path.
    insight = {
        "theme_id": theme_id,
        "title": title,
        "summary": item.get("reasoning") or "",
        "hypothesis_id": item.get("hypothesis_id"),
    }

    if not body.force and theme_id:
        existing = find_existing_prd_for_theme(
            brief_id, theme_id, variant=PRD_VARIANT
        )
        if existing:
            return {
                "prd_id": existing["id"],
                "status": existing["status"],
                "title": existing["title"],
                "variant": PRD_VARIANT,
            }

    prd_id = start_prd(
        brief_id=brief_id,
        insight_index=_BACKLOG_INSIGHT_INDEX,
        title=title,
        template_version=PRD_TEMPLATE_VERSION,
        variant=PRD_VARIANT,
        source="backlog",
        theme_id=theme_id,
    )
    # Lifecycle: mark the theme prd_created so it lands in the Completed tab
    # (keyed by theme_id — works identically for backlog and brief themes).
    _record_prd_action(company.company_id, insight)

    task = asyncio.create_task(
        generate_prd_and_warm(
            prd_id, brief_id, _BACKLOG_INSIGHT_INDEX, insight_override=insight,
            author=company.user_name,
        )
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


# ── "User input needed" questions: surface in chat + answer → scoped edit ──────


@router.get("/{prd_id}/input-questions")
def get_input_questions(
    prd_id: int,
    company: CompanyContext = Depends(require_company),
):
    """List the PRD's structured "User input needed" questions.

    These are extracted from the PRD at generation time (best-effort) and
    surfaced in the PRD's chat as messages with answer buttons. Returns every
    question (pending + answered) so a reopened chat stays consistent; the client
    renders pending ones as actionable and answered ones as resolved.
    """
    require_owned_prd(prd_id, company.company_id)
    return {"questions": list_questions(prd_id)}


class InputAnswerIn(BaseModel):
    answer: str = Field(..., min_length=1)


@router.post("/{prd_id}/input-questions/{question_id}/answer")
def answer_input_question(
    prd_id: int,
    question_id: int,
    body: InputAnswerIn,
    company: CompanyContext = Depends(require_company),
):
    """Answer one "User input needed" question and fold the answer into the PRD.

    Runs the SCOPED editor (app.prd_questions.apply_answer) — NOT a full
    prd-author re-run — over the PRD's current HTML, rewriting only the sections
    the answer affects and self-clearing the answered item from the "User input
    needed" list. The edit is saved through the normal version-snapshot path (so
    it is undoable), the question is marked answered, and the rendered PRD +
    changed-section list are returned so the chat can confirm and the panel can
    refresh live.
    """
    # Import here to keep the module import graph lean (the editor pulls the LLM
    # gateway) and mirror the lazy-import discipline used elsewhere in this file.
    from app.prd_questions import apply_answer

    row = require_owned_prd(prd_id, company.company_id)
    question = get_question(question_id)
    if not question or question.get("prd_id") != prd_id:
        raise HTTPException(404, "Question not found")

    # Edit the RAW payload_md (the pure PRD HTML). Design-agent 'applied' patches
    # are appended on read by get_prd_rendered; editing the raw doc and storing it
    # back keeps those patches folding once (no double-fold).
    prd_html = (row.get("payload_md") or "").strip()
    if not prd_html:
        raise HTTPException(409, "PRD has no content to edit")

    try:
        edit = apply_answer(
            prd_html, question["prompt"], body.answer, enterprise_id=company.company_id
        )
    except RuntimeError as exc:
        # The scoped edit produced nothing usable — leave the PRD untouched and
        # surface it rather than storing an empty document.
        raise HTTPException(502, f"Could not apply the answer: {exc}")

    # Snapshot the pre-edit content so the change is undoable (mirrors PUT /{id}).
    try:
        save_prd_version(prd_id, row.get("title", ""), prd_html, saved_by="auto")
    except Exception:
        logger.warning(
            "auto-version snapshot failed for prd_id=%s before input-answer edit "
            "(undo point not captured)", prd_id, exc_info=True,
        )

    update_prd_content(prd_id, row.get("title", ""), edit["html"])
    answered = answer_question(question_id, body.answer, answered_by=company.user_name)

    return {
        "prd": get_prd_rendered(prd_id),
        "question": answered,
        "sections_changed": edit["sections_changed"],
        "summary": edit["summary"],
    }
