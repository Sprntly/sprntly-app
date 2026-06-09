"""Backlog routes — the sequenced, ranked product backlog.

Two routes (both tenant-scoped via require_company):

  GET   /v1/backlog            — the ranked backlog items (rank-ascending),
                                 optionally filtered by status.
  PATCH /v1/backlog/{id}       — move one item's status
                                 (in_progress | done | dismissed).

Items are produced by the synthesis run (sequence_backlog) — every theme that
didn't make the weekly brief, carrying its rank, score, and triage rationale.
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/backlog", tags=["backlog"])


class StatusUpdate(BaseModel):
    status: str


@router.get("")
def get_backlog(company: CompanyContext = Depends(require_company)):
    """The enterprise's ranked backlog (rank-ascending)."""
    items = list_backlog_items(company.company_id)
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
