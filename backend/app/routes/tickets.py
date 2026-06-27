"""Ticket endpoints — CRUD for ticket edits/attachments/comments + push to PM tools.

  GET    /v1/tickets/{key}/data           -> all overrides for a ticket
  PUT    /v1/tickets/{key}/description    -> save description + acceptance criteria
  PUT    /v1/tickets/{key}/fields         -> save title/priority/status/sprint/assignee
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


class FieldsIn(BaseModel):
    """Editable ticket metadata. Every field is optional so a partial save (e.g.
    just the priority picker) only updates what was sent and never clobbers the
    description / acceptance criteria or the other fields."""
    title: str | None = None
    priority: str | None = None
    status: str | None = None
    sprint: str | None = None
    assignee: dict[str, Any] | None = None


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


@router.put("/{ticket_key}/fields")
def save_fields(
    ticket_key: str,
    body: FieldsIn,
    company: CompanyContext = Depends(require_company),
):
    """Save title/priority/status/sprint/assignee. Only the fields actually sent
    are written (exclude_unset), so a partial save preserves the description and
    the other fields on the same ticket_edits row."""
    fields = body.model_dump(exclude_unset=True)
    c = require_client()
    payload = {
        "company_id": company.company_id,
        "ticket_key": ticket_key,
        "updated_at": utc_now(),
        **fields,
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
# ClickUp: 1=urgent, 2=high, 3=normal, 4=low. The generator also emits
# human-readable labels (urgent/high/normal/low), so accept both.

_PRIORITY_MAP: dict[str, int] = {
    "P0": 1,  # Critical → Urgent
    "P1": 2,  # High → High
    "P2": 3,  # Medium → Normal
    "P3": 4,  # Low → Low
    "urgent": 1,
    "high": 2,
    "normal": 3,
    "low": 4,
}


def _clickup_priority(value: str | None) -> int | None:
    """Map an internal priority (P0–P3 or urgent/high/normal/low) to ClickUp's
    1–4 scale; None when unset/unknown so the field is omitted."""
    if not value:
        return None
    return _PRIORITY_MAP.get(value) or _PRIORITY_MAP.get(value.lower())


class TaskIn(BaseModel):
    """One selected task to push. `task_id` is the stable ticket_key the UI
    selected (e.g. "MER-481"); it keys this task's stored overrides
    (ticket_edits / ticket_comments) and is echoed back so the UI can confirm
    each push. Base title/description/criteria are the generated values; any
    saved override wins over them at push time."""

    task_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    priority: str | None = None


class PushClickUpIn(BaseModel):
    list_id: str = Field(..., min_length=1)
    tasks: list[TaskIn] = Field(..., min_length=1)


def _load_overrides(c: Any, cid: str, ticket_key: str) -> dict[str, Any]:
    """Fetch a ticket's saved edits + comments (the user-reviewed source of
    truth) so the pushed task reflects what the user last saved, not just the
    generator's first draft. Best-effort: a missing/empty override row just
    means we fall back to the base task fields."""
    edit_resp = (
        c.table("ticket_edits").select("*")
        .eq("company_id", cid).eq("ticket_key", ticket_key)
        .limit(1).execute()
    )
    edit = edit_resp.data[0] if edit_resp.data else {}
    comment_resp = (
        c.table("ticket_comments").select("*")
        .eq("company_id", cid).eq("ticket_key", ticket_key)
        .order("created_at").execute()
    )
    return {
        "description": edit.get("description") if edit else None,
        "acceptance_criteria": edit.get("acceptance_criteria") if edit else None,
        "comments": [
            {"author": row.get("author") or "user", "body": row.get("body") or ""}
            for row in (comment_resp.data or [])
        ],
    }


def _render_markdown(
    *, description: str, acceptance_criteria: list[str],
    comments: list[dict[str, Any]],
) -> str:
    """Render the task body as ClickUp markdown: the description, then an
    Acceptance criteria bullet list, then any saved comments as a Notes
    section. Empty sections are skipped."""
    parts: list[str] = []
    if description.strip():
        parts.append(description.strip())
    if acceptance_criteria:
        parts.append("")
        parts.append("## Acceptance criteria")
        parts.extend(f"- {ac}" for ac in acceptance_criteria if str(ac).strip())
    if comments:
        parts.append("")
        parts.append("## Notes")
        parts.extend(
            f"- **{cm['author']}:** {cm['body']}"
            for cm in comments if str(cm.get("body", "")).strip()
        )
    return "\n".join(parts).strip()


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
    """Create the selected tasks as ClickUp tasks in a list (explicit write).

    Body: `{list_id, tasks: [{task_id, title, description, acceptance_criteria,
    priority}]}`. For each task we merge the company's saved overrides
    (ticket_edits → description + acceptance criteria; ticket_comments → Notes)
    over the supplied base fields — the user-reviewed values win — then render a
    markdown body and create the ClickUp task.

    Returns `{ok, created: [{task_id, clickup_task_id, url, title}],
    errors: [{task_id, title, error}]}`. Per-task failures are isolated so a
    partial push still reports every task that did land, with its ClickUp id +
    URL, so the UI can confirm each one. 404 if ClickUp isn't connected; 401 if
    the stored token is no longer valid (reconnect ClickUp).
    """
    try:
        access_token = _clickup_access_token(company.company_id)
    except ClickUpNotConnectedError as e:
        raise HTTPException(404, str(e)) from e

    c = require_client()
    cid = company.company_id

    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for task in body.tasks:
        try:
            overrides = _load_overrides(c, cid, task.task_id)
            # User-reviewed overrides win over the generator's base values.
            description = (
                overrides["description"]
                if overrides["description"] is not None
                else task.description
            )
            acceptance = (
                overrides["acceptance_criteria"]
                if overrides["acceptance_criteria"] is not None
                else task.acceptance_criteria
            )
            markdown = _render_markdown(
                description=description or "",
                acceptance_criteria=acceptance or [],
                comments=overrides["comments"],
            )
            result = clickup_oauth.create_task(
                access_token,
                body.list_id,
                name=task.title,
                markdown_description=markdown or None,
                priority=_clickup_priority(task.priority),
            )
            created.append({
                "task_id": task.task_id,
                "clickup_task_id": result.get("id"),
                "url": result.get("url"),
                "title": task.title,
            })
        except clickup_oauth.ClickUpAuthExpiredError as e:
            # Token-level failure isn't per-task — fail the whole push so the UI
            # prompts a reconnect instead of marking every task as errored.
            raise HTTPException(401, str(e)) from e
        except Exception as e:  # noqa: BLE001 — isolate per-task failures
            logger.warning(
                "ClickUp push failed for task %r (%s): %s",
                task.title, task.task_id, e,
            )
            errors.append({
                "task_id": task.task_id, "title": task.title, "error": str(e),
            })

    return {"ok": not errors, "created": created, "errors": errors}
