"""Deterministic (fast-lane) tests for `app/project_group_context.py` — the
@Sprntly group agent's project-awareness: the injected breadth blocks
(`assemble_group_agent_context` / `assemble_private_project_context`) and the
four on-demand depth read tools (`get_project_memory`, `list_project_artifacts`,
`get_artifact_content`, `get_task_ledger`) wired through `dispatch_read_tool`.

The load-bearing invariant this file mutation-proofs is TENANCY SCOPING:
every read is scoped to the ONE `(project_id, company_id)` the caller already
resolved, and `get_artifact_content` gates on the project's OWN tenant-scoped
manifest before returning any bytes — a `(type, id)` not on this project's
manifest is REFUSED, so a hallucinated or probed id can never resolve to
another project's or another company's content.

  - AC (read-tool IDOR): `get_artifact_content` refuses an id not on the
    manifest even when that artifact physically exists and has readable
    content; the RED->GREEN mutation proof flips the manifest to INCLUDE the
    id and the same content then returns, proving the manifest gate (not
    something else) is what refuses (mirrors the enforcement-proof shape in
    `test_project_delegation.py::test_non_member_assignee_no_write`).
  - the other three read tools only ever call their tenant-scoped db helpers
    with THIS project's/company's ids (no cross-project/cross-tenant reach).
  - dispatch falls through (returns None) for a non-read tool so delegate_task
    and the unknown-tool fallback still apply.
  - the breadth assemblers and the dispatch never raise — a read hiccup
    degrades to a placeholder / a plain apology string (AD-P7), never a
    tool error into `run_tool_loop`.

The belt-and-suspenders REAL-DB proof (a genuine cross-project and
cross-tenant id refused through the real supabase-py client + PostgREST) is
`test_read_tool_idor_live.py`, env-gated behind `RUN_READ_TOOL_IDOR_LIVE` and
registered in `test_ci_lane_coverage.py::_KNOWN_UNRUNNABLE`; this file is the
deterministic backstop that runs in the fast lane on every PR.
"""
from __future__ import annotations

import app.project_group_context as pgc


# ── Tool registry shape ──────────────────────────────────────────────────────


def test_read_tools_are_the_four_project_scoped_tools():
    tools = pgc.read_tools()
    names = [t["name"] for t in tools]
    assert names == [
        "get_project_memory",
        "list_project_artifacts",
        "get_artifact_content",
        "get_task_ledger",
    ]
    # Every tool declares a closed input schema.
    for t in tools:
        assert t["input_schema"]["additionalProperties"] is False
        assert t["description"].strip()

    # get_artifact_content is the only one that takes input, and it requires
    # BOTH a type (enum-bounded) and an id — the pair the manifest gate checks.
    gac = next(t for t in tools if t["name"] == "get_artifact_content")
    props = gac["input_schema"]["properties"]
    assert set(gac["input_schema"]["required"]) == {"artifact_type", "artifact_id"}
    assert props["artifact_type"]["enum"] == [
        "prd", "prototype", "evidence", "report", "ticket_set"
    ]


def test_dispatch_falls_through_for_a_non_read_tool():
    """A tool that is not one of the four read tools returns None so the group
    agent's own dispatch (delegate_task, unknown-tool fallback) still runs."""
    assert (
        pgc.dispatch_read_tool(
            "delegate_task", {"assignee": "x"},
            project_id=1, dataset="acme", company_id="c1",
        )
        is None
    )


# ── ★ get_artifact_content IDOR gate (mutation-proofed) ───────────────────────


def _read_artifact(artifact_type: str, artifact_id: int, *, project_id=1):
    return pgc.dispatch_read_tool(
        "get_artifact_content",
        {"artifact_type": artifact_type, "artifact_id": artifact_id},
        project_id=project_id, dataset="acme", company_id="c1",
    )


def test_get_artifact_content_refuses_id_not_on_this_projects_manifest(monkeypatch):
    """The headline read-tool IDOR proof. A PRD (id 999) that PHYSICALLY EXISTS
    with readable content is REFUSED because it is not on THIS project's
    tenant-scoped manifest — then the RED->GREEN mutation proof puts it on the
    manifest and the same body returns, proving the manifest gate is the thing
    that refuses (not a missing row, not an unreadable body)."""
    import app.db.prds as prds_db

    # The foreign artifact is fully readable at the db layer...
    monkeypatch.setattr(
        prds_db, "get_prd_rendered",
        lambda pid: {"payload_md": "TOP-SECRET foreign PRD body"} if pid == 999 else None,
    )
    # ...but this project's manifest contains ONLY its own PRD (id 1).
    monkeypatch.setattr(
        pgc, "list_artifacts_for_project",
        lambda **kw: [{"type": "prd", "id": 1, "title": "Our PRD"}],
    )
    refused = _read_artifact("prd", 999)
    assert "TOP-SECRET" not in refused
    assert "can't find that artifact" in refused.lower()

    # RED->GREEN: flip the manifest to INCLUDE the foreign id — the gate now
    # lets the content through, proving the manifest membership check (and only
    # it) was what blocked the read above.
    monkeypatch.setattr(
        pgc, "list_artifacts_for_project",
        lambda **kw: [{"type": "prd", "id": 999, "title": "Now listed"}],
    )
    allowed = _read_artifact("prd", 999)
    assert "TOP-SECRET foreign PRD body" in allowed

    # Restore the real manifest posture (id off it) — refused once more.
    monkeypatch.setattr(
        pgc, "list_artifacts_for_project",
        lambda **kw: [{"type": "prd", "id": 1, "title": "Our PRD"}],
    )
    assert "can't find that artifact" in _read_artifact("prd", 999).lower()


