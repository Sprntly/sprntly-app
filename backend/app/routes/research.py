"""Research agent routes.

POST /v1/research/competitors/run — run the Competitor Analysis agent for the
caller's company. Roster comes from companies.competitors[] (set at
onboarding/Settings); if empty, it's auto-discovered and FIXED on first run.
Optional body {"competitors": [...]} overrides the roster for ad-hoc runs
(names are data, not stored). Body {"mode": "deep_dive"} runs the staged CIR
deep-dive instead of the light pass.

POST /v1/research/competitors/deep-dive — the staged CIR deep-dive directly.
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import WorkspaceContext, require_company, require_workspace  # noqa: F401 — re-exported for tests' dependency_overrides
from app.graph.facade import GraphFacade
from app.research.competitor import (
    run_competitor_deep_dive,
    run_competitor_research,
)
from app.research.market import run_market_research

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/research", tags=["research"])


class RunIn(BaseModel):
    competitors: list[str] | None = None
    mode: Literal["light", "deep_dive"] = "light"


@router.post("/competitors/run")
def run_competitors(
    body: RunIn | None = None,
    company: WorkspaceContext = Depends(require_workspace),
):
    facade = GraphFacade()
    competitors = body.competitors if body else None
    mode = body.mode if body else "light"
    try:
        if mode == "deep_dive":
            result = run_competitor_deep_dive(
                facade, company.company_id, competitors=competitors,
            )
        else:
            result = run_competitor_research(
                facade, company.company_id, competitors=competitors,
            )
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, "mode": mode, **result}


@router.post("/competitors/deep-dive")
def run_competitors_deep_dive(
    body: RunIn | None = None,
    company: WorkspaceContext = Depends(require_workspace),
):
    facade = GraphFacade()
    try:
        result = run_competitor_deep_dive(
            facade, company.company_id,
            competitors=(body.competitors if body else None),
        )
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, "mode": "deep_dive", **result}


@router.post("/market/run")
def run_market(company: WorkspaceContext = Depends(require_workspace)):
    facade = GraphFacade()
    try:
        result = run_market_research(facade, company.company_id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, **result}
