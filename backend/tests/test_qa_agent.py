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


# ── router determinism + schema shape ────────────────────────────────────────

def test_route_pins_temperature_to_zero(monkeypatch):
    """Routing is multiple-choice classification, so it must not sample.

    Passing no temperature left the call at the Anthropic API default of 1.0
    (`app/llm.py::_build_base_kwargs` only sets the key when it is not None), so
    the same question could route to a different skill run to run. A regex or
    slash fast-path never reaches the LLM router, so this uses a question with
    no regex rule.
    """
    captured: list[dict] = []
    monkeypatch.setattr(
        qa, "llm_call",
        lambda **k: captured.append(k) or _route_out("retention-churn", 0.9, "churn"),
    )
    d = qa.route("why do users drop off after week two?", enterprise_id="ent")
    assert d.source == "llm", "test needs a question that reaches the LLM router"
    assert len(captured) == 1
    assert captured[0]["temperature"] == 0


def test_route_temperature_reaches_the_anthropic_call(monkeypatch):
    """The kwarg is not merely accepted by `llm_call` — it survives the gateway.

    `conftest` patches `app.llm.call_json`, but the gateway imported that name
    into its own namespace, so this patches `app.graph.gateway.call_json` (the
    same reason `test_ask_skill_routing._patch_gateway_call_json` exists). That
    makes the REAL `llm_call` run, proving temperature is threaded end to end
    rather than swallowed by the gateway signature.
    """
    import app.graph.gateway as gateway_mod

    captured: list[dict] = []

    def _fake_call_json(**kwargs):
        captured.append(kwargs)
        return {"reason": "churn", "skill_id": "retention-churn",
                "confidence": 0.9, "in_scope": True}

    monkeypatch.setattr(gateway_mod, "call_json", _fake_call_json, raising=True)

    d = qa.route("why do users drop off after week two?", enterprise_id="ent")
    assert d.skill_id == "retention-churn" and d.source == "llm"
    assert len(captured) == 1
    assert captured[0]["temperature"] == 0
    # ...and it is the router's own schema that was sent.
    assert list(captured[0]["schema"]["properties"])[0] == "reason"


def test_route_schema_generates_reason_before_the_label():
    """Forced-tool JSON is emitted in schema order, so `reason` must come first.

    With `skill_id` first the label was already committed before the model wrote
    its justification, making that text post-hoc rationalisation. Anthropic's
    ticket-routing guide: "always include your classification reasoning before
    your actual intent output". `additionalProperties: False` pins the contract
    to exactly these four fields.
    """
    props = list(qa._ROUTE_SCHEMA["properties"])
    assert props[0] == "reason", f"reason must be generated first, got {props}"
    assert set(props) == {"reason", "skill_id", "confidence", "in_scope"}
    assert qa._ROUTE_SCHEMA["additionalProperties"] is False
    # Every property stays required — the reorder must not drop the contract.
    assert set(qa._ROUTE_SCHEMA["required"]) == set(props)


def test_out_of_scope_message_judges_topic_not_data():
    """The canned refusal must judge TOPIC, never data volume. The old 'I
    don't have grounded data on that topic, so I won't guess' sentence taught
    the ANSWER model to emit the refusal for in-scope questions on a
    workspace with nothing connected ('how would dark mode look in my
    product?' — ask job 383, 2026-07-26). The message stays topical-only, and
    ASK_SYSTEM carries an explicit no-data carve-out instead."""
    from app.prompts import ASK_SYSTEM

    assert "grounded data" not in qa.OUT_OF_SCOPE_MESSAGE
    assert "must NEVER get that canned reply" in ASK_SYSTEM


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


