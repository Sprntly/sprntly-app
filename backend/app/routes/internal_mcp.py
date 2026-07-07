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
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

# The web ticket view renders an attachment's `sub` directly as an anchor href
# (web/.../TicketDetail.tsx). Now that attachments are AI/token-writable, reject
# script-y URL schemes at the write boundary so a prompt-injected client can't
# store a link that runs script when a teammate clicks it in the app.
_UNSAFE_URL_SCHEME = re.compile(r"^\s*(?:javascript|data|vbscript|file):", re.IGNORECASE)

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


# NOTE: registered AFTER /prd/latest so the static path wins; prd_id is int-typed
# so "latest" could never match this route anyway.
@data_router.get("/prd/{prd_id}", dependencies=[Depends(_require_internal_key)])
def prd_by_id(prd_id: int, company_id: str) -> dict[str, Any]:
    """A specific PRD by id — the parent context of a ticket (its `prd_id`
    comes from list_tickets / get_ticket). Tenant-scoped via require_owned_prd
    (prd → brief → dataset → company); 404 on a foreign/missing id so cross-
    tenant existence is never disclosed."""
    from app.db.prds import get_prd_rendered
    from app.deps.ownership import require_owned_prd

    require_owned_prd(prd_id, company_id)  # raises 404 if not this company's
    return get_prd_rendered(prd_id) or {}


