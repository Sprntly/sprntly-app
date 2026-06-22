"""Multi-Agent endpoints — concurrent generation of all documentation.

Trigger via:

    POST /v1/multi-agent/generate  {"brief_id": N, "insight_index": M, "mode": "aggressive"}
    GET  /v1/multi-agent/{run_id}

The POST is fire-and-forget: it creates a run_id, schedules the multi-agent
orchestrator in the background, and returns immediately. Poll the GET until
all docs show status == 'ready'.

Modes:
  - "standard":   PRD + Evidence + User Stories
  - "aggressive": All of standard PLUS Technical Design, QA Test Cases,
                   Risk/Gap Analysis, Traceability Matrix, and ClickUp
                   context ingestion.
"""
import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.config import settings
from app.db.multi_agent_docs import get_docs_by_run, get_run_status
from app.deps.ownership import require_owned_brief

logger = logging.getLogger(__name__)


def _assert_run_owned(run_id: str, company_id: str) -> list[dict]:
    """Fetch a multi-agent run's docs and assert the caller's company owns them
    (via the run's brief → dataset → company chain). Returns the docs so callers
    reuse them. An empty list (unknown run / still initializing) is allowed —
    there's nothing to leak. 404 on a foreign tenant (no existence disclosure)."""
    docs = get_docs_by_run(run_id)
    if docs:
        require_owned_brief(docs[0]["brief_id"], company_id)
    return docs

router = APIRouter(prefix="/v1/multi-agent", tags=["multi-agent"])

# Strong refs to in-flight background tasks.
_inflight_tasks: set[asyncio.Task] = set()


class GenerateIn(BaseModel):
    brief_id: int = Field(..., ge=1)
    insight_index: int = Field(..., ge=0)
    mode: str = Field(default="aggressive", pattern="^(standard|aggressive)$")
    force: bool = False


@router.post("/generate")
async def generate(
    body: GenerateIn,
    company: CompanyContext = Depends(require_company),
):
    """Kick off multi-agent generation in the background.

    Returns immediately with a run_id. Poll GET /v1/multi-agent/{run_id}
    until status == 'ready' or 'partial' (some agents failed).
    """
    if not settings.multi_agent_enabled:
        raise HTTPException(404, "Multi-agent mode is not enabled")

    # Tenant gate
    brief = require_owned_brief(body.brief_id, company.company_id)
    insights = brief.get("insights") or []
    if not (0 <= body.insight_index < len(insights)):
        raise HTTPException(
            400,
            f"insight_index={body.insight_index} out of range "
            f"(0..{len(insights) - 1})",
        )

    run_id = str(uuid.uuid4())
    dataset = brief.get("dataset", "")

    from app.multi_agent_orchestrator import run_multi_agent_generation

    async def _run():
        try:
            await run_multi_agent_generation(
                brief_id=body.brief_id,
                insight_index=body.insight_index,
                company_id=company.company_id,
                dataset=dataset,
                run_id=run_id,
                mode=body.mode,
            )
        except Exception:
            logger.exception("Multi-agent run failed run_id=%s", run_id)

    task = asyncio.create_task(_run())
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)

    return {
        "run_id": run_id,
        "status": "generating",
        "mode": body.mode,
        "brief_id": body.brief_id,
        "insight_index": body.insight_index,
    }


@router.get("/{run_id}")
def get_status(
    run_id: str,
    company: CompanyContext = Depends(require_company),
):
    """Poll multi-agent run status.

    Returns per-doc status + overall status:
      - "generating": at least one doc still being generated
      - "ready": all docs generated successfully
      - "partial": some docs failed, others ready
    """
    # Tenant guard: verify the caller's company owns this run before returning
    # any status. No docs yet (standard-mode / still initializing) → nothing to
    # leak, return the generating placeholder.
    docs = _assert_run_owned(run_id, company.company_id)
    if not docs:
        return {
            "run_id": run_id,
            "status": "generating",
            "docs": {},
        }
    return get_run_status(run_id)


@router.get("/{run_id}/docs")
def get_all_docs(
    run_id: str,
    company: CompanyContext = Depends(require_company),
):
    """Fetch all generated documents for a multi-agent run.

    Returns full payload_md for each doc. Use the per-doc GET endpoint
    for individual docs.
    """
    # Tenant guard: only return docs (full payload_md) for a run the caller owns.
    docs = _assert_run_owned(run_id, company.company_id)
    return {
        "run_id": run_id,
        "docs": [
            {
                "id": d["id"],
                "doc_type": d["doc_type"],
                "title": d["title"],
                "status": d["status"],
                "payload_md": d.get("payload_md", ""),
                "error": d.get("error"),
            }
            for d in docs
        ],
    }


@router.get("/doc/{doc_id}")
def get_single_doc(
    doc_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Fetch a single multi-agent document by id."""
    from app.db.multi_agent_docs import get_doc
    doc = get_doc(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    # Tenant guard: doc_id is a sequential integer (trivially enumerable), and
    # multi_agent_docs has no company_id — so bind via the doc's brief and 404
    # if the caller's company doesn't own it (no existence disclosure).
    require_owned_brief(doc["brief_id"], company.company_id)
    return doc