def test_answer_scope_gate_spares_anaphoric_followup(monkeypatch):
    # A follow-up whose subject lives in the previous turn ("...about it?") reads
    # as topic-less on its own, which is what the router mistook for
    # out-of-domain. It must answer in context instead of getting the refusal.
    monkeypatch.setattr(
        qa, "llm_call", lambda **k: _route_out("none", 0.9, "no topic", in_scope=False)
    )
    seen = {}
    monkeypatch.setattr(
        qa, "compose_ask_answer",
        lambda dataset, q, **k: seen.update(q=q) or {"answer": "Here you go.", "citations": []},
    )
    history = [
        {"role": "user", "content": "what did users say about the onboarding flow?"},
        {"role": "assistant", "content": "Most complaints were about the email step."},
    ]
    out = qa.answer(
        enterprise_id="ent",
        question="can you get me all the details about it?",
        dataset="acme",
        history=history,
    )
    assert out["answer"] != qa.OUT_OF_SCOPE_MESSAGE
    # ...and the prior turns rode along, so "it" is resolvable.
    assert "onboarding flow" in seen["q"]


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
    # The deep reasoning happens upstream in the KG + Top Insights brief; the PRD
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
        lambda dataset, q, *, enterprise_id, prd_context="", on_delta=None: {
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


# ── CIR runs on a fresh route (no confirm gate) ───────────────────────────────

def test_cir_slash_generates_report(monkeypatch):
    """A fresh /competitive-intelligence-review ask runs the skill and returns a
    real answer — no needs_confirmation interstitial (the old confirm gate was
    never consumed by any UI, so it rendered as an empty message)."""
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())
    out = qa.answer(
        enterprise_id="ent",
        question="/competitive-intelligence-review Linear, Jira, Asana",
        dataset="acme",
    )
    assert out.get("type") != "needs_confirmation"
    assert out["_skill"] == "competitive-intelligence-review"
    assert out["answer"] == "ok"
    assert captured["model"] == qa.HEAVY_MODEL  # CIR is heavy → opus


def test_cir_regex_route_generates_report(monkeypatch):
    """The natural phrasing ('competitor analysis…') regex-routes to CIR and
    also runs it directly."""
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())
    out = qa.answer(
        enterprise_id="ent",
        question="run a competitor analysis for my product",
        dataset="acme",
    )
    assert out["_skill"] == "competitive-intelligence-review"
    assert out["_skill_source"] == "regex"
    assert out["answer"] == "ok"
    assert captured["skill"] == "competitive-intelligence-review"


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


def test_cir_runs_when_pinned(monkeypatch):
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


# ── on_route: the routed skill is announced AT ROUTING TIME ──────────────────
# The decision exists seconds into a run that can last minutes. The hook fires
# the moment it resolves so the caller can persist it where a waiting client can
# read it — not at completion, which is where `_skill` in the payload lands.


def test_on_route_fires_with_the_skill_before_the_answer_call(monkeypatch):
    """Ordering is the feature. `_skill` in the payload lands at completion;
    this has to land before the expensive call starts."""
    events: list[tuple] = []
    monkeypatch.setattr(
        qa, "llm_call",
        lambda **k: events.append(("llm", k.get("purpose"))) or _answer_out(),
    )
    out = qa.answer(
        enterprise_id="ent", question="write user stories for checkout",
        dataset="acme", on_route=lambda s, a: events.append(("route", s, a)),
    )
    assert events[0] == ("route", "user-stories", "Generate user stories")
    assert events[1] == ("llm", "skill_answer"), "the answer call comes after"
    assert len([e for e in events if e[0] == "route"]) == 1, "fires exactly once"
    # The pair matches what the finished payload carries, so the mid-run label
    # and the final one can never disagree.
    assert (events[0][1], events[0][2]) == (out["_skill"], out["_skill_action"])


def test_on_route_reports_none_when_no_skill_is_routed(monkeypatch):
    """The direct path routes nothing. The hook still fires (so the caller knows
    routing is done) but carries None — the writer treats that as 'record
    nothing', which is what keeps the column null."""
    events: list[tuple] = []
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())  # router → none
    monkeypatch.setattr(
        qa, "compose_ask_answer",
        lambda dataset, q, *, enterprise_id, prd_context="", on_delta=None: {
            "answer": "generic", "key_points": [], "citations": [],
            "confidence": 0.5, "unanswered": "",
        },
    )
    qa.answer(enterprise_id="ent", question="what happened last week", dataset="acme",
              on_route=lambda s, a: events.append((s, a)))
    assert events == [(None, "")]


def test_on_route_reports_none_for_an_out_of_scope_question(monkeypatch):
    events: list[tuple] = []
    monkeypatch.setattr(
        qa, "llm_call", lambda **k: _route_out("none", 0.9, "weather", in_scope=False)
    )
    out = qa.answer(enterprise_id="ent", question="what's the weather in tokyo?",
                    dataset="acme", on_route=lambda s, a: events.append((s, a)))
    assert out["answer"] == qa.OUT_OF_SCOPE_MESSAGE
    assert events == [(None, "")]


