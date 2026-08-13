"""HTTP layer for the All-Chats "Artifacts" tab.

  GET /v1/artifacts?dataset=<slug>  -> unified, recency-sorted list of every
                                       PRD, prototype, evidence, report, ticket
                                       set and custom artifact (team document)
                                       for the caller's company.

Tenant-gated exactly like routes/brief.py: `require_company` resolves the
caller's company from the JWT, then `require_owned_dataset(dataset, ...)`
404s on any slug the caller does not own (so a foreign tenant can never list
another company's artifacts, and existence is never disclosed).

The aggregation/scoping lives in db/artifacts.py. PRDs + evidences are scoped
by the brief's `dataset` slug; prototypes by `workspace_id` (= the company
UUID); reports, ticket sets and custom artifacts by `company_id`. This route passes the slug AND the resolved
company UUID so each surface is scoped the way its own writers scoped it.

A report row carries no `html` — the document body is fetched on open from
GET /v1/reports/{id} (routes/reports.py).
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.artifact_summary import summarize_artifact
from app.auth import CompanyContext, require_company
from app.db.artifacts import list_artifacts_for_company
from app.deps.ownership import (
    require_owned_dataset,
    require_owned_evidence,
    require_owned_prd,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])


@router.get("")
def list_artifacts(
    dataset: str,
    company: CompanyContext = Depends(require_company),
):
    """Unified artifact list for a company (PRDs + prototypes + evidence +
    reports).

    Each item is shaped:
        {
          "type": "prd" | "prototype" | "evidence" | "report",
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


class ChatSummaryIn(BaseModel):
    kind: Literal["prd", "evidence", "prototype", "ticket_set"]
    id: int = Field(..., ge=1)


@router.post("/chat-summary")
def chat_summary(
    body: ChatSummaryIn,
    company: CompanyContext = Depends(require_company),
):
    """LLM chat summary of a freshly generated artifact, for the chat thread.

    The frontend calls this once when a generation completes in the panel and
    posts the returned text as an assistant turn. Content is read server-side
    behind the same per-kind ownership gates the artifact's own GET routes use
    (404 on any id the caller's company does not own — existence is never
    disclosed cross-tenant). Best-effort by contract: a summarizer failure
    returns {"summary": null}, never an error — the chat simply goes without.
    """
    if body.kind == "prd":
        row = require_owned_prd(body.id, company.company_id)
        title, content, agent = row.get("title") or "", row.get("payload_md") or "", "prd"
    elif body.kind == "evidence":
        row = require_owned_evidence(body.id, company.company_id)
        title, content, agent = row.get("title") or "", row.get("payload_md") or "", "evidence"
    elif body.kind == "ticket_set":
        # A standalone ticket set is company-scoped (get_set filters company_id
        # in-query), so a foreign id reads as absent and 404s — same posture as
        # the prototype branch below, no brief chain involved.
        from app.db.ticket_sets import get_set

        row = get_set(company.company_id, body.id)
        if not row:
            raise HTTPException(status_code=404, detail="Ticket set not found")
        # A set has no document — the substance is the roster of tickets. Render
        # them as the content so the summary describes the WORK, not the shape
        # of the JSON.
        stories = [s for s in (row.get("stories") or []) if isinstance(s, dict)]
        title = row.get("title") or "Tickets"
        content = "\n\n".join(
            f"### {s.get('title') or '(untitled)'}\n"
            f"{s.get('what') or s.get('user_story') or s.get('body') or ''}"
            for s in stories
        )
        agent = "user_stories"
    else:
        # Prototypes are workspace-scoped directly (no brief chain) — same gate
        # as GET /v1/design-agent/{id}: a foreign row 404s, never 403s.
        from app.db.prds import get_prd
        from app.db.prototypes import get_prototype

        proto = get_prototype(prototype_id=body.id, workspace_id=company.company_id)
        if not proto:
            raise HTTPException(status_code=404, detail="Prototype not found")
        # A prototype row has no document of its own — the substance is the
        # parent PRD plus what the user asked the build to do. Summarize those.
        prd = get_prd(proto["prd_id"]) if proto.get("prd_id") else None
        title = (prd or {}).get("title") or "Prototype"
        parts = [
            f"Build instructions: {proto['instructions']}" if proto.get("instructions") else "",
            f"Target platform: {proto['target_platform']}" if proto.get("target_platform") else "",
            f"Parent PRD:\n{(prd or {}).get('payload_md') or ''}",
        ]
        content, agent = "\n\n".join(p for p in parts if p), "design_agent"

    summary = summarize_artifact(
        company.company_id, kind=body.kind, title=title, content=content, agent=agent
    )
    return {"summary": summary}
