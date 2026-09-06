"""Tests for `app/project_task_execution.py` — the `execute_task` tool,
its inline best-effort handler, and its wiring into BOTH project surfaces
via the unified answer engine's sixth ladder branch: registered on
`SurfaceScope.extra_tools` by the group agent (`routes/projects.py::
_respond_as_group_agent`) and the private surface (`ask_job_runner.py::
_build_private_scope`), dispatched by the shared branch
(`qa_agent.py::_try_scoped_tool_answer`).

Covers:
  - tool-description property tests (length, doable-type, negative-space
    including analysis-is-out) + registration in both agents (AC1/AC2)
  - doable-set gating: non-doable declines with no generation, analysis
    specifically declines with no generator invoked (AC3/AC5)
  - generate-reuse: `task_type="prd"` invokes the EXISTING `prd_runner`
    entrypoints (stubbed here — no new generation function is defined),
    posts the draft + finalize/input questions into the originating chat
    (AC4)
  - best-effort isolation: a generate failure declines with no partial
    artifact and no raise; malformed input never raises; no
    `project_delegations`/membership/invite row is ever written (AC6/AC7/AC8)
  - cost/observability: the handler-level cost-log helper (AC9)
  - non-regression: `delegate_task` is unchanged alongside `execute_task`
    (AC10)

Drives `project_task_execution.handle_execute_task` directly against
`FakeSupabaseClient` (`isolated_settings`), with the reused `prd_runner`
generate entrypoints stubbed (fast/deterministic — proves the handler's
CONTRACT: gating, reuse-not-reimplementation, ordering, never-raises)
rather than exercising a real LLM generation end to end.
"""
from __future__ import annotations

import inspect
import logging
import re

import pytest

from app import project_task_execution
from app.db import projects as projects_db
from tests._company_helpers import company_client


