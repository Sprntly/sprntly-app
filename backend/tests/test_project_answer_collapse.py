"""Private + group project-chat unified-path routing, the four-invariant
mutation proofs, the backgrounding mechanics, and the queue-ready seams —
the deterministic collapse suite (fake-LLM/monkeypatched throughout; the
real-DB + real-LLM arm is `test_project_answer_collapse_live.py`,
DEFERRED-TO-STAGING).
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
from types import SimpleNamespace

import pytest

import app.ask_job_runner as ajr
import app.qa_agent as qa
import app.routes.projects as projects_route
from app import project_delegation, project_task_execution
from app.db.workspaces import ensure_default_workspace
from app.surface_scope import PROJECT_FACTS_AUTHORITATIVE_PREAMBLE, Surface, SurfaceScope


def _build_private_scope_via_assembler(*, project_id, conversation_id, user_id):
    """Retarget shim for the deleted `ask_job_runner._build_private_scope`: the
    private-scope construction relocated into `ProjectContextAssembler.assemble`
    in the answer-path collapse. Membership gate stubbed (these are pure-unit
    scope/routing tests — no real project; the best-effort breadth/roster/
    instructions reads degrade to empty without a DB)."""
    from unittest.mock import patch

    from app.context_assembler import AssembleRequest
    from app.context_assembler_project import ProjectContextAssembler

    with patch("app.db.projects.project_belongs_to_company", return_value=True), \
         patch("app.db.projects.is_project_member", return_value=True):
        return ProjectContextAssembler().assemble(AssembleRequest(
            user_id=user_id, company_id="c1", dataset="acme",
            conversation_id=conversation_id, question="q", workspace_id="w1",
            params={"project_id": project_id, "surface": "private"},
        ))


def _route_out():
    return SimpleNamespace(output={"skill_id": None, "confidence": 0.0, "action": None})


def _ctx(company_id="c1", workspace_id="w1", user_id="u1"):
    return SimpleNamespace(company_id=company_id, workspace_id=workspace_id, user_id=user_id, user_email=None)


# ── Private collapse (AC4) ─────────────────────────────────────────────────



@pytest.fixture
def _offline_db(monkeypatch):
    """A working fake Supabase WITHOUT reloading app modules.

    Six tests in this file reach a path that needs a DB client. On ambient env
    they fail closed locally and make a REAL Anthropic call in CI, where a key
    IS configured — which is how they failed with a 401 rather than an
    assertion, and why `test-backend` was red on main for days.

    `isolated_settings` is the obvious fixture and it cannot be used here: it
    RELOADS app modules, and this file holds module-level references (`ajr`,
    `qa`) that ~30 sibling tests monkeypatch. Reloading rebinds those and the
    siblings start patching a module the code no longer uses.

    So this does the three things `isolated_settings` does that these tests
    need — env, a fresh fake schema, and the patched client factory — and
    deliberately skips the fourth.
    """
    from tests import _fake_supabase
    from tests.conftest import _FAKE_SCHEMA, reset_fake_db

    # The SETTINGS OBJECT, not the environment. `app.config.settings` is built
    # once at import, so `monkeypatch.setenv` after that changes nothing — which
    # is exactly why `isolated_settings` reloads `app.config` rather than just
    # setting variables. Patching the live object gets the same effect without
    # the reload.
    import app.config as config_mod

    monkeypatch.setattr(config_mod.settings, "anthropic_api_key",
                        "test-key-not-used", raising=False)
    monkeypatch.setattr(config_mod.settings, "supabase_url",
                        "https://fake.supabase.co", raising=False)
    monkeypatch.setattr(config_mod.settings, "supabase_service_role_key",
                        "fake-service-role-key", raising=False)

    # NO REAL LLM CALL. The path under test runs `qa.answer -> ask_runner ->
    # call_json`, and these tests only patch `run_tool_loop` — which that path
    # stopped going through. So the call escaped to the real API: locally it
    # failed closed on a missing key, and in CI, where a key IS configured, it
    # reached Anthropic and came back 401. Patched at `call_json` in both
    # modules that hold a reference, which is what `fake_llm` does.
    def _no_network_call_json(system: str = "", user: str = "", **kwargs):
        return {"answer": "", "citations": [], "_schema_version": 1}

    import app.ask_runner as ask_runner_mod
    import app.llm as llm_mod

    monkeypatch.setattr(llm_mod, "call_json", _no_network_call_json, raising=False)
    monkeypatch.setattr(ask_runner_mod, "call_json", _no_network_call_json,
                        raising=False)

    reset_fake_db(_FAKE_SCHEMA)
    fake = _fake_supabase.FakeSupabaseClient()
    import app.db.client as db_client_mod

    monkeypatch.setattr(db_client_mod, "supabase_client", lambda: fake)
    db_client_mod._reset_supabase_client_for_tests()
    yield fake

def test_private_ask_routes_through_single_shot(monkeypatch):
    captured = {}

    def _fake_answer(**kw):
        captured.update(kw)
        return {"answer": "ok", "citations": []}

    monkeypatch.setattr(ajr.qa_agent, "answer", _fake_answer)
    monkeypatch.setattr(ajr, "complete_ask_job", lambda i, p: None)
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)
    import app.project_memory as pm

    monkeypatch.setattr(pm, "maybe_promote_turn", lambda *a, **kw: None)

    asyncio.run(ajr.run_ask_job(
        ask_id=1, enterprise_id="c1", question="q", dataset="d",
        project_id=9, conversation_id=5, user_id="u1",
    ))
    assert captured["scope"] is not None
    assert captured["scope"].surface == Surface.project_private
    assert captured["scope"].project_id == 9


def test_respond_individual_removed():
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.project_individual_agent")
    assert not hasattr(ajr, "respond_individual")
    assert "respond_individual" not in dir(ajr)


# ── True parity via GATED ROUTING: plain-Q&A streams/cancels, tool-intent
# turns don't (AC5/AC5a). Every test below drives the REAL `_build_private_
# scope()` (extra_tools always the full six — declarative, unchanged) so the
# routing decision is made by the actual `is_project_tool_request` gate, not
# by hand-constructing an empty `extra_tools=()` scope — that bypass is
# exactly what the ship-gate's live run caught as masking the un-gated bug
# (a hand-built empty-tools scope "proved" streaming worked while the real
# `_build_private_scope` path, always 6 tools, never reached the composer at
# all). ─────────────────────────────────────────────────────────────────


def test_private_realscope_plainqa_declines_gate_streams(monkeypatch):
    """AC5: the REAL private scope (all 6 tools) + a plain-context question
    the gate DECLINES routes to the untouched composer path and streams —
    exactly like main chat."""
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    deltas = []

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        if on_delta is not None:
            on_delta("partial-text")
        return {"answer": "ok", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)
    # A COUNTER tripwire, not an exception-throwing one: `_try_scoped_tool_
    # answer` catches ANY exception from `run_tool_loop` and, on the PRIVATE
    # surface, degrades to a silent fall-through (AD-P7) — so a tripwire
    # that raises would be swallowed and prove nothing about whether the
    # loop actually ran. Counting calls is immune to that swallow.
    loop_calls = {"n": 0}

    def _tripwire(**kw):
        loop_calls["n"] += 1
        return "loop ran — must not happen for a declined turn"

    monkeypatch.setattr("app.llm.run_tool_loop", _tripwire)
    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=None, user_id="u1")
    assert len(scope.extra_tools) == 6  # real, declarative, unconditional — not hand-emptied
    out = qa.answer(
        enterprise_id="c1", question="what's blocking the launch?", dataset="d",
        scope=scope, on_delta=lambda t: deltas.append(t),
    )
    assert loop_calls["n"] == 0  # the loop was never entered
    assert deltas == ["partial-text"]
    assert out["answer"] == "ok"


def test_private_realscope_plainqa_is_cancelled_aborts(monkeypatch):
    """AC5: same real-scope decline path — `is_cancelled` aborts generation
    before the composer's expensive call, exactly like main chat."""
    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=None, user_id="u1")
    with pytest.raises(qa.AskCancelled):
        qa.answer(
            enterprise_id="c1", question="what's blocking the launch?", dataset="d",
            scope=scope, is_cancelled=lambda: True,
        )