def test_on_route_reports_a_pinned_skill(monkeypatch):
    """A pinned follow-up skips the router but the skill IS resolved, so the
    waiting surface can still name it."""
    events: list[tuple] = []
    monkeypatch.setattr(qa, "llm_call", lambda **k: _answer_out())
    qa.answer(enterprise_id="ent", question="anything", dataset="acme",
              pinned_skill="roadmap", on_route=lambda s, a: events.append((s, a)))
    assert events == [("roadmap", "roadmap")]


def test_on_route_does_not_fire_for_a_pre_routing_interceptor(monkeypatch):
    """call_digest answers without consulting the router at all. Reporting a
    skill it never chose would be the invented signal this hook exists to
    avoid — so nothing is announced and the column stays null."""
    import app.call_digest as cd

    monkeypatch.setattr(cd, "answer", lambda **k: {"answer": "digest",
                                                   "_skill_source": "call-digest"})
    events: list[tuple] = []
    qa.answer(enterprise_id="ent", dataset="acme",
              question="summarize the customer calls from last week",
              on_route=lambda s, a: events.append((s, a)))
    assert events == []


def test_on_route_failure_never_breaks_the_answer(monkeypatch):
    """The hook writes to the DB. A blip there must cost display metadata, not
    the answer the user is paying for."""
    monkeypatch.setattr(qa, "llm_call", lambda **k: _answer_out())

    def _boom(_skill, _action):
        raise RuntimeError("supabase blip")

    out = qa.answer(enterprise_id="ent", question="write user stories for checkout",
                    dataset="acme", on_route=_boom)
    assert out["answer"] == "ok"


# ── on_phase: naming the leg that is actually running ────────────────────────
# Every label is authored beside the call it describes; these tests pin that
# the labels appear in execution order and only on paths that really do the
# work they name.


def test_phases_name_retrieval_then_writing_in_order(monkeypatch):
    phases: list[str] = []
    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda eid, q: {"signals": [1]})
    import app.graph.retrieval as retrieval

    monkeypatch.setattr(retrieval, "render_context_section", lambda b: "LIVE CONTEXT")
    monkeypatch.setattr(qa, "llm_call", lambda **k: _answer_out())

    qa.answer(enterprise_id="ent", question="write a PRD for billing", dataset="acme",
              on_phase=phases.append)

    assert phases == ["Searching your connected sources…", "Writing the answer…"]


def test_no_retrieval_phase_when_retrieval_is_skipped(monkeypatch):
    """A PRD-grounded ask deliberately skips KG retrieval, so it must not claim
    to be searching sources — the label would describe work that never ran."""
    phases: list[str] = []
    monkeypatch.setattr(qa, "llm_call", lambda **k: _answer_out())
    import app.prd_context as prd_context_mod

    monkeypatch.setattr(
        prd_context_mod, "build_prd_context", lambda ent, prd_id: "THE PRD BLOCK"
    )
    qa.answer(enterprise_id="ent", question="anything", dataset="acme",
              pinned_skill="roadmap", prd_id=7, on_phase=phases.append)

    assert phases == ["Writing the answer…"]


def test_phase_sink_failure_never_breaks_the_answer(monkeypatch):
    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda eid, q: None)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _answer_out())

    def _boom(_label):
        raise RuntimeError("stream closed")

    out = qa.answer(enterprise_id="ent", question="write a PRD for billing",
                    dataset="acme", on_phase=_boom)
    assert out["answer"] == "ok"


def test_answer_without_hooks_behaves_exactly_as_before(monkeypatch):
    """Both hooks are optional; every existing caller omits them."""
    monkeypatch.setattr(qa, "llm_call", lambda **k: _answer_out())
    out = qa.answer(enterprise_id="ent", question="write user stories for checkout",
                    dataset="acme")
    assert out["_skill"] == "user-stories"


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

    def _compose(dataset, q, *, enterprise_id, prd_context="", on_delta=None):
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
        lambda dataset, q, *, enterprise_id, prd_context="", on_delta=None: {
            "answer": "plain", "key_points": [], "citations": [],
            "confidence": 0.5, "unanswered": "",
        },
    )
    out = qa.answer(
        enterprise_id="ent", question="what changed", dataset="acme", prd_id=404
    )
    assert out["answer"] == "plain"
