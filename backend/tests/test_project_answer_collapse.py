"""Private project-chat unified-path routing, the mutation proofs, the
backgrounding mechanics, and the queue-ready seams — the deterministic
collapse suite (fake-LLM/monkeypatched throughout; the real-DB + real-LLM
arm is `test_project_answer_collapse_live.py`, DEFERRED-TO-STAGING).
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
from app.ask_planner import Plan
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
    # A project ask now carries its project on `context_source` (not the legacy
    # top-level `project_id`); scope is built by the assembler, whose membership
    # gate we stub (no DB in this pure-unit routing test — breadth reads degrade
    # to empty), same as `_build_private_scope_via_assembler` above.
    monkeypatch.setattr("app.db.projects.project_belongs_to_company", lambda *a, **k: True)
    monkeypatch.setattr("app.db.projects.is_project_member", lambda *a, **k: True)

    asyncio.run(ajr.run_ask_job(
        ask_id=1, enterprise_id="c1", question="q", dataset="d",
        conversation_id=5, user_id="u1",
        context_source={"kind": "project", "params": {"project_id": 9, "surface": "private"}},
    ))
    assert captured["scope"] is not None
    assert captured["scope"].surface == Surface.project_private
    assert captured["scope"].project_id == 9


def _run_ask_observing_commit(monkeypatch, answer_payload):
    """Drive `run_ask_job` with `qa_agent.answer` stubbed to return
    `answer_payload`, observing whether the row is committed (`complete_ask_job`)
    or failed (`fail_ask_job`). Returns `(committed, failed)` capture dicts."""
    monkeypatch.setattr(ajr.qa_agent, "answer", lambda **kw: answer_payload)
    committed = {}
    failed = {}
    monkeypatch.setattr(ajr, "complete_ask_job",
                        lambda i, p: committed.update(id=i, payload=p))
    monkeypatch.setattr(ajr, "fail_ask_job",
                        lambda i, msg, ec: failed.update(id=i, msg=msg, error_class=ec))
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)
    import app.project_memory as pm

    monkeypatch.setattr(pm, "maybe_promote_turn", lambda *a, **kw: None)
    monkeypatch.setattr("app.db.projects.project_belongs_to_company", lambda *a, **k: True)
    monkeypatch.setattr("app.db.projects.is_project_member", lambda *a, **k: True)
    asyncio.run(ajr.run_ask_job(
        ask_id=7, enterprise_id="c1", question="q", dataset="d",
        conversation_id=5, user_id="u1",
        context_source={"kind": "project", "params": {"project_id": 9, "surface": "private"}},
    ))
    return committed, failed


def test_normal_answer_commits_ready(monkeypatch):
    """Regression: a normal non-empty answer is unaffected end-to-end — it
    commits as `ready` and never touches the failure path."""
    committed, failed = _run_ask_observing_commit(
        monkeypatch, {"answer": "a real answer", "citations": []})
    assert failed == {}  # never failed
    assert committed.get("id") == 7
    assert committed["payload"]["answer"] == "a real answer"


def test_empty_composer_answer_still_commits_ready(monkeypatch):
    """The worker deliberately does NOT fail an empty composed answer — that
    is the pre-existing behaviour (an empty composer answer commits `ready`,
    relied on by `test_artifact_context.test_ask_grounds_on_the_open_evidence`,
    which runs the real composer with no live LLM in CI). The empty-tool-loop
    fix lives ONE layer up in `_try_scoped_tool_answer` (Guard 1), which
    degrades to the composer BEFORE the worker ever sees the answer — so the
    worker needs no empty guard, and adding one here would wrongly error every
    real-composer test. This locks that boundary in."""
    committed, failed = _run_ask_observing_commit(
        monkeypatch, {"answer": "   ", "citations": []})
    assert failed == {}  # NOT failed — empty composer answer is committed today
    assert committed.get("id") == 7  # committed as ready, blank body and all


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
# `_build_private_scope` path, always 7 tools, never reached the composer at
# all). ─────────────────────────────────────────────────────────────────


def test_private_realscope_plainqa_declines_gate_streams(monkeypatch):
    """AC5: the REAL private scope (all 7 tools) + a plain-context question
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
    assert len(scope.extra_tools) == 7  # real, declarative, unconditional — not hand-emptied
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
    # delegate_task is TERMINAL: the handler's confirmation OVERRIDES the loop's
    # free text (anti-fabrication — the reply only claims what actually
    # happened), so the answer is the narration, not the loop's raw return.
    assert out["answer"] == "Sent the brief to Fortune's chat."


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
    # delegate_task terminal-override (see test above): answer is the narration.
    assert out["answer"] == "Sent the brief to Jay's chat."


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
    """AC4: the REAL project scope (7 tools) + a natural read/summary
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


# RETIRED (2026-08-20): test_private_context_block_reaches_engine asserted that
# `run_ask_job` builds a project-private `SurfaceScope` from the top-level
# `project_id` (agentic private chat, wired into the ask path). The Slice-1
# rework (PROJECTS-REBUILD-SLICE1-PLAN.md, 2026-08-18) DEFERS project actions on
# the private surface — private now behaves like main (client-driven, streamed,
# scope=None). Re-add with the slice that reintroduces the private tool-scope.


# ── Invariant 2 — task-awareness + delegation survival ─────────────────────


def test_delegate_execute_callable_private_surface(monkeypatch):
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
    qa.answer(
        enterprise_id="c1", question="please delegate the export review to Fortune",
        dataset="d", scope=private_scope,
    )

    assert len(delegate_calls) == 1
    assert len(execute_calls) == 1


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
    # The private scope's `post_turn` is built inside
    # `ProjectContextAssembler.assemble` and closes over the
    # `app.db.conversations.post_individual_turn` it imports there — patch THAT
    # seam (not `ask_job_runner`'s re-export), and BEFORE the scope is built
    # below, so the closure captures the spy.
    monkeypatch.setattr(
        "app.db.conversations.post_individual_turn",
        lambda conv_id, role, content: private_posts.append((conv_id, role, content)),
    )
    private_scope = _build_private_scope_via_assembler(project_id=9, conversation_id=5, user_id="u1")
    qa.answer(
        enterprise_id="c1", question="please draft a PRD for the onboarding flow",
        dataset="d", scope=private_scope,
    )
    assert private_posts == [(5, "assistant", "progress update")]


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


# ── The promise-forcing split (delegate_task survives, complete_task removed) ──


def test_private_delegate_promise_without_call_forces_and_writes_ledger(monkeypatch, caplog):
    """AC13. The model's first turn PROMISES a delegation ("On it — I'll
    delegate this now") but never calls `delegate_task`. The kept forcing
    pass (the `if` that survived the group-only `complete_task` `elif`'s
    removal) re-runs the loop with `force_tool="delegate_task"`, which DOES
    call the tool, and the real handler actually writes a
    `project_delegations` ledger row. The reworded log line carries no
    `group_` prefix — this event now fires for private too."""
    import logging

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

    calls = {"n": 0}

    def _fake_loop(*, dispatch, force_tool=None, **kw):
        calls["n"] += 1
        if force_tool == "delegate_task":
            return dispatch("delegate_task", {"assignee": "Assignee", "task_summary": "Draft it"})
        return "On it — I'll delegate this to Assignee now."

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)

    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=5, user_id="u-assigner")
    with caplog.at_level(logging.WARNING):
        qa.answer(
            enterprise_id="c1", question="please delegate this to Assignee",
            dataset="d", scope=scope,
        )

    assert calls["n"] == 2  # the initial pass + one forced delegate_task re-run
    assert len(recorded) == 1  # the ledger write actually happened
    forcing_lines = [
        r.getMessage() for r in caplog.records
        if "delegation_promise_without_tool_call" in r.getMessage()
    ]
    assert forcing_lines, "expected the forcing-pass log line to fire"
    assert not any("group_" in line for line in forcing_lines)


def test_delegate_forcing_removal_makes_private_delegation_red(monkeypatch):
    """AC13 mutation proof. Simulating deletion of the kept `delegate_task`
    forcing `if` (forcing the promise regex to never match, the same
    observable effect as removing the block) means a promise-only turn
    NEVER re-runs the loop and NO ledger row is written — RED. Restoring
    the real regex writes the row — GREEN. Proves the `complete_task`
    `elif`'s deletion did NOT take the shared `delegate_task` `if` with
    it."""
    import re

    import app.project_delegation as pd

    def _patch_delegation_seams(recorded):
        monkeypatch.setattr(pd, "record_delegation", lambda **kw: recorded.append(kw) or {"id": 1})
        monkeypatch.setattr(
            pd, "resolve_member",
            lambda pid, needle: {"status": "found", "member": {"user_id": "u-assignee", "name": "Assignee"}},
        )
        monkeypatch.setattr(pd, "is_project_member", lambda pid, uid: True)
        monkeypatch.setattr(pd, "_build_brief", lambda *a, **kw: "Brief text")
        monkeypatch.setattr(pd, "create_individual_project_chat", lambda pid, uid: {"id": 77})
        monkeypatch.setattr(pd, "post_individual_turn", lambda conv_id, role, content: {"id": 1})

    def _fake_loop(*, dispatch, force_tool=None, **kw):
        if force_tool == "delegate_task":
            return dispatch("delegate_task", {"assignee": "Assignee", "task_summary": "Draft it"})
        return "On it — I'll delegate this to Assignee now."

    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=5, user_id="u-assigner")

    # RED: the forcing `if` deleted — simulated by making the promise regex
    # never match (a promise-only turn is then indistinguishable from an
    # ordinary plain-text answer, exactly the observable effect of removing
    # the `if`).
    recorded_red: list = []
    _patch_delegation_seams(recorded_red)
    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)
    monkeypatch.setattr(qa, "_DELEGATION_PROMISE_WITHOUT_TOOL_CALL_RE", re.compile(r"(?!)"))
    qa.answer(
        enterprise_id="c1", question="please delegate this to Assignee",
        dataset="d", scope=scope,
    )
    assert recorded_red == []  # RED — no forcing pass, no ledger write

    # GREEN: restore the real regex — the forcing `if` fires again.
    monkeypatch.undo()
    recorded_green: list = []
    _patch_delegation_seams(recorded_green)
    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)
    qa.answer(
        enterprise_id="c1", question="please delegate this to Assignee",
        dataset="d", scope=scope,
    )
    assert len(recorded_green) == 1  # GREEN — the real forcing pass restores the write


# ── complete_task re-wire: admission + forcing pass (re-armed orphaned path) ──


def _patch_completion_seams(monkeypatch, recorded, *, task_summary="the export review"):
    """Stub the DB/notify seams `handle_complete_task` touches so it writes a
    `completed` event (captured in `recorded`) and returns its authoritative
    'Got it — …' confirmation, without a real DB or network."""
    import app.project_delegation as pd

    monkeypatch.setattr(pd, "is_project_member", lambda pid, uid: True)
    monkeypatch.setattr(
        pd, "list_status_for_assignee",
        lambda pid, uid: [
            {"delegation_id": 5, "status": "assigned", "task_summary": task_summary}
        ],
    )
    monkeypatch.setattr(pd, "is_legal_transition", lambda a, b: True)
    monkeypatch.setattr(
        pd, "record_event",
        lambda **kw: recorded.append(kw),
    )
    monkeypatch.setattr(pd, "_notify_assigner_task_completed_email", lambda *a, **kw: None)
    monkeypatch.setattr(pd, "load_delegation_for_authz", lambda did: {})
    monkeypatch.setattr(pd, "status_dto", lambda did: None)


def test_private_completion_admitted_fires_complete_task_and_confirms(monkeypatch):
    """A first-person completion claim is DETERMINISTICALLY admitted to the
    project tool loop; when the model calls `complete_task`, the ledger records
    a `completed` event and the reply is the handler's AUTHORITATIVE
    confirmation (not the model's free text). This is the orphaned path
    re-armed: the tool is now offered (extra_tools) and the turn is admitted by
    `is_project_completion_request`."""
    recorded: list = []
    _patch_completion_seams(monkeypatch, recorded)

    def _fake_loop(*, dispatch, force_tool=None, **kw):
        # The model itself calls complete_task on the first pass.
        return dispatch("complete_task", {"task_summary": "the export review"})

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)

    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=5, user_id="u-assignee")
    result = qa.answer(
        enterprise_id="c1", question="I've finished the export review",
        dataset="d", scope=scope,
    )

    assert len(recorded) == 1
    assert recorded[0]["event"] == "completed"
    # Authoritative override: the reply is the handler's confirmation string.
    assert result["answer"].startswith("Got it")
    assert "export review" in result["answer"]


def test_private_completion_forcing_pass_fires_when_model_skips_tool(monkeypatch, caplog):
    """The 'confirms without doing' failure, completion flavour: the turn is
    admitted as a completion but the model narrates a promise ('noted, I'll
    mark that done') WITHOUT calling `complete_task`. The forcing pass re-runs
    the loop with `force_tool='complete_task'`, which DOES write the ledger."""
    import logging

    recorded: list = []
    _patch_completion_seams(monkeypatch, recorded)

    calls = {"n": 0}

    def _fake_loop(*, dispatch, force_tool=None, **kw):
        calls["n"] += 1
        if force_tool == "complete_task":
            return dispatch("complete_task", {"task_summary": "the export review"})
        return "Noted — I'll mark that done for you."

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)

    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=5, user_id="u-assignee")
    with caplog.at_level(logging.WARNING):
        result = qa.answer(
            enterprise_id="c1", question="I've finished the export review",
            dataset="d", scope=scope,
        )

    assert calls["n"] == 2  # initial pass + one forced complete_task re-run
    assert len(recorded) == 1 and recorded[0]["event"] == "completed"
    assert result["answer"].startswith("Got it")  # authoritative override still wins
    assert any(
        "completion_admitted_without_tool_call" in r.getMessage() for r in caplog.records
    )


def test_private_noncompletion_question_not_admitted_as_completion(monkeypatch, caplog):
    """A non-completion project turn (a wh-question read) is NOT admitted as a
    completion, so the completion forcing pass never fires and no ledger write
    happens — the new gate does not hijack ordinary project traffic. The turn
    still reaches the loop as a READ (its own predicate), returns the model's
    text, and no `complete_task` is forced."""
    import logging

    recorded: list = []
    _patch_completion_seams(monkeypatch, recorded)

    calls = {"n": 0}

    def _fake_loop(*, dispatch, force_tool=None, **kw):
        calls["n"] += 1
        assert force_tool != "complete_task", "must never force complete on a non-completion turn"
        return "There are two open tasks on the ledger."

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)

    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=5, user_id="u-assignee")
    with caplog.at_level(logging.WARNING):
        result = qa.answer(
            enterprise_id="c1", question="what tasks are in the ledger?",
            dataset="d", scope=scope,
        )

    assert calls["n"] == 1  # no forced re-run
    assert recorded == []  # no completion written
    assert result["answer"] == "There are two open tasks on the ledger."
    assert not any(
        "completion_admitted_without_tool_call" in r.getMessage() for r in caplog.records
    )


def test_completion_forcing_removal_makes_private_completion_red(monkeypatch):
    """Mutation proof for the completion forcing seam. Simulating deletion of
    the `admitted_completion` forcing `elif` (by forcing the completion
    predicate off — the same observable effect: `admitted_completion` is False
    so the turn is never admitted/forced) means a completion claim the model
    narrates-without-calling NEVER writes the ledger — RED. Restoring the real
    predicate re-arms the forcing pass and the row is written — GREEN."""
    recorded_red: list = []
    _patch_completion_seams(monkeypatch, recorded_red)

    def _fake_loop(*, dispatch, force_tool=None, **kw):
        if force_tool == "complete_task":
            return dispatch("complete_task", {"task_summary": "the export review"})
        return "Noted — I'll mark that done for you."

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)
    # Benign offline composer for the fall-through path (RED: turn not admitted).
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    monkeypatch.setattr(
        qa, "compose_ask_answer",
        lambda *a, **k: {"answer": "", "key_points": [], "citations": [], "confidence": 0.0, "unanswered": ""},
    )

    # RED: predicate forced off — `admitted_completion` is False everywhere,
    # so the turn is neither admitted nor force-completed (observably identical
    # to deleting the forcing `elif`).
    monkeypatch.setattr(qa, "is_project_completion_request", lambda *a, **k: False)
    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=5, user_id="u-assignee")
    qa.answer(
        enterprise_id="c1", question="I've finished the export review",
        dataset="d", scope=scope,
    )
    assert recorded_red == []  # RED — no forcing, no write

    # GREEN: restore the real predicate — the forcing pass fires and writes.
    monkeypatch.undo()
    recorded_green: list = []
    _patch_completion_seams(monkeypatch, recorded_green)
    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)
    qa.answer(
        enterprise_id="c1", question="I've finished the export review",
        dataset="d", scope=scope,
    )
    assert len(recorded_green) == 1  # GREEN — the real forcing pass restores the write


# ── Gated routing — project context-fold + accept-with-nudge (AC5b/AC5c) ────


def test_project_plainqa_context_fold_reaches_composer(monkeypatch):
    """AC5b: a project-surface plain-Q&A turn (the gate declines) still
    reaches the composer WITH `scope.system_addendum` + `scope.
    context_payload` folded into `history` as a synthetic context row — it
    does NOT answer "as a stranger" with no roster/ledger/memory."""
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    captured = {}

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        captured["history"] = history
        return {"answer": "ok", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)
    scope = SurfaceScope(
        surface=Surface.project_private, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL, project_task_execution.EXECUTE_TASK_TOOL),
        system_addendum="PROJECT ROSTER:\n- Fortune — Designer",
        context_payload="Task ledger:\n- Fortune — Draft the export review (open)",
    )
    qa.answer(enterprise_id="c1", question="what's blocking the launch?", dataset="d", scope=scope)

    history = captured["history"]
    assert history and history[0]["role"] == "context"
    assert "PROJECT ROSTER" in history[0]["content"]
    assert "Fortune — Draft the export review" in history[0]["content"]


def test_project_context_fold_dropped_is_red(monkeypatch):
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
        surface=Surface.project_private, project_id=9,
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


def test_project_fold_prepends_authoritative_preamble():
    """A project scope with a non-empty `context_payload` folds it with the
    "answer from THIS block, don't deflect" header — the composer
    fall-through frames its ledger/roster/memory facts as an authoritative
    block, never a passive, deflectable row. On the UNFIXED code the fold
    has no preamble (the red)."""
    scope = SurfaceScope(
        surface=Surface.project_private, project_id=9,
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


# RETIRED (2026-08-20): test_group_reply_broadcasts_on_completion asserted the
# `POST /group/turns` route GENERATES + persists + broadcasts the @Sprntly reply
# inline (the "backgrounded group reply" / post-and-receive design). The Slice-1
# rework (PROJECTS-REBUILD-SLICE1-PLAN.md, 2026-08-18) REMOVES that path: group
# now behaves like main/private — client-driven and streamed through `/v1/ask` —
# and the plan explicitly requires that a group post NOT trigger the backend
# group-agent (`_respond_as_group_agent`) or it double-generates. `/group/turns`
# is now mount-not-scheduler (persists+broadcasts the HUMAN turn only). Re-add
# with the slice that reintroduces post-and-receive group mode.


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
    on this project?") on the private surface still satisfies the gate
    (`_skip_project_connectors` → True, no source named) → the project tool
    loop claims it → project-ledger facts, NOT a connector deflection."""
    private = _build_private_scope_via_assembler(project_id=9, conversation_id=None, user_id="u1")

    def _spy_scoped(**kw):
        return {"answer": "project ledger facts", "citations": []}

    monkeypatch.setattr(qa, "_try_scoped_tool_answer", _spy_scoped)

    for q in ("what tasks are open?", "who's on this project?"):
        out = qa.answer(enterprise_id="c1", question=q, dataset="d", scope=private)
        assert out["answer"] == "project ledger facts", (private.surface, q)


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


# ── edit_prd-narration grounding — the "narrates Done, never writes" fix ────
#
# Root cause: `run_tool_loop`'s returned text is the MODEL's own free-form
# final turn, composed AFTER it sees the `edit_prd` tool_result — it is not
# guaranteed to equal, or even agree with, what the tool actually returned.
# No surface currently populates `edit_prd_handler` (kept dead-but-required —
# see `SurfaceScope.edit_prd_handler`), so these tests construct one
# synthetically to prove the grounding override survives: whenever `edit_prd`
# WAS called this turn, the handler's own real outcome overrides the model's
# free text.


def test_edit_prd_grounds_narration_on_refusal_not_model_claim(monkeypatch):
    """The model calls edit_prd, the handler refuses (no PRD open), but the
    model's OWN final text still claims success. The final answer must be
    the handler's real refusal, never the model's fabricated "Done"."""
    def _fake_loop(*, dispatch, **kw):
        dispatch("edit_prd", {"instruction": "tighten the scope"})
        # The model's own free-text turn, composed AFTER seeing the
        # refusal tool_result — fabricates success anyway.
        return "Done — it's live! Everyone can see the updated PRD now."

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)

    def _refusing_handler(tool_input):  # noqa: ARG001
        return ("Open a PRD beside this chat and I'll edit it.", None)

    scope = SurfaceScope(
        surface=Surface.project_private, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL,),
        edit_prd_handler=_refusing_handler,
    )
    out = qa.answer(
        enterprise_id="c1", question="tighten the scope of the PRD",
        dataset="d", scope=scope,
    )
    assert out["answer"] == "Open a PRD beside this chat and I'll edit it."
    assert "Done" not in out["answer"]
    assert "live" not in out["answer"]