def test_private_delegation_phrased_fires_gate_no_stream(monkeypatch):
    """AC5/AC5a/AC7: the REAL private scope + a DELEGATION-phrased question
    the gate FIRES on runs the sixth branch (no `on_delta`) and
    `delegate_task` is dispatchable — proving the gate routes by INTENT, not
    by the mere presence of tools."""
    dispatched = []

    def _fake_loop(*, dispatch, **kw):
        dispatched.append(dispatch("delegate_task", {"assignee": "Fortune", "task_summary": "Draft it"}))
        return "sent"

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)
    monkeypatch.setattr(
        "app.project_delegation.handle_delegate_task", lambda **kw: "Sent the brief to Fortune's chat.",
    )
    deltas = []
    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=None, user_id="u1")
    out = qa.answer(
        enterprise_id="c1", question="please delegate the export review to Fortune", dataset="d",
        scope=scope, on_delta=lambda t: deltas.append(t),
    )
    assert deltas == []
    assert dispatched == ["Sent the brief to Fortune's chat."]
    assert out["answer"] == "sent"


def test_private_bare_send_to_member_fires_gate_no_stream(_offline_db, monkeypatch):
    """A bare "send to <roster member>" — NO pronoun object — must reach the
    sixth branch too. `is_project_tool_request` alone declines this shape
    (`_PROJECT_TOOL_DELEGATE_VERB` requires an object: "send THIS to X");
    the roster-aware `_is_bare_send_to_roster_member` OR-clause is what
    admits it — proven here against the REAL private scope, not a hand-
    built one."""
    from app.db import projects as projects_db

    roster = [{"user_id": "u2", "name": "Jay Okon", "job_role": "Engineer"}]
    monkeypatch.setattr(projects_db, "list_members", lambda project_id: roster)

    dispatched = []

    def _fake_loop(*, dispatch, **kw):
        dispatched.append(dispatch("delegate_task", {"assignee": "Jay", "task_summary": "Prioritize the roadmap"}))
        return "sent"

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)
    monkeypatch.setattr(
        "app.project_delegation.handle_delegate_task", lambda **kw: "Sent the brief to Jay's chat.",
    )
    deltas = []
    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=None, user_id="u1")
    out = qa.answer(
        enterprise_id="c1", question="send to Jay to prioritize the roadmap", dataset="d",
        scope=scope, on_delta=lambda t: deltas.append(t),
    )
    assert deltas == []
    assert dispatched == ["Sent the brief to Jay's chat."]
    assert out["answer"] == "sent"


def test_private_bare_send_to_non_member_declines_gate_streams(monkeypatch):
    """The same bare "send ... to X" SHAPE, but X is not on the project's
    roster — must NOT fire the gate (the one thing a pure-regex widen of
    `_PROJECT_TOOL_DELEGATE_VERB` could not have guaranteed). Falls through
    to the ordinary composer path and streams, same as any other declined
    plain turn."""
    from app.db import projects as projects_db

    roster = [{"user_id": "u2", "name": "Jay Okon", "job_role": "Engineer"}]
    monkeypatch.setattr(projects_db, "list_members", lambda project_id: roster)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    deltas = []

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        if on_delta is not None:
            on_delta("partial-text")
        return {"answer": "ok", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)
    loop_calls = {"n": 0}

    def _tripwire(**kw):
        loop_calls["n"] += 1
        return "loop ran — must not happen for a non-member destination"

    monkeypatch.setattr("app.llm.run_tool_loop", _tripwire)
    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=None, user_id="u1")
    out = qa.answer(
        enterprise_id="c1", question="send the report to accounting", dataset="d",
        scope=scope, on_delta=lambda t: deltas.append(t),
    )
    assert loop_calls["n"] == 0
    assert deltas == ["partial-text"]
    assert out["answer"] == "ok"


def test_main_scope_bare_send_never_fires_gate(monkeypatch):
    """AC7-shaped guard: `scope=None` (main chat) never even consults the
    roster gate, so a bare "send to X" phrasing in main chat behaves
    exactly as it always has — `_is_bare_send_to_roster_member` is a no-op
    for `scope is None`."""
    assert qa._is_bare_send_to_roster_member("send to Jay to prioritize this", None) is False
    assert qa._is_bare_send_to_roster_member(
        "send to Jay to prioritize this", SurfaceScope(surface=Surface.main),
    ) is False


