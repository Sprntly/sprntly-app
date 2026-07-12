"""Ticket endpoints — CRUD for ticket edits/attachments/comments + push to PM tools.

  GET    /v1/tickets/{key}/data           -> all overrides for a ticket
  PUT    /v1/tickets/{key}/description    -> save description + acceptance criteria
  PUT    /v1/tickets/{key}/fields         -> save title/priority/status/sprint/assignee
  POST   /v1/tickets/{key}/attachments    -> add an attachment
  DELETE /v1/tickets/{key}/attachments/{id} -> remove an attachment
  POST   /v1/tickets/{key}/comments       -> add a comment
  DELETE /v1/tickets/{key}/comments/{id}  -> remove a comment
  GET    /v1/tickets/{key}/comments/summary -> AI summary of the comment thread
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
from app.connectors import clickup_oauth, jira_oauth
from app.db.client import require_client, utc_now
from app.llm import call_json
from app.stories.push import (
    ClickUpNotConnectedError,
    JiraNotConnectedError,
    _clickup_access_token,
    _jira_creds,
)

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
    # Child issues. None (omitted) = keep the generated subtasks; a list
    # (incl. []) = an explicit override, same semantics as the other fields.
    subtasks: list[str] | None = None
    # Tracker custom-field overrides, keyed by field id, values in the
    # normalized shapes (see app/connectors/tracker_meta.py). MERGED over the
    # stored map (a one-field save keeps sibling fields); a null value clears
    # that one field's override.
    custom_fields: dict[str, Any] | None = None
    # Tracker issue type (Jira Task/Story/Bug/… — the destination's real
    # types from metadata). Pushed on create; type CHANGES sync best-effort.
    issue_type: str | None = None


class AttachmentIn(BaseModel):
    label: str = Field(..., min_length=1)
    sub: str = ""


class CommentIn(BaseModel):
    """`author` is IGNORED for normal comments — the author is resolved
    server-side from the signed-in session (profile name → email) so a
    comment is always attributed to the real person and can't be spoofed.
    One exception passes through: the literal "Sprntly" system author, used
    by the change-loop's Accept & propagate note."""
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
        # `.get(...)` with no default: a NULL column stays None, so a fields-only
        # edit (which never set these) reads back as "no override" and the UI
        # keeps the generated ticket body — rather than an empty string/array
        # that would blank it out.
        "description": edit.get("description") if edit else None,
        "acceptance_criteria": edit.get("acceptance_criteria") if edit else None,
        "title": edit.get("title") if edit else None,
        "priority": edit.get("priority") if edit else None,
        "status": edit.get("status") if edit else None,
        "sprint": edit.get("sprint") if edit else None,
        "assignee": edit.get("assignee") if edit else None,
        "subtasks": edit.get("subtasks") if edit else None,
        "custom_fields": edit.get("custom_fields") if edit else None,
        "issue_type": edit.get("issue_type") if edit else None,
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
    """Save/update description and acceptance criteria for a ticket. A
    tracker-bound ticket pushes the change out immediately (instant sync)."""
    from app.stories.sync import kick_prd_sync_from_key

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
    kick_prd_sync_from_key(cid, ticket_key)
    return {"ok": True}


