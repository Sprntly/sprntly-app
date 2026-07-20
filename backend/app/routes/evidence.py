"""Evidence Page endpoints.

Trigger via:

    POST /v1/evidence/generate  {"brief_id": N, "insight_index": M, "force": false}
    GET  /v1/evidence/{evidence_id}

The POST is fire-and-forget: it inserts a row in `generating` state,
schedules `generate_evidence_kg` in the background, and returns the
evidence_id immediately. Poll the GET until status == 'ready'.

Rows live in the `evidences` table. New rows are stored with
variant='v2' (the current evidence format); historical v1 rows from
before the promotion remain readable but are no longer generated. The
GET is permissive — it returns any row by id regardless of variant so
old bookmarks keep resolving.
"""
import asyncio
import json

from fastapi import Depends, APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth import WorkspaceContext, require_company, require_workspace, require_workspace_from_query  # noqa: F401 — re-exported for tests' dependency_overrides
from app.graph import token_stream
from app.db import (
    find_existing_evidence,
    find_latest_failed_evidence,
    start_evidence,
)
from app.deps.ownership import require_owned_brief, require_owned_evidence
from app.evidence_kg import generate_evidence_kg
from app.prompts import EVIDENCE_TEMPLATE_VERSION, EVIDENCE_VARIANT

router = APIRouter(prefix="/v1/evidence", tags=["evidence"])

# v3: the evidence artifact is the evidence-brief skill's self-contained HTML
# visual brief (rendered in a sandboxed iframe). v1/v2 rows are the legacy
# `:::block` markdown format; the frontend branches rendering on this variant.
_VARIANT = EVIDENCE_VARIANT

# Strong refs to in-flight background generation tasks (see routes/design_agent.py):
# without this, the bare create_task result can be garbage-collected mid-run.
_inflight_tasks: set[asyncio.Task] = set()


class GenerateIn(BaseModel):
    brief_id: int = Field(..., ge=1)
    insight_index: int = Field(..., ge=0)
    force: bool = False


@router.post("/generate")
async def generate(
    body: GenerateIn,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Kick off evidence generation in the background.

    Returns immediately with the evidence_id. If a ready/generating doc
    already exists for (brief, insight) and `force` is false, returns
    the existing row.
    """
    # Tenant gate: the body's brief_id must belong to the caller's company.
    brief = require_owned_brief(body.brief_id, company.company_id, company.workspace_id)
    insights = brief.get("insights") or []
    if not (0 <= body.insight_index < len(insights)):
        raise HTTPException(
            400,
            f"insight_index={body.insight_index} out of range "
            f"(0..{len(insights) - 1})",
        )

    if not body.force:
        existing = find_existing_evidence(
            body.brief_id, body.insight_index, variant=_VARIANT
        )
        if existing:
            return {
                "evidence_id": existing["id"],
                "status": existing["status"],
                "title": existing["title"],
                "variant": _VARIANT,
            }
        # A prior generation FAILED: surface that failure (the client shows an
        # error with an explicit retry that sends force=true) instead of
        # silently kicking off a brand-new LLM run — failed rows aren't in
        # find_existing_evidence, so every reopen used to regenerate forever.
        failed = find_latest_failed_evidence(
            body.brief_id, body.insight_index, variant=_VARIANT
        )
        if failed:
            return {
                "evidence_id": failed["id"],
                "status": "failed",
                "title": failed["title"],
                "variant": _VARIANT,
                "error": failed.get("error"),
            }

    insight = insights[body.insight_index]
    title = insight.get("title") or f"Insight #{body.insight_index + 1}"
    evidence_id = start_evidence(
        brief_id=body.brief_id,
        insight_index=body.insight_index,
        title=title,
        template_version=EVIDENCE_TEMPLATE_VERSION,
        variant=_VARIANT,
    )
    # Ground evidence in the knowledge graph — the provenance trail (SUPPORTS
    # signals + theme convergence) behind the insight. generate_evidence_kg
    # itself falls back to the corpus path when the KG has no backing for the
    # insight, so this never hard-fails.
    task = asyncio.create_task(
        generate_evidence_kg(evidence_id, body.brief_id, body.insight_index)
    )
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
    return {
        "evidence_id": evidence_id,
        "status": "generating",
        "title": title,
        "variant": _VARIANT,
    }


@router.get("/by-insight/{brief_id}/{insight_index}")
def get_by_insight(
    brief_id: int,
    insight_index: int,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Return the latest evidence for a brief insight (ready or in-flight), or 404.

    Read-by-insight lookup so the UI can populate the Evidence tab for the
    insight whose PRD is being viewed/generated. Evidence is produced (multi-
    agent Phase 1) keyed by brief_id+insight_index but there was no read-by-
    insight endpoint, so the panel stayed empty. Ownership-scoped via the brief
    (404 on a foreign/missing brief). Two-segment-deeper path, so it can never be
    shadowed by the single-segment `GET /{evidence_id}` below.
    """
    require_owned_brief(brief_id, company.company_id, company.workspace_id)
    row = find_existing_evidence(brief_id, insight_index, variant=_VARIANT)
    if not row:
        raise HTTPException(status_code=404, detail="No evidence for this insight")
    return row


@router.get("/{evidence_id}/stream")
async def stream_evidence_generation(
    evidence_id: int,
    company: WorkspaceContext = Depends(require_workspace_from_query),
) -> StreamingResponse:
    """SSE token stream of an evidence brief's generation, so the client renders
    the doc as it's written instead of waiting for the whole document (mirrors
    GET /v1/prd/{prd_id}/stream).

    EventSource can't send headers, so the bearer rides as `?token=`
    (require_workspace_from_query). Frames: an optional `{"kind":"replay",…}`
    catch-up (everything a warm-started generation emitted before this client
    connected), `{"kind":"delta","text":…}` as the HTML streams, then a terminal
    `{"kind":"done"|"error"}`. PROGRESSIVE DISPLAY ONLY — the client keeps
    polling GET /{evidence_id}, which stays the authoritative source for the
    finished, persisted brief. Single-worker transport (see
    app.graph.token_stream); on multi-worker this yields nothing and the poll
    still carries the result. Opening after the generation finished receives no
    frames — the poll shows the completed brief.
    """
    # 404 on cross-tenant/missing (evidence → brief → dataset → company).
    require_owned_evidence(evidence_id, company.company_id, company.workspace_id)
    channel = f"evidence:{evidence_id}"

    async def _gen():
        async for event in token_stream.subscribe(channel):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{evidence_id}")
def get(
    evidence_id: int,
    company: WorkspaceContext = Depends(require_workspace),
):
    """Fetch an evidence row by id (only if it belongs to the caller's company).

    Permissive on variant — historical v1 rows still resolve so old
    bookmarks don't 409. The `variant` field on the response identifies
    which format the row was generated under.
    """
    # require_owned_evidence resolves evidence → brief → dataset → company and
    # 404s on mismatch (or a missing row), returning the evidence row.
    return require_owned_evidence(evidence_id, company.company_id, company.workspace_id)