def test_gate_removed_plainqa_routes_to_loop_is_red(monkeypatch):
    """AC5a MUTATION: reverting the sixth-branch guard to the un-gated build
    (`scope.extra_tools` alone, `is_project_tool_request` short-circuited to
    always-True — the exact shape of the bug the ship-gate's live run
    caught) makes a PLAIN-Q&A private turn route to the tool loop and never
    stream — the composer-streams assertion goes RED. Restoring the real
    gate on the identical question makes it GREEN again."""
    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=None, user_id="u1")
    plain_question = "what's blocking the launch?"
    deltas = []

    # RED: simulate the un-gated build by forcing the gate to always fire —
    # same effect as deleting `and is_project_tool_request(...)` from the
    # guard. The real `qa.answer()` call path is exercised, unmodified.
    monkeypatch.setattr(qa, "is_project_tool_request", lambda *a, **kw: True)
    monkeypatch.setattr("app.llm.run_tool_loop", lambda **kw: "looped, never streamed")
    out_red = qa.answer(
        enterprise_id="c1", question=plain_question, dataset="d",
        scope=scope, on_delta=lambda t: deltas.append(t),
    )
    assert out_red["answer"] == "looped, never streamed"  # the mutated (un-gated) behaviour
    with pytest.raises(AssertionError):
        assert deltas, "un-gated build never streams a plain-Q&A turn — RED proof"

    # GREEN: restore the real gate; same question now declines and streams.
    monkeypatch.undo()  # drops BOTH mutations above, restoring the real gate + real run_tool_loop
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        if on_delta is not None:
            on_delta("streamed")
        return {"answer": "ok", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)
    # Counter tripwire again (see the sibling test's comment): an exception
    # here would be swallowed by `_try_scoped_tool_answer`'s private-surface
    # AD-P7 degrade and prove nothing about whether the gate actually
    # declined.
    loop_calls = {"n": 0}

    def _tripwire(**kw):
        loop_calls["n"] += 1
        return "loop ran — the real gate must decline this plain question"

    monkeypatch.setattr("app.llm.run_tool_loop", _tripwire)
    qa.answer(
        enterprise_id="c1", question=plain_question, dataset="d",
        scope=scope, on_delta=lambda t: deltas.append(t),
    )
    assert loop_calls["n"] == 0  # the real gate declined — the loop was never entered
    assert deltas == ["streamed"]  # GREEN


def test_private_read_tools_registered_and_dispatched(monkeypatch):
    captured = {}

    def _fake_loop(*, tools, dispatch, **kw):
        captured["tools"] = [t["name"] for t in tools]
        return dispatch("get_task_ledger", {})

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)
    monkeypatch.setattr(
        "app.project_group_context.dispatch_read_tool",
        lambda name, ti, **kw: "ledger text" if name == "get_task_ledger" else None,
    )
    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=None, user_id="u1")
    out = qa.answer(
        enterprise_id="c1", question="please draft a PRD for the ledger work", dataset="d", scope=scope,
    )
    assert "get_task_ledger" in captured["tools"]
    assert out["answer"] == "ledger text"


# ── Project-content read/summary gate (Defect 2) ─────────────────────────
# Natural read/summary questions ("summarize the PRD") were vetoed by
# `is_project_tool_request` (delegate/execute-only) and fell through to the
# composer, which has breadth but NO read tools — the agent could not fetch
# memory/artifact-content/ledger on demand. `is_project_content_request`
# is the parallel positive gate that unlocks the sixth-branch loop for
# exactly these phrasings, ORed alongside the untouched delegate/execute
# gate. These tests prove: (1) the real gate routes a read question into
# the loop and dispatches a read tool; (2)/(3) `scope=None` and
# `SurfaceScope(surface=Surface.main)` NEVER call either gate — main chat
# stays byte-identical; (4) removing the OR disjunct (the mutation) makes
# the SAME question fall through to the composer instead — RED — and
# restoring it is GREEN.


def test_project_read_question_routes_to_loop_and_dispatches_read_tool(monkeypatch):
    """AC4: the REAL project scope (6 tools) + a natural read/summary
    question ("summarize the PRD" — vetoed by `is_project_tool_request`,
    matched by `is_project_content_request`) enters `_try_scoped_tool_
    answer` and dispatches a read tool, with no streaming deltas."""
    dispatched = []

    def _fake_loop(*, dispatch, **kw):
        dispatched.append(dispatch("get_project_memory", {}))
        return "here is the PRD summary"

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)
    monkeypatch.setattr(
        "app.project_group_context.dispatch_read_tool",
        lambda name, ti, **kw: "prd content" if name == "get_project_memory" else None,
    )
    deltas = []
    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=None, user_id="u1")
    out = qa.answer(
        enterprise_id="c1", question="summarize the PRD", dataset="d",
        scope=scope, on_delta=lambda t: deltas.append(t),
    )
    assert deltas == []  # the sixth-branch loop never streams
    assert dispatched == ["prd content"]
    assert out["answer"] == "here is the PRD summary"


def test_main_scope_none_read_question_byte_identical(monkeypatch):
    """AC7: `scope=None` + the same read question NEVER consults either
    gate (both patched to raise if invoked) and streams via the untouched
    composer, exactly like main chat before this change."""

    def _raise(*a, **kw):
        raise AssertionError("gate must not be called on the main-chat path")

    monkeypatch.setattr(qa, "is_project_tool_request", _raise)
    monkeypatch.setattr(qa, "is_project_content_request", _raise)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        if on_delta is not None:
            on_delta("streamed")
        return {"answer": "ok", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)
    deltas = []
    out = qa.answer(
        enterprise_id="c1", question="summarize the PRD", dataset="d",
        scope=None, on_delta=lambda t: deltas.append(t),
    )
    assert deltas == ["streamed"]
    assert out["answer"] == "ok"


def test_main_surface_enum_read_question_byte_identical(monkeypatch):
    """AC7: `SurfaceScope(surface=Surface.main)` behaves identically to
    `scope=None` — no gate calls, no sixth-branch entry."""

    def _raise(*a, **kw):
        raise AssertionError("gate must not be called on the main-chat path")

    monkeypatch.setattr(qa, "is_project_tool_request", _raise)
    monkeypatch.setattr(qa, "is_project_content_request", _raise)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        if on_delta is not None:
            on_delta("streamed")
        return {"answer": "ok", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)
    deltas = []
    out = qa.answer(
        enterprise_id="c1", question="summarize the PRD", dataset="d",
        scope=SurfaceScope(surface=Surface.main), on_delta=lambda t: deltas.append(t),
    )
    assert deltas == ["streamed"]
    assert out["answer"] == "ok"


def test_read_gate_removed_read_question_routes_to_composer_is_red(monkeypatch):
    """AC6 MUTATION: with the `is_project_content_request` disjunct
    monkeypatched to always-False (the "revert" state — `is_project_tool_
    request` already declines "summarize the PRD" on its own, so this
    isolates the new gate's contribution), the read question no longer
    dispatches a read tool and instead falls through to the composer and
    streams — RED proof that the OR is load-bearing. Restoring it goes
    GREEN."""
    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=None, user_id="u1")
    question = "summarize the PRD"

    # RED: simulate the disjunct removed.
    monkeypatch.setattr(qa, "is_project_content_request", lambda *a, **kw: False)
    loop_calls = {"n": 0}

    def _tripwire(**kw):
        loop_calls["n"] += 1
        return "loop ran — must not happen once the disjunct is removed"

    monkeypatch.setattr("app.llm.run_tool_loop", _tripwire)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        if on_delta is not None:
            on_delta("streamed")
        return {"answer": "ok", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)
    deltas = []
    out_red = qa.answer(
        enterprise_id="c1", question=question, dataset="d",
        scope=scope, on_delta=lambda t: deltas.append(t),
    )
    assert loop_calls["n"] == 0  # the loop is unreachable once the disjunct is gone — RED
    assert deltas == ["streamed"]
    assert out_red["answer"] == "ok"

    # GREEN: restore the real gate; the same question now dispatches a read tool.
    monkeypatch.undo()
    dispatched = []

    def _fake_loop(*, dispatch, **kw):
        dispatched.append(dispatch("get_project_memory", {}))
        return "here is the PRD summary"

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)
    monkeypatch.setattr(
        "app.project_group_context.dispatch_read_tool",
        lambda name, ti, **kw: "prd content" if name == "get_project_memory" else None,
    )
    deltas2 = []
    out_green = qa.answer(
        enterprise_id="c1", question=question, dataset="d",
        scope=scope, on_delta=lambda t: deltas2.append(t),
    )
    assert deltas2 == []
    assert dispatched == ["prd content"]
    assert out_green["answer"] == "here is the PRD summary"