def _create_project(ctx, *, name: str = "Execute-task project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


def _execute(
    project_id: int,
    *,
    requester_user_id: str | None,
    dataset: str = "",
    company_id: str = "unused-in-fake-db",
    task_type: str = "prd",
    task_summary: str = "Draft the pricing page PRD",
    roster: list[dict] | None = None,
    post_turn=None,
):
    return project_task_execution.handle_execute_task(
        project_id=project_id,
        requester_user_id=requester_user_id,
        dataset=dataset,
        company_id=company_id,
        tool_input={"task_type": task_type, "task_summary": task_summary},
        roster=roster if roster is not None else projects_db.list_members(project_id),
        post_turn=post_turn,
    )


def _stub_prd_generation(monkeypatch, *, raise_error: Exception | None = None):
    """Stub the ONE reuse seam (`project_task_execution._run_prd_generation`)
    so no test in this fast lane runs the real `prd_runner` pipeline (KG
    retrieval + a real LLM call). Records each invocation's args so tests
    can assert the reused pipeline was actually driven with the right
    identifiers, without redefining what it does."""
    calls: list[dict] = []

    async def _fake_run(prd_id, brief_id, insight_index, insight, author):
        calls.append(
            {
                "prd_id": prd_id, "brief_id": brief_id,
                "insight_index": insight_index, "insight": insight, "author": author,
            }
        )
        if raise_error is not None:
            raise raise_error

    monkeypatch.setattr(project_task_execution, "_run_prd_generation", _fake_run)
    return calls


# ── Tool-description + registration property tests (AC1/AC2) ─────────────


def test_execute_tool_description_length_and_negative_space():
    desc = project_task_execution.EXECUTE_TASK_TOOL["description"]
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", desc.strip()) if s]
    assert len(sentences) >= 3, "tool description must be >= 3 sentences"

    lower = desc.lower()
    assert "prd" in lower and "draft" in lower, "must name PRD draft as the doable type"
    assert "do not" in lower, "must explicitly say when NOT to call it"
    assert "delegate_task" in desc, "must point at delegate_task for hand-offs"
    assert "analysis" in lower, "must name analysis as explicitly out of v1"
    assert "not yet supported" in lower or "not supported" in lower

    assert project_task_execution.EXECUTE_TASK_TOOL["input_schema"]["properties"][
        "task_type"
    ]["enum"] == ["prd"]
    assert project_task_execution.EXECUTE_TASK_TOOL["input_schema"]["required"] == [
        "task_type", "task_summary",
    ]
    assert project_task_execution.EXECUTE_TASK_TOOL["input_schema"]["additionalProperties"] is False

    # Negative-space: the checks themselves must actually catch a
    # description that DOESN'T carry these rules — proves this isn't vacuous.
    weak = "Call this tool to draft a PRD for the project."
    assert "do not" not in weak.lower()
    assert "analysis" not in weak.lower()


def test_execute_tool_registered_in_both_agents(monkeypatch):
    # Post-collapse: tool registration for BOTH surfaces relocated into
    # `ProjectContextAssembler.assemble` (the 6 project depth tools ride
    # `SurfaceScope.extra_tools`); dispatch is single-sourced on the unified
    # engine's sixth ladder branch (`qa_agent._try_scoped_tool_answer`) — checked
    # ONCE, since both surfaces share it. Retargeted from the deleted
    # `_respond_as_group_agent` / `_build_private_scope` source-scans to a
    # behavioural check on the assembled scope (gate stubbed; the tool list is
    # DB-independent).
    from app import project_task_execution, qa_agent
    from app.context_assembler import AssembleRequest
    from app.context_assembler_project import ProjectContextAssembler
    from app.db import projects as projects_db

    monkeypatch.setattr(projects_db, "project_belongs_to_company", lambda *a, **k: True)
    monkeypatch.setattr(projects_db, "is_project_member", lambda *a, **k: True)

    execute_name = project_task_execution.EXECUTE_TASK_TOOL["name"]
    for surface in ("private", "group"):
        req = AssembleRequest(
            user_id="u1", company_id="c1", dataset="", conversation_id=None,
            question="q", workspace_id="w1",
            params={"project_id": 9, "surface": surface},
        )
        scope = ProjectContextAssembler().assemble(req)
        names = [t["name"] for t in scope.extra_tools]
        assert execute_name in names, f"the {surface} scope must register execute_task"

    dispatch_src = inspect.getsource(qa_agent._try_scoped_tool_answer)
    assert 'name == "execute_task"' in dispatch_src
    assert "handle_execute_task" in dispatch_src


# ── Doable-set gating (AC3/AC5) ────────────────────────────────────────────


def test_execute_declines_non_doable_type(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    calls = _stub_prd_generation(monkeypatch)

    result = _execute(project["id"], requester_user_id=ctx.user_id, task_type="design")
    assert "delegate" in result.lower()
    assert calls == [], "a non-doable type must never reach generation"


def test_execute_analysis_declines_not_supported(isolated_settings, monkeypatch):
    """AGENT_DOABLE_TYPES == ("prd",); the tool enum is exactly ["prd"]; a
    forced task_type="analysis" call declines with no generator invoked."""
    assert project_task_execution.AGENT_DOABLE_TYPES == ("prd",)
    assert project_task_execution.EXECUTE_TASK_TOOL["input_schema"]["properties"][
        "task_type"
    ]["enum"] == ["prd"]

    calls = _stub_prd_generation(monkeypatch)

    # No project/DB seeding needed — the gate short-circuits before any
    # project- or brief-scoped lookup.
    result = project_task_execution.handle_execute_task(
        project_id=1,
        requester_user_id="someone",
        dataset="",
        company_id="unused",
        tool_input={"task_type": "analysis", "task_summary": "Run a risk analysis"},
        roster=[],
        post_turn=None,
    )
    assert "delegate" in result.lower()
    assert calls == [], "analysis must never reach the PRD generate pipeline"


def test_execute_missing_task_summary_declines(isolated_settings, monkeypatch):
    calls = _stub_prd_generation(monkeypatch)
    result = project_task_execution.handle_execute_task(
        project_id=1, requester_user_id="u", dataset="", company_id="c",
        tool_input={"task_type": "prd", "task_summary": "  "},
        roster=[], post_turn=None,
    )
    assert result  # a string, not a raise
    assert calls == []


# ── Generate-reuse (AC4) ────────────────────────────────────────────────


def test_execute_prd_reuses_generate_pipeline(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    calls = _stub_prd_generation(monkeypatch)
    posted: list[str] = []

    result = _execute(
        project["id"], requester_user_id=ctx.user_id,
        task_summary="Draft the onboarding-flow PRD",
        post_turn=posted.append,
    )

    assert len(calls) == 1, "the reused prd_runner entrypoint must be called exactly once"
    call = calls[0]
    assert call["insight"]["query"] == "Draft the onboarding-flow PRD"
    assert call["insight_index"] == 0

    assert len(posted) == 1, "the draft + finalize questions must be posted into the originating chat"
    assert "PRD" in posted[0]

    assert "PRD" in result

    from app.db.client import require_client

    artifacts = (
        require_client()
        .table("project_artifacts")
        .select("*")
        .eq("project_id", project["id"])
        .eq("artifact_type", "prd")
        .execute()
        .data
    )
    assert len(artifacts) == 1, "the generated PRD must be attached to THIS project"
    assert artifacts[0]["artifact_id"] == call["prd_id"]

    prds = require_client().table("prds").select("*").eq("id", call["prd_id"]).execute().data
    assert len(prds) == 1, "start_prd must have created the real prds row _generate_human_prd writes into"


def test_execute_prd_does_not_define_a_new_generation_function():
    """AC4's DRY guard: the module reuses `prd_runner`'s entrypoints — it
    defines no PRD-generation logic of its own. `_run_prd_generation` is a
    thin async wrapper (a call site), not a generator."""
    src = inspect.getsource(project_task_execution)
    assert "_generate_human_prd" in src
    assert "extract_input_questions_task" in src
    # Negative-space: no local def looks like an LLM call site (`call_md`/
    # `call_json`/`llm_call` are never IMPORTED here — every LLM call is
    # reached only through the imported prd_runner functions; the string
    # `llm_call` legitimately appears once, in a docstring, naming what the
    # reused pipeline itself already uses).
    assert "call_md" not in src
    assert "call_json" not in src
    assert "import llm_call" not in src


def test_execute_prd_dedup_attaches_existing_no_regeneration(isolated_settings, monkeypatch):
    """A repeated ask for the same PRD text (same theme) attaches the
    EXISTING PRD to this project instead of regenerating it."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    calls = _stub_prd_generation(monkeypatch)

    first = _execute(project["id"], requester_user_id=ctx.user_id, task_summary="Draft the same PRD")
    assert len(calls) == 1

    second = _execute(project["id"], requester_user_id=ctx.user_id, task_summary="Draft the same PRD")
    assert len(calls) == 1, "a repeated ask for the same PRD must not regenerate"
    assert "already drafted" in second.lower()

    from app.db.client import require_client

    artifacts = (
        require_client()
        .table("project_artifacts")
        .select("*")
        .eq("project_id", project["id"])
        .eq("artifact_type", "prd")
        .execute()
        .data
    )
    assert len(artifacts) == 1, "re-attaching the same PRD must be a no-op, not a duplicate row"


# ── Best-effort isolation (AC6/AC7/AC8) ────────────────────────────────────


def test_execute_generate_failure_declines_no_partial(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _stub_prd_generation(monkeypatch, raise_error=RuntimeError("simulated LLM failure"))
    posted: list[str] = []

    result = _execute(project["id"], requester_user_id=ctx.user_id, post_turn=posted.append)

    assert "couldn't draft" in result.lower()
    assert posted == [], "no partial draft may be posted into the chat on a generate failure"

    from app.db.client import require_client

    artifacts = (
        require_client()
        .table("project_artifacts")
        .select("id")
        .eq("project_id", project["id"])
        .eq("artifact_type", "prd")
        .execute()
        .data
    )
    assert artifacts == [], "a failed generate must never attach an artifact to the project"


def test_execute_never_raises_on_bad_input(isolated_settings, monkeypatch):
    _stub_prd_generation(monkeypatch)

    # Non-dict tool_input.
    result = project_task_execution.handle_execute_task(
        project_id=1, requester_user_id=None, dataset="", company_id="c",
        tool_input=None, roster=None, post_turn=None,
    )
    assert isinstance(result, str) and result

    # Missing requester + a downstream failure inside the prd branch.
    def _boom(*a, **kw):
        raise RuntimeError("simulated downstream failure")

    monkeypatch.setattr(project_task_execution, "_execute_prd", _boom)
    result2 = project_task_execution.handle_execute_task(
        project_id=1, requester_user_id=None, dataset="", company_id="c",
        tool_input={"task_type": "prd", "task_summary": "x"}, roster=[], post_turn=None,
    )
    assert isinstance(result2, str) and result2


def test_execute_creates_no_delegation_or_invite(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _stub_prd_generation(monkeypatch)

    _execute(project["id"], requester_user_id=ctx.user_id)

    from app.db.client import require_client

    assert require_client().table("project_delegations").select("id").execute().data == []
    members = (
        require_client()
        .table("project_members")
        .select("user_id")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert [m["user_id"] for m in members] == [ctx.user_id], (
        "no membership row beyond the project creator must be written"
    )


# ── Cost / observability (AC9) ─────────────────────────────────────────────


def test_execute_emits_cost_line_when_it_calls_llm(caplog):
    """`_log_execute_run` is the identifier-only cost-log helper for any
    LLM call the HANDLER ITSELF makes (beyond the reused pipeline's own
    logging) — proven directly, since the v1 PRD-only path reuses
    generation verbatim and does not exercise this helper on its own."""
    import time

    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        project_task_execution._log_execute_run(
            project_id=42,
            meta={"input_tokens": 10, "output_tokens": 5, "model": "claude-sonnet-4-6"},
            start=time.monotonic(),
            status="complete",
        )

    lines = [r.getMessage() for r in caplog.records if "projects.task.execute" in r.getMessage()]
    assert len(lines) == 1
    assert "project_id=42" in lines[0]
    assert "task_summary" not in lines[0]


def test_log_execute_run_never_raises():
    import time

    # A broken usage dict must not raise — best-effort logging.
    project_task_execution._log_execute_run(
        project_id=1, meta={}, start=time.monotonic(), status="error",
        error_class="RuntimeError",
    )


# ── Non-regression (AC10) ──────────────────────────────────────────────────


def test_delegate_task_unchanged_alongside_execute(isolated_settings, monkeypatch):
    from app import project_delegation

    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db.client import require_client

    assignee_id = "assignee-" + ctx.user_id
    require_client().table("profiles").insert(
        {"id": assignee_id, "email": f"{assignee_id}@co.com", "full_name": "Fortune Adeyemi", "role": "Designer"}
    ).execute()
    projects_db.add_member(project["id"], assignee_id)

    def _fake_call_md(*, system, user, model, meta_out=None, **kwargs):  # noqa: ARG001
        if meta_out is not None:
            meta_out.update({"model": model, "input_tokens": 1, "output_tokens": 1})
        return "Here is the brief. Please proceed with the task."

    monkeypatch.setattr(project_delegation, "call_md", _fake_call_md)

    roster = projects_db.list_members(project["id"])
    result = project_delegation.handle_delegate_task(
        project_id=project["id"],
        assigner_user_id=ctx.user_id,
        source_conversation_id=1,
        source_turn_id=1,
        roster=roster,
        dataset="",
        company_id="unused-in-fake-db",
        tool_input={"assignee": "Fortune", "task_summary": "Draft the pricing page"},
    )
    assert "Assigned to" in result

    delegations = (
        require_client()
        .table("project_delegations")
        .select("id")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert len(delegations) == 1
