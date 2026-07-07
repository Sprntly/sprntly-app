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

from mcp.server.fastmcp import FastMCP

from .auth import require_current_company
from .backend_client import get_json


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
    async def get_ticket(ticket_key: str) -> dict:
        """Get full detail for one ticket by its key (e.g. 'PROJ-123') —
        description, acceptance criteria, attachments, and comments."""
        return await _get_ticket_impl(ticket_key)
