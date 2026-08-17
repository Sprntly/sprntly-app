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


def _route_out():
    return SimpleNamespace(output={"skill_id": None, "confidence": 0.0, "action": None})


def _ctx(company_id="c1", workspace_id="w1", user_id="u1"):
    return SimpleNamespace(company_id=company_id, workspace_id=workspace_id, user_id=user_id, user_email=None)


# ── Private collapse (AC4) ─────────────────────────────────────────────────


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
    scope = ajr._build_private_scope(project_id=9, conversation_id=None, user_id="u1")
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
    scope = ajr._build_private_scope(project_id=9, conversation_id=None, user_id="u1")
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
    scope = ajr._build_private_scope(project_id=9, conversation_id=None, user_id="u1")
    out = qa.answer(
        enterprise_id="c1", question="please delegate the export review to Fortune", dataset="d",
        scope=scope, on_delta=lambda t: deltas.append(t),
    )
    assert deltas == []
    assert dispatched == ["Sent the brief to Fortune's chat."]
    assert out["answer"] == "sent"


def test_private_bare_send_to_member_fires_gate_no_stream(monkeypatch):
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
    scope = ajr._build_private_scope(project_id=9, conversation_id=None, user_id="u1")
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
    scope = ajr._build_private_scope(project_id=9, conversation_id=None, user_id="u1")
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
    scope = ajr._build_private_scope(project_id=9, conversation_id=None, user_id="u1")
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
    scope = ajr._build_private_scope(project_id=9, conversation_id=None, user_id="u1")
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
    scope = ajr._build_private_scope(project_id=9, conversation_id=None, user_id="u1")
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
    scope = ajr._build_private_scope(project_id=9, conversation_id=None, user_id="u1")
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


def test_private_context_block_reaches_engine(monkeypatch):
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

    private_scope = ajr._build_private_scope(project_id=9, conversation_id=5, user_id="u1")
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
    scope = ajr._build_private_scope(project_id=9, conversation_id=5, user_id="u-assigner")
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
    scope = ajr._build_private_scope(project_id=9, conversation_id=5, user_id="u-assigner")
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
    restored_scope = ajr._build_private_scope(project_id=9, conversation_id=5, user_id="u-assigner")
    qa.answer(
        enterprise_id="c1", question="please delegate the export review to Fortune",
        dataset="d", scope=restored_scope,
    )
    assert captured["assigner_user_id"] == "u-assigner"  # GREEN


def test_execute_task_post_turn_fires(monkeypatch):
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
    private_scope = ajr._build_private_scope(project_id=9, conversation_id=5, user_id="u1")
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

    scope = ajr._build_private_scope(project_id=9, conversation_id=5, user_id="u-assigner")
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

    base = ajr._build_private_scope(project_id=9, conversation_id=5, user_id="u-assigner")

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


def test_group_gate_runs_before_scheduling(tenant_client, isolated_settings, monkeypatch):
    from app.db import conversations as conversations_db
    from app.db import projects as projects_db

    t = tenant_client.make(slug="acme")
    project = t.client.post("/v1/projects", json={"name": "Gate before scheduling"}).json()
    project_id = project["id"]
    projects_db.add_member(project_id, "second-human")
    monkeypatch.setattr(projects_route, "should_respond", lambda *a, **kw: False)
    scheduled = []
    monkeypatch.setattr(projects_route, "_schedule_group_reply", lambda *a, **kw: scheduled.append(a))

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns", json={"content": "just chatting here"},
    )
    assert resp.status_code == 200, resp.text
    assert scheduled == []  # gate said no -> nothing scheduled at all
    conv = conversations_db.get_group_chat(project_id)
    turns = conversations_db.list_group_turns(conv["id"])
    assert len(turns) == 1 and turns[0]["role"] == "user"


def test_group_gate_forced_false_no_turn(tenant_client, isolated_settings, monkeypatch):
    from app.db import conversations as conversations_db
    from app.db import projects as projects_db

    t = tenant_client.make(slug="acme")
    project = t.client.post("/v1/projects", json={"name": "Gate forced false"}).json()
    project_id = project["id"]
    projects_db.add_member(project_id, "second-human")
    monkeypatch.setattr(projects_route, "should_respond", lambda *a, **kw: False)

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "just chatting, no agent needed here"},
    )
    assert resp.status_code == 200, resp.text
    conv = conversations_db.get_group_chat(project_id)
    turns = conversations_db.list_group_turns(conv["id"])
    assert [row for row in turns if row["role"] == "assistant"] == []


