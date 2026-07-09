"""Tests for RoleScopedFastMCP (mcp_server/app.py) — per-request tools/list
filtering by token role.

The registry is process-global, but list_tools reads the CompanyContext
contextvar the auth middleware sets per request: a PM token sees every tool,
a developer token never sees the PM-only ones, and no context at all fails
closed to the developer subset. (Calling a hidden tool anyway is covered by
the impl-level gates in test_tools.py.)
"""
from __future__ import annotations

import pytest

from mcp_server import auth
from mcp_server.app import RoleScopedFastMCP
from mcp_server.tools import PM_ONLY_TOOLS, register_tools


def _server() -> RoleScopedFastMCP:
    mcp = RoleScopedFastMCP("test")
    register_tools(mcp)
    return mcp


def _set_ctx(token_role: str):
    ctx = auth.CompanyContext(
        company_id="co-1", user_id="u-1", role="owner", token_role=token_role
    )
    return auth._current_company.set(ctx)


@pytest.mark.asyncio
async def test_pm_token_lists_all_tools():
    mcp = _server()
    reset = _set_ctx("pm")
    try:
        names = {t.name for t in await mcp.list_tools()}
    finally:
        auth._current_company.reset(reset)
    assert PM_ONLY_TOOLS <= names
    assert "list_tickets" in names and "get_prd" in names


@pytest.mark.asyncio
async def test_developer_token_never_lists_pm_only_tools():
    mcp = _server()
    reset = _set_ctx("developer")
    try:
        names = {t.name for t in await mcp.list_tools()}
    finally:
        auth._current_company.reset(reset)
    assert names & PM_ONLY_TOOLS == set()
    # The ticket + PRD surface is intact for developers.
    assert {
        "list_tickets",
        "list_prd_tickets",
        "get_ticket",
        "get_prd",
        "get_prd_prototype",
        "get_prd_evidence",
        "update_ticket_fields",
        "update_ticket_description",
        "add_ticket_comment",
        "add_ticket_attachment",
    } <= names


@pytest.mark.asyncio
async def test_no_context_fails_closed_to_developer_subset():
    """Unauthenticated listing can't happen on /mcp (middleware 401s first),
    but if it ever did, the PM-only tools stay hidden."""
    mcp = _server()
    assert auth._current_company.get() is None
    names = {t.name for t in await mcp.list_tools()}
    assert names & PM_ONLY_TOOLS == set()
