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

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.auth import (
    CompanyContext,
    WorkspaceContext,
    require_company,
    require_workspace,
)
from app.company_document import (
    DOC_TYPES,
    is_valid_doc_type,
    list_company_documents,
    save_company_document,
)
from app.company_template import (
    delete_company_template,
    list_company_templates,
    save_company_template,
)
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


# ── Roadmap doc — the workspace's uploaded roadmap (priorities anchor) ───────
# Backs the onboarding strategy step's roadmap upload (design scene onbstrat) +
# the read-only `roadmapdoc` artifact view. The stored roadmap feeds weekly-brief
# composition as a high-weight priorities signal (see app.synthesis.agent). One
# roadmap per WORKSPACE; the latest upload wins.
def _roadmap_payload(company_id: str, workspace_id: str | None = None) -> dict | None:
    doc = load_roadmap_doc(company_id, workspace_id=workspace_id)
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
    company: WorkspaceContext = Depends(require_workspace),
):
    """Store the active workspace's roadmap upload + its extracted text
    (multipart `file`).

    Reuses the shared ingest converter (the same one the dataset upload path
    uses) to extract text, which then feeds the weekly brief as a priorities
    anchor. Any member may set the roadmap during onboarding.
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
        workspace_id=company.workspace_id,
    )
    return {
        "ok": True,
        "filename": doc.filename,
        "extracted_chars": len(doc.extracted_text or ""),
        "version": doc.version,
    }


@router.get("/roadmap-doc")
def get_roadmap_doc(company: WorkspaceContext = Depends(require_workspace)):
    """Fetch the active workspace's stored roadmap for the read-only artifact
    view.

    404 when none has been uploaded yet (the artifact view shows its empty
    state). Returns the extracted text + metadata; not the raw bytes.
    """
    payload = _roadmap_payload(company.company_id, company.workspace_id)
    if payload is None:
        raise HTTPException(404, "No roadmap uploaded yet")
    return payload


# ── Templates — the company's gold-standard PRD examples ("what good looks like")
# Sibling of the roadmap doc above, but MANY per company: each upload is its own
# row, listed and individually deletable. The extracted text feeds prd-author
# composition as FORMAT/STYLE EXEMPLARS (see app.prd_runner) so generated PRDs
# match the company's structure & voice. 20 MB cap, same converter as roadmap.
TEMPLATE_MAX_UPLOAD_BYTES = ROADMAP_MAX_UPLOAD_BYTES


def _template_item(t) -> dict:
    """Public list-item shape — extracted text + metadata, never the raw bytes."""
    return {
        "id": t.id,
        "label": t.label,
        "type": t.type,
        "filename": t.filename,
        "content_type": t.content_type,
        "extracted_chars": len(t.extracted_text or ""),
        "uploaded_at": t.uploaded_at,
    }


@router.post("/templates")
async def post_template(
    file: Annotated[UploadFile, File(description="Gold-standard PRD example (PDF/DOCX/MD/…)")],
    label: Annotated[str | None, Form()] = None,
    type: Annotated[str, Form()] = "prd",
    company: CompanyContext = Depends(require_company),
):
    """Upload a gold-standard PRD example for the company (multiple allowed).

    Reuses the shared ingest converter to extract text, which then feeds
    prd-author as a FORMAT/STYLE EXEMPLAR. Any member may add a template.
    """
    filename = file.filename or "template"
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > TEMPLATE_MAX_UPLOAD_BYTES:
        raise HTTPException(
            413,
            f"File exceeds {TEMPLATE_MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit",
        )
    saved = save_company_template(
        company.company_id,
        filename=filename,
        data=data,
        label=(label or None),
        type=(type or "prd"),
        content_type=file.content_type,
    )
    return {"ok": True, **_template_item(saved)}


@router.get("/templates")
def get_templates(
    type: str | None = None,
    company: CompanyContext = Depends(require_company),
):
    """List the company's gold-standard templates (newest first). Optionally
    filtered by `type` (e.g. 'prd'). Returns metadata + extracted-char counts,
    not the raw bytes."""
    items = list_company_templates(company.company_id, type=type)
    return {"templates": [_template_item(t) for t in items]}


@router.delete("/templates/{template_id}")
def delete_template(
    template_id: str, company: CompanyContext = Depends(require_company)
):
    """Remove one gold-standard template owned by the company. 404 if it does
    not exist (or belongs to another company)."""
    if not delete_company_template(company.company_id, template_id):
        raise HTTPException(404, "Template not found")
    return {"ok": True, "id": template_id}


# ── Company documents — strategy/context files from the onboarding strategy step
# The GENERALIZED sibling of the roadmap doc + templates above: a single store
# with a `doc_type` discriminator (ceo_memo | team_priorities | research |
# company_strategy), backing the onbstrat grid of typed upload cards. MANY per
# company, like templates. Same converter + 20 MB cap. STORED only for now —
# feeding the extracted text into agent context is a deliberate follow-up.
DOCUMENT_MAX_UPLOAD_BYTES = ROADMAP_MAX_UPLOAD_BYTES


def _document_item(d) -> dict:
    """Public list-item shape — metadata + extracted-char count, never raw bytes."""
    return {
        "id": d.id,
        "doc_type": d.doc_type,
        "filename": d.filename,
        "content_type": d.content_type,
        "extracted_chars": len(d.extracted_text or ""),
        "uploaded_at": d.uploaded_at,
    }


@router.post("/documents")
async def post_company_document(
    file: Annotated[UploadFile, File(description="Strategy/context doc (PDF/DOCX/MD/…)")],
    doc_type: Annotated[str, Form()],
    company: WorkspaceContext = Depends(require_workspace),
):
    """Upload a strategy/context document for the company (multiple allowed).

    `doc_type` must be one of the onboarding strategy cards: ceo_memo,
    team_priorities, research, company_strategy. Reuses the shared ingest
    converter to extract text; the doc is STORED only for now (a follow-up wires
    it into agent context). Any member may add a document during onboarding.
    """
    if not is_valid_doc_type(doc_type):
        raise HTTPException(
            422,
            f"Invalid doc_type '{doc_type}'. Expected one of: {', '.join(DOC_TYPES)}",
        )
    filename = file.filename or "document"
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > DOCUMENT_MAX_UPLOAD_BYTES:
        raise HTTPException(
            413,
            f"File exceeds {DOCUMENT_MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit",
        )
    saved = save_company_document(
        company.company_id,
        doc_type=doc_type,
        filename=filename,
        data=data,
        content_type=file.content_type,
        workspace_id=company.workspace_id,
    )
    return {"ok": True, **_document_item(saved)}


@router.get("/documents")
def get_company_documents(
    doc_type: str | None = None,
    company: WorkspaceContext = Depends(require_workspace),
):
    """List the active workspace's strategy/context documents (newest first).
    Optionally filtered by `doc_type`. Returns metadata + extracted-char
    counts, not the raw bytes."""
    items = list_company_documents(
        company.company_id, doc_type=doc_type, workspace_id=company.workspace_id
    )
    return {"documents": [_document_item(d) for d in items]}
