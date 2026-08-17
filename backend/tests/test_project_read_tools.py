"""§A read-tool contract the project responders depend on.

The project read tools (`get_project_memory`, `list_project_artifacts`,
`get_artifact_content`, `get_task_ledger`) are MERGED and single-sourced in
`app.project_group_context` — the unified answer engine's sixth ladder
branch carries them on `SurfaceScope.extra_tools` for BOTH project surfaces
(private and @Sprntly group), importing `read_tools`/`dispatch_read_tool`
from here rather than forking the tenancy gate. This file pins the contract
those importers rely on: the four-tool stable order, that
`get_artifact_content` refuses a `(type, id)` not on THIS project's manifest
(the read-side twin of the §C write gate — AC6, own + cross-tenant), that
dispatch is scoped to the caller's ids only (AC5), and that a handler failure
never raises into the loop.

The full breadth-assembler + RED->GREEN manifest-gate mutation proofs live in
`test_project_group_context.py`; this file asserts the import surface + the
IDOR-relevant read behaviour the new responders build on.
"""
from __future__ import annotations

import app.project_group_context as pgc


def test_read_tools_imported_from_group_context_stable_order():
    # AC (§A): the responders import these FROM project_group_context (merged,
    # not forked) — four tools, stable order.
    tools = pgc.read_tools()
    assert [t["name"] for t in tools] == [
        "get_project_memory",
        "list_project_artifacts",
        "get_artifact_content",
        "get_task_ledger",
    ]
    for t in tools:
        assert t["input_schema"]["additionalProperties"] is False
        assert t["description"].strip()


def _read_artifact(project_id, company_id, atype, aid):
    return pgc.dispatch_read_tool(
        "get_artifact_content",
        {"artifact_type": atype, "artifact_id": aid},
        project_id=project_id, dataset="acme", company_id=company_id,
    )


def test_get_artifact_content_refuses_id_not_on_project(monkeypatch):
    # AC6 — an id NOT on this project's manifest is refused, and no body read is
    # attempted (the manifest gate short-circuits before _artifact_content_for).
    monkeypatch.setattr(pgc, "list_artifacts_for_project", lambda **kw: [])

    def _boom(*a, **kw):  # noqa: ARG001
        raise AssertionError("must not read a body for an off-manifest id")

    monkeypatch.setattr(pgc, "_artifact_content_for", _boom)
    refused = _read_artifact(1, "c1", "prd", 999)
    assert "can't find that artifact" in refused.lower()


def test_get_artifact_content_refuses_cross_tenant_id(monkeypatch):
    # AC6 — a manifest scoped to company c1 does NOT contain a foreign company's
    # prd id, so reading it as c1 is refused by the same membership check.
    monkeypatch.setattr(
        pgc, "list_artifacts_for_project",
        lambda **kw: [{"type": "prd", "id": 5, "title": "Own PRD"}],
    )
    refused = _read_artifact(1, "c1", "prd", 777)  # 777 is the foreign id
    assert "can't find that artifact" in refused.lower()


def test_dispatch_scoped_to_this_project(monkeypatch):
    # AC5 — dispatch threads exactly the caller's project_id/dataset/company_id
    # into the manifest read; a tool can never widen its own scope.
    seen = {}

    def _spy(**kwargs):
        seen.update(kwargs)
        return [{"type": "prd", "id": 5, "title": "T"}]

    monkeypatch.setattr(pgc, "list_artifacts_for_project", _spy)
    pgc.dispatch_read_tool(
        "list_project_artifacts", {},
        project_id=42, dataset="acme", company_id="c-only",
    )
    assert seen == {"project_id": 42, "dataset": "acme", "company_id": "c-only"}


def test_dispatch_never_raises_on_handler_failure(monkeypatch):
    # AC (§A never-raise) — a db failure degrades to a plain apology string, not
    # an exception into run_tool_loop.
    def _boom(*a, **kw):  # noqa: ARG001
        raise RuntimeError("db down")

    monkeypatch.setattr(pgc.memory_db, "get_summary", _boom)
    out = pgc.dispatch_read_tool(
        "get_project_memory", {}, project_id=1, dataset="d", company_id="c1"
    )
    assert isinstance(out, str)
    assert "problem" in out.lower()


def test_dispatch_returns_none_for_unknown_tool():
    # The responders rely on None-fallthrough to reach the propose tool.
    assert pgc.dispatch_read_tool(
        "propose_prd_patch", {}, project_id=1, dataset="d", company_id="c1"
    ) is None
