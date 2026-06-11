"""Ticket endpoints — CRUD for ticket edits/attachments/comments + push to PM tools.

  GET    /v1/tickets/{key}/data           -> all overrides for a ticket
  PUT    /v1/tickets/{key}/description    -> save description + acceptance criteria
  POST   /v1/tickets/{key}/attachments    -> add an attachment
  DELETE /v1/tickets/{key}/attachments/{id} -> remove an attachment
  POST   /v1/tickets/{key}/comments       -> add a comment
  DELETE /v1/tickets/{key}/comments/{id}  -> remove a comment
  POST   /v1/tickets/lists                -> ClickUp lists to pick a target
  POST   /v1/tickets/push-clickup         -> create the tickets in ClickUp

All routes require_company (tenant scoped). Ticket data is stored in Supabase
tables: ticket_edits, ticket_attachments, ticket_comments.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.connectors import clickup_oauth
from app.db.client import require_client, utc_now
from app.stories.push import ClickUpNotConnectedError, _clickup_access_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/tickets", tags=["tickets"])


# ── Ticket data CRUD ───────────────────────────────────────────────────


class DescriptionIn(BaseModel):
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)


class AttachmentIn(BaseModel):
    label: str = Field(..., min_length=1)
    sub: str = ""


class CommentIn(BaseModel):
    author: str = "user"
    body: str = Field(..., min_length=1)


@router.get("/{ticket_key}/data")
def get_ticket_data(
    ticket_key: str,
    company: CompanyContext = Depends(require_company),
):
    """Get all overrides for a ticket: description, attachments, comments."""
    c = require_client()
    cid = company.company_id

    # Description + acceptance criteria
    edit_resp = (
        c.table("ticket_edits").select("*")
        .eq("company_id", cid).eq("ticket_key", ticket_key)
        .limit(1).execute()
    )
    edit = edit_resp.data[0] if edit_resp.data else None

    # Attachments
    attach_resp = (
        c.table("ticket_attachments").select("*")
        .eq("company_id", cid).eq("ticket_key", ticket_key)
        .order("created_at").execute()
    )

    # Comments
    comment_resp = (
        c.table("ticket_comments").select("*")
        .eq("company_id", cid).eq("ticket_key", ticket_key)
        .order("created_at").execute()
    )

    return {
        "description": edit.get("description", "") if edit else None,
        "acceptance_criteria": edit.get("acceptance_criteria", []) if edit else None,
        "attachments": [
            {"id": a["id"], "label": a["label"], "sub": a["sub"]}
            for a in (attach_resp.data or [])
        ],
        "comments": [
            {"id": c_row["id"], "author": c_row["author"], "body": c_row["body"],
             "time": str(c_row["created_at"])}
            for c_row in (comment_resp.data or [])
        ],
    }


@router.put("/{ticket_key}/description")
def save_description(
    ticket_key: str,
    body: DescriptionIn,
    company: CompanyContext = Depends(require_company),
):
    """Save/update description and acceptance criteria for a ticket."""
    c = require_client()
    cid = company.company_id
    payload = {
        "company_id": cid,
        "ticket_key": ticket_key,
        "description": body.description,
        "acceptance_criteria": body.acceptance_criteria,
        "updated_at": utc_now(),
    }
    c.table("ticket_edits").upsert(
        payload, on_conflict="company_id,ticket_key"
    ).execute()
    return {"ok": True}


@router.post("/{ticket_key}/attachments")
def add_attachment(
    ticket_key: str,
    body: AttachmentIn,
    company: CompanyContext = Depends(require_company),
):
    """Add an attachment to a ticket."""
    c = require_client()
    resp = c.table("ticket_attachments").insert({
        "company_id": company.company_id,
        "ticket_key": ticket_key,
        "label": body.label,
        "sub": body.sub,
    }).execute()
    row = resp.data[0]
    return {"id": row["id"], "label": row["label"], "sub": row["sub"]}


@router.delete("/{ticket_key}/attachments/{attachment_id}")
def remove_attachment(
    ticket_key: str,
    attachment_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Remove an attachment."""
    c = require_client()
    c.table("ticket_attachments").delete().eq(
        "id", attachment_id
    ).eq("company_id", company.company_id).execute()
    return {"ok": True}


@router.post("/{ticket_key}/comments")
def add_comment(
    ticket_key: str,
    body: CommentIn,
    company: CompanyContext = Depends(require_company),
):
    """Add a comment to a ticket."""
    c = require_client()
    resp = c.table("ticket_comments").insert({
        "company_id": company.company_id,
        "ticket_key": ticket_key,
        "author": body.author,
        "body": body.body,
    }).execute()
    row = resp.data[0]
    return {"id": row["id"], "author": row["author"], "body": row["body"],
            "time": str(row["created_at"])}


@router.delete("/{ticket_key}/comments/{comment_id}")
def remove_comment(
    ticket_key: str,
    comment_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Remove a comment."""
    c = require_client()
    c.table("ticket_comments").delete().eq(
        "id", comment_id
    ).eq("company_id", company.company_id).execute()
    return {"ok": True}

# ── Priority mapping ────────────────────────────────────────────────────
# Internal ticket priorities (P0–P3) → ClickUp's 1–4 scale.
# ClickUp: 1=urgent, 2=high, 3=normal, 4=low.

_PRIORITY_MAP: dict[str, int] = {
    "P0": 1,  # Critical → Urgent
    "P1": 2,  # High → High
    "P2": 3,  # Medium → Normal
    "P3": 4,  # Low → Low
}


class TicketIn(BaseModel):
    title: str = Field(..., min_length=1)
    description: str = ""
    priority: str = "P2"


class PushClickUpIn(BaseModel):
    list_id: str = Field(..., min_length=1)
    tickets: list[TicketIn] = Field(..., min_length=1)


@router.post("/lists")
def clickup_lists(company: CompanyContext = Depends(require_company)):
    """List the ClickUp lists this company can push tickets into (target picker).

    404 if ClickUp isn't connected.
    """
    try:
        token = _clickup_access_token(company.company_id)
    except ClickUpNotConnectedError as e:
        raise HTTPException(404, str(e)) from e
    return {"lists": clickup_oauth.list_lists(token)}


@router.post("/push-clickup")
def push_clickup(
    body: PushClickUpIn,
    company: CompanyContext = Depends(require_company),
):
    """Create the given tickets as tasks in a ClickUp list (explicit write).

    404 if ClickUp isn't connected. Per-ticket failures are isolated and
    reported in `errors` rather than failing the whole batch.
    """
    try:
        access_token = _clickup_access_token(company.company_id)
    except ClickUpNotConnectedError as e:
        raise HTTPException(404, str(e)) from e

    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for ticket in body.tickets:
        try:
            task = clickup_oauth.create_task(
                access_token,
                body.list_id,
                name=ticket.title,
                description=ticket.description or None,
                priority=_PRIORITY_MAP.get(ticket.priority),
            )
            created.append({
                "ticket": ticket.title,
                "task_id": task.get("id"),
                "url": task.get("url"),
            })
        except Exception as e:  # noqa: BLE001 — isolate per-ticket failures
            logger.warning("ClickUp push failed for ticket %r: %s", ticket.title, e)
            errors.append({"ticket": ticket.title, "error": str(e)})

    return {"created": created, "errors": errors}