def test_private_context_block_reaches_engine(_offline_db, monkeypatch):
    """The private breadth block is folded into `history` by `routes/ask.py`
    BEFORE `run_ask_job` runs (unchanged by this ticket) — `_run_sync`
    forwards that same `history` into `qa_agent.answer` unmodified, and
    `SurfaceScope.context_payload` is intentionally left empty for private
    (never duplicated)."""
    captured = {}

    def _fake_answer(**kw):
        captured.update(kw)
        return {"answer": "ok", "citations": []}

    monkeypatch.setattr(ajr.qa_agent, "answer", _fake_answer)
    monkeypatch.setattr(ajr, "complete_ask_job", lambda i, p: None)
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)
    import app.project_memory as pm

    monkeypatch.setattr(pm, "maybe_promote_turn", lambda *a, **kw: None)

    history_with_block = [{"role": "user", "content": "[PROJECT CONTEXT]\nRoster: ...\n"}]
    asyncio.run(ajr.run_ask_job(
        ask_id=1, enterprise_id="c1", question="q", dataset="d",
        project_id=9, conversation_id=5, user_id="u1", history=history_with_block,
    ))
    assert captured["history"] == history_with_block
    assert captured["scope"].context_payload == ""


# ── Invariant 2 — task-awareness + delegation survival ─────────────────────


def test_delegate_execute_callable_both_surfaces(monkeypatch):
    def _fake_loop(*, dispatch, **kw):
        dispatch("delegate_task", {"assignee": "X", "task_summary": "Y"})
        dispatch("execute_task", {"task_type": "prd", "task_summary": "Z"})
        return "ok"

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)
    delegate_calls = []
    execute_calls = []
    monkeypatch.setattr(
        "app.project_delegation.handle_delegate_task",
        lambda **kw: delegate_calls.append(kw) or "sent",
    )
    monkeypatch.setattr(
        "app.project_task_execution.handle_execute_task",
        lambda **kw: execute_calls.append(kw) or "drafted",
    )

    private_scope = _build_private_scope_via_assembler(project_id=9, conversation_id=5, user_id="u1")
    group_scope = SurfaceScope(
        surface=Surface.project_group, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL, project_task_execution.EXECUTE_TASK_TOOL),
        assigner_identity={"assigner_user_id": "u2", "source_turn_id": 4},
    )
    for scope in (private_scope, group_scope):
        qa.answer(
            enterprise_id="c1", question="please delegate the export review to Fortune",
            dataset="d", scope=scope,
        )

    assert len(delegate_calls) == 2
    assert len(execute_calls) == 2


def test_private_delegate_identity_threaded(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.llm.run_tool_loop",
        lambda *, dispatch, **kw: dispatch("delegate_task", {"assignee": "X", "task_summary": "Y"}),
    )
    monkeypatch.setattr(
        "app.project_delegation.handle_delegate_task",
        lambda **kw: captured.update(kw) or "sent",
    )
    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=5, user_id="u-assigner")
    qa.answer(
        enterprise_id="c1", question="please delegate the export review to Fortune",
        dataset="d", scope=scope,
    )
    assert captured["assigner_user_id"] == "u-assigner"
    assert captured["source_conversation_id"] == 5


def test_group_delegate_identity_threaded(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.llm.run_tool_loop",
        lambda *, dispatch, **kw: dispatch("delegate_task", {"assignee": "X", "task_summary": "Y"}),
    )
    monkeypatch.setattr(
        "app.project_delegation.handle_delegate_task",
        lambda **kw: captured.update(kw) or "sent",
    )
    scope = SurfaceScope(
        surface=Surface.project_group, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL,),
        assigner_identity={"assigner_user_id": "u-asker", "source_turn_id": 42},
    )
    qa.answer(
        enterprise_id="c1", question="please delegate the export review to Fortune",
        dataset="d", scope=scope,
    )
    assert captured["assigner_user_id"] == "u-asker"
    assert captured["source_turn_id"] == 42


def test_private_delegate_source_content_threaded(monkeypatch):
    """The transcript `_try_scoped_tool_answer` builds for the model (the
    SAME text the private surface's own question is rendered into) reaches
    `handle_delegate_task` as `source_content` — the requester's actual
    words, not left for the brief call to reconstruct from project memory
    alone (root cause #3)."""
    captured = {}
    monkeypatch.setattr(
        "app.llm.run_tool_loop",
        lambda *, dispatch, **kw: dispatch("delegate_task", {"assignee": "X", "task_summary": "Y"}),
    )
    monkeypatch.setattr(
        "app.project_delegation.handle_delegate_task",
        lambda **kw: captured.update(kw) or "sent",
    )
    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=5, user_id="u-assigner")
    qa.answer(
        enterprise_id="c1",
        question="Here's the feedback: users want dark mode. Send this to Fortune to prioritize.",
        dataset="d", scope=scope,
    )
    assert "users want dark mode" in captured["source_content"]


def test_group_delegate_source_content_uses_prerendered_transcript(monkeypatch):
    """Group's `source_content` comes from the full attributed transcript
    (`prerendered_transcript`) — the model's own preceding turn (e.g. the
    themes it just produced) rides into the brief, not only the latest
    trigger message."""
    captured = {}
    monkeypatch.setattr(
        "app.llm.run_tool_loop",
        lambda *, dispatch, **kw: dispatch("delegate_task", {"assignee": "X", "task_summary": "Y"}),
    )
    monkeypatch.setattr(
        "app.project_delegation.handle_delegate_task",
        lambda **kw: captured.update(kw) or "sent",
    )
    scope = SurfaceScope(
        surface=Surface.project_group, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL,),
        assigner_identity={"assigner_user_id": "u-asker", "source_turn_id": 42},
        prerendered_transcript=(
            "Alex (PM): here's the feedback\n"
            "Sprntly: THEMES: users want dark mode; onboarding is too slow.\n"
            "Alex (PM): send this to Fortune to prioritize"
        ),
    )
    qa.answer(
        enterprise_id="c1", question="send this to Fortune to prioritize",
        dataset="d", scope=scope,
    )
    assert "users want dark mode" in captured["source_content"]


