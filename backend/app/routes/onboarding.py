"""Onboarding routes.

POST /v1/onboarding/analyze-website — from a product website URL, infer the
company's industry / business-type / a readable business-context brief /
suggested success metrics (the onboarding redesign pre-fills these; the user can
always edit). Tenant-scoped via require_company; the analysis is persisted to
the caller's company business_context.

Resilient by design: the analyzer NEVER raises — a blocked / unreachable / empty
site returns `ok: false` with empty fields so onboarding falls back to manual
entry instead of failing the request.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import CompanyContext, require_company
from app.onboarding.website_analysis import analyze_website

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/onboarding", tags=["onboarding"])


class AnalyzeWebsiteIn(BaseModel):
    url: str


@router.post("/analyze-website")
def analyze_website_route(
    body: AnalyzeWebsiteIn,
    company: CompanyContext = Depends(require_company),
):
    """Analyze the given product website for the caller's company.

    Returns the full analysis dict (see analyze_website). Always 200 — the
    `ok`/`reason` fields signal a graceful degrade, not an HTTP error, so the
    onboarding UI never has to handle a failed request.
    """
    return analyze_website(company.company_id, body.url)
