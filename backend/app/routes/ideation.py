"""Ideation routes — the prioritized pool of product ideas.

Routes (all tenant-scoped via require_company):

  GET   /v1/ideation            — the visible ideas (rank-ascending): the
                                  weekly shortlist + user-pinned rows.
  GET   /v1/ideation/completed  — completed findings (Completed tab).
  POST  /v1/ideation            — create a user-added idea ("+ Add idea").
  POST  /v1/ideation/reorder    — persist a manual rank order.
  PATCH /v1/ideation/{id}       — move one item's status
                                  (in_progress | done | dismissed).

Items are produced by the synthesis run (sequence_ideation) — every theme that
didn't make the weekly brief's TOP 3, scored and persisted, with the weekly
prioritization pass marking the 25–30 worth showing as `shortlisted`. The GET
route returns ONLY the visible set; the tail stays persisted but hidden (it
competes again on the next weekly run).

Empty-when-no-brief invariant: the ideation pool is the by-product of a weekly
brief generation, so a company that has never had a brief generated MUST show
an empty page. The GET route enforces this explicitly — it returns no items
unless a brief exists for the company — so stale/orphaned rows (e.g. a brief
deleted after the fact) can never surface ideas without an analysis behind
them.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.db.ideation import (
    PATCHABLE_STATUSES,
    create_manual_ideation_item,
    get_ideation_item,
    is_manual_item,
    list_ideation_items,
    list_visible_ideation_items,
    reorder_ideation_items,
    update_ideation_status,
)
from app.db.briefs import get_current_brief
from app.db.companies import slug_for_company_id
from app.db.finding_state import COMPLETED_ACTIONS, list_findings_by_action
from app.evidence_kg import gather_evidence_trail
from app.graph.facade import GraphFacade
from app.graph.retrieval import resolve_insight_hypothesis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ideation", tags=["ideation"])


class StatusUpdate(BaseModel):
    status: str


# IdeationTag values (see ideation_items.tag). Manual "+ Add idea" rows may
# carry one when the UI's idea-type maps cleanly, else null.
_ALLOWED_TAGS = ("something_broken", "something_new", "something_better")


class CreateItem(BaseModel):
    title: str = Field(..., min_length=1)
    tag: str | None = None


class ReorderIn(BaseModel):
    ordered_ids: list[str] = Field(..., min_length=1)


def _company_has_brief(company_id: str) -> bool:
    """True iff a current weekly brief exists for this company.

    Briefs are keyed by the dataset slug (`briefs.dataset == companies.slug`),
    mirroring how the brief routes scope by slug. No slug / no brief row → the
    company has never had an analysis, so its ideation pool must be empty.
    """
    slug = slug_for_company_id(company_id)
    if slug is None:
        return False
    return get_current_brief(slug) is not None


@router.get("")
def get_ideation(company: CompanyContext = Depends(require_company)):
    """The enterprise's visible ideas: the weekly shortlist (25–30, picked by
    the prioritization pass from the latest analysis) plus user-pinned rows.

    Empty when no weekly brief has ever been generated for the company: the
    ideation pool is the remainder of the brief's ranking, so with no brief
    there is no analysis to draw ideas from.
    """
    if not _company_has_brief(company.company_id):
        return {"items": [], "count": 0}
    items = list_visible_ideation_items(company.company_id)
    return {"items": items, "count": len(items)}


@router.get("/completed")
def get_completed(company: CompanyContext = Depends(require_company)):
    """The enterprise's COMPLETED findings (Phase 2 lifecycle).

    "Completed" = brief findings whose action is 'prd_created' or 'done'
    (see db/finding_state.py). Backs the Ideation screen's Completed tab. Each
    item carries title / theme_id / action / last_surfaced_at. Titles are
    resolved from the company's ideation_items (keyed by the same theme_id, over
    ALL rows — a completed finding's title must resolve even when its idea
    isn't shortlisted); when a theme has no ideation row we fall back to the
    theme_id so the item still renders."""
    rows = list_findings_by_action(company.company_id, COMPLETED_ACTIONS)
    titles = {
        r.get("theme_id"): r.get("title")
        for r in list_ideation_items(company.company_id)
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


# How many evidence excerpts the detail popup shows. The trail is sorted
# strongest-first (weight, then confidence), so the head is the useful part; a
# popup that lists 40 signals is a wall, not problem framing.
_DETAIL_EVIDENCE_CAP = 6


@router.get("/{item_id}/detail")
def get_ideation_item_detail(
    item_id: str,
    company: CompanyContext = Depends(require_company),
):
    """One idea, with the KG evidence behind it — backs the Ideation popup.

    The list route returns only what the table needs (title/rank/tag/reasoning).
    Opening an idea asks a different question: *why does this matter?* That
    answer lives in the knowledge graph, not in `ideation_items` — the row
    carries a one-line ranking rationale and nothing else. So we resolve the
    row's theme the same way the ideation PRD path does (see routes/prd.py's
    generate-from-ideation: synthesize an insight from the row, then walk the
    trail) and return the supporting signals as the evidence excerpts the popup
    frames the problem with.

    Manual "+ Add idea" rows have a synthetic ``manual:`` theme_id with no KG
    theme behind them, so they have no recoverable evidence — they return an
    empty trail rather than a misleading one.

    Best-effort on the KG read: a graph failure degrades to an empty trail (the
    popup still renders the idea + its rationale) instead of 500-ing.
    """
    item = get_ideation_item(company.company_id, item_id)
    if item is None:
        raise HTTPException(404, "Ideation item not found")

    theme_id = item.get("theme_id")
    trail: list[dict] = []
    if theme_id and not is_manual_item(item):
        try:
            facade = GraphFacade()
            hypothesis = resolve_insight_hypothesis(
                facade, company.company_id, theme_id, item.get("title")
            )
            trail = gather_evidence_trail(
                facade,
                company.company_id,
                theme_id=theme_id,
                hypothesis=hypothesis,
            )
        except Exception as exc:  # noqa: BLE001 — detail must not hard-fail
            logger.info(
                "ideation detail: evidence trail failed for %s (%s)", item_id, exc
            )
            trail = []

    evidence = [
        {
            "signal_id": s.get("signal_id"),
            "content": s.get("content"),
            "kind": s.get("kind"),
            "source_type": s.get("source_type"),
            "provenance": s.get("provenance") or {},
            "confidence": s.get("confidence"),
        }
        for s in trail[:_DETAIL_EVIDENCE_CAP]
    ]
    # Distinct source types across the WHOLE trail (not just the shown head) —
    # "heard across 3 sources" is a breadth claim about all the evidence.
    sources = sorted({str(s.get("source_type")) for s in trail if s.get("source_type")})

    return {
        "id": item.get("id"),
        "theme_id": theme_id,
        "title": item.get("title"),
        "tag": item.get("tag"),
        "rank": item.get("rank"),
        "score": item.get("score"),
        "status": item.get("status"),
        "reasoning": item.get("reasoning"),
        "evidence": evidence,
        "evidence_count": len(trail),
        "sources": sources,
        "is_manual": is_manual_item(item),
    }


@router.post("")
def create_ideation_item(
    body: CreateItem,
    company: CompanyContext = Depends(require_company),
):
    """Create a user-added idea (the "+ Add idea" flow). Returns the created
    row so the client can render it without a full refetch."""
    if body.tag is not None and body.tag not in _ALLOWED_TAGS:
        raise HTTPException(
            400, f"Unknown tag {body.tag!r}; expected one of {_ALLOWED_TAGS}"
        )
    return create_manual_ideation_item(
        company.company_id, title=body.title.strip(), tag=body.tag
    )


@router.post("/reorder")
def reorder_ideation(
    body: ReorderIn,
    company: CompanyContext = Depends(require_company),
):
    """Persist a new rank order (drag-to-rerank / Re-sequence). Returns the
    visible list (rank-ascending)."""
    items = reorder_ideation_items(company.company_id, body.ordered_ids)
    return {"items": items, "count": len(items)}


@router.patch("/{item_id}")
def patch_ideation_item(
    item_id: str,
    body: StatusUpdate,
    company: CompanyContext = Depends(require_company),
):
    """Move one idea to a new status (in_progress | done | dismissed)."""
    if body.status not in PATCHABLE_STATUSES:
        raise HTTPException(
            400,
            f"Unknown status {body.status!r}; expected one of {PATCHABLE_STATUSES}",
        )
    updated = update_ideation_status(company.company_id, item_id, body.status)
    if updated is None:
        raise HTTPException(404, "Ideation item not found")
    return updated
