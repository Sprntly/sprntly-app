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
import json
import logging

from pathlib import Path

from fastapi import Depends, APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth import WorkspaceContext, require_company, require_workspace, require_workspace_from_query  # noqa: F401 — re-exported for tests' dependency_overrides
from app.graph import token_stream
from app.db import (
    find_existing_prd,
    get_prd_rendered,
    start_prd,
)
from app.db.ideation import get_ideation_item
from app.db.briefs import ensure_uploads_brief, get_current_brief
from app.ingest import convert
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
    PRD_VARIANT, ensure_impl_spec, extract_input_questions_task, generate_prd,
    generate_prd_and_warm,
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
    surfaces in the Ideation screen's Completed tab on the next brief. Swallows
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
    company: WorkspaceContext = Depends(require_workspace),
):
    """Kick off PRD generation in the background.

    Returns immediately with the prd_id. If a ready/generating PRD
    already exists for (brief, insight) and `force` is false, returns
    the existing row.
    """
    # Tenant gate: the body's brief_id must belong to the caller's company
    # (404 on mismatch — no cross-tenant existence disclosure).
    brief = require_owned_brief(body.brief_id, company.company_id, company.workspace_id)
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


# Sentinel insight_index for ideation PRDs. The theme isn't in brief.insights,
# so there is no real index; ideation PRDs dedupe + group by (brief_id, theme_id)
# instead (see db.prds.find_existing_prd_for_theme / list_prd_generations).
_IDEATION_INSIGHT_INDEX = 0


class IdeationGenerateIn(BaseModel):
    ideation_item_id: str = Field(..., min_length=1)
    force: bool = False