@router.put("/{ticket_key}/fields")
def save_fields(
    ticket_key: str,
    body: FieldsIn,
    company: CompanyContext = Depends(require_company),
):
    """Save title/priority/status/sprint/assignee. Only the fields actually sent
    are written (exclude_unset), so a partial save preserves the description and
    the other fields on the same ticket_edits row.

    Tracker-bound tickets speak the tracker's vocabulary: status/priority are
    validated (and legacy names resolved) against the destination's cached
    meta — unknown values 422 with the allowed names. Unbound tickets keep
    the legacy free-text behavior."""
    from app.connectors.tracker_meta import validate_fields_against_meta

    fields = body.model_dump(exclude_unset=True)
    fields = validate_fields_against_meta(company.company_id, ticket_key, fields)
    c = require_client()
    # custom_fields MERGES over the stored map (one jsonb column holds many
    # fields — a single-field save must not clobber siblings; null clears
    # that one field's override).
    if fields.get("custom_fields") is not None:
        existing = (
            c.table("ticket_edits").select("custom_fields")
            .eq("company_id", company.company_id).eq("ticket_key", ticket_key)
            .limit(1).execute().data
            or []
        )
        merged = dict((existing[0].get("custom_fields") if existing else None) or {})
        for fid, value in fields["custom_fields"].items():
            if value is None:
                merged.pop(fid, None)
            else:
                merged[fid] = value
        fields["custom_fields"] = merged
    payload = {
        "company_id": company.company_id,
        "ticket_key": ticket_key,
        "updated_at": utc_now(),
        **fields,
    }
    c.table("ticket_edits").upsert(
        payload, on_conflict="company_id,ticket_key"
    ).execute()
    # Instant push: a bound ticket's edit lands in the tracker now, not at
    # the next scheduler tick. No-op when unbound / a pass is running.
    from app.stories.sync import kick_prd_sync_from_key

    kick_prd_sync_from_key(company.company_id, ticket_key)
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
    """Add a comment to a ticket, attributed to the signed-in user.

    The author comes from the session (profile name → email → "user"), never
    from the client — matching the MCP comment route's attribution model.
    Only the "Sprntly" system author (Accept & propagate notes) passes
    through as sent."""
    author = (
        "Sprntly"
        if body.author == "Sprntly"
        else (company.user_name or company.user_email or "user")
    )
    c = require_client()
    resp = c.table("ticket_comments").insert({
        "company_id": company.company_id,
        "ticket_key": ticket_key,
        "author": author,
        "body": body.body,
    }).execute()
    row = resp.data[0]
    # Instant one-way push: a bound ticket's comment lands in the tracker as
    # a real comment now (no-op when unbound; the sync pass retries failures).
    from app.stories.sync import kick_comment_push

    kick_comment_push(
        company.company_id, ticket_key, row["id"], author, body.body
    )
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


_SUMMARY_SYSTEM = (
    "You summarize a software ticket's comment thread for a product manager and "
    "detect whether the discussion proposes a concrete change to the ticket's "
    "ACCEPTANCE CRITERIA (a new rule/test the ticket should enforce).\n"
    "`summary`: 1-2 plain sentences capturing where the discussion landed — the "
    "decision/consensus and any open question. No preamble, no bullets, no "
    "restating who said what.\n"
    "`proposed_criterion`: ONLY when the thread clearly proposes a new acceptance "
    "criterion, write it as one 'Given… When… Then…' sentence (prefix '[failure]' "
    "for an error path, '[edge]' for an edge case) — otherwise null. Never invent "
    "a change the thread didn't actually ask for."
)

_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": ["string", "null"]},
        "proposed_criterion": {"type": ["string", "null"]},
    },
    "required": ["summary"],
}


@router.get("/{ticket_key}/comments/summary")
def summarize_comments(
    ticket_key: str,
    company: CompanyContext = Depends(require_company),
):
    """An AI summary of the ticket's comment thread + an optional structured
    proposal. Returns {"summary": null} when there's too little to summarize
    (< 2 comments) so the UI can hide the block. When the thread proposes a
    concrete acceptance-criteria change, `proposed_criterion` carries the exact
    'Given/When/Then' rule the change loop's Accept & propagate will apply."""
    c = require_client()
    resp = (
        c.table("ticket_comments").select("*")
        .eq("company_id", company.company_id).eq("ticket_key", ticket_key)
        .order("created_at").execute()
    )
    comments = resp.data or []
    if len(comments) < 2:
        return {"summary": None, "proposed_criterion": None}
    thread = "\n".join(
        f"{r.get('author', 'user')}: {r.get('body', '')}".strip()
        for r in comments if str(r.get("body", "")).strip()
    )
    try:
        out = call_json(system=_SUMMARY_SYSTEM, user=thread, schema=_SUMMARY_SCHEMA, max_tokens=400)
    except Exception:  # noqa: BLE001 — a summary is best-effort, never blocks the tab
        logger.exception("comment summary failed for %s", ticket_key)
        return {"summary": None, "proposed_criterion": None}
    summary = str(out.get("summary") or "").strip() or None
    proposed = str(out.get("proposed_criterion") or "").strip() or None
    return {"summary": summary, "proposed_criterion": proposed}


# ── Tracker metadata (tracker-native vocabulary) ────────────────────────


class TrackerMetaIn(BaseModel):
    """Identify a destination whose vocabulary the UI needs BEFORE a PRD is
    bound to it — e.g. the create drawer's priority / issue-type pickers right
    after the user picks a Jira project. PRD-bound reads use
    GET /v1/stories/sync/{prd_id}/tracker-meta instead."""
    provider: str = Field(..., pattern="^(clickup|jira)$")
    destination_id: str = Field(..., min_length=1)