def test_get_artifact_content_gate_holds_for_a_type_mismatch(monkeypatch):
    """The gate keys on the (type, id) PAIR: an id that is on the manifest as a
    `report` cannot be read as a `prd`, so a probe that guesses the wrong type
    for a known id is refused too."""
    import app.db.prds as prds_db

    monkeypatch.setattr(
        prds_db, "get_prd_rendered",
        lambda pid: {"payload_md": "prd body"},
    )
    monkeypatch.setattr(
        pgc, "list_artifacts_for_project",
        lambda **kw: [{"type": "report", "id": 5, "title": "A report"}],
    )
    assert "can't find that artifact" in _read_artifact("prd", 5).lower()


def test_get_artifact_content_rejects_a_non_integer_id(monkeypatch):
    monkeypatch.setattr(pgc, "list_artifacts_for_project", lambda **kw: [])
    out = pgc.dispatch_read_tool(
        "get_artifact_content",
        {"artifact_type": "prd", "artifact_id": "not-a-number"},
        project_id=1, dataset="acme", company_id="c1",
    )
    assert "isn't valid" in out


# ── The other three read tools only ever touch THIS project/company ───────────


def test_read_tools_pass_only_this_projects_ids_to_their_db_helpers(monkeypatch):
    """No cross-project/cross-tenant reach at the dispatch layer: each of the
    three list/read tools calls its tenant-scoped db helper with exactly the
    project_id/company_id the caller resolved, and nothing else."""
    seen: dict = {}

    monkeypatch.setattr(pgc.memory_db, "get_summary",
                        lambda pid: seen.__setitem__("mem_summary", pid) or {"summary_md": "s"})
    monkeypatch.setattr(pgc.memory_db, "list_entries",
                        lambda pid: seen.__setitem__("mem_entries", pid) or [])
    monkeypatch.setattr(pgc, "list_artifacts_for_project",
                        lambda **kw: seen.__setitem__("artifacts_kw", kw) or [])
    monkeypatch.setattr(pgc.delegation_events_db, "list_status_for_project",
                        lambda pid: seen.__setitem__("ledger", pid) or [])
    monkeypatch.setattr(pgc.projects_db, "list_members", lambda pid: [])

    pgc.dispatch_read_tool("get_project_memory", {}, project_id=42, dataset="acme", company_id="c1")
    pgc.dispatch_read_tool("list_project_artifacts", {}, project_id=42, dataset="acme", company_id="c1")
    pgc.dispatch_read_tool("get_task_ledger", {}, project_id=42, dataset="acme", company_id="c1")

    assert seen["mem_summary"] == 42
    assert seen["mem_entries"] == 42
    assert seen["ledger"] == 42
    assert seen["artifacts_kw"] == {"project_id": 42, "dataset": "acme", "company_id": "c1"}


# ── Never-raise / best-effort degradation (AD-P7) ─────────────────────────────


def test_dispatch_never_raises_on_a_handler_failure(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(pgc, "list_artifacts_for_project", _boom)
    out = pgc.dispatch_read_tool(
        "list_project_artifacts", {}, project_id=1, dataset="acme", company_id="c1"
    )
    assert isinstance(out, str)
    assert "problem" in out.lower()


def test_group_agent_context_degrades_and_never_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("read failed")

    monkeypatch.setattr(pgc.memory_db, "get_summary", _boom)
    monkeypatch.setattr(pgc.memory_db, "get_latest_insight", _boom)
    monkeypatch.setattr(pgc.delegation_events_db, "list_status_for_project", _boom)
    monkeypatch.setattr(pgc, "list_artifacts_for_project", _boom)
    monkeypatch.setattr(pgc.projects_db, "list_members", _boom)

    block = pgc.assemble_group_agent_context(1, "acme", "c1")
    assert "PROJECT CONTEXT" in block  # still a usable block, just placeholders
    assert "(none yet)" in block or "(unavailable)" in block


def test_private_project_context_degrades_and_never_raises(monkeypatch):
    import app.project_context as project_context_mod

    def _boom(*a, **k):
        raise RuntimeError("read failed")

    monkeypatch.setattr(project_context_mod, "assemble_project_context", _boom)
    monkeypatch.setattr(pgc.delegation_events_db, "list_status_for_project", _boom)
    monkeypatch.setattr(pgc, "list_artifacts_for_project", _boom)
    monkeypatch.setattr(pgc.projects_db, "list_members", _boom)

    # Never raises; still returns the scoped roster/ledger/artifacts section.
    block = pgc.assemble_private_project_context(1, "user-1", "acme", "c1")
    assert "never another company's data" in block
