"""On-Call agent routes.

POST /v1/oncall/investigate — investigate a live incident for the caller's
company. Tenant-scoped via require_company (1 user ↔ 1 company).

The agent investigates + proposes only; every proposed action in the response
carries requires_pm_approval=true (PRD invariant). No action is executed here.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth import CompanyContext, require_company
from app.graph.facade import GraphFacade, TenantViolationError
from app.oncall.agent import IncidentInput, investigate_incident

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/oncall", tags=["oncall"])


@router.post("/investigate")
def investigate(
    body: IncidentInput,
    company: CompanyContext = Depends(require_company),
):
    facade = GraphFacade()
    try:
        assessment = investigate_incident(
            facade, company.company_id, incident=body)
    except TenantViolationError as e:
        raise HTTPException(403, str(e)) from e
    return {"ok": True, "assessment": assessment}
