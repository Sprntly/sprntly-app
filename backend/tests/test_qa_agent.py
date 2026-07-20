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


def _route_out(skill_id="none", confidence=0.0, reason="x", in_scope=None):
    out = {"skill_id": skill_id, "confidence": confidence, "reason": reason}
    if in_scope is not None:
        out["in_scope"] = in_scope
    return _Result(out)


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


# ── out-of-scope gate ────────────────────────────────────────────────────────

def test_route_out_of_scope_flag(monkeypatch):
    monkeypatch.setattr(
        qa, "llm_call", lambda **k: _route_out("none", 0.9, "trivia", in_scope=False)
    )
    d = qa.route("who won the champions league final?", enterprise_id="ent")
    assert d.skill_id is None and d.source == "out_of_scope"


def test_route_missing_in_scope_fails_open(monkeypatch):
    # Old-shape router output (no in_scope field) must fall through to the
    # direct path, not the refusal — the gate only fires on an explicit False.
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out("none", 0.0))
    d = qa.route("what happened last week", enterprise_id="ent")
    assert d.source == "none"


def test_route_skill_match_wins_over_scope_flag(monkeypatch):
    # A confident routable-skill match is in-scope by construction, even if the
    # router contradicts itself on the flag.
    monkeypatch.setattr(
        qa, "llm_call",
        lambda **k: _route_out("retention-churn", 0.85, "churn", in_scope=False),
    )
    d = qa.route("why do users churn?", enterprise_id="ent")
    assert d.skill_id == "retention-churn" and d.source == "llm"


def test_answer_out_of_scope_returns_canned(monkeypatch):
    monkeypatch.setattr(
        qa, "llm_call", lambda **k: _route_out("none", 0.9, "weather", in_scope=False)
    )
    def _no_direct(*a, **k):
        raise AssertionError("compose_ask_answer must not run for out-of-scope")
    monkeypatch.setattr(qa, "compose_ask_answer", _no_direct)
    out = qa.answer(
        enterprise_id="ent", question="what's the weather in tokyo?", dataset="acme"
    )
    assert out["answer"] == qa.OUT_OF_SCOPE_MESSAGE
    assert out["type"] == "out_of_scope"
    assert out["key_points"] == [] and out["citations"] == []
    assert out["_skill_source"] == "scope_gate"


def test_answer_pinned_skill_bypasses_scope_gate(monkeypatch):
    # A pinned follow-up has already chosen a PM skill — the router (and its
    # scope flag) is never consulted.
    monkeypatch.setattr(
        qa, "llm_call", lambda **k: _answer_out()
    )
    out = qa.answer(
        enterprise_id="ent", question="anything", dataset="acme",
        pinned_skill="user-stories",
    )
    assert out["_skill"] == "user-stories"


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


def test_answer_intercepts_call_digest_before_routing(monkeypatch):
    # "summarize the customer calls from last week" must short-circuit to the
    # on-demand digest path, NOT flow through the generic skill router.
    import app.call_digest as cd

    monkeypatch.setattr(cd, "answer", lambda **k: {"answer": "digest", "_skill_source": "call-digest"})
    router_calls = []
    monkeypatch.setattr(qa, "llm_call", lambda **k: router_calls.append(k) or _route_out())
    out = qa.answer(
        enterprise_id="ent", question="summarize the customer calls from last week", dataset="acme"
    )
    assert out["_skill_source"] == "call-digest"
    assert router_calls == []  # never reached the router/answer LLM


def test_answer_voc_request_diverts_to_digest_when_source_connected(monkeypatch):
    # A bare "voice of customer report" (no call-noun, so is_call_digest misses
    # it) must divert to the live digest when a call source IS connected —
    # instead of the corpus-less skill answer that wrongly reports "no sources".
    import app.call_digest as cd

    monkeypatch.setattr(cd, "has_call_source", lambda cid: True)
    monkeypatch.setattr(cd, "answer", lambda **k: {"answer": "digest", "_skill_source": "call-digest"})
    router_calls = []
    monkeypatch.setattr(qa, "llm_call", lambda **k: router_calls.append(k) or _route_out())
    out = qa.answer(enterprise_id="ent", question="give me a voice of customer report", dataset="acme")
    assert out["_skill_source"] == "call-digest"
    assert router_calls == []  # never reached the router/answer LLM


