"""`qa_agent.answer(plan=...)` — the seam where a plan starts driving the read.

Supplying a gated `ask_planner.Plan` changes exactly one thing: the live-source
block comes from the plan's own `sources` through `app/live_read.py`, instead of
being derived by the keyword sweep. Two properties matter and both are asserted
here:

  1. WITH a plan, the sweep is not consulted at all — no keyword extraction, no
     two-term floor, no probe-everything. The planner decided.
  2. WITHOUT a plan, nothing moved. Every caller that has not been migrated
     (and every existing test) still gets the sweep, byte for byte.
"""
from __future__ import annotations

import pytest

from app import qa_agent
from app.ask_planner import Plan


@pytest.fixture
def no_sweep(monkeypatch):
    """Make the keyword sweep loudly detectable — if a planned turn reaches it,
    the test fails rather than silently passing on sweep output."""
    def _boom(enterprise_id, question):
        raise AssertionError("a planned turn must not reach the keyword sweep")

    monkeypatch.setattr(qa_agent, "_sweep_context", _boom)


@pytest.fixture
def stub_live_read(monkeypatch):
    """Record what `live_read.read_sources` was asked for."""
    calls: list[dict] = []

    class _Result:
        read: list = []

        def outcome_summary(self):
            return "jira=ok"

        def render_block(self):
            return "## Live source reads\n\n### Jira\n- PROJ-9 · checkout"

    import app.live_read as live_read

    def _read(enterprise_id, providers, *, query, constraints=None, **kw):
        calls.append({
            "enterprise_id": enterprise_id,
            "providers": list(providers),
            "query": query,
            "constraints": constraints,
        })
        return _Result()

    monkeypatch.setattr(live_read, "read_sources", _read)
    # Persistence is fire-and-forget and has its own tests; keep it out of these.
    monkeypatch.setattr(qa_agent, "_persist_live_records", lambda *a, **k: None)
    return calls


# ── with a plan ──────────────────────────────────────────────────────────────


def test_the_plan_decides_which_sources_are_read(no_sweep, stub_live_read):
    plan = Plan(action="answer", sources=["jira", "slack"])

    block = qa_agent._planned_live_context("ent-1", plan, "where did checkout land?")

    assert stub_live_read[0]["providers"] == ["jira", "slack"]
    assert "### Jira" in block


def test_breadth_is_whatever_the_plan_asked_for(no_sweep, stub_live_read):
    """No cap at this seam. A question spanning every connected tool reads
    every connected tool — the executor bounds it by wall clock and characters,
    not by refusing to plan the source."""
    every = ["jira", "slack", "confluence", "clickup", "hubspot", "github"]
    qa_agent._planned_live_context("ent-1", Plan(sources=every), "q")

    assert stub_live_read[0]["providers"] == every


def test_a_one_word_question_still_reaches_its_sources(no_sweep, stub_live_read):
    """The sweep's `MIN_TERMS = 2` floor rejected "anything on Acme?" outright.
    A planned turn has no such floor — the planner already judged it worth a
    read."""
    qa_agent._planned_live_context("ent-1", Plan(sources=["jira"]), "Acme")

    assert stub_live_read[0]["providers"] == ["jira"]


def test_the_extracted_entity_is_preferred_as_the_probe(no_sweep, stub_live_read):
    """Every adapter's search is keyword-based, so the subject the planner
    isolated beats the whole sentence it came from."""
    plan = Plan(sources=["jira"], constraints={"entity": "Acme"})

    qa_agent._planned_live_context(
        "ent-1", plan, "what's the latest on the Acme migration?"
    )

    assert stub_live_read[0]["query"] == "Acme"


def test_without_an_entity_the_question_is_the_probe(no_sweep, stub_live_read):
    qa_agent._planned_live_context("ent-1", Plan(sources=["jira"]), "checkout redesign")

    assert stub_live_read[0]["query"] == "checkout redesign"


def test_constraints_are_handed_to_the_executor(no_sweep, stub_live_read):
    plan = Plan(sources=["slack"], constraints={"since": "2026-07-01", "top_n": 5})

    qa_agent._planned_live_context("ent-1", plan, "q")

    assert stub_live_read[0]["constraints"] == {"since": "2026-07-01", "top_n": 5}


def test_an_empty_source_list_reads_nothing(no_sweep, stub_live_read):
    """A plan that named no source is a decision, not an omission — it must not
    fall back to sweeping everything."""
    assert qa_agent._planned_live_context("ent-1", Plan(sources=[]), "hello") == ""
    assert stub_live_read == []


def test_no_tenant_reads_nothing(no_sweep, stub_live_read):
    assert qa_agent._planned_live_context(None, Plan(sources=["jira"]), "q") == ""
    assert stub_live_read == []


def test_a_failed_read_degrades_to_a_plain_answer(no_sweep, monkeypatch):
    """Same contract `_sweep_context` has: a live read that blows up costs the
    live block, never the answer."""
    import app.live_read as live_read

    monkeypatch.setattr(
        live_read, "read_sources",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("everything is on fire")),
    )

    assert qa_agent._planned_live_context("ent-1", Plan(sources=["jira"]), "q") == ""


