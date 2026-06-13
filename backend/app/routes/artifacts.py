"""HTTP layer for the All-Chats "Artifacts" tab.

  GET /v1/artifacts?dataset=<slug>  -> unified, recency-sorted list of every
                                       generated PRD, prototype, and evidence
                                       for the caller's company.

Tenant-gated exactly like routes/brief.py: `require_company` resolves the
caller's company from the JWT, then `require_owned_dataset(dataset, ...)`
404s on any slug the caller does not own (so a foreign tenant can never list
another company's artifacts, and existence is never disclosed).

The aggregation/scoping lives in db/artifacts.py. PRDs + evidences are scoped
by the brief's `dataset` slug; prototypes by `workspace_id` (= the company
UUID). This route passes the slug AND the resolved company UUID so each
surface is scoped the way its own writers scoped it.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.auth import CompanyContext, require_company
from app.db.artifacts import list_artifacts_for_company
from app.deps.ownership import require_owned_dataset

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])


@router.get("")
def list_artifacts(
    dataset: str,
    company: CompanyContext = Depends(require_company),
):
    """Unified artifact list for a company (PRDs + prototypes + evidence).

    Each item is shaped:
        {
          "type": "prd" | "prototype" | "evidence",
          "id": <int>,
          "title": <str>,
          "status": <str>,
          "created_at": <iso str>,
          "source": {...},   # human context (brief week_label / parent PRD title)
          "open": {...},     # ids the frontend viewer needs to open it
        }
    Sorted by created_at DESC, capped at 200 (newest kept).
    """
    # Tenant gate: the slug must resolve to the caller's company. 404 on any
    # slug the caller doesn't own (never 403 — no cross-tenant existence leak).
    require_owned_dataset(dataset, company.company_id)
    items = list_artifacts_for_company(
        dataset=dataset, company_id=company.company_id
    )
    return {"artifacts": items}
