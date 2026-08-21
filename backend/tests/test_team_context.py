"""The chat knows who works here — and knows it from ONE place.

"do you know my team members and their roles" reached a model holding the
knowledge graph, the document index and the company's connected sources, and
nothing at all that records membership. The two available answers were both
bad: "I don't have access to your team", or a roster assembled out of Slack
authors and Jira assignees — people who touched a tool, presented as
colleagues.

`app.team_context.team_block` is the record (the same rows Settings → Team
shows), the planner's `include_team` is what asks for it, and `_team_only_plan`
is what keeps the sources that caused the wrong roster out of the prompt while
it is being answered. These tests pin each part.
"""
from __future__ import annotations

import app.ask_runner as ask_runner
import app.db.team as db_team
import app.qa_agent as qa
import app.team_context as team_context
from app.ask_planner import Plan

TEAM_BLOCK = (
    "=== THIS WORKSPACE'S TEAM ===\n"
    "MEMBERS (1).\n"
    "- Ada Lovelace — ada@acme.test — job: Engineer — access: admin — user id: u-1"
)


def _member(**kw):
    row = {
        "id": 1, "user_id": "u-1", "role": "admin", "created_at": "2026-01-01",
        "display_name": "Ada Lovelace", "email": "ada@acme.test",
        "avatar_url": None, "job_role": "Engineer",
    }
    row.update(kw)
    return row


# ─── the block ───────────────────────────────────────────────────────────────


def test_a_member_line_carries_name_email_job_access_and_id(monkeypatch):
    monkeypatch.setattr(db_team, "list_company_members", lambda cid: [_member()])

    block = team_context.team_block("co-1")

    assert "THIS WORKSPACE'S TEAM" in block
    assert "Ada Lovelace" in block
    assert "ada@acme.test" in block
    assert "job: Engineer" in block
    assert "access: admin" in block
    assert "user id: u-1" in block


def test_the_block_says_what_it_cannot_be_crossed_with(monkeypatch):
    """Asked for "a table of each member and the number of PRDs they created",
    the answer went looking in Confluence/Drive/Jira and reported none found —
    to a workspace holding twelve of its own. Sprntly records no author on what
    it generates, so the block has to say the count is unanswerable AND that
    the connected sources are the wrong place to look."""
    monkeypatch.setattr(db_team, "list_company_members", lambda cid: [_member()])

    block = team_context.team_block("co-1")

    assert "does not record an author" in block
    assert "CANNOT" in block
    assert "no PRDs" in block


def test_an_unset_field_is_stated_not_dropped(monkeypatch):
    """A member with no profile still renders every field. A line that silently
    loses its email reads as a person who has none."""
    monkeypatch.setattr(
        db_team, "list_company_members",
        lambda cid: [_member(display_name=None, email=None, job_role=None, role=None)],
    )

    block = team_context.team_block("co-1")

    assert "(no name set)" in block
    assert "(no email on file)" in block
    assert "(no job role set)" in block
    assert "(no access level set)" in block
    assert "user id: u-1" in block


def test_the_order_is_stable_between_two_reads(monkeypatch):
    """The same question asked twice must not produce two differently-ordered
    lists — that reads as the assistant looking at different data each time."""
    rows = [
        _member(user_id="u-2", display_name="Zoe Ray", email="zoe@acme.test"),
        _member(user_id="u-1", display_name="Ada Lovelace"),
    ]
    monkeypatch.setattr(db_team, "list_company_members", lambda cid: list(rows))

    first = team_context.team_block("co-1")
    rows.reverse()
    second = team_context.team_block("co-1")

    assert first == second
    assert first.index("Ada Lovelace") < first.index("Zoe Ray")


def test_a_failed_read_renders_nothing_rather_than_an_empty_team(monkeypatch):
    """A block saying "you have no team" because a query timed out would be a
    confident lie about the user's own data."""
    def _boom(cid):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(db_team, "list_company_members", _boom)

    assert team_context.team_block("co-1") == ""
    assert team_context.team_block(None) == ""


def test_a_genuinely_empty_workspace_still_says_so(monkeypatch):
    """Nobody on the roster is a real state, and distinct from a failed read."""
    monkeypatch.setattr(db_team, "list_company_members", lambda cid: [])

    block = team_context.team_block("co-1")

    assert "THIS WORKSPACE'S TEAM" in block
    assert "nobody in it yet" in block


def test_a_truncated_roster_declares_the_truncation(monkeypatch):
    """No silent caps: a clipped list presented as complete is the one failure
    this block exists to prevent."""
    over = team_context._MAX_MEMBERS + 3
    monkeypatch.setattr(
        db_team, "list_company_members",
        lambda cid: [
            _member(user_id=f"u-{i}", display_name=f"Member {i:04d}")
            for i in range(over)
        ],
    )

    block = team_context.team_block("co-1")

    assert block.count("\n- ") == team_context._MAX_MEMBERS
    assert "+3 more members not shown" in block


# ─── the plan's verdict ──────────────────────────────────────────────────────