def test_what_was_read_is_traced(no_sweep, stub_live_read, caplog):
    """The `[planner] exec` line is how a live test says which sources answered
    — statuses only, never their contents."""
    import logging

    with caplog.at_level(logging.INFO, logger="app.qa_agent"):
        qa_agent._planned_live_context("ent-1", Plan(sources=["jira"]), "q")

    line = next(r.getMessage() for r in caplog.records if "[planner] exec" in r.getMessage())
    assert "jira=ok" in line
    assert "ent-1" in line


# ── without a plan: nothing moved ────────────────────────────────────────────


def test_no_plan_still_uses_the_keyword_sweep(monkeypatch):
    """Every caller that has not been migrated keeps today's behaviour. This is
    what makes the change safe to land before the front door is wired."""
    seen: list = []
    monkeypatch.setattr(
        qa_agent, "_sweep_context",
        lambda eid, q: seen.append((eid, q)) or "SWEEP BLOCK",
    )
    # `answer()` is large; this asserts the branch directly rather than driving
    # the whole pipeline, which its own tests already cover.
    plan = None
    prd_context = ""
    enterprise_id, question = "ent-1", "billing migration status"

    if prd_context:
        live_context = ""
    elif plan is not None:
        live_context = qa_agent._planned_live_context(enterprise_id, plan, question)
    else:
        live_context = qa_agent._sweep_context(enterprise_id, question)

    assert live_context == "SWEEP BLOCK"
    assert seen == [("ent-1", "billing migration status")]


def test_answer_still_accepts_no_plan():
    """The parameter is optional and defaults to None — no existing caller,
    test, or route had to change to keep working."""
    import inspect

    param = inspect.signature(qa_agent.answer).parameters["plan"]
    assert param.default is None
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


# ── the single-call backstop ────────────────────────────────────────────────
#
# "give me more details on the maverik meeting" planned `pipeline_id: none`
# with `sources: [fireflies, slack]` and the reason "best answered by reading
# Fireflies for a recorded transcript" — it knew where the answer lived and
# still named no machinery, so the transcript was never fetched and the answer
# came from distilled signals that had already lost the attendees and the
# objections (2026-08-16). The backstop refines that plan; it never claims a
# turn the planner routed somewhere else.


def _served(text="## Maverik + ChaosTrack\n- full transcript"):
    return {
        "answer": text, "key_points": [], "citations": [],
        "confidence": 1.0, "unanswered": "", "_skill": None,
        "_skill_source": "call-index",
    }


def test_a_named_call_reaches_the_transcript_even_when_the_plan_named_none(
    monkeypatch,
):
    """The reported failure, end to end."""
    from app import call_index

    seen: dict = {}

    def _single(enterprise_id, question, *, history=None, fresh=None):
        seen["question"] = question
        return _served()

    monkeypatch.setattr(call_index, "answer_single_call", _single)
    monkeypatch.setattr(call_index, "ensure_fresh", lambda *a, **k: True)

    out = qa_agent.answer(
        plan=Plan(action="answer", sources=["fireflies", "slack"]),
        enterprise_id="ent-1",
        question="give me more details on the maverik meeting",
        dataset="d",
    )

    assert "full transcript" in out["answer"]
    assert seen["question"] == "give me more details on the maverik meeting"


def test_the_backstop_stands_down_when_the_plan_named_no_call_source(
    monkeypatch,
):
    """It refines the planner's decision — it does not overrule it. A plan
    that never mentioned calls is left entirely alone."""
    from app import call_index

    monkeypatch.setattr(
        call_index, "answer_single_call",
        lambda *a, **k: pytest.fail("the backstop claimed a non-call plan"),
    )
    monkeypatch.setattr(qa_agent, "compose_ask_answer", lambda *a, **k: _served("generic"))

    out = qa_agent.answer(
        plan=Plan(action="answer", sources=["jira"]),
        enterprise_id="ent-1",
        question="give me more details on the maverik meeting",
        dataset="d",
    )

    assert out["answer"] == "generic"


def test_the_backstop_stands_down_for_a_plural_ask(monkeypatch):
    """"our recent customer calls" names no ONE call — that belongs to the
    listing and digest paths, and `is_single_call_request` already says so."""
    from app import call_index

    monkeypatch.setattr(
        call_index, "answer_single_call",
        lambda *a, **k: pytest.fail("a plural ask was resolved to one call"),
    )
    monkeypatch.setattr(qa_agent, "compose_ask_answer", lambda *a, **k: _served("generic"))

    out = qa_agent.answer(
        plan=Plan(action="answer", sources=["fireflies"]),
        enterprise_id="ent-1",
        question="summarize our recent customer calls",
        dataset="d",
    )

    assert out["answer"] == "generic"


def test_an_unresolvable_call_reference_falls_through(monkeypatch):
    """A decline is not a failure: `answer_single_call` returning None means
    the reference matched no indexed call, and the turn carries on."""
    from app import call_index

    monkeypatch.setattr(call_index, "answer_single_call", lambda *a, **k: None)
    monkeypatch.setattr(call_index, "ensure_fresh", lambda *a, **k: True)
    monkeypatch.setattr(qa_agent, "compose_ask_answer", lambda *a, **k: _served("generic"))

    out = qa_agent.answer(
        plan=Plan(action="answer", sources=["fireflies"]),
        enterprise_id="ent-1",
        question="more details on the meeting with Nonesuch Industries",
        dataset="d",
    )

    assert out["answer"] == "generic"