def test_edit_prd_grounds_narration_on_success(monkeypatch):
    """Success arm — the handler applied the edit; the grounded narration IS
    the handler's own "Done — ..." text (never a DIFFERENT model
    paraphrase of it)."""
    def _fake_loop(*, dispatch, **kw):
        dispatch("edit_prd", {"instruction": "tighten the scope"})
        return "Sure thing, I made that change for you!"  # model paraphrase

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)

    def _applying_handler(tool_input):  # noqa: ARG001
        return ("Done — I've updated the PRD. Tightened the Scope section.", None)

    scope = SurfaceScope(
        surface=Surface.project_private, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL,),
        edit_prd_handler=_applying_handler,
    )
    out = qa.answer(
        enterprise_id="c1", question="tighten the scope of the PRD",
        dataset="d", scope=scope,
    )
    assert out["answer"] == "Done — I've updated the PRD. Tightened the Scope section."


def test_private_scope_unaffected_by_edit_prd_grounding(monkeypatch):
    """The real private-scope assembler never populates `edit_prd_handler`
    — the grounding override is a no-op there, so a private turn's free
    text (even one that happens to mention "PRD" + "done") passes through
    completely unchanged."""
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


# ── Empty-tool-loop guard (the empty-answer fix) ────────────────────────────
# `run_tool_loop` can burn all its iterations on tool calls and return "" with
# no closing text turn (the "returns nothing, user retries" bug). Surfacing
# that verbatim stores `{"answer": ""}` → a blank bubble. The guard in
# `_try_scoped_tool_answer` treats an empty return EXACTLY like the function's
# existing failure path: PRIVATE degrades to `None` (→ the ordinary composer,
# which can actually answer); a NON-private (group) surface re-raises.


