"""Internal service-to-service API for the MCP server (mcp/).

Same trust model as app/routes/internal.py (the DS-Agent's internal API):
gated by X-Internal-Key, no session cookies or JWTs — purely machine-to-
machine. `mcp/` resolves a customer's bearer token to a company_id via
`/mcp-tokens/resolve`, then calls the data routes below passing that
company_id explicitly as a query param — it never derives company_id from
untrusted client input, and these routes never accept it from anywhere else.

These are thin wrappers around the SAME service functions the /v1/* routes
already call — no business-logic duplication, only route-wiring duplication
(matching the shape of app/routes/internal.py itself).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db.companies import slug_for_company_id
from app.db.mcp_tokens import resolve_mcp_token
from app.routes.internal import _require_internal_key

logger = logging.getLogger(__name__)

resolve_router = APIRouter(prefix="/internal/mcp-tokens", tags=["internal-mcp"])
data_router = APIRouter(prefix="/internal/mcp", tags=["internal-mcp"])


class ResolveTokenBody(BaseModel):
    token: str


@resolve_router.post("/resolve", dependencies=[Depends(_require_internal_key)])
def resolve_token(body: ResolveTokenBody) -> dict[str, Any]:
    ctx = resolve_mcp_token(body.token)
    if not ctx:
        raise HTTPException(401, "invalid_or_revoked_token")
    return ctx


@data_router.get("/datasets", dependencies=[Depends(_require_internal_key)])
def datasets(company_id: str) -> dict[str, Any]:
    """The one dataset belonging to this company (never other tenants' rows)."""
    from app.db.datasets import get_dataset

    slug = slug_for_company_id(company_id)
    row = get_dataset(slug) if slug else None
    return {"datasets": [row] if row else []}


@data_router.get("/brief/current", dependencies=[Depends(_require_internal_key)])
def brief_current(company_id: str) -> dict[str, Any]:
    from app.db.briefs import get_current_brief

    slug = slug_for_company_id(company_id)
    brief = get_current_brief(slug) if slug else None
    if not brief:
        raise HTTPException(404, "no_brief_generated_yet")
    return brief


@data_router.get("/backlog", dependencies=[Depends(_require_internal_key)])
def backlog(company_id: str) -> dict[str, Any]:
    from app.db.backlog import list_backlog_items
    from app.db.briefs import get_current_brief

    # Empty-when-no-brief invariant, mirrored from routes/backlog.py: the
    # backlog is the by-product of a weekly brief, so no brief -> no backlog.
    slug = slug_for_company_id(company_id)
    if not slug or not get_current_brief(slug):
        return {"items": [], "count": 0}
    items = list_backlog_items(company_id)
    return {"items": items, "count": len(items)}


@data_router.get("/prd/latest", dependencies=[Depends(_require_internal_key)])
def prd_latest(company_id: str) -> dict[str, Any]:
    from app.db.prds import get_prd_rendered, latest_prd_for_dataset

    slug = slug_for_company_id(company_id)
    row = latest_prd_for_dataset(slug) if slug else None
    if not row:
        raise HTTPException(404, "no_prd_found")
    return get_prd_rendered(row["id"]) or row


@data_router.get(
    "/tickets/{ticket_key}/data", dependencies=[Depends(_require_internal_key)]
)
def ticket_data(ticket_key: str, company_id: str) -> dict[str, Any]:
    from app.db.client import require_client

    c = require_client()
    edit_resp = (
        c.table("ticket_edits")
        .select("*")
        .eq("company_id", company_id)
        .eq("ticket_key", ticket_key)
        .limit(1)
        .execute()
    )
    # No 404 here: ticket_edits is an overrides table, not a source of truth
    # for ticket existence (mirrors routes/tickets.py:get_ticket_data exactly)
    # — a ticket with no local edits yet still returns 200 with null fields.
    edit = edit_resp.data[0] if edit_resp.data else None

    attach_resp = (
        c.table("ticket_attachments")
        .select("*")
        .eq("company_id", company_id)
        .eq("ticket_key", ticket_key)
        .order("created_at")
        .execute()
    )
    comment_resp = (
        c.table("ticket_comments")
        .select("*")
        .eq("company_id", company_id)
        .eq("ticket_key", ticket_key)
        .order("created_at")
        .execute()
    )

    return {
        "description": edit.get("description") if edit else None,
        "acceptance_criteria": edit.get("acceptance_criteria") if edit else None,
        "title": edit.get("title") if edit else None,
        "priority": edit.get("priority") if edit else None,
        "status": edit.get("status") if edit else None,
        "sprint": edit.get("sprint") if edit else None,
        "assignee": edit.get("assignee") if edit else None,
        "attachments": [
            {"id": a["id"], "label": a["label"], "sub": a["sub"]}
            for a in (attach_resp.data or [])
        ],
        "comments": [
            {
                "id": c_row["id"],
                "author": c_row["author"],
                "body": c_row["body"],
                "time": str(c_row["created_at"]),
            }
            for c_row in (comment_resp.data or [])
        ],
    }
