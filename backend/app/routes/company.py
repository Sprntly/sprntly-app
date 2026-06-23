"""Company config routes — KPI tree + coworker names.

GET  /v1/company/kpi-tree   — current tree (404 if unset)   [any member]
PUT  /v1/company/kpi-tree   — validate + persist (version auto-bumps) [admin]
GET  /v1/company/coworkers  — current coworker names (empty map if unset) [member]
PUT  /v1/company/coworkers  — persist coworker names        [admin]

Backs design-v4 onboarding page 05 (KPI tree) + page 07 (coworker names) +
dashboard 09; Synthesis reads the KPI tree for strategic-alignment scoring.

Access model (v0 access-boundary fix): these are org-wide company config.
READS are open to any member (members/viewers can see the org's KPI tree +
coworker names). WRITES mutate config that affects every user, so they are
gated to admin/owner via `_require_admin` — the same helper the Settings →
Team write routes use (app/routes/team.py). Non-admins get 403.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.auth import CompanyContext, require_company
from app.coworkers import CoworkerNames, load_coworker_names, save_coworker_names
from app.kpi_tree import (
    KpiTree,
    MetricSelection,
    build_tree_from_selection,
    load_kpi_tree,
    save_kpi_tree,
)
from app.roadmap_doc import load_roadmap_doc, save_roadmap_doc
from app.routes.team import _require_admin

router = APIRouter(prefix="/v1/company", tags=["company"])

# 20 MB hard cap per roadmap upload — mirrors the dataset upload cap. A roadmap
# deck/spreadsheet at this size already strains the converter; bigger is wrong-
# format.
ROADMAP_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@router.get("/kpi-tree")
def get_kpi_tree(company: CompanyContext = Depends(require_company)):
    tree = load_kpi_tree(company.company_id)
    if tree is None:
        raise HTTPException(404, "KPI tree not set — complete onboarding step 5")
    return tree.model_dump()


@router.put("/kpi-tree")
def put_kpi_tree(tree: KpiTree, company: CompanyContext = Depends(require_company)):
    _require_admin(company)
    saved = save_kpi_tree(company.company_id, tree)
    return {"ok": True, "version": saved.version}


@router.put("/kpi-tree/from-selection")
def put_kpi_tree_from_selection(
    selection: MetricSelection, company: CompanyContext = Depends(require_company)
):
    """Onboarding metrics page: persist the PM's metric picks, inferring the
    North Star server-side. The client sends the metrics the PM selected (the UI
    asks for 3–5); we choose which is the North Star and store the KPI tree.
    Returns the inferred North Star so the client can reflect it."""
    _require_admin(company)
    tree = build_tree_from_selection(selection.metrics)
    saved = save_kpi_tree(company.company_id, tree)
    return {"ok": True, "version": saved.version, "north_star": saved.north_star.metric}


@router.get("/coworkers")
def get_coworkers(company: CompanyContext = Depends(require_company)):
    return load_coworker_names(company.company_id).model_dump()


@router.put("/coworkers")
def put_coworkers(
    names: CoworkerNames, company: CompanyContext = Depends(require_company)
):
    _require_admin(company)
    saved = save_coworker_names(company.company_id, names)
    return {"ok": True, "coworker_names": saved.model_dump()}


# ── Roadmap doc — the company's uploaded roadmap (priorities anchor) ──────────
# Backs the onboarding strategy step's roadmap upload (design scene onbstrat) +
# the read-only `roadmapdoc` artifact view. The stored roadmap feeds weekly-brief
# composition as a high-weight priorities signal (see app.synthesis.agent). One
# roadmap per company; the latest upload wins.
def _roadmap_payload(company_id: str) -> dict | None:
    doc = load_roadmap_doc(company_id)
    if doc is None:
        return None
    # Don't ship the raw base64 blob in the artifact JSON by default — the
    # extracted text is what the read-only view renders. (A future download
    # affordance can fetch the source separately.)
    return {
        "filename": doc.filename,
        "content_type": doc.content_type,
        "extracted_text": doc.extracted_text,
        "uploaded_at": doc.uploaded_at,
        "version": doc.version,
    }


@router.post("/roadmap-doc")
async def post_roadmap_doc(
    file: Annotated[UploadFile, File(description="Roadmap doc (PDF/DOCX/MD/spreadsheet/deck)")],
    company: CompanyContext = Depends(require_company),
):
    """Store the company's roadmap upload + its extracted text (multipart `file`).

    Reuses the shared ingest converter (the same one the dataset upload path
    uses) to extract text, which then feeds the weekly brief as a priorities
    anchor. Any member may set the company roadmap during onboarding.
    """
    filename = file.filename or "roadmap"
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > ROADMAP_MAX_UPLOAD_BYTES:
        raise HTTPException(
            413,
            f"File exceeds {ROADMAP_MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit",
        )
    doc = save_roadmap_doc(
        company.company_id,
        filename=filename,
        data=data,
        content_type=file.content_type,
    )
    return {
        "ok": True,
        "filename": doc.filename,
        "extracted_chars": len(doc.extracted_text or ""),
        "version": doc.version,
    }


@router.get("/roadmap-doc")
def get_roadmap_doc(company: CompanyContext = Depends(require_company)):
    """Fetch the company's stored roadmap for the read-only artifact view.

    404 when none has been uploaded yet (the artifact view shows its empty
    state). Returns the extracted text + metadata; not the raw bytes.
    """
    payload = _roadmap_payload(company.company_id)
    if payload is None:
        raise HTTPException(404, "No roadmap uploaded yet")
    return payload