def test_the_pure_team_plan_is_team_only():
    assert qa._team_only_plan(Plan(action="answer", include_team=True)) is True


def test_any_other_grounding_keeps_the_full_compose():
    assert qa._team_only_plan(None) is False
    assert qa._team_only_plan(Plan(action="answer", include_team=False)) is False
    # "what has Dave shipped this week" — about the WORK, so the graph stays.
    assert qa._team_only_plan(
        Plan(action="answer", include_team=True, include_knowledge_graph=True)
    ) is False
    assert qa._team_only_plan(
        Plan(action="answer", include_team=True, sources=["slack"])
    ) is False
    assert qa._team_only_plan(
        Plan(action="answer", include_team=True, documents=["doc-1"])
    ) is False


def test_the_planner_flag_is_what_gates_the_read(monkeypatch):
    """A plan without the flag costs nothing — no roster row is read."""
    monkeypatch.setattr(team_context, "team_block", lambda cid: TEAM_BLOCK)

    assert qa._planned_team_context("co-1", Plan(action="answer")) == ""
    assert qa._planned_team_context(None, Plan(action="answer", include_team=True)) == ""
    assert "Ada Lovelace" in qa._planned_team_context(
        "co-1", Plan(action="answer", include_team=True)
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


def test_a_team_only_ask_reads_no_index_no_kg_no_corpus(
    isolated_settings, fake_llm, monkeypatch
):
    """The roster is the whole grounding: the three readers a wrong roster
    could come through are not consulted at all."""
    calls: list[str] = []
    monkeypatch.setattr(ask_runner, "load_corpus", _spy(calls, "corpus"))
    monkeypatch.setattr(ask_runner, "document_grounding", _spy(calls, "docs", ("", [])))
    monkeypatch.setattr(ask_runner, "_retrieve_kg_bundle", _spy(calls, "kg", None))
    fake_llm["payload"] = _payload()

    ask_runner.compose_ask_answer(
        "tailspin", "do you know my team members and their roles",
        enterprise_id="co-1",
        team_context_fn=lambda: TEAM_BLOCK, library_only=True,
    )

    assert calls == []
    call = fake_llm["calls"][0]
    assert "THIS WORKSPACE'S TEAM" in call["user"]
    assert "ada@acme.test" in call["user"]
    # The addendum that says how to read the block rides with it.
    assert "THIS WORKSPACE'S TEAM" in call["system"]


def test_a_prd_tab_ask_receives_the_team_block_too(
    isolated_settings, fake_llm, monkeypatch
):
    """"who owns this" is asked from beside a PRD as often as from the main
    chat — the library's reported failure, avoided in advance here."""
    monkeypatch.setattr(ask_runner, "document_grounding", lambda *a, **k: ("", []))
    fake_llm["payload"] = _payload()

    ask_runner.compose_ask_answer(
        "tailspin", "who on my team should own this?", enterprise_id="co-1",
        prd_context="CURRENT PRD CONTEXT\nThe document.",
        team_context_fn=lambda: TEAM_BLOCK, library_only=True,
    )

    call = fake_llm["calls"][0]
    assert "THIS WORKSPACE'S TEAM" in call["user"]
    assert "THIS WORKSPACE'S TEAM" in call["system"]
    # The PRD block keeps its slot (the cacheable prefix), unchanged.
    assert "CURRENT PRD CONTEXT" in (
        call["kwargs"].get("user_cacheable_prefix") or ""
    )


def test_both_own_record_blocks_ride_one_prompt(
    isolated_settings, fake_llm, monkeypatch
):
    """Library and team are separate parameters and separate sections; a
    question that wants both gets both, on either branch."""
    monkeypatch.setattr(ask_runner, "document_grounding", lambda *a, **k: ("", []))
    fake_llm["payload"] = _payload()

    ask_runner.compose_ask_answer(
        "tailspin", "who is here and what have we uploaded?", enterprise_id="co-1",
        prd_context="CURRENT PRD CONTEXT\nThe document.",
        library_context_fn=lambda: "=== THIS WORKSPACE'S SKILLS AND TEMPLATES ===",
        team_context_fn=lambda: TEAM_BLOCK, library_only=True,
    )

    call = fake_llm["calls"][0]
    assert "THIS WORKSPACE'S SKILLS AND TEMPLATES" in call["user"]
    assert "THIS WORKSPACE'S TEAM" in call["user"]


def test_an_ask_with_no_team_block_is_unchanged(
    isolated_settings, fake_llm, monkeypatch
):
    """The common case must not pay for or change anything."""
    monkeypatch.setattr(ask_runner, "document_grounding", lambda *a, **k: ("", []))
    fake_llm["payload"] = _payload()

    ask_runner.compose_ask_answer(
        "tailspin", "what are the requirements?", enterprise_id="co-1",
        prd_context="CURRENT PRD CONTEXT\nThe document.",
    )

    call = fake_llm["calls"][0]
    assert "THIS WORKSPACE'S TEAM" not in call["user"]
    assert "THIS WORKSPACE'S TEAM" not in call["system"]
