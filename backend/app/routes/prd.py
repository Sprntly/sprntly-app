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
import hashlib
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
from app.db.artifact_shares import get_or_mint_canonical_share
from app.db.ideation import get_ideation_item
from app.db.briefs import ensure_uploads_brief, get_current_brief
from app.db.conversations import bind_conversation_to_prd
from app.db.evidences import find_existing_evidence_for_theme
from app.project_from_prd import maybe_auto_create_project_for_prd
from app.evidence_kg import generate_task_evidence
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
    mark_prd_generating,
    resolve_prd_id_by_public_id,
    restore_prd_version,
    save_prd_version,
    update_prd_content,
)
from app.deps.ownership import require_owned_brief, require_owned_dataset, require_owned_prd
from app.prd_command import classify_prd_command
from app.prd_runner import (
    PRD_VARIANT, ensure_impl_spec, extract_input_questions_task, generate_prd,
    generate_prd_and_warm, regenerate_prd_into_template,
)
from app.prompts import (
    EVIDENCE_TEMPLATE_VERSION,
    EVIDENCE_VARIANT,
    PRD_TEMPLATE_VERSION,
)

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

    # Best-effort eager mint of the canonical share token — the PRD Share
    # dropdown reads a pre-existing token rather than minting on open, so
    # the token should exist by the time the panel first renders. Must NEVER
    # break PRD creation: the GET-time lazy fallback (see get()/latest()
    # below) backfills a token on first read if this fails or is skipped.
    try:
        get_or_mint_canonical_share(
            artifact_type="prd", artifact_id=prd_id,
            owner_company_id=company.company_id,
            owner_workspace_id=company.workspace_id,
            created_by_user_id=company.user_id,
        )
    except Exception:  # noqa: BLE001 — eager mint is best-effort, never fatal
        logger.warning("eager canonical share mint failed for prd_id=%s", prd_id)

    task = asyncio.create_task(
        generate_prd_and_warm(
            prd_id, body.brief_id, body.insight_index,
            author=company.user_name,
            company_id=company.company_id, user_id=company.user_id,
            prd_title=title,
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
    never made the Top Insights brief's top-3).

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


# Chat-task PRDs: "generate a PRD for <specific need>" typed in chat. The user's
# own words are the source — there is no brief insight and no KG theme, so
# insight_index is a storage sentinel and dedup keys on a synthetic theme_id
# derived from the normalized task text: re-issuing the same ask returns the
# same PRD (find-or-create, like the brief/ideation paths).
_CHAT_TASK_INSIGHT_INDEX = 0
_CHAT_TASK_TITLE_MAX = 90


def _chat_task_theme_id(task: str, artifact_template_id: str | None = None) -> str:
    """Deterministic dedup key for a chat task ('chat:<sha1[:16]>' of the
    whitespace-collapsed, lowercased text). Same precedent as ideation's
    'manual:%' synthetic theme ids — never resolves in the KG.

    THE FORMAT IS PART OF THE KEY, when one was asked for. Without this, asking
    for the same PRD in a different format returns the FIRST one unchanged
    (find-or-create matches on the task text alone) — so "regenerate this in our
    Acme format" would hand back the document in the old format and report
    success, which is the exact silent-substitution failure the rest of this
    feature exists to prevent. Same task in a different format is a different
    document.

    The no-format key is byte-identical to what this function has always
    returned, deliberately: every chat PRD ever generated is keyed on it, and
    changing it would make find-or-create miss on all of them and quietly
    regenerate duplicates of documents that already exist."""
    norm = " ".join(task.lower().split())
    if artifact_template_id:
        norm = f"{norm}|tpl:{artifact_template_id}"
    return "chat:" + hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def _requested_template_id(
    company_id: str, template_id: str | None, artifact_type: str = "prd"
) -> str | None:
    """Validate a format the caller asked for, or None when they asked for none.

    Refused HERE, at the route, rather than at generation time — see
    `artifact_templates.store.require_usable_template` for why that placement is
    the whole point: once a generation is running, its only options are to
    substitute a different format silently or to fail the document, and the user
    is no longer in a position to pick.

    404 for a missing-or-foreign id (indistinguishable, like every other
    ownership check here); 409 for a format that exists but cannot write this
    kind of document, or has never compiled cleanly."""
    if not template_id or not template_id.strip():
        return None
    from app.artifact_templates.store import (
        TemplateNotFound,
        TemplateStoreError,
        require_usable_template,
    )

    try:
        row = require_usable_template(company_id, template_id.strip(), artifact_type)
    except TemplateNotFound as exc:
        raise HTTPException(404, str(exc))
    except TemplateStoreError as exc:
        raise HTTPException(409, str(exc))
    return row["id"]


def _chat_task_title(task: str) -> str:
    """A row/tab title derived from the task text: collapsed whitespace,
    trailing punctuation stripped, first letter capitalized, word-truncated."""
    title = " ".join(task.split()).strip().rstrip(".!?,;: ")
    if len(title) > _CHAT_TASK_TITLE_MAX:
        title = title[:_CHAT_TASK_TITLE_MAX].rsplit(" ", 1)[0].rstrip(".!?,;: ") + "…"
    return (title[:1].upper() + title[1:]) if title else "Chat PRD"


class TaskSourceDoc(BaseModel):
    """A document the user attached earlier in the chat thread — extracted
    text, forwarded so the PRD grounds on it (the reported bug: a doc attached
    two messages before "generate a PRD" was silently forgotten)."""
    name: str = Field(..., min_length=1, max_length=300)
    content: str = Field(..., min_length=1, max_length=60_000)


# Total budget across all attached docs — mirrors the ask path's 100k clamp.
_TASK_SOURCE_DOCS_TOTAL_MAX = 150_000


class TaskGenerateIn(BaseModel):
    task: str = Field(..., min_length=3, max_length=4000)
    force: bool = False
    source_docs: list[TaskSourceDoc] | None = Field(default=None, max_length=8)
    # The chat conversation this command came from, when there is one. Bound to
    # the PRD server-side so navigating away mid-generation can't orphan the
    # chat from its document. Optional — older clients omit it.
    conversation_id: int | None = None
    # The uploaded format this PRD must be written into, when the user named one
    # ("generate a PRD using our Acme format" — the Ask planner resolves the name
    # to an id). Omitted means the company's ACTIVE format, which is what every
    # request got before this existed and what most still get.
    artifact_template_id: str | None = Field(default=None, max_length=64)


def _render_source_docs(docs: list[TaskSourceDoc]) -> str:
    """Join attached docs into one markdown block (newest-last order preserved),
    clamped to the total budget so a pile of large docs can't blow the prompt."""
    joined = "\n\n".join(f"--- {d.name} ---\n{d.content.strip()}" for d in docs)
    return joined[:_TASK_SOURCE_DOCS_TOTAL_MAX]


@router.post("/generate-from-task")
async def generate_from_task(
    body: TaskGenerateIn,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Kick off PRD generation for a SPECIFIC TASK the user described in chat
    ("generate a PRD for dark mode on mobile").

    The task text becomes the synthetic insight (title + summary) fed to the
    prd-author pipeline — the same insight_override shape the ideation and
    import paths use. Grounding is KG-first: with no theme backing, the task
    text drives topic retrieval over the tenant's KG (the same retrieval the
    Ask path uses), and only an empty/unreachable KG falls back to the company
    corpus. The user's words steer the document either way. In parallel, an
    Evidence artifact is generated from the same semantic retrieval IF it finds
    backing signals — with no backing the evidence doc is skipped entirely (no
    row → the Evidence tab stays hidden). Anchors to the company's current
    brief when one exists (dataset grounding), else to the per-company uploads
    brief so new accounts can still use the command. Fire-and-forget like
    /generate: returns the prd_id immediately; poll GET /v1/prd/{prd_id} until
    status == 'ready'.
    """
    slug = slug_for_company_id(company.company_id)
    if not slug:
        raise HTTPException(
            409, "No dataset for this company yet — connect a source first."
        )
    brief = get_current_brief(slug)
    brief_id = brief["id"] if brief else ensure_uploads_brief(slug)

    task_text = body.task.strip()
    # Validated BEFORE anything is created: a format the caller cannot use must
    # be a refusal they can act on, not a PRD quietly written in another one.
    template_id = _requested_template_id(
        company.company_id, body.artifact_template_id
    )
    theme_id = _chat_task_theme_id(task_text, template_id)
    title = _chat_task_title(task_text)

    if not body.force:
        existing = find_existing_prd_for_theme(brief_id, theme_id, variant=PRD_VARIANT)
        if existing:
            # Re-issuing the command resolves the SAME PRD — the new chat still
            # needs to point at it, or reopening that chat shows no PRD at all.
            auto_project_id = None
            if body.conversation_id is not None:
                bind_conversation_to_prd(
                    body.conversation_id, existing["id"],
                    company.company_id, company.user_id,
                )
                auto_project_id = maybe_auto_create_project_for_prd(
                    company_id=company.company_id, workspace_id=company.workspace_id,
                    user_id=company.user_id, prd_id=existing["id"],
                    prd_title=existing["title"], conversation_id=body.conversation_id,
                )
            return {
                "prd_id": existing["id"],
                "status": existing["status"],
                "title": existing["title"],
                "variant": PRD_VARIANT,
                # The project this PRD's originating chat was forked into (new
                # or pre-existing), so the client can land the user in that
                # project's private chat. None when nothing was forked.
                "project_id": auto_project_id,
            }

    # Synthetic insight — mirrors a brief insight so the PRD prompt resolves
    # identically to the brief/ideation paths. No theme_id inside the insight:
    # the synthetic id has no KG backing, so grounding resolves via topic
    # retrieval over the tenant KG (corpus only as last resort). `query`
    # carries the user's raw ask for the parallel Evidence-artifact retrieval.
    insight = {
        "title": title,
        "summary": f"Requested by the user in chat: {task_text}",
        "query": task_text,
    }

    prd_id = start_prd(
        brief_id=brief_id,
        insight_index=_CHAT_TASK_INSIGHT_INDEX,
        title=title,
        template_version=PRD_TEMPLATE_VERSION,
        variant=PRD_VARIANT,
        source="chat",
        theme_id=theme_id,
        # The originating-question linkage (mirrors db/reports.py's `question`):
        # this is the ONE PRD-generation path with a genuine user-typed question
        # behind it — brief/ideation/import PRDs have no chat question, so they
        # leave this NULL. No ask_id: this command runs outside the ask_jobs
        # pipeline (no ask row exists to reference).
        question=task_text,
    )
    # No _record_prd_action: there is no real theme to advance in the lifecycle.

    # Link the commanding chat to this PRD NOW — before the (multi-second)
    # generation runs and before the client could navigate away.
    auto_project_id = None
    if body.conversation_id is not None:
        bind_conversation_to_prd(
            body.conversation_id, prd_id, company.company_id, company.user_id
        )
        auto_project_id = maybe_auto_create_project_for_prd(
            company_id=company.company_id, workspace_id=company.workspace_id,
            user_id=company.user_id, prd_id=prd_id,
            prd_title=title, conversation_id=body.conversation_id,
        )

    # Documents attached earlier in the chat thread ride along as authoritative
    # source material (extra_source_md) — layered on top of the normal KG/task
    # grounding, unlike the import path which replaces grounding entirely.
    extra_source_md = _render_source_docs(body.source_docs) if body.source_docs else None

    task = asyncio.create_task(
        generate_prd_and_warm(
            prd_id, brief_id, _CHAT_TASK_INSIGHT_INDEX, insight_override=insight,
            author=company.user_name,
            company_id=company.company_id, user_id=company.user_id,
            prd_title=title,
            extra_source_md=extra_source_md,
            artifact_template_id=template_id,
        )
    )
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)

    # In parallel: the Evidence artifact, generated from semantic KG retrieval
    # over the task — skipped inside (no row) when the KG has no backing signals.
    # Same originating question/conversation linkage as the PRD above.
    ev_task = asyncio.create_task(
        generate_task_evidence(
            brief_id, insight, theme_id,
            template_version=EVIDENCE_TEMPLATE_VERSION, variant=EVIDENCE_VARIANT,
            question=task_text,
            conversation_id=body.conversation_id,
            company_id=company.company_id,
            user_id=company.user_id,
        )
    )
    _inflight_tasks.add(ev_task)
    ev_task.add_done_callback(_inflight_tasks.discard)

    return {
        "prd_id": prd_id,
        "status": "generating",
        "title": title,
        "variant": PRD_VARIANT,
        # The project this PRD's originating chat was forked into (new or
        # pre-existing), so the client can land the user in that project's
        # private chat. None when nothing was forked.
        "project_id": auto_project_id,
    }


class ClarifyTaskIn(BaseModel):
    task: str = Field(..., min_length=3, max_length=4000)
    source_docs: list[TaskSourceDoc] | None = Field(default=None, max_length=8)


@router.post("/clarify-task")
def clarify_task(
    body: ClarifyTaskIn,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Sufficiency gate before chat-task PRD generation (clarify-first, issue d).

    Runs on EVERY chat-PRD command — detailed-looking prompts included; length
    is not sufficiency — over the task text + any documents attached earlier in
    the thread. Returns either sufficient=true (the client generates
    immediately) or 3–5 targeted questions the chat asks first, whose answers
    the client folds into the task. Fail-open: any gate failure returns
    sufficient=true, so this endpoint can never block generation.
    """
    from app.prd_clarify import clarify_prd_task

    docs_md = _render_source_docs(body.source_docs) if body.source_docs else None
    return clarify_prd_task(company.company_id, body.task.strip(), docs_md)


class ClassifyCommandIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)


@router.post("/classify-command")
def classify_command(
    body: ClassifyCommandIn,
    company: WorkspaceContext = Depends(require_workspace),
):
    """LLM fallback for the chat command decision (tier 2 of the client's
    slash→regex→LLM ladder): does this message ask us to CREATE a PRD, and for
    what task? Called by ChatScreen only when the message names a PRD but the
    regex tier (BriefChat.isPrdCommand) didn't match — novel phrasings.
    Fail-open: any classifier error returns not-a-command, and the client
    falls through to the ask agent exactly as before this endpoint existed."""
    # enterprise_id == company_id (same identity the generate routes bind).
    return classify_prd_command(company.company_id, body.text)


@router.get("/{prd_id}/evidence")
def get_prd_evidence(
    prd_id: int,
    company: WorkspaceContext = Depends(require_workspace),
):
    """The Evidence artifact behind a chat-task PRD, or 404.

    Chat-task evidence is keyed (brief_id, theme_id) — not insight_index, which
    is only a storage sentinel for these rows. 404 covers both "not a chat PRD"
    and "retrieval found no backing → doc was skipped"; the client hides the
    Evidence tab in either case. May answer a `generating` row — the client
    polls GET /v1/evidence/{id} until terminal."""
    row = require_owned_prd(prd_id, company.company_id, company.workspace_id)
    theme_id = row.get("theme_id") or ""
    if row.get("source") != "chat" or not theme_id.startswith("chat:"):
        raise HTTPException(404, "No evidence for this PRD")
    doc = find_existing_evidence_for_theme(row["brief_id"], theme_id)
    if not doc:
        raise HTTPException(404, "No evidence for this PRD")
    return doc


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
    # The chat conversation this import was commanded from, when there is one.
    # Bound to the new PRD here (server-side) so leaving the page mid-import can
    # never orphan the chat from the document it produced. Optional: other
    # callers (and older clients) simply omit it.
    conversation_id: int | None = Form(None),
    # The uploaded format this import must be re-laid-out into, when the user
    # named one ("import this as a PRD using our Acme format"). A FORM field
    # rather than JSON because this endpoint is multipart — the file is the
    # body. Omitted means the company's active format, as before.
    #
    # This path needs it as much as generate-from-task does, and is easy to
    # miss: attaching a document to a chat message dispatches an IMPORT, not a
    # generate, so "create a PRD using Template 1" with a file attached lands
    # here. That is exactly how the requested format was first observed being
    # ignored.
    artifact_template_id: str | None = Form(None),
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

    # Before the file is even read: a refusal the caller can act on beats a
    # 25 MB upload that produces a document in the wrong format.
    template_id = _requested_template_id(company.company_id, artifact_template_id)

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

    # Link the commanding chat to this PRD NOW — before the (multi-second)
    # generation runs and before the client could navigate away.
    if conversation_id is not None:
        bind_conversation_to_prd(
            conversation_id, prd_id, company.company_id, company.user_id
        )
        maybe_auto_create_project_for_prd(
            company_id=company.company_id, workspace_id=company.workspace_id,
            user_id=company.user_id, prd_id=prd_id,
            prd_title=title, conversation_id=conversation_id,
        )

    task = asyncio.create_task(
        generate_prd_and_warm(
            prd_id,
            brief_id,
            _IMPORT_INSIGHT_INDEX,
            insight_override=insight,
            author=company.user_name,
            import_source_md=extracted,
            company_id=company.company_id, user_id=company.user_id,
            prd_title=title,
            artifact_template_id=template_id,
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
    result = rendered or row
    # Canonical share token: read-mostly get-or-create. Backfills exactly
    # once for a legacy PRD with no pre-existing share; every later call
    # returns the same token (see get_or_mint_canonical_share).
    result["share_token"] = get_or_mint_canonical_share(
        artifact_type="prd", artifact_id=row["id"],
        owner_company_id=company.company_id,
        owner_workspace_id=company.workspace_id,
        created_by_user_id=company.user_id,
    )["token"]
    return result


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
    # Canonical share token: read-mostly get-or-create (see the /latest
    # handler's identical comment above).
    row["share_token"] = get_or_mint_canonical_share(
        artifact_type="prd", artifact_id=row["id"],
        owner_company_id=company.company_id,
        owner_workspace_id=company.workspace_id,
        created_by_user_id=company.user_id,
    )["token"]
    # The stamp's NAME, resolved server-side so the PRD panel's "Format: {name}"
    # label needs no second fetch — the id alone is a uuid nobody recognises.
    # None both for an unstamped row (the built-in; the client renders its own
    # label) and for a stamped one whose format was since deleted (the id
    # survives deletion by design — see 20260806160000's header). Best-effort:
    # a lookup hiccup costs the label, never the document.
    name = None
    template_id = row.get("artifact_template_id")
    if template_id:
        try:
            from app.db.artifact_templates import get_template_by_id

            t = get_template_by_id(company.company_id, template_id)
            name = t.get("name") if t else None
        except Exception:  # noqa: BLE001
            logger.warning(
                "format-name lookup failed for prd_id=%s", prd_id, exc_info=True
            )
    row["artifact_template_name"] = name
    return row


@router.get("/by-public-id/{public_id}")
def get_id_by_public_id(
    public_id: str,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Resolve a PRD's opaque `public_id` (the only identifier
    `useArtifactUrlSync.ts` puts in the URL) to its real internal `id`, for
    the caller's OWN internal navigation — e.g. opening a `?prd={public_id}`
    deep-link. Same ownership check GET /{prd_id} already enforces; a
    foreign-tenant or malformed public_id 404s identically to a foreign
    integer id, so this grants no new access, only a different identifier
    shape. Returns `{"id": <int>}` — callers hand that straight to the same
    `openPrdTab`/`GET /{prd_id}` path every other PRD-open already uses."""
    resolved_id = resolve_prd_id_by_public_id(public_id)
    if resolved_id is None:
        raise HTTPException(404, "PRD not found")
    require_owned_prd(resolved_id, company.company_id, company.workspace_id)
    return {"id": resolved_id}


@router.get("/{prd_id}/stream")
async def stream_prd_generation(
    prd_id: int,
    company: WorkspaceContext = Depends(require_workspace_from_query),
) -> StreamingResponse:
    """SSE token stream of a PRD's Part A generation, so the client renders the
    PRD as it's written instead of waiting for the whole document.

    EventSource can't send headers, so the bearer rides as `?token=`
    (require_workspace_from_query). Frames: `{"kind":"delta","text":…}` as the HTML
    streams, then a terminal `{"kind":"done"|"error"}`. A client joining
    mid-generation (the warm-started brief-insight PRDs) first gets one
    `{"kind":"replay","text":…}` catch-up frame with everything emitted so far.
    PROGRESSIVE DISPLAY ONLY — the client keeps polling GET /{prd_id}, which
    stays the authoritative source for the finished, persisted PRD.
    Single-worker transport (see app.graph.token_stream); on multi-worker this
    yields nothing and the poll still carries the result. Opening after the
    generation finished receives no frames — the poll shows the completed PRD.
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

def _actor(company: WorkspaceContext) -> str:
    """Who to record on a version snapshot.

    `prd_versions.saved_by` used to be the literal string "auto" on every
    automatic snapshot, so version history recorded WHAT made the snapshot and
    never WHO — which made a two-user editing report impossible to confirm from
    the data alone. The column is free text rendered straight into the history
    list ("Edit · {saved_by} · {date}"), so an address needs no schema or
    frontend change; rows written before this stay "auto" and still render.

    Total by construction — it resolves an identity or returns "auto", and
    never raises. Every call site sits INSIDE the `try` that guards the
    snapshot write, so an actor lookup that raised would be caught by that
    `except` and cost the user their undo point: the snapshot would never be
    attempted, while the log claimed it "failed". The label is metadata; the
    snapshot is the recovery point, and metadata must never cost the recovery
    point. Same discipline as `profile_name_for_user` in app/auth.py, which is
    best-effort precisely so name resolution can't fail the request.
    """
    return getattr(company, "user_email", None) or getattr(company, "user_id", None) or "auto"


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
        save_prd_version(prd_id, row.get("title", ""), row.get("payload_md", ""), saved_by=_actor(company))
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
        save_prd_version(prd_id, row.get("title", ""), prd_html, saved_by=_actor(company))
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


class InputAnswerItem(BaseModel):
    question_id: int
    answer: str = Field(..., min_length=1)


class InputAnswersBatchIn(BaseModel):
    answers: list[InputAnswerItem] = Field(..., min_length=1)


@router.post("/{prd_id}/input-questions/answer-batch")
def answer_input_questions_batch(
    prd_id: int,
    body: InputAnswersBatchIn,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Answer SEVERAL "User input needed" questions in one scoped edit.

    The chat's question popup collects the whole batch before submitting
    (owner directive: answer everything first, send once) — so the answers
    arrive together, and folding them together is strictly better than N
    sequential calls to the single-answer route: one scoped-editor pass over
    the document instead of N (each of which re-reads and re-writes the whole
    HTML), one undoable version snapshot instead of N stacked ones, and no
    window where the PRD reflects half a batch.

    Same contract as the single-answer route otherwise: every question must
    belong to this PRD and the edit is all-or-nothing — a failed edit leaves
    the document untouched and NO question marked answered.
    """
    from app.prd_questions import apply_answers

    row = require_owned_prd(prd_id, company.company_id, company.workspace_id)

    # Resolve every target up front — a batch naming a foreign or unknown
    # question dies before any LLM work, not after.
    pairs: list[tuple[dict, str]] = []
    for item in body.answers:
        question = get_question(item.question_id)
        if not question or question.get("prd_id") != prd_id:
            raise HTTPException(404, f"Question {item.question_id} not found")
        pairs.append((question, item.answer))

    prd_html = (row.get("payload_md") or "").strip()
    if not prd_html:
        raise HTTPException(409, "PRD has no content to edit")

    try:
        edit = apply_answers(
            prd_html,
            [(q["prompt"], a) for q, a in pairs],
            enterprise_id=company.company_id,
        )
    except RuntimeError as exc:
        raise HTTPException(502, f"Could not apply the answers: {exc}")

    # ONE snapshot for the whole batch — undo restores the pre-batch document.
    try:
        save_prd_version(prd_id, row.get("title", ""), prd_html, saved_by=_actor(company))
    except Exception:
        logger.warning(
            "auto-version snapshot failed for prd_id=%s before input-answer batch "
            "(undo point not captured)", prd_id, exc_info=True,
        )

    update_prd_content(prd_id, row.get("title", ""), edit["html"])
    answered = [
        answer_question(q["id"], a, answered_by=company.user_name) for q, a in pairs
    ]

    return {
        "prd": get_prd_rendered(prd_id),
        "questions": answered,
        "sections_changed": edit["sections_changed"],
        "summary": edit["summary"],
    }


class ChatEditIn(BaseModel):
    instruction: str = Field(..., min_length=3, max_length=4000)


@router.post("/{prd_id}/chat-edit")
def chat_edit(
    prd_id: int,
    body: ChatEditIn,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Apply a free-form chat edit instruction to the PRD ("make this PRD
    shorter", "add a rollout section").

    The chat-driven counterpart of answer_input_question: the SAME scoped
    editor discipline (app.prd_questions.apply_chat_edit — targeted rewrite of
    only the affected sections, never a full prd-author re-run), the SAME
    undoable version-snapshot persistence, and the same response shape minus
    the question — so the chat can confirm which sections changed and the
    panel can refresh live. Before this endpoint, an edit-phrased chat message
    on a PRD tab was answered in text only and the document never changed
    (issue b of the chat→PRD bug set).

    Delegates to the shared `apply_chat_edit_scoped` (`project_chat_edit.py`)
    guard-off (`project_id=None`) — this is the byte-identical main-chat hot
    path; the project-scoped write (with the ★ cross-project IDOR gate) lives
    on `POST /v1/projects/{id}/prd/chat-edit` instead. Request model, response
    keys, and status codes (200 / 409 empty PRD / 502 edit failure) are
    unchanged by the extraction.
    """
    from app.project_chat_edit import apply_chat_edit_scoped

    return apply_chat_edit_scoped(
        prd_id, body.instruction, company, project_id=None
    )


class ChangeTemplateIn(BaseModel):
    # The target format's id — or null for Sprntly's built-in format, which is
    # a real choice here (a stamped PRD switching BACK), not the "no
    # preference" it means on the generate routes.
    artifact_template_id: str | None = Field(default=None, max_length=64)


@router.post("/{prd_id}/change-template")
async def change_template(
    prd_id: int,
    body: ChangeTemplateIn,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Re-write an existing PRD into a different format, in place.

    Not a relabel: the document's CONTENT is regenerated into the target
    format's structure, via the import path's faithful re-layout mode — the
    current document (the user's edits included) is the only source material,
    so nothing is invented and nothing re-grounds. The current version is
    snapshotted to history first, so the switch is always undoable.

    Ordered gates, every one answered before anything is written:
    ownership (404, indistinguishable from missing, like every check here) →
    `ready` only (409 — a generating row is already someone's job, a failed one
    has nothing trustworthy to re-lay-out) → the target format must be usable
    (`_requested_template_id`: 404 foreign / 409 wrong-type-or-uncompiled; null
    passes through as the built-in) → already in that format is a no-op the
    response states rather than an error, because the user's intent is already
    true.

    Fire-and-forget like /generate: the row goes back to `generating` and the
    existing poll (GET /v1/prd/{id}) and token stream (`prd:<id>`) carry the
    regeneration exactly as they carry a first generation. A failed
    regeneration restores the previous document's `ready` status with its
    content untouched — the caller detects the failed switch by the row's
    `artifact_template_id` still holding its old value."""
    row = require_owned_prd(prd_id, company.company_id, company.workspace_id)

    if row.get("status") != "ready":
        raise HTTPException(
            409,
            "This PRD is still being worked on — try again when it's ready.",
        )

    # Validated BEFORE the snapshot and the status flip: a format the caller
    # cannot use must be a refusal they can act on, not a PRD stuck generating.
    template_id = _requested_template_id(
        company.company_id, body.artifact_template_id
    )

    current = row.get("artifact_template_id") or None
    if (template_id or None) == current:
        # Already true — the no-op case. Stated, not 409'd: "make it X" when it
        # is X is a satisfied request, and the client reads `unchanged` to skip
        # the regenerating state entirely.
        return {
            "prd_id": prd_id,
            "status": "ready",
            "unchanged": True,
            "artifact_template_id": current,
        }

    prd_html = (row.get("payload_md") or "").strip()
    if not prd_html:
        raise HTTPException(409, "This PRD has no content to re-write yet.")

    # The undo point, taken from the RAW payload_md — same discipline as the
    # chat-edit and input-answer paths: design-agent 'applied' patches fold on
    # read (get_prd_rendered), so snapshotting and re-laying-out the raw doc
    # keeps them folding exactly once.
    try:
        save_prd_version(
            prd_id, row.get("title", ""), prd_html, saved_by=_actor(company)
        )
    except Exception:  # noqa: BLE001 — losing the undo point must not lose the switch
        logger.warning(
            "auto-version snapshot failed for prd_id=%s before change-template "
            "(undo point not captured)", prd_id, exc_info=True,
        )

    mark_prd_generating(prd_id)

    task = asyncio.create_task(
        regenerate_prd_into_template(
            prd_id,
            source_md=prd_html,
            brief_id=row["brief_id"],
            insight_index=row.get("insight_index") or 0,
            title=row.get("title") or "PRD",
            artifact_template_id=template_id,
            company_id=company.company_id,
            user_id=company.user_id,
            author=company.user_name,
        )
    )
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)

    return {
        "prd_id": prd_id,
        "status": "generating",
        "title": row.get("title") or "PRD",
        "artifact_template_id": template_id,
        "variant": PRD_VARIANT,
    }