def test_empty_tool_loop_private_degrades_to_composer(monkeypatch, caplog):
    """The primary fix: the loop returns "" (model called tools every turn,
    no closing text). On the PRIVATE surface `_try_scoped_tool_answer` returns
    None, `qa.answer` falls through to the ordinary composer, and the user
    gets a REAL answer — never a blank bubble."""
    # The loop enters (gate fires on "summarize the PRD") but produces no text.
    monkeypatch.setattr("app.llm.run_tool_loop", lambda **kw: "   ")

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        return {"answer": "the composer's real answer", "key_points": [],
                "citations": [], "confidence": 0.6, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)
    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=None, user_id="u1")
    import logging
    with caplog.at_level(logging.WARNING, logger="app.qa_agent"):
        out = qa.answer(
            enterprise_id="c1", question="summarize the PRD", dataset="d", scope=scope,
        )
    # Degraded to a real composer answer, NOT `{"answer": ""}`.
    assert out["answer"] == "the composer's real answer"
    # The empty-loop degrade was logged (no longer invisible).
    assert any("scoped_tool_reply_empty" in r.message for r in caplog.records)


def test_empty_tool_loop_nonprivate_reraises(monkeypatch):
    """The NON-private (group) failure contract: an empty tool-loop return
    re-raises rather than degrading (group has no single-shot composer
    fallback), mirroring the function's existing except-branch `raise`. The
    group surface was retired on this branch, so a non-private surface stands
    in for it — the branch under test is `scope.surface != project_private`."""
    monkeypatch.setattr("app.llm.run_tool_loop", lambda **kw: "")
    scope = SurfaceScope(
        surface=Surface.main, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL,),
    )
    with pytest.raises(qa.EmptyScopedToolAnswer):
        qa._try_scoped_tool_answer(
            scope=scope, question="summarize the PRD", history=None,
            enterprise_id="c1", dataset="d",
        )