def test_delegate_identity_blanked_is_red(monkeypatch):
    """MUTATION: blank the threaded identity -> the attribution assertion
    goes RED; restore it -> GREEN (PI13)."""
    captured = {}
    monkeypatch.setattr(
        "app.llm.run_tool_loop",
        lambda *, dispatch, **kw: dispatch("delegate_task", {"assignee": "X", "task_summary": "Y"}),
    )
    monkeypatch.setattr(
        "app.project_delegation.handle_delegate_task",
        lambda **kw: captured.update(kw) or "sent",
    )

    blanked_scope = SurfaceScope(
        surface=Surface.project_private, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL,),
        assigner_identity={"assigner_user_id": None, "source_conversation_id": None},
    )
    qa.answer(
        enterprise_id="c1", question="please delegate the export review to Fortune",
        dataset="d", scope=blanked_scope,
    )
    with pytest.raises(AssertionError):
        assert captured["assigner_user_id"] == "u-assigner"  # RED

    captured.clear()
    restored_scope = _build_private_scope_via_assembler(project_id=9, conversation_id=5, user_id="u-assigner")
    qa.answer(
        enterprise_id="c1", question="please delegate the export review to Fortune",
        dataset="d", scope=restored_scope,
    )
    assert captured["assigner_user_id"] == "u-assigner"  # GREEN


def test_execute_task_post_turn_fires(_offline_db, monkeypatch):
    monkeypatch.setattr(
        "app.llm.run_tool_loop",
        lambda *, dispatch, **kw: dispatch("execute_task", {"task_type": "prd", "task_summary": "Z"}),
    )

    def _fake_execute(**kw):
        cb = kw.get("post_turn")
        if cb is not None:
            cb("progress update")
        return "drafted"

    monkeypatch.setattr("app.project_task_execution.handle_execute_task", _fake_execute)

    private_posts = []
    monkeypatch.setattr(
        ajr, "post_individual_turn",
        lambda conv_id, role, content: private_posts.append((conv_id, role, content)),
    )
    private_scope = _build_private_scope_via_assembler(project_id=9, conversation_id=5, user_id="u1")
    qa.answer(
        enterprise_id="c1", question="please draft a PRD for the onboarding flow",
        dataset="d", scope=private_scope,
    )
    assert private_posts == [(5, "assistant", "progress update")]

    group_posts = []
    group_scope = SurfaceScope(
        surface=Surface.project_group, project_id=9,
        extra_tools=(project_task_execution.EXECUTE_TASK_TOOL,),
        post_turn=lambda content: group_posts.append(content),
    )
    qa.answer(
        enterprise_id="c1", question="please draft a PRD for the onboarding flow",
        dataset="d", scope=group_scope,
    )
    assert group_posts == ["progress update"]


def test_delegate_writes_delegations_row(monkeypatch):
    import app.project_delegation as pd

    recorded = []
    monkeypatch.setattr(pd, "record_delegation", lambda **kw: recorded.append(kw) or {"id": 1})
    monkeypatch.setattr(
        pd, "resolve_member",
        lambda pid, needle: {"status": "found", "member": {"user_id": "u-assignee", "name": "Assignee"}},
    )
    monkeypatch.setattr(pd, "is_project_member", lambda pid, uid: True)
    monkeypatch.setattr(pd, "_build_brief", lambda *a, **kw: "Brief text")
    monkeypatch.setattr(pd, "create_individual_project_chat", lambda pid, uid: {"id": 77})
    monkeypatch.setattr(pd, "post_individual_turn", lambda conv_id, role, content: {"id": 1})
    monkeypatch.setattr(
        "app.llm.run_tool_loop",
        lambda *, dispatch, **kw: dispatch("delegate_task", {"assignee": "Assignee", "task_summary": "Draft it"}),
    )

    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=5, user_id="u-assigner")
    qa.answer(
        enterprise_id="c1", question="please delegate this to Assignee",
        dataset="d", scope=scope,
    )
    assert len(recorded) == 1  # a real delegate_task call seeds a project_delegations row


def test_delegate_unregistered_is_red(monkeypatch):
    """MUTATION: `delegate_task` NOT registered on `extra_tools` -> the model
    can never call it, so `record_delegation` is never reached (RED);
    re-registering it restores the row-written path (GREEN)."""
    import app.project_delegation as pd

    recorded = []
    monkeypatch.setattr(pd, "record_delegation", lambda **kw: recorded.append(kw) or {"id": 1})
    monkeypatch.setattr(
        pd, "resolve_member",
        lambda pid, needle: {"status": "found", "member": {"user_id": "u-assignee", "name": "Assignee"}},
    )
    monkeypatch.setattr(pd, "is_project_member", lambda pid, uid: True)
    monkeypatch.setattr(pd, "_build_brief", lambda *a, **kw: "Brief text")
    monkeypatch.setattr(pd, "create_individual_project_chat", lambda pid, uid: {"id": 77})
    monkeypatch.setattr(pd, "post_individual_turn", lambda conv_id, role, content: {"id": 1})

    base = _build_private_scope_via_assembler(project_id=9, conversation_id=5, user_id="u-assigner")

    # RED: delegate_task removed from extra_tools; the fake loop can only
    # ever be asked to call tools it was offered, so it calls nothing.
    monkeypatch.setattr("app.llm.run_tool_loop", lambda **kw: "no tool available")
    unregistered = dataclasses.replace(
        base, extra_tools=tuple(t for t in base.extra_tools if t["name"] != "delegate_task"),
    )
    qa.answer(
        enterprise_id="c1", question="please delegate this to Assignee",
        dataset="d", scope=unregistered,
    )
    assert recorded == []

    # GREEN: delegate_task restored + actually called.
    monkeypatch.setattr(
        "app.llm.run_tool_loop",
        lambda *, dispatch, **kw: dispatch("delegate_task", {"assignee": "Assignee", "task_summary": "Draft it"}),
    )
    qa.answer(
        enterprise_id="c1", question="please delegate this to Assignee",
        dataset="d", scope=base,
    )
    assert len(recorded) == 1


# ── Invariant 3 — group when-to-respond gate ────────────────────────────────


# ── Invariant 4 — group multi-party context ─────────────────────────────────


# ── Gated routing — group context-fold + accept-with-nudge (AC5b/AC5c) ─────