@router.post("/tracker-meta")
def tracker_meta_for_destination(
    body: TrackerMetaIn,
    refresh: bool = False,
    company: CompanyContext = Depends(require_company),
):
    """A destination's normalized vocabulary (statuses / priorities / issue
    types / custom fields), cached per destination. 404 when the provider
    isn't connected or metadata can't be fetched (and none is cached) — the
    UI keeps its default pickers."""
    from app.db.tracker_meta import get_or_fetch_meta

    # get_or_fetch_meta degrades (stale cache → None) instead of raising, so
    # "not connected" and "fetch failed" both surface as the 404 below.
    meta = get_or_fetch_meta(
        company.company_id, body.provider, body.destination_id,
        refresh=refresh,
    )
    if meta is None:
        raise HTTPException(
            404, f"No metadata available for {body.provider} destination "
                 f"{body.destination_id!r}"
        )
    return {"provider": body.provider, "destination_id": body.destination_id,
            "meta": meta}


def _parse_ticket_key(ticket_key: str) -> tuple[int, str]:
    """Split a ticket key ("prd-{prd_id}-{ticket_id}") into its parts. The
    ticket_id half is the story's stable id — the key jira_issue_map and
    prd_ticket_sync.statuses are keyed by. 400 on a malformed key."""
    parts = ticket_key.split("-", 2)
    if len(parts) == 3 and parts[0] == "prd" and parts[1].isdigit() and parts[2]:
        return int(parts[1]), parts[2]
    raise HTTPException(400, f"Malformed ticket key {ticket_key!r}")


@router.get("/{ticket_key}/transitions")
def ticket_transitions(
    ticket_key: str,
    company: CompanyContext = Depends(require_company),
):
    """The status moves LEGAL for this ticket right now — what the status
    dropdown offers when the PRD is tracker-bound.

    Jira: statuses change via workflow transitions and the legal set depends
    on the issue's current state, so this proxies the issue's live
    transitions. ClickUp: any list status is always legal, so the full list
    vocabulary is returned in the SAME shape (one web contract). 404 when the
    PRD is unbound or the ticket was never pushed — the web falls back to the
    default status options."""
    from app.connectors.tracker_meta import jira_category_key_to_canonical
    from app.db.jira_sync import get_jira_issue_key
    from app.db.ticket_sync import get_sync_config
    from app.db.tracker_meta import get_or_fetch_meta

    prd_id, ticket_id = _parse_ticket_key(ticket_key)
    cfg = get_sync_config(company.company_id, prd_id)
    if cfg is None:
        raise HTTPException(404, "This PRD's tickets are not bound to a tracker")

    provider = cfg.get("provider")
    if provider == "jira":
        issue_key = get_jira_issue_key(
            company.company_id, cfg["destination_id"], ticket_id
        )
        if not issue_key:
            raise HTTPException(404, "This ticket was never pushed to Jira")
        try:
            access_token, cloud_id = _jira_creds(company.company_id)
        except JiraNotConnectedError as e:
            raise HTTPException(404, str(e)) from e
        transitions = [
            {**t, "category": jira_category_key_to_canonical(t.get("category"))}
            for t in jira_oauth.list_transitions(access_token, cloud_id, issue_key)
        ]
        return {"provider": provider, "transitions": transitions}

    # ClickUp (and any future workflow-free tracker): every list status is a
    # legal target — serve the cached vocabulary in the transitions shape.
    meta = get_or_fetch_meta(
        company.company_id, provider, cfg["destination_id"]
    )
    if not meta:
        raise HTTPException(404, "No metadata available for this destination")
    transitions = [
        {
            "id": None,
            "name": s.get("name"),
            "to_status_id": s.get("id"),
            "to_status_name": s.get("name"),
            "category": s.get("category"),
        }
        for s in meta.get("statuses") or []
    ]
    return {"provider": provider, "transitions": transitions}


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


# Internal ticket priorities (P0–P3 / urgent…low) → Jira's named-priority scheme.
_JIRA_PRIORITY_MAP: dict[str, str] = {
    "P0": "Highest", "P1": "High", "P2": "Medium", "P3": "Low",
    "urgent": "Highest", "high": "High", "normal": "Medium", "low": "Low",
}