def test_terminal_narration_not_treated_as_empty(monkeypatch):
    """A terminal-tool narration turn (here `delegate_task`) is NOT the empty
    case: even when the model's OWN free text is blank, the handler's
    non-empty narration is the legit answer and the empty guard must not
    fire. Proves the guard sits AFTER the narration overrides."""
    def _fake_loop(*, dispatch, **kw):
        dispatch("delegate_task", {"assignee": "Priya", "task_summary": "Draft it"})
        return ""  # model composed no closing text of its own

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)
    monkeypatch.setattr(
        "app.project_delegation.handle_delegate_task",
        lambda **kw: "Sent the brief to Priya's chat.",
    )
    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=None, user_id="u1")
    out = qa.answer(
        enterprise_id="c1", question="please delegate the export review to Priya",
        dataset="d", scope=scope,
    )
    # The handler narration survives — the guard did NOT degrade it to a blank.
    assert out["answer"] == "Sent the brief to Priya's chat."


# ── Sixth-branch gate yields to a report pipeline the planner already
# resolved (report-routing fix) ─────────────────────────────────────────────
# A report-phrased ask on a project surface ("how have my customers been
# saying? give me a voice-of-customer report") lexically satisfies
# `is_project_content_request` (leading interrogative + a content noun) just
# like a genuine project-content question does — but the connector-blind
# project tool loop has no report-generation tool at all, so it only ever
# declines. When the planner has ALREADY resolved this turn to a report
# pipeline at high confidence, the gate must defer instead, mirroring the
# existing `_skip_project_connectors` clause rather than inventing a new
# mechanism. Reports are company-wide by construction (`call_digest.answer`,
# `market_intel.answer` etc. take no `project_id`), so the deferred turn
# reaches the SAME shared report path main chat's identical phrasing already
# produces — no project-scoping is added anywhere.


@pytest.mark.parametrize(
    "question, pipeline_id, patch_target",
    [
        (
            "How have my customers been saying? Give me a voice-of-customer report",
            "voice-of-customer-report",
            "app.call_digest.answer",
        ),
        (
            "give me a market intel report",
            "market-intelligence-report",
            "app.market_intel.answer",
        ),
    ],
)
def test_report_phrased_project_ask_defers_to_shared_report_path(
    monkeypatch, question, pipeline_id, patch_target,
):
    """MUTATION-shaped proof of the fix. With the deferral clause present the
    turn reaches the shared report engine (GREEN); forcing the predicate to
    always report "nothing to defer to" (the pre-fix gate) reproduces the
    wrongful decline (RED) — the exact reported symptom."""
    scope = SurfaceScope(
        surface=Surface.project_private, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL,),
    )
    plan = Plan(pipeline_id=pipeline_id, confidence=0.92)

    calls = {"scoped": 0, "report": 0}

    def _spy_scoped(**kw):
        calls["scoped"] += 1
        return {"answer": "DECLINED by the connector-blind project loop", "citations": []}

    def _fake_report(**kw):
        calls["report"] += 1
        return {"answer": "Here is the report you asked for.", "citations": []}

    monkeypatch.setattr(qa, "_try_scoped_tool_answer", _spy_scoped)
    monkeypatch.setattr(patch_target, _fake_report)

    # Sanity: the phrasing really does lexically satisfy the sixth-branch
    # content gate — this is what makes the deferral load-bearing rather than
    # incidental (a phrasing the gate never matched would pass either way).
    from app.skill_router import is_project_content_request

    assert is_project_content_request(question) is True, question

    # GREEN (fix present): the turn reaches the shared report engine, never
    # the project tool loop.
    out = qa.answer(
        enterprise_id="c1", question=question, dataset="d",
        scope=scope, plan=plan,
    )
    assert calls["scoped"] == 0
    assert calls["report"] == 1
    assert out["answer"] == "Here is the report you asked for."

    # RED (mutation: force the predicate as if no plan ever deferred it) —
    # reproduces the wrongful decline the bug report described.
    calls["scoped"] = 0
    calls["report"] = 0
    monkeypatch.setattr(qa, "_defers_to_report_pipeline", lambda *a, **kw: False)
    out = qa.answer(
        enterprise_id="c1", question=question, dataset="d",
        scope=scope, plan=plan,
    )
    assert calls["scoped"] == 1
    assert calls["report"] == 0
    assert out["answer"].startswith("DECLINED")


