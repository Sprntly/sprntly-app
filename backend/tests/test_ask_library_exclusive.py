"""Library questions are answered FROM the library — and from nothing else.

The reported failure, twice: "can you list the templates i have" answered with
Confluence pages titled "Template - …". The planner's half was already right
(include_library=true, kg=false, documents=[], sources=[] — verified in the
live logs); the answer stage lost it two ways:

  * the library thunk was only ever built for the NO-PRD branch, and the
    question was asked from a PRD tab — the block the planner requested never
    reached the model;
  * the DOCUMENT INDEX rode the prompt regardless of the plan, and its catalog
    rows for wiki pages titled "Template - …" (name + summary) read exactly
    like format descriptions. The library block's own warning kept losing to
    an index sitting beside it.

The fix is `library_only`: when the plan wants the library and named no other
grounding, the compose skips corpus, KG retrieval AND document grounding, and
the library block (plus its addendum) is the answer's whole world. These tests
pin each half.
"""
from __future__ import annotations

import app.ask_runner as ask_runner
import app.qa_agent as qa
from app.ask_planner import Plan

LIBRARY_BLOCK = (
    "=== THIS WORKSPACE'S SKILLS AND TEMPLATES ===\n"
    "TEMPLATES here means these uploaded formats and nothing else.\n"
    "PRD templates:\n- Quitino — ACTIVE"
)


def _spy(calls, name, result=None):
    def _fn(*a, **k):
        calls.append(name)
        return result

    return _fn


def _payload():
    return {
        "answer": "x", "key_points": [], "citations": [],
        "confidence": 0.5, "unanswered": "",
    }


# ─── the compose: what a library-only ask reads, and what it never touches ───


def test_a_library_only_ask_reads_no_index_no_kg_no_corpus(
    isolated_settings, fake_llm, monkeypatch
):
    calls: list[str] = []
    monkeypatch.setattr(ask_runner, "load_corpus", _spy(calls, "corpus"))
    monkeypatch.setattr(
        ask_runner, "document_grounding", _spy(calls, "docs", ("", []))
    )
    monkeypatch.setattr(
        ask_runner, "_retrieve_kg_bundle", _spy(calls, "kg", None)
    )
    fake_llm["payload"] = _payload()

    ask_runner.compose_ask_answer(
        "asurion", "can you list the templates i have", enterprise_id="co-1",
        library_context_fn=lambda: LIBRARY_BLOCK, library_only=True,
    )

    # The library block is the whole grounding: none of the three readers the
    # contamination came through was even consulted.
    assert calls == []
    user = fake_llm["calls"][0]["user"]
    system = fake_llm["calls"][0]["system"]
    assert "THIS WORKSPACE'S SKILLS AND TEMPLATES" in user
    assert "Quitino" in user
    # The addendum that says how to read the block rides with it.
    assert "THIS WORKSPACE'S SKILLS AND TEMPLATES" in system


def test_a_mixed_plan_keeps_every_reader_it_asked_for(
    isolated_settings, fake_llm, monkeypatch
):
    """include_library WITH the knowledge graph ("which of my templates fits
    last week's feedback") is not library-only — nothing narrows."""
    calls: list[str] = []
    monkeypatch.setattr(
        ask_runner, "document_grounding", _spy(calls, "docs", ("", []))
    )
    monkeypatch.setattr(
        ask_runner, "_retrieve_kg_bundle", _spy(calls, "kg", None)
    )
    fake_llm["payload"] = _payload()

    ask_runner.compose_ask_answer(
        "asurion", "which template fits the billing feedback?",
        enterprise_id="co-1",
        library_context_fn=lambda: LIBRARY_BLOCK, library_only=False,
    )

    assert "docs" in calls
    assert "kg" in calls
    assert "Quitino" in fake_llm["calls"][0]["user"]


def test_a_prd_tab_ask_still_receives_the_library_block(
    isolated_settings, fake_llm, monkeypatch
):
    """The reported failure's exact shape: asked from a PRD tab. The block now
    rides the PRD branch too — in the uncached user turn, so the per-PRD
    cacheable prefix stays byte-stable — with its addendum on the system
    prompt, and the document index stays out."""
    calls: list[str] = []
    monkeypatch.setattr(
        ask_runner, "document_grounding", _spy(calls, "docs", ("", []))
    )
    fake_llm["payload"] = _payload()

    ask_runner.compose_ask_answer(
        "asurion", "can you list the templates i have", enterprise_id="co-1",
        prd_context="CURRENT PRD CONTEXT\nThe Quitino-formatted PRD.",
        library_context_fn=lambda: LIBRARY_BLOCK, library_only=True,
    )

    assert calls == []
    call = fake_llm["calls"][0]
    assert "THIS WORKSPACE'S SKILLS AND TEMPLATES" in call["user"]
    assert "THIS WORKSPACE'S SKILLS AND TEMPLATES" in call["system"]
    # The PRD block keeps its slot (the cacheable prefix), unchanged.
    assert "CURRENT PRD CONTEXT" in (
        call["kwargs"].get("user_cacheable_prefix") or ""
    )


def test_a_prd_tab_ask_without_a_library_block_is_byte_identical_to_before(
    isolated_settings, fake_llm, monkeypatch
):
    """The common case — a plain PRD-tab question — must not pay for or change
    anything: no library section, no addendum, question-only user template."""
    monkeypatch.setattr(
        ask_runner, "document_grounding", lambda *a, **k: ("", [])
    )
    fake_llm["payload"] = _payload()

    ask_runner.compose_ask_answer(
        "asurion", "what are the requirements?", enterprise_id="co-1",
        prd_context="CURRENT PRD CONTEXT\nThe document.",
    )

    call = fake_llm["calls"][0]
    assert "THIS WORKSPACE'S SKILLS AND TEMPLATES" not in call["user"]
    assert "THIS WORKSPACE'S SKILLS AND TEMPLATES" not in call["system"]


# ─── the plan's verdict ──────────────────────────────────────────────────────


def test_the_pure_library_plan_is_library_only():
    plan = Plan(
        action="answer", include_library=True, include_knowledge_graph=False,
    )
    assert qa._library_only_plan(plan) is True


def test_any_other_grounding_keeps_the_full_compose():
    assert qa._library_only_plan(None) is False
    assert qa._library_only_plan(
        Plan(action="answer", include_library=False, include_knowledge_graph=False)
    ) is False
    # The mixed question: library + KG.
    assert qa._library_only_plan(
        Plan(action="answer", include_library=True, include_knowledge_graph=True)
    ) is False
    # A named document or source is other grounding too.
    assert qa._library_only_plan(
        Plan(action="answer", include_library=True,
             include_knowledge_graph=False, documents=["doc-1"])
    ) is False
    assert qa._library_only_plan(
        Plan(action="answer", include_library=True,
             include_knowledge_graph=False, sources=["slack"])
    ) is False
