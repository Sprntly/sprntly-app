"""HTTP layer for stored public-feedback reports.

  GET /v1/public-feedback/reports/{report_id} -> the run's rendered html +
                                                 identity (window, question,
                                                 created_at)

The report itself is produced on the chat path (app/public_feedback.py) and
persisted per run in `public_feedback_runs`; this route re-serves a stored
report so the Artifacts surface can reopen it. Tenant-gated like
routes/artifacts.py: `require_company` resolves the caller's company, and the
db read is company-scoped — a foreign id 404s, never disclosing existence.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth import CompanyContext, require_company
from app.db.public_feedback_runs import get_public_feedback_run

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/public-feedback", tags=["public-feedback"])


@router.get("/reports/{report_id}")
def get_report(
    report_id: int,
    company: CompanyContext = Depends(require_company),
):
    """One stored public-feedback report (html + identity), company-scoped."""
    row = get_public_feedback_run(company.company_id, report_id)
    if not row:
        raise HTTPException(status_code=404, detail="Report not found")
    return {
        "id": row["id"],
        "window_label": row.get("window_label") or "",
        "question": row.get("question") or "",
        "html": row.get("html") or "",
        "created_at": row.get("created_at"),
    }
