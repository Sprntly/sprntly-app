"""Business Context routes — the company's structured "lens".

GET  /v1/company/business-context          — current doc (404 if unset)
PUT  /v1/company/business-context           — validate + save; every known leaf
                                              the human sends is stamped src="user"
                                              (so the agent never overwrites it)
POST /v1/company/business-context/refresh   — run the Business Context agent

Separate file from routes/company.py on purpose (avoids collisions with
in-flight branches editing that module). All routes require_company.
"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from app.auth import CompanyContext, require_company
from app.business_context import (
    BusinessContext,
    Meta,
    load_business_context,
    save_business_context,
)
from app.graph.facade import GraphFacade
from app.research.business_context_agent import run_business_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/company", tags=["company"])


def _stamp_user_edits(doc: BusinessContext) -> BusinessContext:
    """A human is asserting these values via the editor → every KNOWN leaf is
    src='user' (the authoritative provenance the agent must never overwrite).
    Unknown leaves are left as-is so they stay gap-fillable by the agent."""
    today = date.today().isoformat()

    def stamp_layer(layer) -> None:
        for attr, m in vars(layer).items():
            if isinstance(m, Meta) and m.is_known and not m.is_user_authoritative:
                setattr(layer, attr, Meta(
                    value=m.value, src="user", conf=m.conf or "high",
                    as_of=today, evidence=m.evidence,
                ))

    for layer_name in ("identity", "business_model", "product_value",
                       "market_competition", "goals_strategy"):
        stamp_layer(getattr(doc, layer_name))
    for seg in doc.users_segments.segments:
        stamp_layer(seg)
    for term in doc.vocabulary.terms:
        stamp_layer(term)
    return doc


@router.get("/business-context")
def get_business_context(company: CompanyContext = Depends(require_company)):
    doc = load_business_context(company.company_id)
    if doc is None:
        raise HTTPException(
            404, "Business context not built yet — run refresh or complete onboarding"
        )
    return doc.model_dump()


@router.put("/business-context")
def put_business_context(
    doc: BusinessContext, company: CompanyContext = Depends(require_company)
):
    saved = save_business_context(company.company_id, _stamp_user_edits(doc))
    return {"ok": True, "version": saved.version}


@router.post("/business-context/refresh")
def refresh_business_context(company: CompanyContext = Depends(require_company)):
    facade = GraphFacade()
    try:
        result = run_business_context(facade, company.company_id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, **result}