def test_report_pipeline_deferral_is_a_noop_with_no_plan(monkeypatch):
    """AC byte-identity for every caller that predates the planner threading:
    `plan=None` (the sixth branch's own default) must behave exactly as it
    did before this fix — the project tool loop still claims a genuine
    project-content question with no plan supplied at all."""
    scope = SurfaceScope(
        surface=Surface.project_private, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL,),
    )
    calls = {"scoped": 0}

    def _spy_scoped(**kw):
        calls["scoped"] += 1
        return {"answer": "project ledger facts", "citations": []}

    monkeypatch.setattr(qa, "_try_scoped_tool_answer", _spy_scoped)
    out = qa.answer(
        enterprise_id="c1", question="what tasks are open?", dataset="d",
        scope=scope,
    )
    assert calls["scoped"] == 1
    assert out["answer"] == "project ledger facts"


def test_sixth_branch_gate_and_report_defer_use_same_predicate():
    """Symmetry proof (source-level), mirroring `test_sixth_branch_gate_and_
    connector_skip_use_same_predicate`: the gate's report-defer clause reads
    `_defers_to_report_pipeline(plan)` — one predicate, one call site, so the
    mechanism cannot silently drift out of sync with itself."""
    src = inspect.getsource(qa.answer)
    assert "and not _defers_to_report_pipeline(plan)" in src


def test_defers_to_report_pipeline_predicate_unit():
    """Pure-unit coverage of the predicate itself: every member of the report
    set (five `PIPELINE_SKILLS` report ids + the `call-digest` machinery id)
    defers; a non-report pipeline id, a non-answer action, and no plan at all
    do not."""
    from app.skill_router import PIPELINE_SKILLS

    for report_id in sorted(PIPELINE_SKILLS) + ["call-digest"]:
        assert qa._defers_to_report_pipeline(
            Plan(pipeline_id=report_id, confidence=0.9)
        ) is True, report_id

    # A non-report machinery id (a lookup/utility, not report generation).
    assert qa._defers_to_report_pipeline(
        Plan(pipeline_id="tracker-lookup", confidence=0.9)
    ) is False
    # No pipeline resolved at all — the ordinary outcome for project-meta/
    # delegation/edit-doc phrasings.
    assert qa._defers_to_report_pipeline(Plan()) is False
    # A non-answer action must never be read as a report request even if a
    # stray pipeline_id rode along.
    non_answer_plan = dataclasses.replace(
        Plan(pipeline_id="voice-of-customer-report", confidence=0.9),
        action="list_artifacts",
    )
    assert qa._defers_to_report_pipeline(non_answer_plan) is False
    # No plan at all.
    assert qa._defers_to_report_pipeline(None) is False


def test_sixth_branch_still_claims_genuine_project_turns_with_plan_present(monkeypatch):
    """Regression per the report-routing fix (hard constraint #1). Supplying
    a resolved `plan` must NOT broadly stand the sixth branch down — a plan
    whose `pipeline_id` is None (the ordinary outcome for project-meta,
    delegation, and edit-doc phrasings, none of which resemble a report
    request) leaves the gate exactly as the existing gates already decide
    it. Covers the three regression classes the fix names explicitly: a
    project-meta question, a delegation phrase, and an edit-PRD phrase."""
    no_report_plan = Plan()  # action=answer, pipeline_id=None
    assert no_report_plan.pipeline_id is None

    calls = {"scoped": 0}

    def _spy_scoped(**kw):
        calls["scoped"] += 1
        return {"answer": "project agent turn", "citations": []}

    monkeypatch.setattr(qa, "_try_scoped_tool_answer", _spy_scoped)

    private = SurfaceScope(
        surface=Surface.project_private, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL,),
    )
    for question in (
        "what tasks are open?",                     # project-meta
        "please delegate the export review to Priya", # delegation
    ):
        calls["scoped"] = 0
        out = qa.answer(
            enterprise_id="c1", question=question, dataset="d",
            scope=private, plan=no_report_plan,
        )
        assert calls["scoped"] == 1, question
        assert out["answer"] == "project agent turn", question

    # Edit-PRD phrase — needs a scope that registers an edit handler (private
    # never does; see SurfaceScope.edit_prd_handler), same construction the
    # existing edit-prd-grounding tests above use.
    def _handler(tool_input):  # noqa: ARG001
        return ("Done — I've updated the PRD.", None)

    edit_capable = SurfaceScope(
        surface=Surface.project_private, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL,),
        edit_prd_handler=_handler,
    )
    calls["scoped"] = 0
    out = qa.answer(
        enterprise_id="c1", question="tighten the scope of the PRD",
        dataset="d", scope=edit_capable, plan=no_report_plan,
    )
    assert calls["scoped"] == 1
    assert out["answer"] == "project agent turn"


# ── Ticket-ownership-vs-delegation confabulation fix (F2) ──────────────────
# "assign the auth ticket to David" in a project with NO tickets is lexically
# an `is_project_tool_request` match — the delegate-verb regex is object-blind
# between a ticket and a person — even though the planner already correctly
# classifies it `assign_tickets` (ticket OWNERSHIP, not a task delegation).
# The delegate tool loop has no ticket-assignment tool at all, so it
# fabricated a delegation row instead of declining. When the planner has
# ALREADY resolved this turn to a ticket-family action, the gate must defer,
# mirroring the existing `_defers_to_report_pipeline` clause rather than
# inventing a new mechanism.