def test_group_plainqa_context_fold_reaches_composer(monkeypatch):
    """AC5b: a GROUP plain-Q&A turn (the gate declines) still reaches the
    composer WITH `scope.system_addendum` + `scope.context_payload` folded
    into `history` as a synthetic context row — it does NOT answer "as a
    stranger" with no roster/ledger/memory."""
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    captured = {}

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        captured["history"] = history
        return {"answer": "ok", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)
    scope = SurfaceScope(
        surface=Surface.project_group, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL, project_task_execution.EXECUTE_TASK_TOOL),
        system_addendum="PROJECT ROSTER:\n- Fortune — Designer",
        context_payload="Task ledger:\n- Fortune — Draft the export review (open)",
    )
    qa.answer(enterprise_id="c1", question="what's blocking the launch?", dataset="d", scope=scope)

    history = captured["history"]
    assert history and history[0]["role"] == "context"
    assert "PROJECT ROSTER" in history[0]["content"]
    assert "Fortune — Draft the export review" in history[0]["content"]


def test_group_context_fold_dropped_is_red(monkeypatch):
    """AC5b MUTATION: dropping the fold seam (`_fold_project_context`
    forced to a no-op — the exact effect of deleting the seam) makes the
    roster-present assertion RED; restoring it makes it GREEN."""
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    captured = {}

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        captured["history"] = history
        return {"answer": "ok", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)
    scope = SurfaceScope(
        surface=Surface.project_group, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL,),
        system_addendum="PROJECT ROSTER:\n- Fortune — Designer",
        context_payload="Task ledger:\n- Fortune — Draft the export review (open)",
    )

    # RED: the fold seam is a no-op (deleted).
    monkeypatch.setattr(qa, "_fold_project_context", lambda scope, history: history)
    qa.answer(enterprise_id="c1", question="what's blocking the launch?", dataset="d", scope=scope)
    history_red = captured.get("history")
    with pytest.raises(AssertionError):
        assert history_red and "PROJECT ROSTER" in history_red[0]["content"]  # RED

    # GREEN: restore the real seam.
    monkeypatch.undo()
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)
    captured.clear()
    qa.answer(enterprise_id="c1", question="what's blocking the launch?", dataset="d", scope=scope)
    history_green = captured["history"]
    assert "PROJECT ROSTER" in history_green[0]["content"]  # GREEN


# ── Authoritative preamble single-source (AC9 mechanism, AC10, AC11) ───────


def test_group_fold_prepends_authoritative_preamble():
    """A group scope with a non-empty `context_payload` folds it with the
    SAME "answer from THIS block, don't deflect" header the private surface
    already uses — the group composer fall-through no longer frames its
    ledger/roster/memory facts as a passive, deflectable row. On the
    UNFIXED code the fold has no preamble (the red)."""
    scope = SurfaceScope(
        surface=Surface.project_group, project_id=9,
        system_addendum="PROJECT ROSTER:\n- Fortune — Designer",
        context_payload="Task ledger:\n- Fortune — Draft the export review (open)",
    )
    folded = qa._fold_project_context(scope, [])
    assert folded and folded[0]["role"] == "context"
    content = folded[0]["content"]
    assert content.startswith("PROJECT ROSTER")  # system_addendum FIRST, order preserved
    assert PROJECT_FACTS_AUTHORITATIVE_PREAMBLE in content
    # The preamble is bound directly to the facts payload (single newline),
    # not the addendum.
    assert f"{PROJECT_FACTS_AUTHORITATIVE_PREAMBLE}\nTask ledger:" in content


def test_private_fold_adds_no_preamble():
    """Private scope leaves `context_payload == ""` — its own breadth
    already reached history upstream (`routes/ask.py`) WITH the preamble —
    so the fold must add NO preamble here (no double-framing)."""
    scope = SurfaceScope(
        surface=Surface.project_private, project_id=9,
        system_addendum="You are Sprntly's private assistant for this project.",
        context_payload="",
    )
    folded = qa._fold_project_context(scope, [])
    assert folded and folded[0]["role"] == "context"
    content = folded[0]["content"]
    assert content == "You are Sprntly's private assistant for this project."
    assert PROJECT_FACTS_AUTHORITATIVE_PREAMBLE not in content


def test_main_scope_fold_is_noop_mutation_proofed():
    """AC11, mutation-proofed: `scope=None`/main is a pure no-op — `history`
    comes back byte-identical, no preamble, no fold row. A variant that
    FORCES the preamble/fold onto the main path (the mutation) is RED;
    restoring the `scope is None or scope.surface == Surface.main` guard
    makes it GREEN."""
    history = [{"role": "user", "content": "hello"}]

    # GREEN — the real, guarded function.
    assert qa._fold_project_context(None, history) is history
    main_scope = SurfaceScope(surface=Surface.main)
    assert qa._fold_project_context(main_scope, history) is history

    # RED — a mutated variant with the main-guard removed, exactly the
    # effect of deleting `if scope is None or scope.surface == Surface.main:
    # return history`. Constructed as a throwaway local function, never
    # monkeypatched onto `qa` or any shared module.
    def _mutated_fold_no_main_guard(scope, history):
        parts = []
        if scope is not None and scope.system_addendum:
            parts.append(scope.system_addendum)
        if scope is not None and scope.context_payload:
            parts.append(f"{PROJECT_FACTS_AUTHORITATIVE_PREAMBLE}\n{scope.context_payload}")
        fold_block = "\n\n".join(parts)
        if not fold_block:
            return history
        return [{"role": "context", "content": fold_block}] + list(history or [])

    # A main scope carrying leaked project text (the shape the guard exists
    # to prevent from ever reaching main chat) — the mutated variant folds
    # it in; the real, guarded function must not.
    leaked_scope = SurfaceScope(
        surface=Surface.main,
        system_addendum="PROJECT ROSTER: this must never reach main chat",
    )
    mutated_result = _mutated_fold_no_main_guard(leaked_scope, history)
    with pytest.raises(AssertionError):
        assert mutated_result is history  # RED: the mutation leaks project text into main
    assert qa._fold_project_context(leaked_scope, history) is history  # GREEN: real guard holds


def test_nudge_on_missed_delegation(monkeypatch):
    """AC5c: a delegation-phrased turn the gate MISSES (false negative —
    e.g. an implied hand-off with no matching verb) still reaches the
    composer with the accept-with-nudge instruction present in the folded
    system addendum, so the model is told to ask the user to phrase it
    explicitly rather than silently doing nothing."""
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    captured = {}

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        captured["history"] = history
        return {"answer": "ok", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)
    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=None, user_id="u1")
    # A phrasing implying a hand-off with none of the gate's matched verbs —
    # a genuine false negative, not a veto hit.
    missed = "it'd be great if Fortune could take point on the export section"
    from app.skill_router import is_project_tool_request

    assert is_project_tool_request(missed) is False  # confirms this IS a miss

    qa.answer(enterprise_id="c1", question=missed, dataset="d", scope=scope)
    history = captured["history"]
    assert history and "phrase it explicitly" in history[0]["content"]