@data_router.get(
    "/tickets/{ticket_key}/data", dependencies=[Depends(_require_internal_key)]
)
def ticket_data(ticket_key: str, company_id: str) -> dict[str, Any]:
    """Full current ticket = generated base content (from prd_tickets.stories)
    merged with per-ticket overrides (ticket_edits) + comments + attachments.

    A developer needs the generated title / description / acceptance criteria /
    scope to implement the ticket; those live in the base story, so returning
    only the overrides (as this route once did) left an unedited ticket looking
    empty. Overrides win where set; base-story context fields (what/why/scope/
    subtasks/labels) are always included."""
    from app.db.client import require_client
    from app.db.prd_tickets import find_ticket_story

    c = require_client()
    story, prd_id = find_ticket_story(company_id, ticket_key)

    edit_resp = (
        c.table("ticket_edits")
        .select("*")
        .eq("company_id", company_id)
        .eq("ticket_key", ticket_key)
        .limit(1)
        .execute()
    )
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

    # 404 only when there is NO trace of this ticket for the company — no
    # generated story, no edit, no comments, no attachments (the tool turns
    # the 404 into a friendly "not found" message).
    if (
        story is None
        and edit is None
        and not (attach_resp.data or [])
        and not (comment_resp.data or [])
    ):
        raise HTTPException(404, "ticket_not_found")

    story = story or {}
    edit = edit or {}

    def _merged(field: str):
        """Override value when set (non-null), else the base story's value."""
        v = edit.get(field)
        return v if v is not None else story.get(field)

    return {
        "id": ticket_key,
        "prd_id": prd_id,
        "title": _merged("title"),
        # Description: an explicit edit wins; else the generated story body.
        "description": edit.get("description")
        if edit.get("description") is not None
        else story.get("body"),
        "acceptance_criteria": _merged("acceptance_criteria"),
        # Base stories carry no status; an unedited ticket's canonical status is
        # "Backlog" (matches the web UI's null→Backlog default), so filters and
        # the AI see a real value rather than null.
        "status": edit.get("status") or "Backlog",
        "priority": _merged("priority"),
        "sprint": edit.get("sprint"),
        "assignee": edit.get("assignee"),
        "ticket_type": story.get("ticket_type"),
        # Generated context a developer needs to implement the ticket.
        "what": story.get("what"),
        "why_now": story.get("why_now"),
        "user_story": story.get("user_story"),
        "scope": story.get("scope"),
        "out_of_scope": story.get("out_of_scope"),
        "subtasks": story.get("subtasks"),
        "labels": story.get("labels"),
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


@data_router.get("/tickets", dependencies=[Depends(_require_internal_key)])
def list_tickets(
    company_id: str, status: str | None = None, ticket_type: str | None = None
) -> dict[str, Any]:
    """Every ticket for a company, flattened across PRDs, with each ticket's
    CURRENT status merged in (from ticket_edits) so a developer sees state at a
    glance. Optional `status` / `ticket_type` filters (case-insensitive).

    Tickets are elements of each PRD's `prd_tickets.stories` array (keyed by the
    story's stable `id` = `ticket_key`). Full per-ticket detail (description,
    acceptance criteria, comments, attachments) comes from GET /tickets/{key}/data.
    """
    from app.db.client import require_client

    c = require_client()
    rows = (
        c.table("prd_tickets")
        .select("prd_id, stories")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )
    # One query for all this company's edits → map ticket_key → override fields,
    # so the list reflects edited status/priority/title without N round-trips.
    edits = (
        c.table("ticket_edits")
        .select("ticket_key, status, priority, title")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )
    edit_by_key = {e["ticket_key"]: e for e in edits}

    want_status = status.strip().lower() if status else None
    want_type = ticket_type.strip().lower() if ticket_type else None

    tickets: list[dict[str, Any]] = []
    for row in rows:
        for story in row.get("stories") or []:
            if not isinstance(story, dict):
                continue
            e = edit_by_key.get(story.get("id"), {})
            # Unedited status defaults to "Backlog" (as in get_ticket / the web
            # UI) so the recommended `status=Backlog` filter actually finds the
            # generated-but-unedited backlog. title/priority use is-not-None
            # (not `or`) to match get_ticket's merge exactly — an explicit ""
            # edit wins consistently across both surfaces.
            cur_status = e.get("status") or "Backlog"
            cur_type = story.get("ticket_type")
            if want_status and cur_status.lower() != want_status:
                continue
            if want_type and (cur_type or "").lower() != want_type:
                continue
            tickets.append(
                {
                    "id": story.get("id"),
                    "title": e["title"] if e.get("title") is not None else story.get("title"),
                    "ticket_type": cur_type,
                    "status": cur_status,
                    "priority": e["priority"] if e.get("priority") is not None else story.get("priority"),
                    "prd_id": row.get("prd_id"),
                }
            )
    return {"tickets": tickets, "count": len(tickets)}


class TicketDescriptionIn(BaseModel):
    description: str = ""
    # None (omitted) = leave the ticket's existing/generated acceptance criteria
    # untouched; a list (incl. []) = an explicit replacement. This is why the
    # route writes it only when non-None — a description-only update must not
    # silently wipe generated criteria.
    acceptance_criteria: list[str] | None = None


class TicketFieldsIn(BaseModel):
    """All optional — only the fields actually sent are written (exclude_unset),
    so a partial update never clobbers the description or the untouched fields
    on the same ticket_edits row. Mirrors routes/tickets.py:FieldsIn."""

    title: str | None = None
    priority: str | None = None
    status: str | None = None
    sprint: str | None = None
    assignee: dict[str, Any] | None = None


class TicketCommentIn(BaseModel):
    body: str = Field(..., min_length=1)


class TicketAttachmentIn(BaseModel):
    label: str = Field(..., min_length=1)
    sub: str = ""


@data_router.put(
    "/tickets/{ticket_key}/description",
    dependencies=[Depends(_require_internal_key)],
)
def save_ticket_description(
    ticket_key: str, company_id: str, body: TicketDescriptionIn
) -> dict[str, Any]:
    """Upsert a ticket's description; replace acceptance criteria only when
    explicitly provided (None = leave the existing/generated criteria intact,
    so a description-only edit doesn't wipe them)."""
    from app.db.client import require_client, utc_now

    payload = {
        "company_id": company_id,
        "ticket_key": ticket_key,
        "description": body.description,
        "updated_at": utc_now(),
    }
    if body.acceptance_criteria is not None:
        payload["acceptance_criteria"] = body.acceptance_criteria
    require_client().table("ticket_edits").upsert(
        payload, on_conflict="company_id,ticket_key"
    ).execute()
    return {"ok": True}


@data_router.put(
    "/tickets/{ticket_key}/fields", dependencies=[Depends(_require_internal_key)]
)
def save_ticket_fields(
    ticket_key: str, company_id: str, body: TicketFieldsIn
) -> dict[str, Any]:
    """Upsert only the sent fields (title/priority/status/sprint/assignee),
    preserving the description + other fields (mirrors
    routes/tickets.py:save_fields)."""
    from app.db.client import require_client, utc_now

    fields = body.model_dump(exclude_unset=True)
    require_client().table("ticket_edits").upsert(
        {
            "company_id": company_id,
            "ticket_key": ticket_key,
            "updated_at": utc_now(),
            **fields,
        },
        on_conflict="company_id,ticket_key",
    ).execute()
    return {"ok": True}


@data_router.post(
    "/tickets/{ticket_key}/comments", dependencies=[Depends(_require_internal_key)]
)
def add_ticket_comment(
    ticket_key: str, company_id: str, user_id: str, body: TicketCommentIn
) -> dict[str, Any]:
    """Insert a comment on a ticket, attributed to the TOKEN OWNER.

    The author is resolved server-side from `user_id` (the token's owner) →
    their profile name, else email, else "mcp" — never accepted from the
    caller, so the AI client can't attribute a comment to someone else.
    Mirrors routes/tickets.py:add_comment otherwise."""
    from app.db.client import require_client
    from app.db.companies import display_name_for_user

    author = display_name_for_user(user_id) or "mcp"
    resp = (
        require_client()
        .table("ticket_comments")
        .insert(
            {
                "company_id": company_id,
                "ticket_key": ticket_key,
                "author": author,
                "body": body.body,
            }
        )
        .execute()
    )
    row = resp.data[0]
    return {
        "id": row["id"],
        "author": row["author"],
        "body": row["body"],
        "time": str(row["created_at"]),
    }


@data_router.post(
    "/tickets/{ticket_key}/attachments",
    dependencies=[Depends(_require_internal_key)],
)
def add_ticket_attachment(
    ticket_key: str, company_id: str, body: TicketAttachmentIn
) -> dict[str, Any]:
    """Attach a link/reference to a ticket — e.g. a developer linking their PR
    or branch. `label` is the display text, `sub` an optional secondary line
    (URL/note). Mirrors routes/tickets.py:add_attachment.

    `sub` is rendered as a clickable href in the app, so script-y URL schemes
    are rejected here (this is an AI/token-writable surface)."""
    from app.db.client import require_client

    if _UNSAFE_URL_SCHEME.match(body.sub or ""):
        raise HTTPException(400, "unsafe_attachment_url")

    resp = (
        require_client()
        .table("ticket_attachments")
        .insert(
            {
                "company_id": company_id,
                "ticket_key": ticket_key,
                "label": body.label,
                "sub": body.sub,
            }
        )
        .execute()
    )
    row = resp.data[0]
    return {"id": row["id"], "label": row["label"], "sub": row["sub"]}