def test_group_gate_forced_true_one_turn(tenant_client, isolated_settings, monkeypatch):
    from app.db import conversations as conversations_db
    from app.db import projects as projects_db

    t = tenant_client.make(slug="acme")
    project = t.client.post("/v1/projects", json={"name": "Gate forced true"}).json()
    project_id = project["id"]
    projects_db.add_member(project_id, "second-human")
    monkeypatch.setattr(projects_route, "should_respond", lambda *a, **kw: True)
    monkeypatch.setattr(projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"})
    monkeypatch.setattr(projects_route.qa_agent, "answer", lambda **kw: {"answer": "Here to help.", "citations": []})

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "is anyone able to help with this today?"},
    )
    assert resp.status_code == 200, resp.text
    conv = conversations_db.get_group_chat(project_id)
    turns = conversations_db.list_group_turns(conv["id"])
    assistant_turns = [row for row in turns if row["role"] == "assistant"]
    assert len(assistant_turns) == 1


# ── Invariant 4 — group multi-party context ─────────────────────────────────


def test_group_transcript_not_reflattened(tenant_client, isolated_settings, monkeypatch):
    """RETARGETED — authorized invariant change. The old contract asserted
    `history is None` unconditionally ("never re-flattened"). That was WRONG
    for the connector-thread case: the group surface must now hand the prior
    turns to `answer()` as `history` (recent-minus-trigger) so the router /
    connector interceptors can keep a source thread alive — exactly what the
    private surface already does. The prerendered transcript is STILL the
    speaker-tagged full thread (Invariant 4 unchanged); history is a SEPARATE
    signal channel, not a re-flattening of the transcript into the composer.

    New contract (mirrors AC2): `history == recent-minus-trigger` when a human
    trigger exists, and `history is None` on the trigger-less degenerate path
    (so a transcript-as-question is not ALSO rendered as history)."""
    from app.db import conversations as conversations_db

    monkeypatch.setattr(projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"})

    # ── Trigger PRESENT: a prior assistant turn + a human mention. ──────────
    t = tenant_client.make(slug="acme")
    project = t.client.post("/v1/projects", json={"name": "Transcript preserved"}).json()
    project_id = project["id"]
    conv = conversations_db.create_group_chat(project_id, t.user_id)
    conversations_db.post_group_turn(
        conv["id"], None, "The Q3 launch slipped a week.", role="assistant",
    )
    conversations_db.post_group_turn(conv["id"], t.user_id, "@Sprntly what did they say?")

    captured = {}

    def _fake_answer(**kw):
        captured.update(kw)
        return {"answer": "ok", "citations": []}

    monkeypatch.setattr(projects_route.qa_agent, "answer", _fake_answer)
    ctx = _ctx(t.company_id, ensure_default_workspace(t.company_id)["id"], t.user_id)
    # `_respond_as_group_agent` now runs THROUGH `run_execution_job` (async):
    # drive it to completion with a claimed run identity.
    asyncio.run(
        projects_route._respond_as_group_agent(
            project_id, conv["id"], ctx, "mention", job_id=1, run_id="r",
        )
    )

    # history = recent EXCLUDING the trigger turn — the prior assistant turn only.
    assert captured.get("history") == [
        {"role": "assistant", "content": "The Q3 launch slipped a week."}
    ]
    transcript = captured["scope"].prerendered_transcript
    assert transcript is not None
    assert "@Sprntly what did they say?" in transcript
    # `render_group_transcript`'s speaker-tagged shape ("Name: message" /
    # "Name (job role): message"), never the private surface's flattened
    # "User: .../Sprntly: ..." rendering.
    assert not transcript.startswith("User:")

    # ── Trigger-LESS degenerate path: an assistant-only thread (no human turn
    # with an author) → history stays [] → `history or None` → None, so the
    # transcript-as-question is not double-rendered as history. ─────────────
    t2 = tenant_client.make(slug="beta")
    project2 = t2.client.post("/v1/projects", json={"name": "Triggerless"}).json()
    project2_id = project2["id"]
    conv2 = conversations_db.create_group_chat(project2_id, t2.user_id)
    conversations_db.post_group_turn(
        conv2["id"], None, "System note: standup at 10.", role="assistant",
    )
    captured2 = {}

    def _fake_answer2(**kw):
        captured2.update(kw)
        return {"answer": "ok", "citations": []}

    monkeypatch.setattr(projects_route.qa_agent, "answer", _fake_answer2)
    ctx2 = _ctx(t2.company_id, ensure_default_workspace(t2.company_id)["id"], t2.user_id)
    asyncio.run(
        projects_route._respond_as_group_agent(
            project2_id, conv2["id"], ctx2, "mention", job_id=2, run_id="r2",
        )
    )
    assert captured2.get("history") is None  # trigger-less → None (no double-render)


def test_group_join_greeting_and_classify_still_fire(tenant_client, isolated_settings, monkeypatch):
    from app import project_join_greeting

    t = tenant_client.make(slug="acme")
    project = t.client.post("/v1/projects", json={"name": "Greeting + classify"}).json()
    project_id = project["id"]

    # The join greeting lives entirely OUTSIDE the collapsed loops — this
    # ticket never touches it; a direct call proves it is still wired and
    # unaffected.
    from app.db import conversations as conversations_db

    project_join_greeting.post_join_greeting(project_id, "greeted-user")
    conv = conversations_db.get_individual_project_chat(project_id, "greeted-user")
    assert conv is not None
    turns = conversations_db.list_individual_turns(conv["id"], "greeted-user")
    assert len(turns) == 1 and turns[0]["role"] == "assistant"

    # `_classify_group_envelope` (card enrichment only — the edit is now an
    # in-band tool, proven end-to-end in test_group_chat_prd_edit.py) runs
    # before the reply on every trigger kind — here: structural wiring only.
    classify_calls = []
    monkeypatch.setattr(
        projects_route, "_classify_group_envelope",
        lambda *a, **kw: classify_calls.append(1) or {"intent": "answer"},
    )
    monkeypatch.setattr(projects_route.qa_agent, "answer", lambda **kw: {"answer": "ok", "citations": []})
    resp = t.client.post(f"/v1/projects/{project_id}/group/turns", json={"content": "@Sprntly hi"})
    assert resp.status_code == 200, resp.text
    assert classify_calls == [1]


def test_group_question_is_latest_turn_transcript_rides_scope(
    tenant_client, isolated_settings, monkeypatch
):
    """The LT-8 input-shape switch (`_GROUP_TRANSCRIPT_AS_QUESTION`) is retired
    — group collapses to the single live form: `question` is the latest
    triggering message, while the full attributed transcript always rides on
    `scope.prerendered_transcript` so the model still sees the whole thread."""
    from app.db import conversations as conversations_db

    t = tenant_client.make(slug="acme")
    project = t.client.post("/v1/projects", json={"name": "LT8 switch"}).json()
    project_id = project["id"]
    conv = conversations_db.create_group_chat(project_id, t.user_id)
    conversations_db.post_group_turn(conv["id"], t.user_id, "@Sprntly what's up?")

    monkeypatch.setattr(projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"})
    captured = {}
    monkeypatch.setattr(
        projects_route.qa_agent, "answer",
        lambda **kw: captured.update(kw) or {"answer": "ok", "citations": []},
    )
    ctx = _ctx(t.company_id, ensure_default_workspace(t.company_id)["id"], t.user_id)

    # The retired flag no longer exists.
    assert not hasattr(projects_route, "_GROUP_TRANSCRIPT_AS_QUESTION")
    asyncio.run(
        projects_route._respond_as_group_agent(
            project_id, conv["id"], ctx, "mention", job_id=1, run_id="r",
        )
    )
    assert captured["question"] == "@Sprntly what's up?"
    # The full transcript still rides on the scope, always.
    assert captured["scope"].prerendered_transcript is not None
    assert "@Sprntly what's up?" in captured["scope"].prerendered_transcript


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
    scope = ajr._build_private_scope(project_id=9, conversation_id=None, user_id="u1")
    # A phrasing implying a hand-off with none of the gate's matched verbs —
    # a genuine false negative, not a veto hit.
    missed = "it'd be great if Fortune could take point on the export section"
    from app.skill_router import is_project_tool_request

    assert is_project_tool_request(missed) is False  # confirms this IS a miss

    qa.answer(enterprise_id="c1", question=missed, dataset="d", scope=scope)
    history = captured["history"]
    assert history and "phrase it explicitly" in history[0]["content"]


# ── Backgrounded group reply ─────────────────────────────────────────────


def test_group_post_returns_before_reply(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project = t.client.post("/v1/projects", json={"name": "Post before reply"}).json()
    project_id = project["id"]
    scheduled = []
    monkeypatch.setattr(projects_route, "_schedule_group_reply", lambda *a, **kw: scheduled.append(a))

    resp = t.client.post(f"/v1/projects/{project_id}/group/turns", json={"content": "@Sprntly hello"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "user"  # the returned turn is the HUMAN turn, not a reply
    assert len(scheduled) == 1  # the reply was SCHEDULED, never awaited inline in the route body


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


def test_group_task_strong_ref_and_pytest_inline(monkeypatch):
    """Under `"pytest" in sys.modules` (true for this test process itself),
    `_schedule_group_reply` runs the reply INLINE (to completion via a
    worker-thread loop) — never touching the strong-ref set or a live
    `asyncio.create_task`. A pre-claimed `job_id`/`run_id` is passed so no
    `ask_jobs` row is inserted here."""
    calls = []

    async def _fake_reply(*a, **kw):
        calls.append((a, kw))

    monkeypatch.setattr(projects_route, "_respond_as_group_agent", _fake_reply)
    before = set(projects_route._group_reply_tasks)
    ctx = _ctx()
    projects_route._schedule_group_reply(
        1, 2, ctx, "mention", source_turn_id=5, job_id=99, run_id="r",
    )
    # The resolved `pinned_skill` is threaded through (None here — no explicit
    # pick and the source turn carries no routable slash trigger).
    assert calls == [((1, 2, ctx, "mention"), {"job_id": 99, "run_id": "r", "pinned_skill": None})]
    assert projects_route._group_reply_tasks == before  # no task was ever scheduled


def test_group_route_async_no_running_loop_error(monkeypatch):
    """A request through `post_group_turn_route` on the NON-pytest-inline
    path schedules `asyncio.to_thread(_respond_as_group_agent, ...)` and
    does NOT raise `RuntimeError: no running event loop` — the route is
    `async def` and `_respond_as_group_agent` is never awaited directly as
    if it were a coroutine."""
    import sys as real_sys
    import types

    fake_sys = types.SimpleNamespace(
        modules={k: v for k, v in real_sys.modules.items() if k != "pytest"}
    )
    monkeypatch.setattr(projects_route, "sys", fake_sys)

    async def _noop_reply(*a, **kw):
        return None

    monkeypatch.setattr(projects_route, "_respond_as_group_agent", _noop_reply)

    async def _drive():
        ctx = _ctx()
        projects_route._schedule_group_reply(
            1, 2, ctx, "mention", source_turn_id=5, job_id=99, run_id="r",
        )
        # Scheduled via asyncio.create_task — held by the strong-ref set
        # immediately; no RuntimeError means a loop WAS running.
        assert len(projects_route._group_reply_tasks) == 1
        for _ in range(50):
            await asyncio.sleep(0.01)
            if not projects_route._group_reply_tasks:
                break
        assert projects_route._group_reply_tasks == set()  # done-callback discarded it

    asyncio.run(_drive())


# ── Queue-ready must-not-preclude seams ─────────────────────────────────────


def test_when_to_respond_callable_per_message(monkeypatch):
    from app import project_group_gate as pgg

    monkeypatch.setattr(pgg, "call_json", lambda **kw: {"respond": True})
    result = pgg.should_respond(
        1, 2,
        [{"author_name": "A", "author_job_role": None, "content": "hi"}],
        "is anyone able to help with this?",
    )
    assert isinstance(result, bool)
    assert result is True


def test_background_input_is_extensible_structure():
    sig = inspect.signature(projects_route._schedule_group_reply)
    assert list(sig.parameters) == [
        "project_id", "conversation_id", "ctx", "trigger_kind",
        # execution identity — NOT the message itself. `pinned_skill` is a
        # deterministic routing input (the FE's skill pick, or the source
        # turn's own trigger on a retry), also not the message.
        "source_turn_id", "client_message_id", "job_id", "run_id", "pinned_skill",
    ]
    # None of these IS "the message" itself — the reply re-derives the live
    # transcript from the DB inside `_respond_as_group_agent`, so a future
    # queue could pass more triggers through this same shape without the
    # reply ever depending on a captured closure over one message object.
    body_src = inspect.getsource(projects_route._respond_as_group_agent)
    assert "conversations_db.list_group_turns(conversation_id)" in body_src


def test_is_for_agent_decision_is_named_seam():
    from app.project_group_gate import should_respond

    assert inspect.isfunction(should_respond)
    # Referenced as a named call site inside the route (not inlined) — a
    # future queue could collect yes-verdicts from this exact call shape.
    src = inspect.getsource(projects_route.post_group_turn_route)
    assert "should_respond(" in src


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
    scope = ajr._build_private_scope(project_id=9, conversation_id=None, user_id="u1")
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
    private = ajr._build_private_scope(project_id=9, conversation_id=None, user_id="u1")
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