@pytest.mark.parametrize(
    "action", ["assign_tickets", "update_ticket", "generate_tickets"],
)
def test_ticket_action_verdict_defers_the_sixth_branch(monkeypatch, action):
    """MUTATION-shaped proof of the fix. With the deferral clause present the
    turn falls through to the composer (GREEN, no fabricated delegation row);
    forcing the predicate to always report "nothing to defer to" (the pre-fix
    gate) reproduces the wrongful claim (RED) — the exact reported symptom."""
    question = "assign the auth ticket to David"
    scope = SurfaceScope(
        surface=Surface.project_private, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL,),
    )
    plan = Plan(action=action)

    calls = {"scoped": 0}

    def _spy_scoped(**kw):
        calls["scoped"] += 1
        return {"answer": "FABRICATED by the connector-blind project loop", "citations": []}

    monkeypatch.setattr(qa, "_try_scoped_tool_answer", _spy_scoped)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        return {"answer": "There's no auth ticket in this project yet.", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)

    # Sanity: the phrasing really does lexically satisfy the sixth-branch
    # tool gate — this is what makes the deferral load-bearing rather than
    # incidental (a phrasing the gate never matched would pass either way).
    from app.skill_router import is_project_tool_request

    assert is_project_tool_request(question) is True, question

    # GREEN (fix present): the turn reaches the ordinary composer, never the
    # tool loop that fabricated the delegation row.
    out = qa.answer(
        enterprise_id="c1", question=question, dataset="d",
        scope=scope, plan=plan,
    )
    assert calls["scoped"] == 0
    assert out["answer"] == "There's no auth ticket in this project yet."

    # RED (mutation: force the predicate as if no plan ever deferred it) —
    # reproduces the fabricated-delegation-row symptom the bug report named.
    calls["scoped"] = 0
    monkeypatch.setattr(qa, "_defers_to_ticket_action", lambda *a, **kw: False)
    out = qa.answer(
        enterprise_id="c1", question=question, dataset="d",
        scope=scope, plan=plan,
    )
    assert calls["scoped"] == 1
    assert out["answer"].startswith("FABRICATED")


def test_delegate_verdict_is_not_deferred(monkeypatch):
    """Must-not-regress: `delegate` is NOT in the ticket-family defer set —
    it is the delegate tool loop's OWN action — so a genuine delegation
    turn ("ask David to review the PRD") must still reach `delegate_task`
    unchanged, never fall through to the composer."""
    scope = SurfaceScope(
        surface=Surface.project_private, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL,),
    )
    plan = Plan(action="delegate")

    calls = {"scoped": 0}

    def _spy_scoped(**kw):
        calls["scoped"] += 1
        return {"answer": "Handed off to David.", "citations": []}

    monkeypatch.setattr(qa, "_try_scoped_tool_answer", _spy_scoped)

    out = qa.answer(
        enterprise_id="c1", question="ask David to review the PRD", dataset="d",
        scope=scope, plan=plan,
    )
    assert calls["scoped"] == 1
    assert out["answer"] == "Handed off to David."


def test_ticket_action_deferral_is_a_noop_with_no_plan(monkeypatch):
    """AC byte-identity for every caller that predates the planner threading:
    `plan=None` (the sixth branch's own default) must behave exactly as it
    did before this fix — the project tool loop still claims a genuine
    delegation turn with no plan supplied at all."""
    scope = SurfaceScope(
        surface=Surface.project_private, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL,),
    )
    calls = {"scoped": 0}

    def _spy_scoped(**kw):
        calls["scoped"] += 1
        return {"answer": "Handed off to David.", "citations": []}

    monkeypatch.setattr(qa, "_try_scoped_tool_answer", _spy_scoped)
    out = qa.answer(
        enterprise_id="c1", question="ask David to review the PRD", dataset="d",
        scope=scope,
    )
    assert calls["scoped"] == 1
    assert out["answer"] == "Handed off to David."


def test_sixth_branch_gate_and_ticket_action_defer_use_same_predicate():
    """Symmetry proof (source-level), mirroring `test_sixth_branch_gate_and_
    report_defer_use_same_predicate`: the gate's ticket-action-defer clause
    reads `_defers_to_ticket_action(plan)` — one predicate, one call site, so
    the mechanism cannot silently drift out of sync with itself. The clause is
    now guarded by the admitted-completion exemption (a completion claim
    overrides the deferral), so the exact source shape is the disjunction."""
    src = inspect.getsource(qa.answer)
    # The defer clause is now a disjunction: an admitted completion overrides
    # the ticket-action deferral. Assert the code shape (whitespace-normalised
    # so comments/indentation don't make it brittle).
    normalised = " ".join(src.split())
    assert (
        "not _defers_to_ticket_action(plan) or is_project_completion_request(routing_text, history)"
        in normalised
    )


def test_defers_to_ticket_action_predicate_unit():
    """Pure-unit coverage of the predicate itself: every member of the
    ticket-family action set defers; `delegate`, a non-ticket action, and no
    plan at all do not."""
    for ticket_action in sorted(qa._TICKET_ACTION_IDS):
        assert qa._defers_to_ticket_action(
            Plan(action=ticket_action)
        ) is True, ticket_action

    # The delegate loop's own action must never be read as ticket ownership.
    assert qa._defers_to_ticket_action(Plan(action="delegate")) is False
    # The ordinary default action.
    assert qa._defers_to_ticket_action(Plan()) is False
    # No plan at all.
    assert qa._defers_to_ticket_action(None) is False


def test_admitted_completion_overrides_update_ticket_deferral(monkeypatch):
    """Live-found blocker fix. The ask-planner mislabels a first-person
    completion CLAIM as `action=update_ticket`; without the exemption
    `_defers_to_ticket_action(plan)` vetoes the completion branch and
    `complete_task` never fires (completion falls to the non-deterministic
    background classifier). With the exemption, an admitted completion
    overrides the deferral and the sixth branch claims the turn.

    Mutation-proof: neutralising `is_project_completion_request` (the observable
    effect of deleting the exemption AND the admission disjunct) reproduces the
    veto — the turn is deferred and the tool loop is never entered (RED)."""
    question = "I'm done with the security review"
    scope = SurfaceScope(
        surface=Surface.project_private, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL, project_delegation.COMPLETE_TASK_TOOL),
    )
    plan = Plan(action="update_ticket")  # the planner's live misclassification

    calls = {"scoped": 0}

    def _spy_scoped(**kw):
        calls["scoped"] += 1
        return {"answer": "Got it — I've marked that task as done on the ledger.", "citations": []}

    monkeypatch.setattr(qa, "_try_scoped_tool_answer", _spy_scoped)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    monkeypatch.setattr(
        qa, "compose_ask_answer",
        lambda *a, **k: {"answer": "composer", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""},
    )

    # Sanity: the planner really does defer this action, so the exemption is
    # load-bearing (not incidental).
    assert qa._defers_to_ticket_action(plan) is True

    # GREEN (exemption present): the admitted completion overrides the deferral
    # → the sixth branch claims the turn and reaches complete_task.
    out = qa.answer(
        enterprise_id="c1", question=question, dataset="d", scope=scope, plan=plan,
    )
    assert calls["scoped"] == 1
    assert out["answer"].startswith("Got it")

    # RED (mutation: neutralise the completion predicate — both the exemption
    # and the admission disjunct go False, reproducing the pre-fix veto).
    calls["scoped"] = 0
    monkeypatch.setattr(qa, "is_project_completion_request", lambda *a, **k: False)
    out = qa.answer(
        enterprise_id="c1", question=question, dataset="d", scope=scope, plan=plan,
    )
    assert calls["scoped"] == 0  # deferred — the tool loop was never entered
    assert out["answer"] == "composer"


