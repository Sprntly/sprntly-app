"""The v1 MCP tools: 5 read-only actions over a customer's Sprntly workspace.

None of these take a `dataset`/`company` parameter — one-user-one-company is
a schema-enforced product invariant on the backend (require_company 500s on
multiple memberships), so the company scope is resolved once, server-side,
from the bearer token (see auth.py/middleware.py) and never from client
input. This closes off cross-tenant parameter tampering as a class of bug.

Each tool's actual logic lives in a module-level `_*_impl` function, with
`@mcp.tool()` as a thin registration wrapper in `register_tools`. This keeps
the logic directly unit-testable (see tests/test_tools.py) without needing
to go through FastMCP's decorator/registry machinery.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .auth import require_current_company
from .backend_client import get_json, request_json


async def _list_datasets_impl() -> dict:
    ctx = require_current_company()
    return await get_json("/datasets", company_id=ctx.company_id)


async def _get_current_brief_impl() -> dict:
    ctx = require_current_company()
    result = await get_json("/brief/current", company_id=ctx.company_id)
    if result is None:
        return {"message": "No brief has been generated yet for this workspace."}
    return result


async def _get_backlog_impl() -> dict:
    ctx = require_current_company()
    return await get_json("/backlog", company_id=ctx.company_id)


async def _get_latest_prd_impl() -> dict:
    ctx = require_current_company()
    result = await get_json("/prd/latest", company_id=ctx.company_id)
    if result is None:
        return {"message": "No PRD has been generated yet for this workspace."}
    return result


async def _get_ticket_impl(ticket_key: str) -> dict:
    ctx = require_current_company()
    result = await get_json(f"/tickets/{ticket_key}/data", company_id=ctx.company_id)
    if result is None:
        return {"message": f"Ticket {ticket_key!r} was not found in your workspace."}
    return result


async def _list_tickets_impl(
    status: str | None = None, ticket_type: str | None = None
) -> dict:
    ctx = require_current_company()
    params: dict[str, str] = {"company_id": ctx.company_id}
    if status:
        params["status"] = status
    if ticket_type:
        params["ticket_type"] = ticket_type
    return await get_json("/tickets", **params)


async def _get_prd_impl(prd_id: int) -> dict:
    ctx = require_current_company()
    result = await get_json(f"/prd/{prd_id}", company_id=ctx.company_id)
    if result is None:
        return {"message": f"PRD {prd_id} was not found in your workspace."}
    return result


async def _update_ticket_fields_impl(
    ticket_key: str,
    status: str | None = None,
    priority: str | None = None,
    title: str | None = None,
    sprint: str | None = None,
    assignee: dict[str, Any] | None = None,
) -> dict:
    ctx = require_current_company()
    # Only include the fields the caller actually set, so a partial update
    # never blanks the untouched fields on the ticket (the backend upserts
    # exactly what it receives).
    payload: dict[str, Any] = {}
    if status is not None:
        payload["status"] = status
    if priority is not None:
        payload["priority"] = priority
    if title is not None:
        payload["title"] = title
    if sprint is not None:
        payload["sprint"] = sprint
    if assignee is not None:
        payload["assignee"] = assignee
    if not payload:
        return {"message": "No fields to update — pass at least one of status, "
                "priority, title, sprint, or assignee."}
    await request_json(
        "PUT", f"/tickets/{ticket_key}/fields", json=payload, company_id=ctx.company_id
    )
    return {"ok": True, "ticket_key": ticket_key, "updated": sorted(payload)}


async def _update_ticket_description_impl(
    ticket_key: str, description: str, acceptance_criteria: list[str] | None = None
) -> dict:
    ctx = require_current_company()
    payload: dict[str, Any] = {"description": description}
    # Only send acceptance_criteria when the caller actually provided it, so a
    # description-only edit leaves the generated/existing criteria untouched
    # (sending [] would wipe them).
    if acceptance_criteria is not None:
        payload["acceptance_criteria"] = acceptance_criteria
    await request_json(
        "PUT",
        f"/tickets/{ticket_key}/description",
        json=payload,
        company_id=ctx.company_id,
    )
    return {"ok": True, "ticket_key": ticket_key}


async def _add_ticket_comment_impl(ticket_key: str, body: str) -> dict:
    ctx = require_current_company()
    # No author arg: the backend resolves the author from the token owner
    # (user_id) so a comment is always attributed to the real person and
    # can't be spoofed by the client.
    return await request_json(
        "POST",
        f"/tickets/{ticket_key}/comments",
        json={"body": body},
        company_id=ctx.company_id,
        user_id=ctx.user_id,
    )


async def _add_ticket_attachment_impl(
    ticket_key: str, label: str, sub: str = ""
) -> dict:
    ctx = require_current_company()
    return await request_json(
        "POST",
        f"/tickets/{ticket_key}/attachments",
        json={"label": label, "sub": sub},
        company_id=ctx.company_id,
    )


def register_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def list_datasets() -> dict:
        """List the Sprntly workspace connected to this token."""
        return await _list_datasets_impl()

    @mcp.tool()
    async def get_current_brief() -> dict:
        """Get the latest weekly product brief — the top prioritized
        insights/findings for your Sprntly workspace."""
        return await _get_current_brief_impl()

    @mcp.tool()
    async def get_backlog() -> dict:
        """List the ranked product backlog — prioritized items beyond the
        weekly brief's top findings."""
        return await _get_backlog_impl()

    @mcp.tool()
    async def get_latest_prd() -> dict:
        """Get the most recently generated PRD (Product Requirements
        Document) for your workspace."""
        return await _get_latest_prd_impl()

    @mcp.tool()
    async def get_prd(prd_id: int) -> dict:
        """Get a specific PRD by its id — useful for the full product context
        behind a ticket (a ticket's `prd_id` comes from list_tickets /
        get_ticket)."""
        return await _get_prd_impl(prd_id)

    @mcp.tool()
    async def get_ticket(ticket_key: str) -> dict:
        """Get full detail for one ticket by its key: the generated title,
        description, acceptance criteria, scope, and context (what/why),
        merged with any edits, plus its comments and attachments. This is what
        you read to implement a ticket."""
        return await _get_ticket_impl(ticket_key)

    @mcp.tool()
    async def list_tickets(
        status: str | None = None, ticket_type: str | None = None
    ) -> dict:
        """List tickets in your workspace with each ticket's current status
        (id, title, type, status, priority, prd_id). Optionally filter by
        `status` (e.g. "In progress") or `ticket_type`. Use a ticket's id as
        the ticket_key for get_ticket and the update tools."""
        return await _list_tickets_impl(status=status, ticket_type=ticket_type)

    @mcp.tool()
    async def update_ticket_fields(
        ticket_key: str,
        status: str | None = None,
        priority: str | None = None,
        title: str | None = None,
        sprint: str | None = None,
        assignee: dict[str, Any] | None = None,
    ) -> dict:
        """Update one or more fields on a ticket. Only the fields you pass are
        changed; the rest are left as-is. `status` is free-text — the common
        values are "Backlog", "In progress", "In review", and "Done"; use
        those for consistency."""
        return await _update_ticket_fields_impl(
            ticket_key,
            status=status,
            priority=priority,
            title=title,
            sprint=sprint,
            assignee=assignee,
        )

    @mcp.tool()
    async def update_ticket_description(
        ticket_key: str,
        description: str,
        acceptance_criteria: list[str] | None = None,
    ) -> dict:
        """Update a ticket's description. Optionally also replace its
        acceptance criteria — omit `acceptance_criteria` to leave the existing
        (or generated) criteria unchanged; pass a list to replace them."""
        return await _update_ticket_description_impl(
            ticket_key, description, acceptance_criteria
        )

    @mcp.tool()
    async def add_ticket_comment(ticket_key: str, body: str) -> dict:
        """Add a comment to a ticket. The comment is attributed to you (the
        token owner) — you can't post as someone else."""
        return await _add_ticket_comment_impl(ticket_key, body)

    @mcp.tool()
    async def add_ticket_attachment(
        ticket_key: str, label: str, sub: str = ""
    ) -> dict:
        """Attach a link/reference to a ticket — e.g. link the PR or branch
        you're implementing it in. `label` is the display text (or URL); `sub`
        is an optional secondary line (a note or the URL)."""
        return await _add_ticket_attachment_impl(ticket_key, label, sub)