def test_answer_voc_request_falls_through_when_no_source(monkeypatch):
    # With NO call source connected, the same bare request must fall through to
    # the normal skill route (which explains what to connect), NOT the digest.
    import app.call_digest as cd

    monkeypatch.setattr(cd, "has_call_source", lambda cid: False)
    def _no_digest(**k):
        raise AssertionError("call_digest.answer must not run when no source is connected")
    monkeypatch.setattr(cd, "answer", _no_digest)
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())
    out = qa.answer(enterprise_id="ent", question="give me a voice of customer report", dataset="acme")
    assert out["_skill"] == "voice-of-customer-report"  # regex fast-path → skill route


def test_answer_pinned_skill_bypasses_call_digest(monkeypatch):
    # A pinned follow-up wins even if the text looks like a call digest.
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())
    out = qa.answer(enterprise_id="ent", question="summarize the customer calls",
                    dataset="acme", pinned_skill="user-stories")
    assert out["_skill"] == "user-stories"


def test_answer_direct_path(monkeypatch):
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())  # router → none
    monkeypatch.setattr(
        qa,
        "compose_ask_answer",
        lambda dataset, q, *, enterprise_id, prd_context="": {
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


# ── KG grounding of the single-shot skill answer ──────────────────────────────

def test_single_shot_grounds_skill_on_kg_when_present(monkeypatch):
    """A generic skill (prd-author) is handed the tenant's KG bundle so it has
    real signal to work from — no more corpus-less "not enough signal" refusal."""
    captured = {}
    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda eid, q: {"signals": [1], "themes": []})
    import app.graph.retrieval as retrieval
    monkeypatch.setattr(
        retrieval, "render_context_section", lambda b: "LIVE CONTEXT FROM CONNECTED SOURCES\n- churn up 12%"
    )
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())

    out = qa.answer(enterprise_id="ent", question="write a PRD for billing", dataset="acme")

    assert out["_skill"] == "prd-author"
    assert "LIVE CONTEXT FROM CONNECTED SOURCES" in captured["input"]  # KG folded in
    assert "churn up 12%" in captured["input"]
    assert qa.ASK_SYSTEM_KG_ADDENDUM in captured["system"]  # model told to treat it as evidence
    assert captured["input"].rstrip().endswith("Question: write a PRD for billing")


def test_single_shot_stays_corpus_less_when_kg_empty(monkeypatch):
    """No tenant signal (empty KG / no company / read error) → the pre-fix path:
    no KG block, no KG addendum. Preserves behaviour for signal-less tenants."""
    captured = {}
    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda eid, q: None)
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())

    out = qa.answer(enterprise_id="ent", question="write a PRD for billing", dataset="acme")

    assert out["_skill"] == "prd-author"
    assert "LIVE CONTEXT" not in captured["input"]
    assert qa.ASK_SYSTEM_KG_ADDENDUM not in captured["system"]
    assert captured["input"] == "Question: write a PRD for billing"


def test_kg_grounding_does_not_touch_wired_call_digest_path(monkeypatch):
    """The dedicated call/VoC process owns its own grounding and must not be
    re-routed through the generic KG-grounded single-shot path."""
    import app.call_digest as cd
    monkeypatch.setattr(cd, "answer", lambda **k: {"answer": "digest", "_skill_source": "call-digest"})
    # If the single-shot path were taken, this would fire; it must NOT.
    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda eid, q: (_ for _ in ()).throw(AssertionError("KG path taken")))
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    out = qa.answer(enterprise_id="ent", question="summarize the customer calls from last week", dataset="acme")
    assert out["_skill_source"] == "call-digest"


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


# ── PRD-tab grounding (prd_id) ───────────────────────────────────────────────

def test_answer_prd_id_grounds_skill_answer(monkeypatch):
    """A PRD-tab ask routed to a skill carries the CURRENT PRD CONTEXT block on
    the gateway's CACHEABLE user prefix (byte-stable across turns → prompt-cache
    reads) — NOT in the uncached input — and the PRD addendum in the system
    prompt."""
    calls = []
    monkeypatch.setattr(
        qa, "llm_call", lambda **k: calls.append(k) or _answer_out()
    )
    import app.prd_context as prd_context_mod

    monkeypatch.setattr(
        prd_context_mod,
        "build_prd_context",
        lambda ent, prd_id: f"=== CURRENT PRD CONTEXT ===\nprd {prd_id} for {ent}",
    )
    out = qa.answer(
        enterprise_id="ent", question="anything", dataset="acme",
        pinned_skill="roadmap", prd_id=7,
    )
    assert out["answer"] == "ok"
    answer_call = calls[-1]
    assert "CURRENT PRD CONTEXT" in answer_call["user_cacheable_prefix"]
    assert "prd 7 for ent" in answer_call["user_cacheable_prefix"]
    assert "CURRENT PRD CONTEXT" not in answer_call["input"]
    assert answer_call["input"] == "Question: anything"
    assert "CURRENT PRD CONTEXT" in answer_call["system"]


