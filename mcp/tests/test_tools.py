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
    BearerAuthMiddleware would before a tool call runs. token_role defaults
    to 'pm' (full tool set) — the pre-roles behavior every passthrough test
    below assumes."""
    ctx = auth.CompanyContext(company_id="co-1", user_id="u-1", role="owner")
    token = auth._current_company.set(ctx)
    yield ctx
    auth._current_company.reset(token)


@pytest.fixture
def developer_ctx(monkeypatch):
    """A context resolved from a developer-role token: ticket + PRD tools
    only; datasets/backlog/brief must refuse."""
    ctx = auth.CompanyContext(
        company_id="co-1", user_id="u-1", role="owner", token_role="developer"
    )
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


# ── ticket read/edit tools ──


def _stub_writes(monkeypatch):
    """Stub tools.request_json; record every write call and echo an ok body."""
    calls: list[dict] = []

    async def _fake_request_json(method, path, json=None, **params):
        calls.append({"method": method, "path": path, "json": json, "params": params})
        return {"ok": True}

    monkeypatch.setattr(tools, "request_json", _fake_request_json)
    return calls


@pytest.mark.asyncio
async def test_list_tickets_scopes_to_token_owner(company_ctx, monkeypatch):
    """list_tickets always sends the token owner's user_id as the assignee
    filter — 'my tickets' is derived from the token, never a tool param."""
    calls = _stub_backend(monkeypatch, {"/tickets": {"tickets": [{"id": "t1"}], "count": 1}})
    result = await tools._list_tickets_impl()
    assert result["count"] == 1
    assert calls == [("/tickets", {"company_id": "co-1", "assignee_user_id": "u-1"})]


@pytest.mark.asyncio
async def test_list_tickets_forwards_filters(company_ctx, monkeypatch):
    calls = _stub_backend(monkeypatch, {"/tickets": {"tickets": [], "count": 0}})
    await tools._list_tickets_impl(status="In progress", ticket_type="bug")
    assert calls == [(
        "/tickets",
        {
            "company_id": "co-1",
            "assignee_user_id": "u-1",
            "status": "In progress",
            "ticket_type": "bug",
        },
    )]


@pytest.mark.asyncio
async def test_list_prd_tickets_passthrough_not_assignee_scoped(company_ctx, monkeypatch):
    """list_prd_tickets sends the prd_id and company scope but NO assignee
    filter — it deliberately shows the PRD's full ticket set, and forwards
    the optional status/ticket_type filters."""
    calls = _stub_backend(monkeypatch, {"/tickets": {"tickets": [{"id": "t1"}], "count": 1}})
    result = await tools._list_prd_tickets_impl(42)
    assert result["count"] == 1
    assert calls == [("/tickets", {"company_id": "co-1", "prd_id": 42})]

    calls = _stub_backend(monkeypatch, {"/tickets": {"tickets": [], "count": 0}})
    await tools._list_prd_tickets_impl(42, status="Done", ticket_type="bug")
    assert calls == [(
        "/tickets",
        {"company_id": "co-1", "prd_id": 42, "status": "Done", "ticket_type": "bug"},
    )]


@pytest.mark.asyncio
async def test_get_prd_passthrough_and_friendly_when_none(company_ctx, monkeypatch):
    calls = _stub_backend(monkeypatch, {"/prd/5": {"title": "My PRD"}})
    result = await tools._get_prd_impl(5)
    assert result["title"] == "My PRD"
    assert calls == [("/prd/5", {"company_id": "co-1"})]

    _stub_backend(monkeypatch, {"/prd/9": None})
    missing = await tools._get_prd_impl(9)
    assert "not found" in missing["message"].lower()


@pytest.mark.asyncio
async def test_add_ticket_attachment_passthrough(company_ctx, monkeypatch):
    calls = _stub_writes(monkeypatch)
    await tools._add_ticket_attachment_impl("t1", "PR #42", "https://x/pull/42")
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/tickets/t1/attachments"
    assert calls[0]["json"] == {"label": "PR #42", "sub": "https://x/pull/42"}
    assert calls[0]["params"] == {"company_id": "co-1"}


@pytest.mark.asyncio
async def test_update_ticket_fields_sends_only_set_fields(company_ctx, monkeypatch):
    calls = _stub_writes(monkeypatch)
    result = await tools._update_ticket_fields_impl("t1", status="in_progress")
    assert result["ok"] is True
    assert result["updated"] == ["status"]
    assert calls == [
        {
            "method": "PUT",
            "path": "/tickets/t1/fields",
            "json": {"status": "in_progress"},  # priority/title/etc. omitted
            "params": {"company_id": "co-1"},
        }
    ]


@pytest.mark.asyncio
async def test_update_ticket_fields_no_fields_is_a_noop(company_ctx, monkeypatch):
    calls = _stub_writes(monkeypatch)
    result = await tools._update_ticket_fields_impl("t1")
    assert "No fields" in result["message"]
    assert calls == []  # never hits the backend with an empty update


@pytest.mark.asyncio
async def test_update_ticket_description_passthrough(company_ctx, monkeypatch):
    calls = _stub_writes(monkeypatch)
    await tools._update_ticket_description_impl("t1", "desc", ["a", "b"])
    assert calls[0]["method"] == "PUT"
    assert calls[0]["path"] == "/tickets/t1/description"
    assert calls[0]["json"] == {"description": "desc", "acceptance_criteria": ["a", "b"]}
    assert calls[0]["params"] == {"company_id": "co-1"}


@pytest.mark.asyncio
async def test_update_ticket_description_omits_criteria_when_not_given(company_ctx, monkeypatch):
    """A description-only edit must NOT send acceptance_criteria at all, so the
    backend leaves the generated/existing criteria intact (sending [] wiped them)."""
    calls = _stub_writes(monkeypatch)
    await tools._update_ticket_description_impl("t1", "desc")
    assert calls[0]["json"] == {"description": "desc"}
    assert "acceptance_criteria" not in calls[0]["json"]


@pytest.mark.asyncio
async def test_add_ticket_comment_passes_user_id_no_author(company_ctx, monkeypatch):
    """The tool sends the token owner's user_id (so the backend attributes the
    comment to the real person) and never an author field."""
    calls = _stub_writes(monkeypatch)
    await tools._add_ticket_comment_impl("t1", "nice work")
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/tickets/t1/comments"
    assert calls[0]["json"] == {"body": "nice work"}  # no author — resolved server-side
    assert calls[0]["params"] == {"company_id": "co-1", "user_id": "u-1"}


@pytest.mark.asyncio
async def test_write_tools_raise_without_company_context(monkeypatch):
    _stub_writes(monkeypatch)
    with pytest.raises(auth.McpAuthError):
        await tools._add_ticket_comment_impl("t1", "x")


# ── token-role gating ──


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "impl",
    [
        tools._list_datasets_impl,
        tools._get_current_brief_impl,
        tools._get_backlog_impl,
        tools._get_latest_prd_impl,
    ],
)
async def test_pm_only_tools_refuse_developer_token(developer_ctx, monkeypatch, impl):
    """A developer token gets the friendly refusal AND the backend is never
    called — the impl-level check is the hard gate, not just list filtering."""
    calls = _stub_backend(monkeypatch, {})
    result = await impl()
    assert "PM tokens" in result["message"]
    assert calls == []


@pytest.mark.asyncio
async def test_ticket_and_prd_tools_allowed_for_developer_token(developer_ctx, monkeypatch):
    calls = _stub_backend(
        monkeypatch,
        {
            "/tickets": {"tickets": [], "count": 0},
            "/prd/5": {"title": "My PRD"},
        },
    )
    assert (await tools._list_tickets_impl())["count"] == 0
    assert (await tools._get_prd_impl(5))["title"] == "My PRD"
    assert (await tools._list_prd_tickets_impl(5))["count"] == 0
    assert len(calls) == 3

    write_calls = _stub_writes(monkeypatch)
    await tools._add_ticket_comment_impl("t1", "hello")
    assert write_calls[0]["path"] == "/tickets/t1/comments"


@pytest.mark.asyncio
async def test_pm_only_tools_pass_for_pm_token(company_ctx, monkeypatch):
    """The default 'pm' context (as every pre-roles token resolves) still
    reaches the backend for all the PM-only tools."""
    calls = _stub_backend(
        monkeypatch,
        {
            "/datasets": {"datasets": []},
            "/brief/current": {"week_label": "Wk 1"},
            "/backlog": {"items": [], "count": 0},
            "/prd/latest": {"title": "PRD"},
        },
    )
    await tools._list_datasets_impl()
    await tools._get_current_brief_impl()
    await tools._get_backlog_impl()
    await tools._get_latest_prd_impl()
    assert [path for path, _ in calls] == [
        "/datasets", "/brief/current", "/backlog", "/prd/latest",
    ]


def test_pm_only_tools_constant_matches_registered_names():
    """PM_ONLY_TOOLS drives list filtering in app.py — a typo'd name there
    would silently filter nothing, so pin the exact set."""
    assert tools.PM_ONLY_TOOLS == {
        "list_datasets", "get_current_brief", "get_backlog", "get_latest_prd",
    }