@router.post("/generate-from-ideation")
async def generate_from_ideation(
    body: IdeationGenerateIn,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Kick off PRD generation for an IDEATION item (a theme ranked ≥ 4 that
    never made the weekly brief's top-3).

    Ideation themes aren't in brief.insights, so we synthesize an insight from
    the ideation row ({theme_id, title, summary}) — the same shape the KG
    evidence trail and PRD prompt consume — and attach the PRD to the company's
    CURRENT brief for tenant/dataset grounding. Dedup + version history key on
    (brief_id, theme_id). Fire-and-forget like /generate: returns the prd_id
    immediately; poll GET /v1/prd/{prd_id} until status == 'ready'.
    """
    # Tenant gate: the item must belong to the caller's company (404 otherwise).
    item = get_ideation_item(company.company_id, body.ideation_item_id)
    if item is None:
        raise HTTPException(404, "Ideation item not found")

    # Ideation PRDs anchor to the company's current brief (dataset grounding).
    # No brief ⇒ no analysis ⇒ nothing to ground a PRD on.
    slug = slug_for_company_id(company.company_id)
    brief = get_current_brief(slug) if slug else None
    if not brief:
        raise HTTPException(
            409, "No current brief for this company — generate a brief first."
        )
    brief_id = brief["id"]

    theme_id = item.get("theme_id")
    title = item.get("title") or "Ideation item"
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
        insight_index=_IDEATION_INSIGHT_INDEX,
        title=title,
        template_version=PRD_TEMPLATE_VERSION,
        variant=PRD_VARIANT,
        source="ideation",
        theme_id=theme_id,
    )
    # Lifecycle: mark the theme prd_created so it lands in the Completed tab
    # (keyed by theme_id — works identically for ideation and brief themes).
    _record_prd_action(company.company_id, insight)

    task = asyncio.create_task(
        generate_prd_and_warm(
            prd_id, brief_id, _IDEATION_INSIGHT_INDEX, insight_override=insight,
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


# Uploaded PRDs don't index a brief (they have no KG lineage). insight_index is a
# storage sentinel only — the synthetic insight is passed via insight_override.
_IMPORT_INSIGHT_INDEX = 0

# Guard: reject absurdly large uploads before reading into memory. A text PRD or
# a slide deck is comfortably under this; bigger is almost certainly not a PRD.
_MAX_IMPORT_BYTES = 25 * 1024 * 1024  # 25 MB


@router.post("/import")
async def import_prd(
    file: UploadFile = File(...),
    dataset: str = Form(...),
    company: WorkspaceContext = Depends(require_workspace),
):
    """Import an existing PRD the customer uploaded (PDF/PPT/DOCX/…).

    Parses the file to text (no LLM — `app.ingest.convert`), then generates a
    normal PRD from it via the prd-author skill in FAITHFUL RE-LAYOUT mode
    (`import_source_md`): the doc's content is restructured into our format,
    inventing nothing. The result is a standard `prds` row (source='upload') so
    it lands in Artifacts and drives Tickets → Jira exactly like any other PRD —
    no KG/brief required (it anchors to the per-company uploads brief).

    Fire-and-forget: inserts a 'generating' row and schedules generation; poll
    GET /v1/prd/{prd_id} until status == 'ready'.
    """
    # Tenant gate: the dataset (company slug) must belong to the caller (404).
    require_owned_dataset(dataset, company.company_id, company.workspace_id)

    data = await file.read()
    if not data:
        raise HTTPException(400, "Uploaded file is empty.")
    if len(data) > _MAX_IMPORT_BYTES:
        raise HTTPException(413, "File too large (max 25 MB).")

    # Parse to text in a worker thread — pypdf/python-pptx are blocking.
    extracted = await asyncio.to_thread(convert, file.filename or "upload", data)
    if not extracted.strip():
        raise HTTPException(
            422,
            "Could not extract any text from the uploaded file. Scanned/image-only "
            "PDFs and legacy .ppt are not supported — export to PDF or .pptx.",
        )

    title = (Path(file.filename or "").stem or "Imported PRD").strip()

    # Anchor to the per-company uploads brief (prds.brief_id is NOT NULL).
    brief_id = ensure_uploads_brief(dataset)

    # Synthetic insight — carries the title so the PRD prompt + decision log
    # resolve identically to the brief/ideation paths (no theme_id: no KG trail).
    insight = {"title": title, "summary": "Imported from an uploaded document."}

    prd_id = start_prd(
        brief_id=brief_id,
        insight_index=_IMPORT_INSIGHT_INDEX,
        title=title,
        template_version=PRD_TEMPLATE_VERSION,
        variant=PRD_VARIANT,
        source="upload",
    )

    task = asyncio.create_task(
        generate_prd_and_warm(
            prd_id,
            brief_id,
            _IMPORT_INSIGHT_INDEX,
            insight_override=insight,
            author=company.user_name,
            import_source_md=extracted,
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
    company: WorkspaceContext = Depends(require_workspace),
):
    """Return the most recent ready PRD for a dataset (company slug).

    Used by the PRD screen to auto-load the last generated PRD on refresh
    instead of showing an empty pane.
    """
    require_owned_dataset(dataset, company.company_id, company.workspace_id)
    row = latest_prd_for_dataset(dataset)
    if not row:
        raise HTTPException(404, "No PRD found for this workspace")
    rendered = get_prd_rendered(row["id"])
    return rendered or row


@router.get("/{prd_id}")
def get(
    prd_id: int,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Fetch a PRD row by id (only if it belongs to the caller's company)."""
    require_owned_prd(prd_id, company.company_id, company.workspace_id)
    row = get_prd_rendered(prd_id)
    if not row:
        raise HTTPException(404, "PRD not found")
    return row


@router.get("/{prd_id}/stream")
async def stream_prd_generation(
    prd_id: int,
    company: WorkspaceContext = Depends(require_workspace_from_query),
) -> StreamingResponse:
    """SSE token stream of a PRD's Part A generation, so the client renders the
    PRD as it's written instead of waiting for the whole document.

    EventSource can't send headers, so the bearer rides as `?token=`
    (require_workspace_from_query). Frames: `{"kind":"delta","text":…}` as the HTML
    streams, then a terminal `{"kind":"done"|"error"}`. PROGRESSIVE DISPLAY ONLY
    — the client keeps polling GET /{prd_id}, which stays the authoritative
    source for the finished, persisted PRD. Single-worker transport (see
    app.graph.token_stream); on multi-worker this yields nothing and the poll
    still carries the result. Opening late (generation already finished) simply
    receives no frames — the poll shows the completed PRD.
    """
    require_owned_prd(prd_id, company.company_id, company.workspace_id)  # 404 on cross-tenant/missing
    channel = f"prd:{prd_id}"

    async def _gen():
        async for event in token_stream.subscribe(channel):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Send to Claude Code: on-demand Implementation Spec ─────────────────


@router.post("/{prd_id}/impl-spec")
def generate_impl_spec(
    prd_id: int,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Produce the machine-readable Implementation Spec for a PRD on demand.

    Backs the PRD's "Send to Claude Code" action. The spec is generated the first
    time (via the `implementation-spec` skill, fed the finished human PRD) and
    cached in the PRD row; re-sends reuse the cache until the human PRD changes.
    Synchronous: the caller shows a loading state and pastes the returned
    `llm_part` into Claude Code. Returns {"llm_part": ..., "cached": ...}.
    """
    require_owned_prd(prd_id, company.company_id, company.workspace_id)
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
    company: WorkspaceContext = Depends(require_workspace),
):
    """Save PRD edits to Supabase. Auto-creates a version snapshot."""
    row = require_owned_prd(prd_id, company.company_id, company.workspace_id)
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
    company: WorkspaceContext = Depends(require_workspace),
):
    """Explicitly save a named version of the PRD."""
    require_owned_prd(prd_id, company.company_id, company.workspace_id)
    version = save_prd_version(prd_id, body.title, body.payload_md, saved_by=body.label)
    return version


@router.get("/{prd_id}/versions")
def get_versions(
    prd_id: int,
    company: WorkspaceContext = Depends(require_workspace),
):
    """List all versions of a PRD, newest first."""
    require_owned_prd(prd_id, company.company_id, company.workspace_id)
    return list_prd_versions(prd_id)


@router.get("/{prd_id}/generations")
def get_generations(
    prd_id: int,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Prior generations of this PRD (other prds rows sharing the same
    brief+insight), newest first — the regeneration history surfaced in the
    PRD's Version History dropdown."""
    require_owned_prd(prd_id, company.company_id, company.workspace_id)
    return {"generations": list_prd_generations(prd_id)}


@router.post("/{prd_id}/versions/{version_id}/restore")
def restore_version(
    prd_id: int,
    version_id: int,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Restore a PRD to a specific version."""
    require_owned_prd(prd_id, company.company_id, company.workspace_id)
    result = restore_prd_version(prd_id, version_id)
    if not result:
        raise HTTPException(404, "Version not found")
    return result


# ── "User input needed" questions: surface in chat + answer → scoped edit ──────


# The prd-author template renders unresolved [ESCALATE]/[NEED] items under this
# section eyebrow, and the section is SELF-CLEARING (the scoped answer editor
# removes it once the last item resolves) — so its presence in the stored HTML
# is a reliable "this PRD still has extractable input items" signal, checked
# without any LLM call.
_INPUT_SECTION_MARKER = "User input needed"


@router.get("/{prd_id}/input-questions")
async def get_input_questions(
    prd_id: int,
    company: WorkspaceContext = Depends(require_workspace),
):
    """List the PRD's structured "User input needed" questions.

    These are extracted from the PRD at generation time (best-effort) and
    surfaced in the PRD's chat as messages with answer buttons. Returns every
    question (pending + answered) so a reopened chat stays consistent; the client
    renders pending ones as actionable and answered ones as resolved.

    Lazy backfill: PRDs generated before extraction existed (most of what the
    Artifacts screen opens) have a "User input needed" section in the document
    but no stored questions. When such a PRD is opened, schedule the SAME
    best-effort extraction in the background and answer `extracting: true`; the
    client polls until the rows land. The single-flight registry in
    app.prd_questions makes concurrent opens (and the generation-time run for a
    just-finished PRD, whose first fetch can race the pipeline's extraction)
    schedule exactly one run.
    """
    row = require_owned_prd(prd_id, company.company_id, company.workspace_id)
    questions = list_questions(prd_id)
    if questions:
        return {"questions": questions, "extracting": False}

    from app.prd_questions import is_extracting, mark_extracting

    if is_extracting(prd_id):
        return {"questions": [], "extracting": True}
    if (
        row.get("status") == "ready"
        and _INPUT_SECTION_MARKER in (row.get("payload_md") or "")
        and mark_extracting(prd_id)
    ):
        task = asyncio.create_task(extract_input_questions_task(prd_id, reserved=True))
        _inflight_tasks.add(task)
        task.add_done_callback(_inflight_tasks.discard)
        return {"questions": [], "extracting": True}
    return {"questions": [], "extracting": False}


class InputAnswerIn(BaseModel):
    answer: str = Field(..., min_length=1)


@router.post("/{prd_id}/input-questions/{question_id}/answer")
def answer_input_question(
    prd_id: int,
    question_id: int,
    body: InputAnswerIn,
    company: WorkspaceContext = Depends(require_workspace),
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

    row = require_owned_prd(prd_id, company.company_id, company.workspace_id)
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
