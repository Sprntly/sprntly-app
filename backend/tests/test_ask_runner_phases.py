"""Real progress-phase emissions on the common direct-answer path.

`compose_ask_answer` announces two real pipeline boundaries to the chat wait
surface: retrieval start and answer dispatch (which covers the model's prefill
window). These tests exercise only that control flow + the emissions — every
heavy leg (corpus, embedding, retrieval, the LLM call) is mocked, so no network,
no embeddings, and no real Anthropic call is involved.
"""
from __future__ import annotations

import pytest

from app import ask_runner


class _FakeCorpus:
    docs = ["doc"]

    def joined(self) -> str:
        return "corpus body"


_ANSWER = {
    "answer": "ok",
    "key_points": [],
    "citations": [],
    "confidence": 0.5,
    "unanswered": "",
}


@pytest.fixture
def stub_compose_deps(monkeypatch: pytest.MonkeyPatch):
    """Mock every heavy leg of compose_ask_answer so the test drives only its
    control flow and phase emissions."""
    monkeypatch.setattr(ask_runner, "load_corpus", lambda dataset: _FakeCorpus())
    monkeypatch.setattr(
        ask_runner, "_resolve_question_embedding", lambda eid, q: ([0.1], False)
    )
    monkeypatch.setattr(ask_runner, "document_grounding", lambda *a, **k: ("", []))
    monkeypatch.setattr(ask_runner, "_retrieve_kg_bundle", lambda *a, **k: None)
    monkeypatch.setattr(ask_runner, "company_facts_block", lambda eid: "")
    monkeypatch.setattr(ask_runner, "call_json", lambda **k: dict(_ANSWER))
    # Force the plain call_json branch (not answer-first) deterministically.
    monkeypatch.setattr("app.answer_first.enabled", lambda: False)


def test_emits_retrieval_then_dispatch_phase(stub_compose_deps):
    seen: list[str] = []
    out = ask_runner.compose_ask_answer(
        "northwind", "What is our pricing?", enterprise_id=None, on_phase=seen.append
    )
    assert out["answer"] == "ok"
    # Both real boundaries fired, in order: retrieval start, then answer dispatch.
    assert seen == [
        "Searching your connected sources…",
        "Putting your answer together…",
    ]


def test_no_sink_is_a_safe_noop(stub_compose_deps):
    # No on_phase wired → must never raise, and still return the answer.
    out = ask_runner.compose_ask_answer("northwind", "hi", enterprise_id=None)
    assert out["answer"] == "ok"


def test_failing_sink_never_breaks_the_answer(stub_compose_deps):
    # A display publish that raises must be swallowed (best-effort transport).
    def _boom(_label: str) -> None:
        raise RuntimeError("sink down")

    out = ask_runner.compose_ask_answer(
        "northwind", "hi", enterprise_id=None, on_phase=_boom
    )
    assert out["answer"] == "ok"


def test_retrieval_phase_skipped_on_prd_grounded_path(stub_compose_deps):
    # PRD-grounded asks skip corpus/KG retrieval, so boundary 1 must NOT claim a
    # source search there — only the answer-dispatch phase fires.
    seen: list[str] = []
    ask_runner.compose_ask_answer(
        "northwind", "summarize this", enterprise_id=None,
        prd_context="THE PRD", on_phase=seen.append,
    )
    assert seen == ["Putting your answer together…"]
