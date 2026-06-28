"""Unit tests for the unified Q&A agent router + answer dispatch.

`route()` and `answer()` flow through the gateway's `llm_call` (imported into
the qa_agent namespace) and `compose_ask_answer`; these tests patch those refs
directly so no Anthropic / Supabase call is made.
"""
from __future__ import annotations

import app.qa_agent as qa


class _Result:
    def __init__(self, output):
        self.output = output


def _route_out(skill_id="none", confidence=0.0, reason="x"):
    return _Result({"skill_id": skill_id, "confidence": confidence, "reason": reason})


def _answer_out():
    return _Result(
        {"answer": "ok", "key_points": [], "citations": [], "confidence": 0.9, "unanswered": ""}
    )


# ── routing ──────────────────────────────────────────────────────────────────

def test_slash_fastpath(monkeypatch):
    calls = []
    monkeypatch.setattr(qa, "llm_call", lambda **k: calls.append(k) or _route_out())
    d = qa.route("/prioritize rank these", enterprise_id="ent")
    assert d.skill_id == "prioritize" and d.source == "slash"
    assert calls == []  # fast-path: no LLM


def test_slash_nonroutable_falls_through(monkeypatch):
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())  # router says none
    d = qa.route("/business-context build it", enterprise_id="ent")
    assert d.skill_id != "business-context"  # non-routable, never slash-selected


def test_regex_fastpath(monkeypatch):
    calls = []
    monkeypatch.setattr(qa, "llm_call", lambda **k: calls.append(k) or _route_out())
    d = qa.route("generate a PRD for onboarding", enterprise_id="ent")
    assert d.skill_id == "prd-author" and d.source == "regex"
    assert calls == []  # regex short-circuits the LLM router


def test_llm_router_selects(monkeypatch):
    monkeypatch.setattr(
        qa, "llm_call", lambda **k: _route_out("retention-churn", 0.82, "churn")
    )
    d = qa.route("why do users stop logging in after a couple weeks?", enterprise_id="ent")
    assert d.skill_id == "retention-churn" and d.source == "llm"


def test_llm_router_below_threshold_is_direct(monkeypatch):
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out("roadmap", 0.3, "weak"))
    d = qa.route("hello there", enterprise_id="ent")
    assert d.skill_id is None


def test_llm_router_rejects_nonroutable(monkeypatch):
    # "verify …" hits the fact-check regex, but fact-check is non-routable, so
    # the regex fast-path is skipped; even if the LLM names it, it's rejected.
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out("fact-check", 0.99, "x"))
    d = qa.route("verify these market claims", enterprise_id="ent")
    assert d.skill_id is None


def test_llm_router_failure_is_direct(monkeypatch):
    def boom(**k):
        raise RuntimeError("router down")

    monkeypatch.setattr(qa, "llm_call", boom)
    d = qa.route("some ambiguous question about strategy", enterprise_id="ent")
    assert d.skill_id is None and d.source == "none"


# ── answer dispatch ────────────────────────────────────────────────────────────

def test_answer_skill_path_uses_sonnet(monkeypatch):
    # user-stories is a non-script, non-heavy skill → single-shot gateway call.
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())
    out = qa.answer(
        enterprise_id="ent", question="write user stories for checkout", dataset="acme"
    )
    assert out["_skill"] == "user-stories"
    assert captured["skill"] == "user-stories"
    assert captured["model"] == qa.ANSWER_MODEL


def test_answer_heavy_skill_escalates_to_opus(monkeypatch):
    # competitive-intelligence-review is the remaining HEAVY skill. It's also
    # cost-gated, so pin it to skip the confirm-gate and reach the answer path.
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())
    out = qa.answer(enterprise_id="ent", question="size up our competitors",
                    dataset="acme", pinned_skill="competitive-intelligence-review")
    assert out["_skill"] == "competitive-intelligence-review"
    assert captured["model"] == qa.HEAVY_MODEL


def test_answer_prd_author_stays_on_sonnet(monkeypatch):
    # The deep reasoning happens upstream in the KG + weekly brief; the PRD
    # composes off that material and answers on the default (sonnet) model.
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())
    out = qa.answer(enterprise_id="ent", question="write a PRD for billing", dataset="acme")
    assert out["_skill"] == "prd-author"
    assert captured["model"] == qa.ANSWER_MODEL


