"""Tests for app.agents.qa_test_cases — Given/When/Then test SCENARIOS.

The QA agent now runs through the vendored `test-scenario-builder` skill (its
SKILL.md is the METHOD layer) and emits the Sprntly `:::qa-scenarios` output
contract the frontend renders. These pin the skill binding, the output
contract framing, and the input assembly without calling a real model.
"""
from __future__ import annotations

import asyncio

from app.graph.gateway import LLMResult


def _llm_result(output, model="claude-sonnet-4-6"):
    return LLMResult(
        output=output, model=model, prompt_version="qa-test-cases-v2",
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
        stop_reason="end_turn",
    )


_PRD = {
    "id": 7,
    "title": "First-Handoff Wizard",
    "payload_md": "## Requirements\n- Admin can enable guest deal alerts in one click",
    "llm_part": "## Part B\nWHEN admin enables alerts THEN ...",
}

_SCENARIOS_DOC = (
    "# QA Test Scenarios — First-Handoff Wizard\n\n"
    "Covers enablement; the riskiest area is duplicate guest records.\n\n"
    ':::qa-scenarios\n{"scenarios": [{"id": "QA-001", "group": "happy", '
    '"title": "Admin enables alerts", "given": "admin on settings", '
    '"when": "clicks enable", "then": "alerts on", "traces": "R1", '
    '"risk": "high"}], "open_questions": []}\n:::\n'
)


def test_binds_test_scenario_builder_skill(monkeypatch):
    from app.agents import qa_test_cases

    captured = {}

    def fake_llm(**kw):
        captured.update(kw)
        return _llm_result(_SCENARIOS_DOC)

    monkeypatch.setattr(qa_test_cases, "llm_call", fake_llm)
    md = qa_test_cases.generate_qa_test_cases_sync("ent-A", _PRD, evidence_md="e")

    # The test-scenario-builder skill is bound as the METHOD layer.
    assert captured["skill"] == "test-scenario-builder"
    assert captured["agent"] == "qa_test_cases"
    assert captured["prompt_version"] == "qa-test-cases-v2"
    # The Sprntly output contract (the :::qa-scenarios block) is in the system.
    assert ":::qa-scenarios" in captured["system"]
    assert "happy" in captured["system"] and "failure" in captured["system"]
    # The PRD (Part A + Part B) is fed as the spec to verify.
    assert "Admin can enable guest deal alerts" in captured["input"]
    assert "Part B" in captured["input"]
    # Output is passed through unchanged for the renderer.
    assert md == _SCENARIOS_DOC


def test_binding_survives_the_skill_no_longer_being_vendored():
    """`test-scenario-builder` is no longer a vendored skill, and the QA agent's
    `skill="test-scenario-builder"` binding must DEGRADE, not raise.

    It was one of ~78 chat-routable methods and is not on the nine-skill
    keep-list, so its directory is gone. The binding stays at the call site —
    it is how the decision log attributes the call — and
    `gateway._build_method_prefix` answers a missing directory with an empty
    method block plus a `+bare` version suffix. This is the ACCEPTED degradation
    (the generated scenarios are now model-shaped rather than method-shaped);
    what would not be acceptable is the 500 an intolerant lookup produced."""
    from app.graph.gateway import _build_method_prefix
    from app.skills.loader import list_skills

    assert "test-scenario-builder" not in list_skills()
    block, suffix = _build_method_prefix("test-scenario-builder", None)
    assert block == ""
    assert suffix == "+bare"


def test_build_input_frames_prd_as_spec():
    from app.agents.qa_test_cases import _build_input

    out = _build_input(_PRD, evidence_md="trail", clickup_context="task")
    assert "requirements and acceptance criteria" in out.lower()
    assert "Admin can enable guest deal alerts" in out  # Part A
    assert "WHEN admin enables alerts" in out            # Part B
    assert "trail" in out and "task" in out


def test_async_persists_scenarios_doc(monkeypatch):
    from app.agents import qa_test_cases

    completed = {}
    monkeypatch.setattr(qa_test_cases, "llm_call",
                        lambda **kw: _llm_result(_SCENARIOS_DOC))
    monkeypatch.setattr(qa_test_cases, "complete_doc",
                        lambda doc_id, title, md: completed.update(
                            doc_id=doc_id, title=title, md=md))
    monkeypatch.setattr(qa_test_cases, "fail_doc",
                        lambda *a, **k: completed.update(failed=True))

    asyncio.run(qa_test_cases.generate_qa_test_cases(99, "ent-A", _PRD))

    assert completed.get("failed") is None
    assert completed["doc_id"] == 99
    assert completed["title"].startswith("QA Test Scenarios — First-Handoff")
    assert ":::qa-scenarios" in completed["md"]
