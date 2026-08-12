"""★ Scope expansion — the @Sprntly GROUP agent can propose a PRD edit too, not
just the private chat.

When `PROJECT_PRD_EDIT_ENABLED` is on, `_respond_as_group_agent` hands the
`propose_prd_patch` tool to its `run_tool_loop` and routes a `propose_prd_patch`
tool call through the SAME `handle_propose_prd_patch` — with the SAME §C IDOR
gate and `workspace_id=ctx.company_id` — as the private responder. When off, the
tool is absent and a propose call falls through to the unknown-tool string.

The LLM is faked (`run_tool_loop` patched, mirroring `test_group_chat_turns.py`);
the fake INVOKES `dispatch("propose_prd_patch", …)` so the wiring + tenant key
are exercised, and `handle_propose_prd_patch` is spied so this test asserts the
group path's reachability + IDOR scoping without re-seeding PRDs (the write-side
mutation proofs live in `test_project_prd_patch_tool.py`; the real-DB group
proof in the env-gated live test).
"""
from __future__ import annotations

import pytest

from tests._company_helpers import company_client


def _create_project(ctx):
    return ctx.client.post("/v1/projects", json={"name": "Group PRD-edit project"}).json()


@pytest.fixture
def spy_group_propose(isolated_settings, monkeypatch):
    """Patch run_tool_loop to invoke the propose tool via dispatch, and spy the
    propose handler. Returns the shared state so tests inspect the captured
    tools + handler kwargs."""
    state: dict = {"tools": None, "propose_kwargs": None, "dispatch_out": None}

    import app.routes.projects as projects_route

    def _fake_run_tool_loop(*, system, user, tools, dispatch, model, meta_out=None, **kwargs):
        state["tools"] = [t["name"] for t in tools]
        # Drive a propose_prd_patch tool call through the real _dispatch.
        state["dispatch_out"] = dispatch(
            "propose_prd_patch",
            {"prd_id": 5, "rationale": "tighten", "patch_md": "## X\n\nY"},
        )
        if meta_out is not None:
            meta_out.update({
                "model": model, "input_tokens": 1, "output_tokens": 1,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            })
        return "done"

    def _spy_handle(tool_input, **kwargs):
        state["propose_kwargs"] = kwargs
        return "proposed (spy)"

    monkeypatch.setattr(projects_route, "run_tool_loop", _fake_run_tool_loop)
    monkeypatch.setattr(projects_route, "handle_propose_prd_patch", _spy_handle)
    return state


def test_group_agent_proposes_patch_when_flag_on(monkeypatch, spy_group_propose):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "@Sprntly rewrite the problem statement on the PRD"},
    )
    assert r.status_code == 200

    # The propose tool was in the group agent's registry...
    assert "propose_prd_patch" in spy_group_propose["tools"]
    # ...and _dispatch routed the call to the handler with the SAME tenant key
    # the accept/reject routes filter on (workspace_id == ctx.company_id).
    kw = spy_group_propose["propose_kwargs"]
    assert kw is not None
    assert kw["project_id"] == project["id"]
    assert kw["company_id"] == ctx.company_id
    assert kw["workspace_id"] == ctx.company_id
    assert spy_group_propose["dispatch_out"] == "proposed (spy)"


def test_group_agent_no_propose_tool_when_flag_off(monkeypatch, spy_group_propose):
    monkeypatch.delenv("PROJECT_PRD_EDIT_ENABLED", raising=False)
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "@Sprntly rewrite the problem statement on the PRD"},
    )
    assert r.status_code == 200

    # Tool absent from the registry; a propose call falls through to unknown-tool
    # and the handler is never reached.
    assert "propose_prd_patch" not in spy_group_propose["tools"]
    assert spy_group_propose["propose_kwargs"] is None
    assert "unknown tool" in spy_group_propose["dispatch_out"]