def test_answer_direct_path(monkeypatch):
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())  # router → none
    monkeypatch.setattr(
        qa,
        "compose_ask_answer",
        lambda dataset, q, *, enterprise_id: {
            "answer": "generic", "key_points": [], "citations": [],
            "confidence": 0.5, "unanswered": "",
        },
    )
    out = qa.answer(enterprise_id="ent", question="what happened last week", dataset="acme")
    assert out["answer"] == "generic" and "_skill" not in out


def test_answer_pinned_skill_skips_routing(monkeypatch):
    purposes = []
    monkeypatch.setattr(
        qa, "llm_call", lambda **k: purposes.append(k.get("purpose")) or _answer_out()
    )
    out = qa.answer(
        enterprise_id="ent", question="anything", dataset="acme", pinned_skill="roadmap"
    )
    assert out["_skill"] == "roadmap"
    assert "route" not in purposes  # router never consulted


def test_answer_history_folded_into_skill_input(monkeypatch):
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())
    qa.answer(
        enterprise_id="ent",
        question="turn that into a roadmap",
        dataset="acme",
        pinned_skill="roadmap",  # non-script skill → single-shot, captures input
        history=[{"role": "user", "content": "here are 3 features: A, B, C"}],
    )
    assert "here are 3 features" in captured["input"]


# ── script skills run via the tool loop (on our infra) ────────────────────────

def test_script_skill_uses_tool_loop_not_single_shot(monkeypatch):
    """A script skill (prioritize) answers through run_tool_loop, not llm_call."""
    loop_calls = {}
    single_shot = []
    monkeypatch.setattr(
        qa, "run_tool_loop", lambda **k: loop_calls.update(k) or "Ranked: A > B"
    )
    monkeypatch.setattr(qa, "llm_call", lambda **k: single_shot.append(k) or _answer_out())
    out = qa.answer(enterprise_id="ent", question="prioritize A, B with RICE", dataset="acme")
    assert out["_skill"] == "prioritize"
    assert out["answer"] == "Ranked: A > B"
    assert single_shot == []  # did NOT take the single-shot path
    # the prioritize script tool was offered to the loop
    assert loop_calls["tools"][0]["name"] == "prioritize_score"


# ── CIR confirm gate ──────────────────────────────────────────────────────────

def test_cost_gated_skill_returns_confirmation(monkeypatch):
    out = qa.answer(
        enterprise_id="ent",
        question="/competitive-intelligence-review Linear, Jira, Asana",
        dataset="acme",
    )
    assert out["type"] == "needs_confirmation"
    assert out["skill"] == "competitive-intelligence-review"
    assert {o["id"] for o in out["options"]} == {"quick", "full"}


def test_verify_pass_off_by_default(monkeypatch):
    monkeypatch.setattr(qa, "llm_call", lambda **k: _answer_out())
    out = qa.answer(enterprise_id="ent", question="write a PRD for billing", dataset="acme")
    assert "_verification" not in out  # disabled → untouched


def test_verify_pass_when_enabled_annotates(monkeypatch):
    calls = []

    def fake_llm(**k):
        calls.append(k.get("purpose"))
        if k.get("purpose") == "fact_check":
            return _Result({"verdict": "grounded"})
        return _answer_out()

    monkeypatch.setattr(qa, "llm_call", fake_llm)
    monkeypatch.setattr(qa, "VERIFY_ENABLED", True)
    out = qa.answer(enterprise_id="ent", question="write a PRD for billing", dataset="acme")
    assert out["_verification"] == {"verdict": "grounded"}
    assert "fact_check" in calls


def test_cost_gated_skill_runs_when_pinned(monkeypatch):
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())
    out = qa.answer(
        enterprise_id="ent",
        question="full review please",
        dataset="acme",
        pinned_skill="competitive-intelligence-review",
    )
    assert out.get("type") != "needs_confirmation"
    assert out["_skill"] == "competitive-intelligence-review"
    assert captured["model"] == qa.HEAVY_MODEL  # CIR is heavy → opus
