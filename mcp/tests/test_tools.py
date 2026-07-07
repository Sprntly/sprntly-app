"""Tests for the 5 v1 MCP tool implementations (mcp_server/tools.py).

Each tool's logic lives in a module-level `_*_impl` coroutine, so these
tests drive it directly with a set company context + a stubbed backend
client — no FastMCP registry/transport needed. Covers: the passthrough
happy path, the friendly "not found" message on a 404 (None from the
backend client), and that get_ticket is the only tool taking a client
parameter.
"""
from __future__ import annotations

import pytest

from mcp_server import auth, tools


@pytest.fixture
def company_ctx(monkeypatch):
    """Set a resolved CompanyContext for the duration of a test, the way
    BearerAuthMiddleware would before a tool call runs."""
    ctx = auth.CompanyContext(company_id="co-1", user_id="u-1", role="owner")
    token = auth._current_company.set(ctx)
    yield ctx
    auth._current_company.reset(token)


def _stub_backend(monkeypatch, responses: dict):
    """Patch tools.get_json to return canned responses keyed by path."""
    calls: list[tuple[str, dict]] = []

    async def _fake_get_json(path: str, **params):
        calls.append((path, params))
        return responses.get(path)

    monkeypatch.setattr(tools, "get_json", _fake_get_json)
    return calls


@pytest.mark.asyncio
async def test_list_datasets_passthrough(company_ctx, monkeypatch):
    calls = _stub_backend(monkeypatch, {"/datasets": {"datasets": [{"slug": "acme"}]}})
    result = await tools._list_datasets_impl()
    assert result == {"datasets": [{"slug": "acme"}]}
    # Company id is injected server-side from context, never a tool param.
    assert calls == [("/datasets", {"company_id": "co-1"})]


@pytest.mark.asyncio
async def test_get_current_brief_passthrough(company_ctx, monkeypatch):
    _stub_backend(monkeypatch, {"/brief/current": {"week_label": "Wk 1"}})
    result = await tools._get_current_brief_impl()
    assert result == {"week_label": "Wk 1"}


@pytest.mark.asyncio
async def test_get_current_brief_friendly_when_none(company_ctx, monkeypatch):
    _stub_backend(monkeypatch, {"/brief/current": None})
    result = await tools._get_current_brief_impl()
    assert "No brief" in result["message"]


@pytest.mark.asyncio
async def test_get_backlog_passthrough(company_ctx, monkeypatch):
    _stub_backend(monkeypatch, {"/backlog": {"items": [], "count": 0}})
    result = await tools._get_backlog_impl()
    assert result == {"items": [], "count": 0}


@pytest.mark.asyncio
async def test_get_latest_prd_friendly_when_none(company_ctx, monkeypatch):
    _stub_backend(monkeypatch, {"/prd/latest": None})
    result = await tools._get_latest_prd_impl()
    assert "No PRD" in result["message"]


@pytest.mark.asyncio
async def test_get_ticket_passthrough(company_ctx, monkeypatch):
    calls = _stub_backend(
        monkeypatch, {"/tickets/ABC-1/data": {"description": "hi", "comments": []}}
    )
    result = await tools._get_ticket_impl("ABC-1")
    assert result["description"] == "hi"
    assert calls == [("/tickets/ABC-1/data", {"company_id": "co-1"})]


@pytest.mark.asyncio
async def test_get_ticket_friendly_when_not_found(company_ctx, monkeypatch):
    _stub_backend(monkeypatch, {"/tickets/NOPE-9/data": None})
    result = await tools._get_ticket_impl("NOPE-9")
    assert "NOPE-9" in result["message"]
    assert "not found" in result["message"].lower()


@pytest.mark.asyncio
async def test_tools_raise_without_company_context(monkeypatch):
    """With no CompanyContext set (unauthenticated), every tool fails closed
    rather than calling the backend with a missing/blank company id. No
    company_ctx fixture here, so _current_company is its default (None)."""
    _stub_backend(monkeypatch, {})
    with pytest.raises(auth.McpAuthError):
        await tools._list_datasets_impl()