def test_genuine_update_ticket_edit_still_defers_not_hijacked(monkeypatch):
    """The exemption must NOT hijack a genuine ticket-edit. "change the
    acceptance criteria on the export ticket" is NOT a completion claim, so
    `is_project_completion_request` is False and the exemption disjunct stays
    False — the ticket-action deferral still governs and the turn falls through
    to the composer/ticket path, never the completion tool loop."""
    from app.skill_router import is_project_completion_request

    question = "change the acceptance criteria on the export ticket"
    # The guard that makes the exemption safe: a ticket edit is not a completion.
    assert is_project_completion_request(question) is False

    scope = SurfaceScope(
        surface=Surface.project_private, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL, project_delegation.COMPLETE_TASK_TOOL),
    )
    plan = Plan(action="update_ticket")

    calls = {"scoped": 0}

    def _spy_scoped(**kw):
        calls["scoped"] += 1
        return {"answer": "SHOULD NOT REACH THE COMPLETION LOOP", "citations": []}

    monkeypatch.setattr(qa, "_try_scoped_tool_answer", _spy_scoped)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    monkeypatch.setattr(
        qa, "compose_ask_answer",
        lambda *a, **k: {"answer": "There's no export ticket to edit yet.", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""},
    )

    out = qa.answer(
        enterprise_id="c1", question=question, dataset="d", scope=scope, plan=plan,
    )
    assert calls["scoped"] == 0  # the completion loop was NOT hijacked
    assert out["answer"] == "There's no export ticket to edit yet."


# ── Lexical-gate veto vs planner-approved delegation (fix part B) ──────────
# `skill_router._PROJECT_TOOL_MENTION_VETO` runs FIRST inside
# `is_project_tool_request` and its alternation includes "summarize" — so
# "can you have David look into X and summarize the differences", which the
# PLANNER already classified `delegate`, was vetoed and never reached the
# tool loop. `_admits_on_delegate_plan` trusts the planner's verdict instead
# of growing/loosening the regex.


def test_admits_on_delegate_plan_predicate_unit():
    """Pure-unit coverage: `plan.action == "delegate"` admits; a non-delegate
    action and no plan at all do not."""
    assert qa._admits_on_delegate_plan(Plan(action="delegate")) is True
    assert qa._admits_on_delegate_plan(Plan(action="assign_tickets")) is False
    assert qa._admits_on_delegate_plan(Plan()) is False
    assert qa._admits_on_delegate_plan(None) is False


def test_sixth_branch_gate_and_delegate_plan_admit_use_same_predicate():
    """Symmetry proof (source-level): the gate's admit-on-delegate-plan
    disjunct reads `_admits_on_delegate_plan(plan)` — one predicate, one call
    site, so the mechanism cannot silently drift out of sync with itself."""
    src = inspect.getsource(qa.answer)
    assert "or _admits_on_delegate_plan(plan)" in src


def test_veto_masked_delegation_admitted_via_planner_verdict(monkeypatch):
    """MUTATION-shaped proof of the fix. The phrasing lexically fails
    `is_project_tool_request` (its mention veto's alternation includes
    "summarize"), so WITHOUT the new disjunct the turn falls through to the
    composer even though the planner already resolved it `delegate` (RED —
    the exact reported symptom). WITH the disjunct present the turn is
    admitted into the tool loop (GREEN)."""
    question = "can you have David look into the export bug and summarize the differences"
    scope = SurfaceScope(
        surface=Surface.project_private, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL,),
    )
    plan = Plan(action="delegate")

    calls = {"scoped": 0}

    def _spy_scoped(**kw):
        calls["scoped"] += 1
        return {"answer": "Handed off to David.", "citations": []}

    monkeypatch.setattr(qa, "_try_scoped_tool_answer", _spy_scoped)

    # Sanity: the phrasing really is lexically vetoed — this is what makes
    # the new disjunct load-bearing rather than incidental (a phrasing the
    # veto never caught would pass either way).
    from app.skill_router import is_project_tool_request

    assert is_project_tool_request(question) is False, question

    # GREEN (fix present): the planner's `delegate` verdict admits the turn
    # into the tool loop despite the lexical veto.
    out = qa.answer(
        enterprise_id="c1", question=question, dataset="d",
        scope=scope, plan=plan,
    )
    assert calls["scoped"] == 1
    assert out["answer"] == "Handed off to David."

    # RED (mutation: force the new predicate off, as if it never existed) —
    # reproduces the reported symptom: a planner-approved delegation vetoed
    # by the crude regex never reaches the tool loop.
    calls["scoped"] = 0
    monkeypatch.setattr(qa, "_admits_on_delegate_plan", lambda *a, **kw: False)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        return {"answer": "I can't find that in the project.", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)
    out = qa.answer(
        enterprise_id="c1", question=question, dataset="d",
        scope=scope, plan=plan,
    )
    assert calls["scoped"] == 0
    assert out["answer"] == "I can't find that in the project."


def test_answer_first_summarize_not_admitted_via_delegate_disjunct(monkeypatch):
    """Must-not-regress / narrow-scope proof: a pure summarize ask (planner
    classifies it `answer`, never `delegate`) must NOT be admitted via the
    new disjunct — the fix trusts the planner's verdict, it does not make
    every "summarize"-containing turn a delegation. Phrased WITHOUT a
    project-content noun ("PRD"/"report"/etc.) so this isolates the new
    disjunct specifically — "summarize the PRD" independently satisfies the
    pre-existing `is_project_content_request` gate regardless of this fix,
    which would make the assertion below vacuous."""
    question = "summarize the differences"
    scope = SurfaceScope(
        surface=Surface.project_private, project_id=9,
        extra_tools=(project_delegation.DELEGATE_TASK_TOOL,),
    )
    plan = Plan(action="answer")

    calls = {"scoped": 0}

    def _spy_scoped(**kw):
        calls["scoped"] += 1
        return {"answer": "FABRICATED — should never be reached", "citations": []}

    monkeypatch.setattr(qa, "_try_scoped_tool_answer", _spy_scoped)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        return {"answer": "Here's the PRD summary.", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)

    out = qa.answer(
        enterprise_id="c1", question=question, dataset="d",
        scope=scope, plan=plan,
    )
    assert calls["scoped"] == 0
    assert out["answer"] == "Here's the PRD summary."


# ── Delegation-confabulation fix: composer split (fix part 2) ──────────────


def test_composer_fold_addendum_omits_delegate_guidance_tool_addendum_keeps_it():
    """The delegate_task-specific guidance — including the verbatim
    handoff-confirmation template — must reach the tool-loop's system prompt
    (`system_addendum`, where `delegate_task` is genuinely callable) but must
    NOT reach the gate-decline / composer fall-through's own addendum
    (`composer_fold_addendum`, where no tools exist). Both still carry the
    accept-with-nudge sentence."""
    from app.ask_job_runner import _PRIVATE_SCOPE_DELEGATE_GUIDANCE
    from app.surface_scope import PROJECT_TOOL_NUDGE

    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=None, user_id="u1")

    assert _PRIVATE_SCOPE_DELEGATE_GUIDANCE in scope.system_addendum
    assert "I've asked <name> to <task>" in scope.system_addendum
    assert PROJECT_TOOL_NUDGE in scope.system_addendum

    assert _PRIVATE_SCOPE_DELEGATE_GUIDANCE not in scope.composer_fold_addendum
    assert "I've asked <name> to <task>" not in scope.composer_fold_addendum
    assert "delegate_task tool" not in scope.composer_fold_addendum
    assert PROJECT_TOOL_NUDGE in scope.composer_fold_addendum