# ── Backgrounded group reply ─────────────────────────────────────────────


def test_group_reply_broadcasts_on_completion(tenant_client, isolated_settings, monkeypatch):
    from app.db import conversations as conversations_db

    t = tenant_client.make(slug="acme")
    project = t.client.post("/v1/projects", json={"name": "Broadcast on completion"}).json()
    project_id = project["id"]
    monkeypatch.setattr(projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"})
    monkeypatch.setattr(projects_route.qa_agent, "answer", lambda **kw: {"answer": "Reply text", "citations": []})
    broadcasts = []
    monkeypatch.setattr(
        projects_route, "publish_broadcast",
        lambda topic, event, payload: broadcasts.append((topic, event, payload)),
    )

    resp = t.client.post(f"/v1/projects/{project_id}/group/turns", json={"content": "@Sprntly hi"})
    assert resp.status_code == 200, resp.text
    assert any(b[1] == "turn.created" and b[2]["role"] == "assistant" for b in broadcasts)
    conv = conversations_db.get_group_chat(project_id)
    assistant_turns = [row for row in conversations_db.list_group_turns(conv["id"]) if row["role"] == "assistant"]
    assert len(assistant_turns) == 1
    assert assistant_turns[0]["content"] == "Reply text"


# ── Queue-ready must-not-preclude seams ─────────────────────────────────────


# ── Sixth-branch gate yields to a NAMED live source (connector parity) ──────
# The project-only tool loop (`_try_scoped_tool_answer`, no connector reader)
# must NOT claim a turn that names a live source, or a source-named follow-up
# collapses into the connector-blind loop and fabricates a "no readable
# channels" denial. The gate now ANDs in the EXISTING `_skip_project_
# connectors` predicate (True only when NO source is named — exactly when the
# project loop should claim the turn; False for a named source, so it falls
# through to the connector interceptors). No new helper (a `_names_live_
# source` module-level extraction would collide with the nested closure of
# that name inside `answer()` and `UnboundLocalError` on every project turn).


class _GateSentinel(Exception):
    """Raised in place of the decline-path fold so we can assert the gate
    DECLINED (fell through) without running the real interceptor/LLM tail."""


def test_sixth_branch_declines_named_source_project_surface(monkeypatch):
    """AC4/AC6 + MUTATION. A project-surface question that NAMES a live source
    ("what did slack say…") must NOT be claimed by the project tool loop — the
    gate's `and _skip_project_connectors(...)` clause makes it fall through to
    the connector interceptor path. Mutation (clause removed → predicate forced
    True) → the sixth branch STEALS the named-source turn (the bug) = RED."""
    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=None, user_id="u1")
    named_source_q = "what did slack say about the launch?"

    calls = {"scoped": 0}

    def _spy_scoped(**kw):
        calls["scoped"] += 1
        return {"answer": "STOLEN by the connector-blind project loop", "citations": []}

    # Force the FIRST disjunct True so the NEW clause is the sole decider
    # (mirrors `test_gate_removed_plainqa_routes_to_loop_is_red`'s forcing).
    monkeypatch.setattr(qa, "is_project_tool_request", lambda *a, **kw: True)
    monkeypatch.setattr(qa, "_try_scoped_tool_answer", _spy_scoped)
    # Halt right after the gate declines — the real decline tail (interceptors
    # + LLM) is out of scope for this deterministic unit.
    def _sentinel(*a, **kw):
        raise _GateSentinel
    monkeypatch.setattr(qa, "_fold_project_context", _sentinel)

    # GREEN (clause present): real `_skip_project_connectors` → False for a
    # named source → gate DECLINES → `_try_scoped_tool_answer` never called →
    # execution reaches the decline-path fold (our sentinel).
    with pytest.raises(_GateSentinel):
        qa.answer(enterprise_id="c1", question=named_source_q, dataset="d", scope=scope)
    assert calls["scoped"] == 0  # the project loop did NOT claim the named source

    # RED (clause removed): force the predicate True — the exact truth value the
    # gate would carry WITHOUT the `and _skip_project_connectors(...)` clause →
    # the sixth branch fires and steals the named-source turn.
    monkeypatch.setattr(qa, "_skip_project_connectors", lambda *a, **kw: True)
    out = qa.answer(enterprise_id="c1", question=named_source_q, dataset="d", scope=scope)
    assert calls["scoped"] == 1
    assert out["answer"].startswith("STOLEN")  # mutation reproduces the bug


def test_sixth_branch_still_claims_unnamed_project_noun(monkeypatch):
    """AC5 regression. An UNNAMED project-noun ("what tasks are open?", "who's
    on this project?") on BOTH group and private still satisfies the gate
    (`_skip_project_connectors` → True, no source named) → the project tool
    loop claims it → project-ledger facts, NOT a connector deflection."""
    private = _build_private_scope_via_assembler(project_id=9, conversation_id=None, user_id="u1")
    group = dataclasses.replace(private, surface=Surface.project_group)

    def _spy_scoped(**kw):
        return {"answer": "project ledger facts", "citations": []}

    monkeypatch.setattr(qa, "_try_scoped_tool_answer", _spy_scoped)

    for scope in (private, group):
        for q in ("what tasks are open?", "who's on this project?"):
            out = qa.answer(enterprise_id="c1", question=q, dataset="d", scope=scope)
            assert out["answer"] == "project ledger facts", (scope.surface, q)


def test_sixth_branch_gate_and_connector_skip_use_same_predicate():
    """AC7 symmetry. The sixth-branch gate AND the connector-interceptor skip
    both consult the SINGLE `_skip_project_connectors` predicate — a turn can
    never be both claimed by the project loop and admitted to the connectors.
    Guaranteed by reuse of ONE predicate (source-level proof)."""
    src = inspect.getsource(qa.answer)
    # The gate ANDs the predicate in (claim only when it returns True)…
    assert "and _skip_project_connectors(scope, routing_text, history)" in src
    # …and the interceptor guards negate the SAME predicate (skip when True).
    assert "not _skip_project_connectors(" in src
    # One predicate, ≥2 call sites (gate + at least one interceptor guard).
    assert src.count("_skip_project_connectors(") >= 2


