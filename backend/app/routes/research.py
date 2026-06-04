"""Research agent routes.

POST /v1/research/competitors/run — run the Competitor Analysis agent for
the caller's company (roster from companies.competitors[], set at
onboarding/Settings). Optional body {"competitors": [...]} overrides the
roster for ad-hoc runs (names are data, not stored).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import CompanyContext, require_company
from app.graph.facade import GraphFacade
from app.research.competitor import run_competitor_research
from app.research.market import run_market_research

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/research", tags=["research"])


class RunIn(BaseModel):
    competitors: list[str] | None = None


@router.post("/competitors/run")
def run_competitors(
    body: RunIn | None = None,
    company: CompanyContext = Depends(require_company),
):
    facade = GraphFacade()
    try:
        result = run_competitor_research(
            facade, company.company_id,
            competitors=(body.competitors if body else None),
        )
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, **result}


@router.post("/market/run")
def run_market(company: CompanyContext = Depends(require_company)):
    facade = GraphFacade()
    try:
        result = run_market_research(facade, company.company_id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, **result}