def _jira_priority(value: str | None) -> str | None:
    """Map an internal priority to a Jira named priority. A value outside the
    legacy vocab passes through AS-IS: the drawer's picker now sends the
    project's real priority names (from tracker metadata), which Jira accepts
    verbatim — only an unset value omits the field."""
    if not value:
        return None
    return (
        _JIRA_PRIORITY_MAP.get(value)
        or _JIRA_PRIORITY_MAP.get(value.lower())
        or value
    )


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
    # Atlassian accountId (from GET /jira/members) to assign the created issue to.
    # None = leave unassigned. Ignored for ClickUp pushes.
    assignee_account_id: str | None = None


class PushClickUpIn(BaseModel):
    list_id: str = Field(..., min_length=1)
    tasks: list[TaskIn] = Field(..., min_length=1)


class PushJiraIn(BaseModel):
    project_key: str = Field(..., min_length=1)
    tasks: list[TaskIn] = Field(..., min_length=1)
    issue_type: str = Field(default="Task", min_length=1)


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


@router.post("/jira/projects")
def jira_projects(company: CompanyContext = Depends(require_company)):
    """List the Jira projects this company can push tickets into (target picker).

    404 if Jira isn't connected.
    """
    try:
        access_token, cloud_id = _jira_creds(company.company_id)
    except JiraNotConnectedError as e:
        raise HTTPException(404, str(e)) from e
    return {"projects": jira_oauth.list_projects(access_token, cloud_id)}


class JiraMembersIn(BaseModel):
    project_key: str = Field(..., min_length=1)
    query: str | None = None


@router.post("/jira/members")
def jira_members(
    body: JiraMembersIn,
    company: CompanyContext = Depends(require_company),
):
    """List the users assignable to issues in a Jira project (assignee picker).

    Returns `{members: [{accountId, displayName, email, active, avatarUrl}]}`.
    `query` narrows by name/email for type-ahead. 404 if Jira isn't connected.
    """
    try:
        access_token, cloud_id = _jira_creds(company.company_id)
    except JiraNotConnectedError as e:
        raise HTTPException(404, str(e)) from e
    members = jira_oauth.list_assignable_users(
        access_token, cloud_id, body.project_key, query=body.query
    )
    return {"members": members}


@router.post("/push-jira")
def push_jira(
    body: PushJiraIn,
    company: CompanyContext = Depends(require_company),
):
    """Create the selected tasks as Jira issues in a project (explicit write).

    Mirrors push-clickup: merges each ticket's saved overrides (ticket_edits →
    description + acceptance criteria; ticket_comments → Notes) over the base
    fields, renders a text body, and creates one issue per task.

    Returns `{ok, created: [{task_id, jira_issue_key, url, title}],
    errors: [{task_id, title, error}]}`. Per-task failures are isolated. 404 if
    Jira isn't connected; 401 if the stored token is no longer valid (reconnect).
    """
    try:
        access_token, cloud_id = _jira_creds(company.company_id)
    except JiraNotConnectedError as e:
        raise HTTPException(404, str(e)) from e

    c = require_client()
    cid = company.company_id

    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for task in body.tasks:
        try:
            overrides = _load_overrides(c, cid, task.task_id)
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
            body_text = _render_markdown(
                description=description or "",
                acceptance_criteria=acceptance or [],
                comments=overrides["comments"],
            )
            result = jira_oauth.create_issue(
                access_token, cloud_id,
                project_key=body.project_key,
                summary=task.title,
                description=body_text or None,
                issue_type=body.issue_type,
                priority_name=_jira_priority(task.priority),
                assignee_account_id=task.assignee_account_id or None,
            )
            created.append({
                "task_id": task.task_id,
                "jira_issue_key": result.get("key"),
                "url": result.get("url"),
                "title": task.title,
            })
        except jira_oauth.JiraAuthExpiredError as e:
            # Token-level failure isn't per-task — fail the whole push so the UI
            # prompts a reconnect instead of marking every task as errored.
            raise HTTPException(401, str(e)) from e
        except Exception as e:  # noqa: BLE001 — isolate per-task failures
            logger.warning(
                "Jira push failed for task %r (%s): %s",
                task.title, task.task_id, e,
            )
            errors.append({
                "task_id": task.task_id, "title": task.title, "error": str(e),
            })

    return {"ok": not errors, "created": created, "errors": errors}