def test_answer_prd_id_skips_kg_retrieval_on_skill_path(monkeypatch):
    """A PRD-grounded skill ask must NOT run KG retrieval (embeddings HTTP call
    + pgvector) — the PRD block is the grounding. A plain skill ask still does."""
    retrievals = []
    monkeypatch.setattr(
        qa, "_retrieve_kg_bundle",
        lambda eid, q: retrievals.append(q) or None,
    )
    monkeypatch.setattr(qa, "llm_call", lambda **k: _answer_out())
    import app.prd_context as prd_context_mod

    monkeypatch.setattr(
        prd_context_mod, "build_prd_context", lambda ent, prd_id: "THE PRD BLOCK"
    )
    qa.answer(enterprise_id="ent", question="anything", dataset="acme",
              pinned_skill="roadmap", prd_id=7)
    assert retrievals == []  # PRD-grounded → no KG retrieval

    qa.answer(enterprise_id="ent", question="anything", dataset="acme",
              pinned_skill="roadmap")
    assert len(retrievals) == 1  # non-PRD skill ask unchanged


def test_answer_prd_prefix_stable_across_turns(monkeypatch):
    """Turns 2+ of the same PRD conversation must send a byte-identical
    cacheable prefix (same PRD content → cache read), with only the question
    varying in the uncached input."""
    calls = []
    monkeypatch.setattr(qa, "llm_call", lambda **k: calls.append(k) or _answer_out())
    import app.prd_context as prd_context_mod

    monkeypatch.setattr(
        prd_context_mod, "build_prd_context",
        lambda ent, prd_id: f"=== CURRENT PRD CONTEXT ===\nprd {prd_id}",
    )
    qa.answer(enterprise_id="ent", question="first question", dataset="acme",
              pinned_skill="roadmap", prd_id=7)
    qa.answer(enterprise_id="ent", question="second question", dataset="acme",
              pinned_skill="roadmap", prd_id=7)
    assert calls[0]["user_cacheable_prefix"] == calls[1]["user_cacheable_prefix"]
    assert calls[0]["input"] != calls[1]["input"]


def test_answer_prd_id_grounds_direct_answer(monkeypatch):
    """Router → none: the direct compose_ask_answer path receives the block via
    prd_context (kept out of the question so decision-log text stays small)."""
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())  # router → none
    import app.prd_context as prd_context_mod

    monkeypatch.setattr(
        prd_context_mod, "build_prd_context", lambda ent, prd_id: "THE PRD BLOCK"
    )
    seen = {}

    def _compose(dataset, q, *, enterprise_id, prd_context=""):
        seen.update(question=q, prd_context=prd_context)
        return {"answer": "generic", "key_points": [], "citations": [],
                "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _compose)
    out = qa.answer(
        enterprise_id="ent", question="what changed", dataset="acme", prd_id=7
    )
    assert out["answer"] == "generic"
    assert seen["prd_context"] == "THE PRD BLOCK"
    assert "THE PRD BLOCK" not in seen["question"]


def test_answer_prd_context_failure_degrades_to_plain_ask(monkeypatch):
    """build_prd_context returning '' (missing prd, foreign tenant, read error)
    must not break the answer — the ask runs exactly as a plain chat."""
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    import app.prd_context as prd_context_mod

    monkeypatch.setattr(
        prd_context_mod, "build_prd_context", lambda ent, prd_id: ""
    )
    monkeypatch.setattr(
        qa,
        "compose_ask_answer",
        lambda dataset, q, *, enterprise_id, prd_context="": {
            "answer": "plain", "key_points": [], "citations": [],
            "confidence": 0.5, "unanswered": "",
        },
    )
    out = qa.answer(
        enterprise_id="ent", question="what changed", dataset="acme", prd_id=404
    )
    assert out["answer"] == "plain"
