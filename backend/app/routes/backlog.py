"""Backlog routes — the sequenced, ranked product backlog.

Two routes (both tenant-scoped via require_company):

  GET   /v1/backlog            — the ranked backlog items (rank-ascending),
                                 optionally filtered by status.
  PATCH /v1/backlog/{id}       — move one item's status
                                 (in_progress | done | dismissed).

Items are produced by the synthesis run (sequence_backlog) — every theme that
didn't make the weekly brief's TOP 3, carrying its rank, score, and triage
rationale. The backlog is therefore the REMAINDER (rank ≥ 4) of the same
analysis that produced the brief.

Empty-when-no-brief invariant: the backlog is the by-product of a weekly brief
generation, so a company that has never had a brief generated MUST show an
empty backlog. The GET route enforces this explicitly — it returns no items
unless a brief exists for the company — so stale/orphaned rows (e.g. a brief
deleted after the fact) can never surface a backlog without an analysis behind
it.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import CompanyContext, require_company
from app.db.backlog import (
    PATCHABLE_STATUSES,
    list_backlog_items,
    update_backlog_status,
)
from app.db.briefs import get_current_brief
from app.db.companies import slug_for_company_id
from app.db.finding_state import COMPLETED_ACTIONS, list_findings_by_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/backlog", tags=["backlog"])


class StatusUpdate(BaseModel):
    status: str


def _company_has_brief(company_id: str) -> bool:
    """True iff a current weekly brief exists for this company.

    Briefs are keyed by the dataset slug (`briefs.dataset == companies.slug`),
    mirroring how the brief routes scope by slug. No slug / no brief row → the
    company has never had an analysis, so its backlog must be empty.
    """
    slug = slug_for_company_id(company_id)
    if slug is None:
        return False
    return get_current_brief(slug) is not None


@router.get("")
def get_backlog(company: CompanyContext = Depends(require_company)):
    """The enterprise's ranked backlog (ranks ≥ 4 of the latest analysis).

    Empty when no weekly brief has ever been generated for the company: the
    backlog is the remainder of the brief's ranking, so with no brief there is
    no analysis to draw a backlog from.
    """
    if not _company_has_brief(company.company_id):
        return {"items": [], "count": 0}
    items = list_backlog_items(company.company_id)
    return {"items": items, "count": len(items)}


@router.get("/completed")
def get_completed(company: CompanyContext = Depends(require_company)):
    """The enterprise's COMPLETED findings (Phase 2 lifecycle).

    "Completed" = brief findings whose action is 'prd_created' or 'done'
    (see db/finding_state.py). Backs the Backlog screen's Completed tab. Each
    item carries title / theme_id / action / last_surfaced_at. Titles are
    resolved from the company's backlog_items (keyed by the same theme_id);
    when a theme has no backlog row we fall back to the theme_id so the item
    still renders."""
    rows = list_findings_by_action(company.company_id, COMPLETED_ACTIONS)
    titles = {
        r.get("theme_id"): r.get("title")
        for r in list_backlog_items(company.company_id)
    }
    items = [
        {
            "theme_id": r.get("theme_id"),
            "title": titles.get(r.get("theme_id")) or r.get("theme_id"),
            "action": r.get("action"),
            "last_surfaced_at": r.get("last_surfaced_at"),
        }
        for r in rows
    ]
    return {"items": items, "count": len(items)}


@router.patch("/{item_id}")
def patch_backlog_item(
    item_id: str,
    body: StatusUpdate,
    company: CompanyContext = Depends(require_company),
):
    """Move one backlog item to a new status (in_progress | done | dismissed)."""
    if body.status not in PATCHABLE_STATUSES:
        raise HTTPException(
            400,
            f"Unknown status {body.status!r}; expected one of {PATCHABLE_STATUSES}",
        )
    updated = update_backlog_status(company.company_id, item_id, body.status)
    if updated is None:
        raise HTTPException(404, "Backlog item not found")
    return updated
