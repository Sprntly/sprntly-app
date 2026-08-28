"""The chat knows what a project is, whose it is, and can make one.

Reported flat: "the chat does not know what a project is and cannot create one
from a prompt". Both halves were true and neither was a bug — the capability
was never wired. The word "project" appeared nowhere in `ASK_SYSTEM`, no block
listed one, and the planner's action menu had `create_artifact` and
`list_artifacts` but nothing for the container those documents live in. So
"what projects do I have" was answered out of the knowledge graph (where the
nearest thing is a Jira board) and "create a project for X" landed on `answer`,
which — knowing the product has projects — replied as though it had made one.

Three parts, pinned below: the block (`app.projects_context`), the narrowing
that keeps every OTHER kind of "project" out of the prompt while it answers,
and the `create_project` action reaching the client wire.
"""
from __future__ import annotations

import pytest

import app.ask_runner as ask_runner
import app.chat_intent as chat_intent
import app.projects_context as projects_context
import app.qa_agent as qa
from app.ask_planner import Plan
from app.db import projects as projects_db

PROJECTS_BLOCK = (
    "=== THIS WORKSPACE'S PROJECTS ===\n"
    "A PROJECT in Sprntly is a shared container for one topic.\n"
    "- Billing revamp — 3 members — 2 PRDs — project id: 7"
)


def _project(**kw):
    row = {
        "id": 7, "name": "Billing revamp", "member_count": 3,
        "artifact_counts": {"prd": 2, "prototype": 1},
        "memory_count": 4,
    }
    row.update(kw)
    return row


@pytest.fixture
def request_scope():
    """A caller and a workspace in request scope, cleared afterwards — the
    same set/reset discipline `ask_job_runner._run_sync` uses, and for the same
    reason: a leaked ContextVar scopes the next test to the last one's user."""
    ws = ask_runner.set_active_workspace_id("ws-1")
    conv = ask_runner.set_active_conversation(None, "u-1")
    yield
    ask_runner.reset_active_conversation(conv)
    ask_runner.reset_active_workspace_id(ws)


def _rows(monkeypatch, rows):
    monkeypatch.setattr(
        projects_db, "list_projects_for_workspace",
        lambda company_id, workspace_id, user_id: list(rows),
    )


# ─── the block ───────────────────────────────────────────────────────────────


def test_the_block_explains_what_a_project_is(monkeypatch, request_scope):
    """The half that was actually missing. A model that can list projects but
    cannot say what one IS answers "what is a project" with the English word,
    or with the connected tracker's version."""
    _rows(monkeypatch, [_project()])

    block = projects_context.projects_block("co-1")

    assert "shared container" in block
    assert "NOT a Jira project" in block
    assert "memory" in block


def test_a_project_line_carries_its_shape(monkeypatch, request_scope):
    _rows(monkeypatch, [_project()])

    block = projects_context.projects_block("co-1")

    assert "Billing revamp" in block
    assert "3 members" in block
    assert "2 PRDs" in block
    assert "1 prototype" in block
    assert "4 memory notes" in block
    assert "project id: 7" in block


def test_an_empty_project_still_renders_its_row(monkeypatch, request_scope):
    """A project spends its first hour with nothing in it; "no artifacts yet"
    is a fact about it, not a reason to drop the line."""
    _rows(monkeypatch, [_project(
        artifact_counts={}, member_count=1, memory_count=0,
    )])

    block = projects_context.projects_block("co-1")

    assert "1 member" in block
    assert "no artifacts yet" in block


def test_a_member_of_nothing_gets_the_explanation_anyway(monkeypatch, request_scope):
    """Belonging to no project is an ordinary state for a new workspace — the
    answer is "here is what they are for", not silence."""
    _rows(monkeypatch, [])

    block = projects_context.projects_block("co-1")

    assert "shared container" in block
    assert "None yet" in block
    assert "offer to create one" in block


def test_no_request_scope_renders_nothing(monkeypatch):
    """Without a workspace and a caller the list cannot be scoped, and an
    unscoped list is either a leak or a lie — render neither."""
    monkeypatch.setattr(
        projects_db, "list_projects_for_workspace",
        lambda *a, **k: [_project()],
    )

    assert projects_context.projects_block("co-1") == ""
    assert projects_context.projects_block(None) == ""


def test_a_failed_read_renders_nothing_rather_than_an_empty_list(monkeypatch, request_scope):
    def _boom(*a, **k):
        raise RuntimeError("supabase down")

    _rows(monkeypatch, [])
    monkeypatch.setattr(projects_db, "list_projects_for_workspace", _boom)

    assert projects_context.projects_block("co-1") == ""


def test_a_truncated_list_declares_the_truncation(monkeypatch, request_scope):
    over = projects_context._MAX_PROJECTS + 2
    _rows(monkeypatch, [_project(id=i, name=f"Project {i:03d}") for i in range(over)])

    block = projects_context.projects_block("co-1")

    assert block.count("\n- ") == projects_context._MAX_PROJECTS
    assert "+2 more not shown" in block


# ─── the plan's verdict ──────────────────────────────────────────────────────