def test_declined_turn_folds_composer_addendum_not_system_addendum(monkeypatch):
    """AC: when the sixth-branch gate declines a turn (a plain-Q&A ask), the
    composer receives `composer_fold_addendum` — never the tool-loop's
    `system_addendum` with its delegate_task confirmation template. Proves
    the confabulation fix end-to-end through `qa_agent.answer`'s real
    fall-through seam, not just `_fold_project_context` in isolation."""
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    captured = {}

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        captured["history"] = history
        return {"answer": "ok", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)
    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=None, user_id="u1")

    qa.answer(
        enterprise_id="c1", question="what's blocking the launch?",
        dataset="d", scope=scope,
    )

    history = captured["history"]
    assert history and history[0]["role"] == "context"
    content = history[0]["content"]
    assert "I've asked <name> to <task>" not in content
    assert "delegate_task tool" not in content
    assert "If this message is asking you to hand off" in content  # PROJECT_TOOL_NUDGE


def test_fold_prefers_composer_fold_addendum_over_system_addendum():
    """Direct unit proof on `_fold_project_context`: when both fields are
    set, the folded content comes from `composer_fold_addendum`, not
    `system_addendum` — and falls back to `system_addendum` when
    `composer_fold_addendum` is empty (byte-for-byte pre-existing behavior
    for every caller that predates the new field, proven by the sibling
    tests in the "Authoritative preamble single-source" section above)."""
    scope = SurfaceScope(
        surface=Surface.project_private, project_id=9,
        system_addendum="TOOL-LOOP TEXT — must not leak to composer",
        composer_fold_addendum="COMPOSER TEXT — the real fold",
    )
    folded = qa._fold_project_context(scope, [])
    content = folded[0]["content"]
    assert content == "COMPOSER TEXT — the real fold"
    assert "TOOL-LOOP TEXT" not in content


# ── Delegation-confabulation fix: get_task_ledger grounding (fix part 3) ───


def test_ledger_reply_grounded_in_real_tool_return_not_model_free_text():
    """The model calls `get_task_ledger`, gets the REAL ledger back, but then
    composes its OWN free-text final turn that doesn't match it (the
    fabricated-ledger defect). The real dispatch return must override the
    model's free text — mirrors the `edit_prd`/`delegate_task` grounding
    precedent, extended to this read tool."""
    def _fake_loop(*, dispatch, **kw):
        dispatch("get_task_ledger", {})
        # The model's own final turn — NOT what the tool actually returned.
        return "You have 3 open tasks, all assigned to yourself."

    import app.llm
    from unittest.mock import patch

    with patch.object(app.llm, "run_tool_loop", _fake_loop), \
         patch(
             "app.project_group_context.dispatch_read_tool",
             lambda name, ti, **kw: (
                 "- #1: Draft the export review — assigned to Fortune by Priya (open)"
                 if name == "get_task_ledger" else None
             ),
         ):
        scope = _build_private_scope_via_assembler(project_id=9, conversation_id=None, user_id="u1")
        out = qa.answer(
            enterprise_id="c1", question="what's on the task ledger?",
            dataset="d", scope=scope,
        )

    assert out["answer"] == "- #1: Draft the export review — assigned to Fortune by Priya (open)"
    assert "3 open tasks" not in out["answer"]


# ── Delegation-confabulation fix: forcing-pass past-tense backstop (fix 4) ─


def test_forcing_pass_fires_on_past_tense_promise_without_call(monkeypatch, caplog):
    """Backstop (b): a model turn that narrates the handoff as ALREADY DONE
    ("I've asked David to review the prd…") without ever calling
    `delegate_task` must still trigger the forcing pass (the pre-fix regex
    deliberately excluded past tense) — proving the broadened regex closes
    this gap on top of the composer-split fix (part 2)."""
    import logging

    import app.project_delegation as pd

    recorded = []
    monkeypatch.setattr(pd, "record_delegation", lambda **kw: recorded.append(kw) or {"id": 1})
    monkeypatch.setattr(
        pd, "resolve_member",
        lambda pid, needle: {"status": "found", "member": {"user_id": "u-assignee", "name": "David"}},
    )
    monkeypatch.setattr(pd, "is_project_member", lambda pid, uid: True)
    monkeypatch.setattr(pd, "_build_brief", lambda *a, **kw: "Brief text")
    monkeypatch.setattr(pd, "create_individual_project_chat", lambda pid, uid: {"id": 77})
    monkeypatch.setattr(pd, "post_individual_turn", lambda conv_id, role, content: {"id": 1})

    calls = {"n": 0}

    def _fake_loop(*, dispatch, force_tool=None, **kw):
        calls["n"] += 1
        if force_tool == "delegate_task":
            return dispatch("delegate_task", {"assignee": "David", "task_summary": "Review the PRD"})
        # Past-tense promise — the exact shape the old composer's confirmation
        # template used to reproduce with no tool ever called.
        return "I've asked David to review the prd — I'll bring his answer back here once it's in."

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)

    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=5, user_id="u-assigner")
    with caplog.at_level(logging.WARNING):
        qa.answer(
            enterprise_id="c1", question="tell David to review the prd",
            dataset="d", scope=scope,
        )

    assert calls["n"] == 2  # initial pass + one forced delegate_task re-run
    assert len(recorded) == 1  # the ledger write actually happened
    assert any(
        "delegation_promise_without_tool_call" in r.getMessage() for r in caplog.records
    )


def test_forcing_pass_does_not_refire_when_delegate_task_already_ran(monkeypatch):
    """Guard proof: when `delegate_task` WAS called in the initial pass (so
    `delegate_task_narrations` is non-empty), the forcing pass must NOT fire
    a second time even if the model's raw text also happens to contain a
    past-tense promise phrase — the broadened regex (fix part 4) only ever
    matters when the tool provably did not run."""
    import app.project_delegation as pd

    monkeypatch.setattr(pd, "record_delegation", lambda **kw: {"id": 1})
    monkeypatch.setattr(
        pd, "resolve_member",
        lambda pid, needle: {"status": "found", "member": {"user_id": "u-assignee", "name": "David"}},
    )
    monkeypatch.setattr(pd, "is_project_member", lambda pid, uid: True)
    monkeypatch.setattr(pd, "_build_brief", lambda *a, **kw: "Brief text")
    monkeypatch.setattr(pd, "create_individual_project_chat", lambda pid, uid: {"id": 77})
    monkeypatch.setattr(pd, "post_individual_turn", lambda conv_id, role, content: {"id": 1})

    calls = {"n": 0}

    def _fake_loop(*, dispatch, force_tool=None, **kw):
        calls["n"] += 1
        # The tool loop actually calls delegate_task itself this pass, AND
        # the model's raw text also contains a past-tense promise phrase.
        dispatch("delegate_task", {"assignee": "David", "task_summary": "Review the PRD"})
        return "I've asked David to review the prd — I'll bring his answer back here once it's in."

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_loop)

    scope = _build_private_scope_via_assembler(project_id=9, conversation_id=5, user_id="u-assigner")
    qa.answer(
        enterprise_id="c1", question="tell David to review the prd",
        dataset="d", scope=scope,
    )

    assert calls["n"] == 1  # no forced re-run — the guard held
