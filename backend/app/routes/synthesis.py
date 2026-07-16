"""Synthesis Agent routes.

POST /v1/synthesis/seed   — extract the company's corpus docs into the KG
                            (pilot bridge: docs → Signals/Themes).
POST /v1/synthesis/brief  — generate the KG-driven weekly brief; saved under
                            the company's dataset slug so the existing Brief
                            screen renders it.

Both tenant-scoped (/seed via require_workspace, /brief via the Weekly-Brief
module gate); the company's slug doubles as the dataset slug (aligned at
onboarding).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth import CompanyContext, WorkspaceContext, require_company, require_workspace  # noqa: F401 — re-exported for tests' dependency_overrides
from app.corpus import load_corpus
from app.entitlements import require_weekly_brief_module
from app.db.client import require_client
from app.graph.extractor import extract_document
from app.graph.facade import GraphFacade, TenantViolationError
from app.synthesis.agent import run_synthesis
from app.synthesis_brief import seed_incremental

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/synthesis", tags=["synthesis"])


def _company_slug(company_id: str) -> str:
    r = (
        require_client().table("companies")
        .select("slug").eq("id", company_id).execute()
    )
    if not r.data:
        raise HTTPException(404, "Company not found")
    return r.data[0]["slug"]


@router.post("/seed")
def seed_from_corpus(company: WorkspaceContext = Depends(require_workspace)):
    """Extract the company's uploaded corpus into the knowledge graph."""
    slug = _company_slug(company.company_id)
    try:
        corpus = load_corpus(slug)
    except (FileNotFoundError, RuntimeError) as e:
        raise HTTPException(404, f"No corpus for dataset {slug!r}: {e}") from e

    facade = GraphFacade()
    totals = {"signals": 0, "themes": 0, "skipped": 0, "docs": 0}
    errors: list[str] = []
    for doc in corpus.docs:
        try:
            r = extract_document(
                facade, company.company_id, doc_name=doc.name, text=doc.text
            )
            for k in ("signals", "themes", "skipped"):
                totals[k] += r[k]
            totals["docs"] += 1
        except Exception as e:  # noqa: BLE001 — error-isolation per source/doc
            logger.exception("extraction failed for doc %s", doc.name)
            errors.append(f"{doc.name}: {e}")
    return {**totals, "errors": errors}


@router.post("/brief")
# On-demand brief generation → Weekly Brief module gate. /seed (KG ingestion)
# stays ungated by owner decision — the KG also grounds PRDs and chat.
def generate_brief(company: CompanyContext = Depends(require_weekly_brief_module)):
    """Generate the KG-driven weekly brief (replaces the legacy corpus brief).

    Incrementally seeds the KG first (only new/changed corpus docs — unchanged
    ones are skipped cheaply by content hash) so a last-minute upload still makes
    the brief. With event-driven ingestion warming the KG as data arrives, this
    seed is normally a cheap no-op rather than a full from-scratch ingestion."""
    slug = _company_slug(company.company_id)
    facade = GraphFacade()
    try:
        seed_incremental(facade, company.company_id, slug)
        brief = run_synthesis(facade, company.company_id, dataset_slug=slug)
    except TenantViolationError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, "dataset": slug,
            "insights": len(brief.get("insights", [])),
            "summary_headline": brief.get("summary_headline", "")}