def test_the_pure_projects_plan_is_projects_only():
    assert qa._projects_only_plan(Plan(action="answer", include_projects=True)) is True


def test_any_other_grounding_keeps_the_full_compose():
    assert qa._projects_only_plan(None) is False
    assert qa._projects_only_plan(Plan(action="answer", include_projects=False)) is False
    # "what Jira projects can we push to" — the tracker, so its source stays.
    assert qa._projects_only_plan(
        Plan(action="answer", include_projects=True, sources=["jira"])
    ) is False
    assert qa._projects_only_plan(
        Plan(action="answer", include_projects=True, include_knowledge_graph=True)
    ) is False


def test_the_planner_flag_is_what_gates_the_read(monkeypatch):
    monkeypatch.setattr(projects_context, "projects_block", lambda cid: PROJECTS_BLOCK)

    assert qa._planned_projects_context("co-1", Plan(action="answer")) == ""
    assert qa._planned_projects_context(
        None, Plan(action="answer", include_projects=True)
    ) == ""
    assert "Billing revamp" in qa._planned_projects_context(
        "co-1", Plan(action="answer", include_projects=True)
    )


# ─── the compose ─────────────────────────────────────────────────────────────


def _payload():
    return {
        "answer": "x", "key_points": [], "citations": [],
        "confidence": 0.5, "unanswered": "",
    }


def _spy(calls, name, result=None):
    def _fn(*a, **k):
        calls.append(name)
        return result

    return _fn


def test_a_projects_only_ask_reads_no_index_no_kg_no_corpus(
    isolated_settings, fake_llm, monkeypatch
):
    """Every connected tracker has "projects" and the document index is full of
    them — the block is the whole grounding, so none of the three is read."""
    calls: list[str] = []
    monkeypatch.setattr(ask_runner, "load_corpus", _spy(calls, "corpus"))
    monkeypatch.setattr(ask_runner, "document_grounding", _spy(calls, "docs", ("", [])))
    monkeypatch.setattr(ask_runner, "_retrieve_kg_bundle", _spy(calls, "kg", None))
    fake_llm["payload"] = _payload()

    ask_runner.compose_ask_answer(
        "tailspin", "what projects do I have?", enterprise_id="co-1",
        projects_context_fn=lambda: PROJECTS_BLOCK, library_only=True,
    )

    assert calls == []
    call = fake_llm["calls"][0]
    assert "THIS WORKSPACE'S PROJECTS" in call["user"]
    assert "Billing revamp" in call["user"]
    # The addendum that says how to read the block rides with it.
    assert "THIS WORKSPACE'S PROJECTS" in call["system"]


def test_a_prd_tab_ask_receives_the_projects_block_too(
    isolated_settings, fake_llm, monkeypatch
):
    monkeypatch.setattr(ask_runner, "document_grounding", lambda *a, **k: ("", []))
    fake_llm["payload"] = _payload()

    ask_runner.compose_ask_answer(
        "tailspin", "which project is this PRD in?", enterprise_id="co-1",
        prd_context="CURRENT PRD CONTEXT\nThe document.",
        projects_context_fn=lambda: PROJECTS_BLOCK, library_only=True,
    )

    call = fake_llm["calls"][0]
    assert "THIS WORKSPACE'S PROJECTS" in call["user"]
    assert "THIS WORKSPACE'S PROJECTS" in call["system"]
    assert "CURRENT PRD CONTEXT" in (
        call["kwargs"].get("user_cacheable_prefix") or ""
    )


def test_an_ask_with_no_projects_block_is_unchanged(
    isolated_settings, fake_llm, monkeypatch
):
    monkeypatch.setattr(ask_runner, "document_grounding", lambda *a, **k: ("", []))
    fake_llm["payload"] = _payload()

    ask_runner.compose_ask_answer(
        "tailspin", "what are the requirements?", enterprise_id="co-1",
        prd_context="CURRENT PRD CONTEXT\nThe document.",
    )

    call = fake_llm["calls"][0]
    assert "THIS WORKSPACE'S PROJECTS" not in call["user"]
    assert "THIS WORKSPACE'S PROJECTS" not in call["system"]


# ─── create_project reaches the client ───────────────────────────────────────


def test_create_project_is_on_the_wire():
    """The set IS the wire: an action missing here falls through to `answer`,
    where the chat says it created something and nothing exists."""
    assert "create_project" in chat_intent._CLIENT_INTENTS


def test_a_named_project_survives_to_the_envelope():
    envelope = chat_intent._plan_to_envelope(
        Plan(action="create_project", action_confidence=0.9, task="Billing revamp"),
        prd_id=None,
    )

    assert envelope["intent"] == "create_project"
    assert envelope["task"] == "Billing revamp"


def test_a_project_with_no_subject_degrades_to_an_answer():
    """An untitled container is worse than a question back. `answer` can ask
    what it should be called; a blank create cannot."""
    envelope = chat_intent._plan_to_envelope(
        Plan(action="create_project", action_confidence=0.9, task="   "),
        prd_id=None,
    )

    assert envelope["intent"] == "answer"