def test_main_scope_none_gate_unchanged(monkeypatch):
    """AC8 byte-identity. For `scope=None` / `Surface.main` the sixth-branch
    gate never fires (short-circuits on `scope is not None`) and `_skip_
    project_connectors` short-circuits False — main routing/output unchanged."""
    # `_skip_project_connectors` is inert for main-family scopes.
    assert qa._skip_project_connectors(None, "what tasks are open?", None) is False
    assert qa._skip_project_connectors(
        SurfaceScope(surface=Surface.main), "what did slack say?", None
    ) is False

    # The gate predicates are NEVER consulted on the main path (scope=None):
    def _raise(*a, **kw):
        raise AssertionError("gate must not be consulted on the main-chat path")

    monkeypatch.setattr(qa, "is_project_tool_request", _raise)
    monkeypatch.setattr(qa, "is_project_content_request", _raise)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        if on_delta is not None:
            on_delta("streamed")
        return {"answer": "ok", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)
    deltas = []
    out = qa.answer(
        enterprise_id="c1", question="what tasks are open?", dataset="d",
        scope=None, on_delta=lambda t: deltas.append(t),
    )
    assert deltas == ["streamed"]  # main streams via the untouched composer
    assert out["answer"] == "ok"


# ── Group edit-narration grounding — the "narrates Done, never writes" fix ──
#
# Root cause: `run_tool_loop`'s returned text is the MODEL's own free-form
# final turn, composed AFTER it sees the `edit_prd` tool_result — it is not
# guaranteed to equal, or even agree with, what the tool actually returned.
# Two failure shapes:
#   (a) the model calls `edit_prd`, gets back a refusal/no-op narration, but
#       composes its own "Done — it's live" final answer anyway;
#   (b) the model never calls `edit_prd` at all, and just narrates success.
# `_try_scoped_tool_answer` now grounds the final answer in the tool's real
# outcome for (a) (an unconditional override whenever edit_prd was called at
# all) and detects+corrects the free-text fabrication for (b).


def test_group_edit_prd_grounds_narration_on_refusal_not_model_claim(monkeypatch):
    """(a) — the model calls edit_prd, the handler refuses (no PRD open),
    but the model's OWN final text still claims success. The final answer
    must be the handler's real refusal, never the model's fabricated
    "Done"."""
    def _fake_loop(*, dispatch, **kw):
        dispatch("edit_prd", {"instruction": "tighten the scope"})
        # The model's own free-text turn, composed AFTER seeing the
        # refusal tool_result — fabricates success anyway.
        return "Done — it's live! Everyone can see the updated PRD now."

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)

    def _refusing_handler(tool_input):  # noqa: ARG001
        return ("Open a PRD beside this chat and I'll edit it.", None)

    scope = SurfaceScope(
        surface=Surface.project_group, project_id=9,
        extra_tools=(qa_project_group_context_edit_prd_tool(),),
        edit_prd_handler=_refusing_handler,
    )
    out = qa.answer(
        enterprise_id="c1", question="@Sprntly tighten the scope of the PRD",
        dataset="d", scope=scope,
    )
    assert out["answer"] == "Open a PRD beside this chat and I'll edit it."
    assert "Done" not in out["answer"]
    assert "live" not in out["answer"]


def test_group_edit_prd_grounds_narration_on_success(monkeypatch):
    """(a), success arm — the handler applied the edit; the grounded
    narration IS the handler's own "Done — ..." text (never a DIFFERENT
    model paraphrase of it)."""
    def _fake_loop(*, dispatch, **kw):
        dispatch("edit_prd", {"instruction": "tighten the scope"})
        return "Sure thing, I made that change for you!"  # model paraphrase

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)

    def _applying_handler(tool_input):  # noqa: ARG001
        return ("Done — I've updated the PRD. Tightened the Scope section.", None)

    scope = SurfaceScope(
        surface=Surface.project_group, project_id=9,
        extra_tools=(qa_project_group_context_edit_prd_tool(),),
        edit_prd_handler=_applying_handler,
    )
    out = qa.answer(
        enterprise_id="c1", question="@Sprntly tighten the scope of the PRD",
        dataset="d", scope=scope,
    )
    assert out["answer"] == "Done — I've updated the PRD. Tightened the Scope section."


def test_group_edit_prd_corrects_fabricated_success_without_tool_call(monkeypatch):
    """(b) — the model never calls edit_prd at all, yet its own final text
    claims the PRD was updated. There is no tool result to ground on; the
    only honest answer is the corrective clarify, never the model's unearned
    claim."""
    monkeypatch.setattr(
        "app.llm.run_tool_loop",
        lambda **kw: "Done — I've updated the PRD, it's live now.",
    )
    scope = SurfaceScope(
        surface=Surface.project_group, project_id=9,
        extra_tools=(qa_project_group_context_edit_prd_tool(),),
        edit_prd_handler=lambda tool_input: ("unused", None),  # noqa: ARG005
    )
    out = qa.answer(
        enterprise_id="c1", question="@Sprntly tighten the scope of the PRD",
        dataset="d", scope=scope,
    )
    assert out["answer"] != "Done — I've updated the PRD, it's live now."
    assert "didn't actually make that change" in out["answer"]


def test_group_fabrication_guard_does_not_fire_on_unrelated_done_reply(monkeypatch):
    """The (b) guard is scoped to PRD-edit-claim language — an unrelated
    "Done" (e.g. a delegate_task confirmation) must pass through unchanged,
    never gets corrected, even on a turn that DID reach the tool loop (via
    the edit-intent gate) but ended up narrating something else entirely."""
    monkeypatch.setattr(
        "app.llm.run_tool_loop",
        lambda **kw: "Done — I've asked Ada to help with that.",
    )
    scope = SurfaceScope(
        surface=Surface.project_group, project_id=9,
        extra_tools=(qa_project_group_context_edit_prd_tool(),),
        edit_prd_handler=lambda tool_input: ("unused", None),  # noqa: ARG005
    )
    out = qa.answer(
        enterprise_id="c1", question="@Sprntly tighten the scope of the PRD",
        dataset="d", scope=scope,
    )
    assert out["answer"] == "Done — I've asked Ada to help with that."


def test_private_scope_unaffected_by_edit_prd_grounding(monkeypatch):
    """Private/main never register an `edit_prd_handler` — the grounding
    override and the fabrication guard are BOTH no-ops there, so a private
    turn's free text (even one that happens to mention "PRD" + "done")
    passes through completely unchanged."""
    monkeypatch.setattr(
        "app.llm.run_tool_loop",
        lambda **kw: "Done — I've updated the PRD summary in my notes.",
    )
    private_scope = _build_private_scope_via_assembler(project_id=9, conversation_id=5, user_id="u1")
    out = qa.answer(
        enterprise_id="c1", question="what's the PRD status", dataset="d",
        scope=private_scope,
    )
    assert out["answer"] == "Done — I've updated the PRD summary in my notes."


def qa_project_group_context_edit_prd_tool() -> dict:
    """The real `edit_prd` tool schema — imported lazily so this file's
    module-level imports stay unchanged; every test above registers it on
    `extra_tools` so the tool-loop branch is actually reached (an empty
    `extra_tools` never routes here)."""
    from app.project_group_context import EDIT_PRD_TOOL

    return EDIT_PRD_TOOL
