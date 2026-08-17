"""Unit tests for the unified Q&A agent router + answer dispatch.

`route()` and `answer()` flow through the gateway's `llm_call` (imported into
the qa_agent namespace) and `compose_ask_answer`; these tests patch those refs
directly so no Anthropic / Supabase call is made.

WHAT A "ROUTED SKILL" IS NOW. These tests used to reach the single-shot answer
path by naming a vendored built-in (`roadmap`, `user-stories`, `prd-author`,
`prioritize`) — a chat turn could route to any of ~78. It cannot route to a
built-in at all now, so the two things it CAN route to stand in instead:

  * `CUSTOM_SKILL` — one of the company's own uploads, seeded by
    `_seed_custom_skill`. This is the stand-in wherever the test is really
    about the single-shot answer's SHAPE (model, grounding, prefix, phases,
    PRD context), because that path is unchanged and a custom skill is what
    still walks it.
  * a PIPELINE id — where the test is about ROUTING reaching real machinery.

Every assertion those tests made is preserved; only the id changed.
"""
from __future__ import annotations

import os

import pytest

import app.db.custom_skills as custom_skills_db
import app.qa_agent as qa
import app.skills.resolver as resolver

# One of the company's uploaded skills — the generic invocable id these tests
# use to reach the single-shot answer path.
CUSTOM_SKILL = "house-method"


def _seed_custom_skill(monkeypatch, slug: str = CUSTOM_SKILL):
    """Make `slug` a real custom skill for every company.

    Patches BOTH per-request reads, for the same reason
    `test_qa_router_custom_skills._seed_library` does: `_custom_skill_block`
    lists the library and `_routable` re-checks the id by slug.
    """
    row = {
        "slug": slug, "name": slug, "description": "The house method, written by us.",
        "method": f"# {slug}\nmethod text", "modules": {}, "references": {},
        "content_hash": "hash" + slug,
    }
    monkeypatch.setattr(custom_skills_db, "list_custom_skills", lambda cid: [dict(row)])
    monkeypatch.setattr(
        resolver, "get_custom_skill",
        lambda cid, wanted: dict(row) if wanted == slug else None,
    )
    return row


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
    """The slash trigger still resolves — for a CUSTOM skill.

    It used to accept any routable built-in (`/prioritize`). That half is gone
    with the built-in skill layer; this branch survives because it is the wire
    protocol behind the composer's skill chip, which re-attaches the trigger to
    the message text, so deleting it would make a company's own uploads
    uninvocable."""
    _seed_custom_skill(monkeypatch)
    calls = []
    monkeypatch.setattr(qa, "llm_call", lambda **k: calls.append(k) or _route_out())
    d = qa.route(f"/{CUSTOM_SKILL} rank these", enterprise_id="ent")
    assert d.skill_id == CUSTOM_SKILL and d.source == "slash"
    assert calls == []  # fast-path: no LLM


def test_slash_builtin_falls_through(monkeypatch):
    """A VENDORED id is never slash-selected.

    Was `test_slash_nonroutable_falls_through`, covering the handful of
    built-ins on a NON_ROUTABLE opt-out list. The property is unconditional
    now: `resolve_skill` is built-in-first, so honouring `/prd-author` here
    would promise an upload and deliver the built-in's method."""
    _seed_custom_skill(monkeypatch)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())  # router says none
    d = qa.route("/prd-author build it", enterprise_id="ent")
    assert d.skill_id != "prd-author"


def test_regex_fastpath(monkeypatch):
    """The keyword tier still short-circuits — for a PIPELINE.

    Was "generate a PRD for onboarding" → prd-author. That rule is deleted: it
    picked a long-output document generator for anything mentioning a PRD, so
    "what's in the PRD for onboarding?" came back as a full PRD."""
    calls = []
    monkeypatch.setattr(qa, "llm_call", lambda **k: calls.append(k) or _route_out())
    d = qa.route("run a competitive intelligence report", enterprise_id="ent")
    assert d.skill_id == "competitive-intelligence-review" and d.source == "regex"
    assert calls == []  # regex short-circuits the LLM router


def test_llm_router_selects(monkeypatch):
    monkeypatch.setattr(
        qa, "llm_call", lambda **k: _route_out("company-research", 0.82, "ours")
    )
    d = qa.route("what does our pricing look like out there?", enterprise_id="ent")
    assert d.skill_id == "company-research" and d.source == "llm"


def test_llm_router_below_threshold_is_direct(monkeypatch):
    monkeypatch.setattr(
        qa, "llm_call", lambda **k: _route_out("company-research", 0.3, "weak")
    )
    d = qa.route("hello there", enterprise_id="ent")
    assert d.skill_id is None


def test_llm_router_rejects_a_builtin_id(monkeypatch):
    """Even a confident built-in pick is refused — nothing can run it.

    Was `test_llm_router_rejects_nonroutable`, which relied on `fact-check`
    being on a per-skill opt-out list. No vendored id is invocable from chat
    now, so the guarantee holds without an allow-list."""
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out("prd-author", 0.99, "x"))
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
        lambda **k: _route_out("company-research", 0.85, "ours", in_scope=False),
    )
    d = qa.route("how are we placed at the moment?", enterprise_id="ent")
    assert d.skill_id == "company-research" and d.source == "llm"


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
        lambda **k: captured.append(k) or _route_out("company-research", 0.9, "ours"),
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
        return {"reason": "ours", "skill_id": "company-research",
                "confidence": 0.9, "in_scope": True}

    monkeypatch.setattr(gateway_mod, "call_json", _fake_call_json, raising=True)

    d = qa.route("why do users drop off after week two?", enterprise_id="ent")
    assert d.skill_id == "company-research" and d.source == "llm"
    assert len(captured) == 1
    assert captured[0]["temperature"] == 0
    # ...and it is the router's own schema that was sent.
    assert list(captured[0]["schema"]["properties"])[0] == "reason"


def test_route_schema_generates_reason_before_the_label():
    """Forced-tool JSON is emitted in schema order, so ORDER is a mechanism here.

    Two orderings are load-bearing, for the same underlying reason:

    * `reason` first. With `skill_id` first the label was already committed
      before the model wrote its justification, making that text post-hoc
      rationalisation. Anthropic's ticket-routing guide: "always include your
      classification reasoning before your actual intent output".
    * `company_skill_id` before `skill_id` (2026-08-02). The company's own
      library has to be judged on its own merits BEFORE the alternatives are
      considered; judged afterwards it competed as one flat peer among 74
      built-ins and reliably lost to a near-miss. `skill_id` now names one of
      four pipelines rather than one of 74 methods, and the ordering matters
      for the same reason.

    `additionalProperties: False` pins the contract to exactly these six fields.
    """
    props = list(qa._ROUTE_SCHEMA["properties"])
    assert props[0] == "reason", f"reason must be generated first, got {props}"
    assert set(props) == {
        "reason", "company_skill_id", "company_confidence",
        "skill_id", "confidence", "in_scope",
    }
    assert props.index("company_skill_id") < props.index("skill_id"), (
        f"the company library must be judged first, got {props}"
    )
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


def test_ask_system_forbids_raw_html_and_redrawn_skill_chrome():
    """An ask answer is rendered by a markdown renderer with no raw-HTML pass,
    so any markup the model emits is PRINTED as tag text. A reader who asked
    "create a ticket to address this" on a VoC report (no PRD → the ask path
    answers in markdown instead of opening the Tickets surface) got the
    user-stories skill's action row as literal source:
    `<div style="display:flex…"><button style="background:#2e8a57">✓ Push to
    Jira</button>…`. A skill's delivery format specifies the surface the APP
    renders; ASK_SYSTEM is where the model is told that, and that its own
    output channel is markdown."""
    from app.prompts import ASK_SYSTEM

    assert "never raw HTML" in ASK_SYSTEM
    assert "Never draw a skill's UI chrome" in ASK_SYSTEM
    for tag in ("<div>", "<button>", 'style="…"'):
        assert tag in ASK_SYSTEM, f"ASK_SYSTEM must name {tag} as never-emit"


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


def test_answer_scope_gate_spares_anaphoric_followup_history_travels_separately(
    monkeypatch,
):
    # A follow-up whose subject lives in the previous turn ("...about it?") reads
    # as topic-less on its own, which is what the router mistook for
    # out-of-domain. It must answer in context instead of getting the refusal.
    monkeypatch.setattr(
        qa, "llm_call", lambda **k: _route_out("none", 0.9, "no topic", in_scope=False)
    )
    seen = {}
    monkeypatch.setattr(
        qa, "compose_ask_answer",
        lambda dataset, q, **k: seen.update(q=q, history=k.get("history"))
        or {"answer": "Here you go.", "citations": []},
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
    # The prior turns still reach the composer — but as the SEPARATE `history`
    # argument, not folded into the retrieval question. Retrieval sees only
    # the bare follow-up; the model still gets "onboarding flow" via history.
    assert seen["q"] == "can you get me all the details about it?"
    assert "onboarding flow" not in seen["q"]
    assert seen["history"] == history


def test_answer_pinned_skill_bypasses_scope_gate(monkeypatch):
    # A pinned follow-up has already chosen a skill — the router (and its
    # scope flag) is never consulted.
    _seed_custom_skill(monkeypatch)
    monkeypatch.setattr(
        qa, "llm_call", lambda **k: _answer_out()
    )
    out = qa.answer(
        enterprise_id="ent", question="anything", dataset="acme",
        pinned_skill=CUSTOM_SKILL,
    )
    assert out["_skill"] == CUSTOM_SKILL


# ── answer dispatch ────────────────────────────────────────────────────────────

def test_answer_skill_path_uses_sonnet(monkeypatch):
    # A custom skill is non-heavy → single-shot gateway call on the default model.
    _seed_custom_skill(monkeypatch)
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())
    out = qa.answer(
        enterprise_id="ent", question="score this the house way", dataset="acme",
        pinned_skill=CUSTOM_SKILL,
    )
    assert out["_skill"] == CUSTOM_SKILL
    assert captured["skill"] == CUSTOM_SKILL
    assert captured["model"] == qa.ANSWER_MODEL
    # The uploaded METHOD really was injected, and labelled as user content.
    assert captured["skill_spec"] is not None
    assert qa.ASK_SYSTEM_CUSTOM_SKILL_ADDENDUM in captured["system"]


def test_answer_heavy_skill_escalates_to_opus(monkeypatch):
    # competitive-intelligence-review is the remaining HEAVY skill. It's also
    # cost-gated, so pin it to skip the confirm-gate and reach the answer path.
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())
    out = qa.answer(enterprise_id="ent", question="size up our competitors",
                    dataset="acme", pinned_skill="competitive-intelligence-review")
    assert out["_skill"] == "competitive-intelligence-review"
    assert captured["model"] == qa.HEAVY_MODEL


# `test_answer_prd_author_stays_on_sonnet` was removed here. Its subject — the
# model a chat-routed `prd-author` answer runs on — no longer exists: chat
# cannot route to prd-author, which is bound by name from `prd_runner.py`. The
# model choice it guarded lives in that pipeline, which never passed an opus
# override and still does not.


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


def test_answer_voc_request_without_a_call_source_still_reaches_the_merged_path(
    monkeypatch,
):
    """CHANGED 2026-08-05 with the VoC merge. This case used to assert the
    opposite — that with no call source `call_digest.answer` must NOT run and
    the turn fell through to the generic skill answer. That assertion encoded
    the either/or that WAS the reported bug: `has_call_source` decided whether a
    company saw live calls or its knowledge graph, never both.

    `call_digest.answer` now merges the two and degrades per-source on its own,
    so there is nothing left for a capability gate to decide. A company with no
    call source but a populated graph belongs on the merged path (it degrades to
    KG-only and answers); a company with neither gets the digest's own
    what-to-connect message, which is the same guidance the generic skill answer
    used to give. What is pinned here is the ROUTE: the bare request still
    declines the fast-path interception and arrives via normal routing.
    """
    import app.call_digest as cd

    monkeypatch.setattr(cd, "has_call_source", lambda cid: False)
    seen: list = []
    monkeypatch.setattr(
        cd, "answer",
        lambda **k: seen.append(k) or {"answer": "merged", "_skill_source": "call-digest"},
    )
    monkeypatch.setattr(qa, "llm_call", lambda **k: _answer_out())

    out = qa.answer(
        enterprise_id="ent", question="give me a voice of customer report",
        dataset="acme",
    )

    assert out["_skill_source"] == "call-digest"
    # Reached from the VoC dispatch, not the interception the capability gate
    # still (correctly) declines.
    assert len(seen) == 1 and seen[0]["question"] == "give me a voice of customer report"


def test_voc_dispatch_no_longer_consults_the_call_source_gate(monkeypatch):
    """The gate is gone from this branch, not merely satisfied. If any code path
    still asks `has_call_source` before dispatching VoC, the either/or can grow
    back the next time someone edits it."""
    import app.call_digest as cd

    def _must_not_ask(cid):
        raise AssertionError("the VoC dispatch must not gate on has_call_source")

    monkeypatch.setattr(qa, "_answer_voc_report",
                        lambda *a: (_ for _ in ()).throw(AssertionError("KG-only path taken")))
    monkeypatch.setattr(cd, "answer", lambda **k: {"_skill_source": "call-digest"})
    monkeypatch.setattr(
        qa, "route",
        lambda q, **k: qa.RouteDecision("voice-of-customer-report", 1.0, "llm"),
    )
    monkeypatch.setattr(cd, "has_call_source", _must_not_ask)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _answer_out())

    out = qa.answer(enterprise_id="ent", question="what are customers feedback",
                    dataset="acme")

    assert out["_skill_source"] == "call-digest"


def test_llm_routed_voc_reaches_the_merged_path(monkeypatch):
    """The reported question matched NO regex — the haiku router classified it
    voice-of-customer-report and it landed on this dispatch. Fixing the dispatch
    is what covers every entry path, so pin the LLM-routed one explicitly."""
    import app.call_digest as cd
    import app.skill_router as sr

    assert sr.is_call_digest("what are customers feedback") is False
    assert sr.is_voc_report_request("what are customers feedback") is False

    seen: list = []
    monkeypatch.setattr(
        cd, "answer",
        lambda **k: seen.append(k) or {"answer": "merged", "_skill_source": "call-digest"},
    )
    monkeypatch.setattr(
        qa, "route",
        lambda q, **k: qa.RouteDecision("voice-of-customer-report", 0.9, "llm"),
    )
    monkeypatch.setattr(qa, "llm_call", lambda **k: _answer_out())

    out = qa.answer(enterprise_id="ent", question="what are customers feedback",
                    dataset="acme")

    assert out["_skill_source"] == "call-digest" and len(seen) == 1


def test_pinned_voc_still_answers_from_the_kg_alone(monkeypatch):
    """`pinned_skill` behaviour is deliberately unchanged: a pinned
    voice-of-customer-report is a pipeline id, survives `_invocable`, and runs
    `_answer_voc_report` over the KG bundle with no live fetch. This is the one
    caller that keeps that function alive — it is not dead code."""
    import app.call_digest as cd

    def _no_digest(**k):
        raise AssertionError("a pinned VoC must not run the live digest")

    monkeypatch.setattr(cd, "answer", _no_digest)
    # `**kw` absorbs the `scale=VOC_SCALE` the pinned path now passes — it
    # retrieves at the same width as the merged path so an explicit
    # `/voice-of-customer-report` can't return less feedback than the plain
    # question would.
    monkeypatch.setattr(
        qa, "_retrieve_kg_bundle",
        lambda eid, q, **kw: {"signals": [1], "themes": []},
    )
    import app.graph.retrieval as retrieval
    monkeypatch.setattr(retrieval, "render_context_section", lambda b: "KG SIGNAL")
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())

    out = qa.answer(
        enterprise_id="ent", question="give me a voice of customer report",
        dataset="acme", pinned_skill="voice-of-customer-report",
    )

    assert out["_skill"] == "voice-of-customer-report"
    assert captured["purpose"] == "voc_from_kg"
    assert "KG SIGNAL" in captured["input"]


# ─── interception contest: a company's own skill may beat the call digest ────


def _contest_out(slug, confidence, reason="fits"):
    return _Result({
        "reason": reason, "company_skill_id": slug, "confidence": confidence,
    })


def _no_digest(**k):  # pragma: no cover — asserted by not being called
    raise AssertionError("call_digest.answer must not run")


def test_a_company_skill_can_beat_the_digest_interception(monkeypatch):
    """The reported bug: a company uploads its own churn method, asks the exact
    question it exists for, and the deterministic interception answers instead
    because the router never ran. With uploads present the interception is now
    contested, and a confident company skill wins."""
    _seed_custom_skill(monkeypatch)
    import app.call_digest as cd

    monkeypatch.setattr(cd, "answer", _no_digest)
    monkeypatch.setattr(
        qa, "llm_call",
        lambda **k: _contest_out(CUSTOM_SKILL, 0.95)
        if k.get("purpose") == "intercept_contest" else _answer_out(),
    )

    out = qa.answer(
        enterprise_id="ent",
        question="summarize the customer calls from last week",
        dataset="acme",
    )

    assert out["_skill"] == CUSTOM_SKILL


def test_the_contest_reports_the_skill_to_on_route(monkeypatch):
    """Closes the observability gap that hid this bug: an intercepted turn used
    to report routed_skill=None because interceptions never reach the hook."""
    _seed_custom_skill(monkeypatch)
    import app.call_digest as cd

    monkeypatch.setattr(cd, "answer", _no_digest)
    monkeypatch.setattr(
        qa, "llm_call",
        lambda **k: _contest_out(CUSTOM_SKILL, 0.9)
        if k.get("purpose") == "intercept_contest" else _answer_out(),
    )
    seen: list = []

    qa.answer(
        enterprise_id="ent", question="summarize the customer calls from last week",
        dataset="acme", on_route=lambda sid, action: seen.append(sid),
    )

    assert seen == [CUSTOM_SKILL]


def test_a_weak_contest_pick_leaves_the_digest_alone(monkeypatch):
    """The bar to override a deterministic path that works is HIGHER than the
    ordinary routing threshold — a marginal call must not steal the turn."""
    _seed_custom_skill(monkeypatch)
    import app.call_digest as cd

    monkeypatch.setattr(cd, "answer", lambda **k: {"_skill_source": "call-digest"})
    monkeypatch.setattr(
        qa, "llm_call",
        lambda **k: _contest_out(CUSTOM_SKILL, qa._INTERCEPT_CONTEST_FLOOR - 0.01)
        if k.get("purpose") == "intercept_contest" else _answer_out(),
    )

    out = qa.answer(
        enterprise_id="ent",
        question="summarize the customer calls from last week",
        dataset="acme",
    )

    assert out["_skill_source"] == "call-digest"
    assert qa._INTERCEPT_CONTEST_FLOOR > qa._LLM_ROUTE_THRESHOLD


def test_a_none_verdict_leaves_the_digest_alone(monkeypatch):
    """The control case: the company HAS uploads, but this question is ordinary
    call analysis, so the built-in keeps it. 'none' is the common answer."""
    _seed_custom_skill(monkeypatch)
    import app.call_digest as cd

    monkeypatch.setattr(cd, "answer", lambda **k: {"_skill_source": "call-digest"})
    monkeypatch.setattr(
        qa, "llm_call",
        lambda **k: _contest_out("none", 0.0)
        if k.get("purpose") == "intercept_contest" else _answer_out(),
    )

    out = qa.answer(
        enterprise_id="ent",
        question="summarize the customer calls from last week",
        dataset="acme",
    )

    assert out["_skill_source"] == "call-digest"


def test_a_company_with_no_uploads_never_pays_for_the_contest(monkeypatch):
    """Cost gate, matching `_keyword_prior`'s: a tenant with no uploads must
    reach the interception with no model call of any kind — same behaviour and
    same latency as before this existed."""
    import app.call_digest as cd

    monkeypatch.setattr(custom_skills_db, "list_custom_skills", lambda cid: [])
    monkeypatch.setattr(cd, "answer", lambda **k: {"_skill_source": "call-digest"})
    calls: list = []
    monkeypatch.setattr(qa, "llm_call", lambda **k: calls.append(k) or _answer_out())

    out = qa.answer(
        enterprise_id="ent",
        question="summarize the customer calls from last week",
        dataset="acme",
    )

    assert out["_skill_source"] == "call-digest"
    assert calls == []


def test_the_cheap_call_index_listing_is_never_contested(monkeypatch):
    """Control case that must not regress: the interceptions delivering DATA a
    skill cannot obtain keep their turn. A listing is a ~4s table read — a
    vaguely-related upload must not be allowed to steal it and turn it into a
    minutes-long generation."""
    _seed_custom_skill(monkeypatch)
    import app.call_index as ci

    monkeypatch.setattr(ci, "is_listing_request", lambda q: True)
    monkeypatch.setattr(ci, "ensure_fresh", lambda cid: True)
    monkeypatch.setattr(
        ci, "answer_listing",
        lambda cid, q, *, fresh: {"_skill_source": "call-index"},
    )
    calls: list = []
    monkeypatch.setattr(qa, "llm_call", lambda **k: calls.append(k) or _answer_out())

    out = qa.answer(
        enterprise_id="ent", question="which calls did we have last week",
        dataset="acme",
    )

    assert out["_skill_source"] == "call-index"
    assert calls == [], "the listing path must not run a contest"


def test_a_named_transcript_ask_reaches_the_single_call_leg(monkeypatch):
    """THE ROUTING HALF, which a predicate test cannot see.

    "get me the Genworth transcript" matches BOTH gates: `_LISTING_VERB` covers
    "get" and `_CALL_NOUN` covers "transcript". The listing branch runs first,
    so before this the question was answered with the LIST — plus the line
    saying the index holds titles and dates and not transcripts — for a
    question that names one call and asks for its content.

    Testing this at the ladder rather than on `is_single_call_request` is the
    point: the predicate was already right, and the answer was still wrong.
    """
    import app.call_index as ci

    monkeypatch.setattr(ci, "ensure_fresh", lambda cid: True)
    monkeypatch.setattr(
        ci, "answer_listing",
        lambda cid, q, *, fresh: {"_skill_source": "call-index-listing"},
    )
    monkeypatch.setattr(
        ci, "answer_single_call",
        lambda cid, q, *, history, fresh: {"_skill_source": "call-index-single"},
    )
    monkeypatch.setattr(qa, "llm_call", lambda **k: _answer_out())

    out = qa.answer(
        enterprise_id="ent", question="get me the Genworth transcript",
        dataset="acme",
    )

    assert out["_skill_source"] == "call-index-single"


def test_a_general_transcript_ask_still_gets_the_listing(monkeypatch):
    """The control: naming no call, it belongs to the listing exactly as before.
    Without this, 'prefer single-call' would be a licence to answer any
    transcript question from one arbitrary call."""
    import app.call_index as ci

    monkeypatch.setattr(ci, "ensure_fresh", lambda cid: True)
    monkeypatch.setattr(
        ci, "answer_listing",
        lambda cid, q, *, fresh: {"_skill_source": "call-index-listing"},
    )
    monkeypatch.setattr(
        ci, "answer_single_call",
        lambda cid, q, *, history, fresh: {"_skill_source": "call-index-single"},
    )
    monkeypatch.setattr(qa, "llm_call", lambda **k: _answer_out())

    out = qa.answer(
        enterprise_id="ent", question="which calls have transcripts",
        dataset="acme",
    )

    assert out["_skill_source"] == "call-index-listing"


def test_a_contest_failure_keeps_the_built_in(monkeypatch):
    """Fails CLOSED, the opposite of `_custom_skill_block`'s fail-open: the
    fallback here is the interception that would have run anyway, so a gateway
    hiccup costs the caller their override and nothing else."""
    _seed_custom_skill(monkeypatch)
    import app.call_digest as cd

    monkeypatch.setattr(cd, "answer", lambda **k: {"_skill_source": "call-digest"})

    def _boom(**k):
        if k.get("purpose") == "intercept_contest":
            raise RuntimeError("gateway down")
        return _answer_out()

    monkeypatch.setattr(qa, "llm_call", _boom)

    out = qa.answer(
        enterprise_id="ent",
        question="summarize the customer calls from last week",
        dataset="acme",
    )

    assert out["_skill_source"] == "call-digest"


def test_a_foreign_slug_from_the_contest_is_refused(monkeypatch):
    """The tenant boundary holds here too — a hallucinated or another company's
    slug must never displace the built-in."""
    _seed_custom_skill(monkeypatch)
    import app.call_digest as cd

    monkeypatch.setattr(cd, "answer", lambda **k: {"_skill_source": "call-digest"})
    monkeypatch.setattr(
        qa, "llm_call",
        lambda **k: _contest_out("someone-elses-skill", 0.99)
        if k.get("purpose") == "intercept_contest" else _answer_out(),
    )

    out = qa.answer(
        enterprise_id="ent",
        question="summarize the customer calls from last week",
        dataset="acme",
    )

    assert out["_skill_source"] == "call-digest"


def test_the_contest_runs_at_most_once_per_answer(monkeypatch):
    """Memoized: the three digest entry points are checked in sequence, and a
    question matching more than one must not pay for two model calls."""
    _seed_custom_skill(monkeypatch)
    import app.call_digest as cd

    monkeypatch.setattr(cd, "has_call_source", lambda cid: True)
    monkeypatch.setattr(cd, "answer", lambda **k: {"_skill_source": "call-digest"})
    contests: list = []

    def _fake(**k):
        if k.get("purpose") == "intercept_contest":
            contests.append(k)
            return _contest_out("none", 0.0)
        return _answer_out()

    monkeypatch.setattr(qa, "llm_call", _fake)

    qa.answer(
        enterprise_id="ent",
        question="summarize the customer calls and give me a voice of customer report",
        dataset="acme",
    )

    assert len(contests) == 1


def test_answer_pinned_skill_bypasses_call_digest(monkeypatch):
    _seed_custom_skill(monkeypatch)
    # A pinned follow-up wins even if the text looks like a call digest.
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())
    out = qa.answer(enterprise_id="ent", question="summarize the customer calls",
                    dataset="acme", pinned_skill=CUSTOM_SKILL)
    assert out["_skill"] == CUSTOM_SKILL


def test_answer_direct_path(monkeypatch):
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())  # router → none
    monkeypatch.setattr(
        qa,
        "compose_ask_answer",
        lambda dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k: {
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
    _seed_custom_skill(monkeypatch)
    out = qa.answer(
        enterprise_id="ent", question="anything", dataset="acme",
        pinned_skill=CUSTOM_SKILL,
    )
    assert out["_skill"] == CUSTOM_SKILL
    assert "route" not in purposes  # router never consulted


def test_answer_history_folded_into_skill_input(monkeypatch):
    _seed_custom_skill(monkeypatch)
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())
    qa.answer(
        enterprise_id="ent",
        question="turn that into a plan",
        dataset="acme",
        pinned_skill=CUSTOM_SKILL,  # → single-shot, captures input
        history=[{"role": "user", "content": "here are 3 features: A, B, C"}],
    )
    assert "here are 3 features" in captured["input"]


# ── KG grounding of the single-shot skill answer ──────────────────────────────

def test_single_shot_grounds_skill_on_kg_when_present(monkeypatch):
    """A skill answer is handed the tenant's KG bundle so it has real signal to
    work from — no more corpus-less "not enough signal" refusal.

    Was routed to `prd-author` by the (deleted) PRD keyword rule; a company's
    own upload walks the identical path and is now what reaches it."""
    _seed_custom_skill(monkeypatch)
    captured = {}
    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda eid, q: {"signals": [1], "themes": []})
    import app.graph.retrieval as retrieval
    monkeypatch.setattr(
        retrieval, "render_context_section", lambda b: "LIVE CONTEXT FROM CONNECTED SOURCES\n- churn up 12%"
    )
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())

    out = qa.answer(enterprise_id="ent", question="score the billing epic",
                    dataset="acme", pinned_skill=CUSTOM_SKILL)

    assert out["_skill"] == CUSTOM_SKILL
    assert "LIVE CONTEXT FROM CONNECTED SOURCES" in captured["input"]  # KG folded in
    assert "churn up 12%" in captured["input"]
    assert qa.ASK_SYSTEM_KG_ADDENDUM in captured["system"]  # model told to treat it as evidence
    assert captured["input"].rstrip().endswith("Question: score the billing epic")


def test_single_shot_stays_corpus_less_when_kg_empty(monkeypatch):
    """No tenant signal (empty KG / no company / read error) → the pre-fix path:
    no KG block, no KG addendum. Preserves behaviour for signal-less tenants."""
    _seed_custom_skill(monkeypatch)
    captured = {}
    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda eid, q: None)
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())

    out = qa.answer(enterprise_id="ent", question="score the billing epic",
                    dataset="acme", pinned_skill=CUSTOM_SKILL)

    assert out["_skill"] == CUSTOM_SKILL
    assert "LIVE CONTEXT" not in captured["input"]
    assert qa.ASK_SYSTEM_KG_ADDENDUM not in captured["system"]
    assert captured["input"] == "Question: score the billing epic"


def test_skill_path_kg_grounding_honours_the_sentinel(isolated_settings):
    """`_kg_grounding` calls `_retrieve_kg_bundle(enterprise_id, question)`
    with no `question_embedding` at all — the second route into the same
    hole `compose_ask_answer` has. `isolated_settings` never sets
    `OPENAI_API_KEY`, so this exercises the REAL no-key `embed_texts`
    fallback (unmocked): `_kg_grounding` gets the same "no theme kNN on a
    zero vector" guarantee as the direct-path ask (AC1), with no change
    required to `qa_agent.py` itself — the guarantee lives in
    `retrieve_context`'s own defence-in-depth check."""
    from unittest.mock import patch

    from app.graph.facade import GraphFacade

    calls: list = []
    with patch.object(
        GraphFacade, "find_candidates",
        lambda self, ent, typ, vec, k=10: calls.append(vec) or [],
    ):
        qa._kg_grounding("ent-skill", "how is the pipeline?")

    assert calls == [], f"find_candidates called for theme kNN with no key: {calls}"


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


# ── workspace configuration in the single-shot skill answer prompt ────────────


def _seed_company_with_config(
    isolated_settings, company_id, *, website="https://sprntly.ai",
    display_name="Sprntly", product_name="Sprntly",
):
    """A companies row + its primary product row — the two reads
    `ask_runner.company_facts_block` composes into the answer-prompt
    configuration block."""
    db = isolated_settings["supabase"]
    db.table("companies").insert(
        {"id": company_id, "slug": f"slug-{company_id}", "display_name": display_name}
    ).execute()
    db.table("products").insert(
        {
            "id": f"prod-{company_id}",
            "company_id": company_id,
            "name": product_name,
            "website": website,
            "is_primary": 1,
        }
    ).execute()


def test_answer_single_shot_prompt_carries_company_domain_over_skill_typo(
    monkeypatch, isolated_settings
):
    """Regression: a custom skill's METHOD text carries the WRONG domain
    (the actual incident); the workspace's own, correct domain must still
    ride the prompt so it can outrank it. Fails on unfixed code — the
    cacheable prefix carries no company facts at all when there is no PRD
    context, so the domain appears nowhere in the assembled prompt. (AC12)"""
    from app.qa_agent import RouteDecision, _answer_single_shot
    from app.skills.loader import SkillSpec

    _seed_company_with_config(isolated_settings, "co-1")
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())
    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda eid, q: None)

    decision = RouteDecision(skill_id="my-estimator", confidence=1.0, source="slash")
    skill_spec = SkillSpec(
        id="my-estimator",
        method="# Estimation method\nLearn more at [sprintly.ai](https://sprintly.ai)",
        content_hash="abc123def456",
    )

    _answer_single_shot(
        decision, "co-1", "what's our domain?", [], skill_spec=skill_spec,
    )

    prefix = captured["user_cacheable_prefix"]
    assert prefix is not None
    from app.ask_runner import WORKSPACE_CONFIG_HEADER

    assert WORKSPACE_CONFIG_HEADER in prefix
    lines = prefix.splitlines()
    domain_lines = [l for l in lines if "https://sprntly.ai" in l]
    assert domain_lines, f"correct domain missing from prefix: {prefix!r}"


def test_answer_single_shot_sets_cacheable_prefix_to_company_facts_without_prd(
    monkeypatch, isolated_settings
):
    """No prd_context, tenant with workspace configuration → the cacheable
    prefix IS the config block (not None, the pre-fix value). (AC8)"""
    from app.qa_agent import RouteDecision, _answer_single_shot
    from app.ask_runner import company_facts_block

    _seed_company_with_config(isolated_settings, "co-1")
    facts = company_facts_block("co-1")
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())
    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda eid, q: None)

    decision = RouteDecision(skill_id=CUSTOM_SKILL, confidence=1.0, source="slash")
    _answer_single_shot(decision, "co-1", "what should we build next?", [])

    assert captured["user_cacheable_prefix"] == facts


def test_answer_single_shot_prefix_is_none_when_no_facts_and_no_prd(monkeypatch):
    """No workspace configuration (no company/product row) and no PRD →
    user_cacheable_prefix is None, exactly as before this ticket. (AC6, AC8)"""
    from app.qa_agent import RouteDecision, _answer_single_shot

    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())
    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda eid, q: None)

    decision = RouteDecision(skill_id=CUSTOM_SKILL, confidence=1.0, source="slash")
    _answer_single_shot(decision, "ent-with-no-config", "what next?", [])

    assert captured["user_cacheable_prefix"] is None


def test_answer_single_shot_facts_addendum_follows_custom_skill_addendum(
    monkeypatch, isolated_settings
):
    """With `skill_spec` set (a custom skill) AND workspace configuration
    present, the model reads the custom-skill addendum BEFORE the company-
    facts precedence clause. (AC8)"""
    from app.qa_agent import RouteDecision, _answer_single_shot
    from app.prompts import ASK_SYSTEM_COMPANY_FACTS_ADDENDUM, ASK_SYSTEM_CUSTOM_SKILL_ADDENDUM
    from app.skills.loader import SkillSpec

    _seed_company_with_config(isolated_settings, "co-1")
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())
    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda eid, q: None)

    decision = RouteDecision(skill_id="my-estimator", confidence=1.0, source="slash")
    skill_spec = SkillSpec(id="my-estimator", method="# Method\nDo the thing.")

    _answer_single_shot(decision, "co-1", "q?", [], skill_spec=skill_spec)

    system = captured["system"]
    assert system.index(ASK_SYSTEM_CUSTOM_SKILL_ADDENDUM) < system.index(
        ASK_SYSTEM_COMPANY_FACTS_ADDENDUM
    )


# ── script skills: REMOVED ───────────────────────────────────────────────────
# `test_script_skill_uses_tool_loop_not_single_shot` covered `app/skills/
# scripts.py` — RICE/ICE scoring, A/B sample size, SaaS-metric math and PRD lint
# running as deterministic Python through `run_tool_loop` instead of being
# estimated by the model. That module and `_answer_with_script` are deleted and
# the math is model-estimated now. It is the ONE removal in this change that
# alters behaviour beyond prompting, and it is intended — so the test goes with
# its subject rather than being weakened into something that still passes.


# ── CIR runs on a fresh route (no confirm gate) ───────────────────────────────

def test_cir_named_competitors_generates_report(monkeypatch):
    """A CIR ask naming competitors runs the pipeline and returns a real answer
    — no needs_confirmation interstitial (the old confirm gate was never
    consumed by any UI, so it rendered as an empty message).

    Was written as `/competitive-intelligence-review Linear, Jira, Asana`. The
    slash trigger is custom-skills-only now, so the same request arrives the way
    a user actually types it and routes through the keyword tier instead."""
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _answer_out())
    out = qa.answer(
        enterprise_id="ent",
        question="competitive analysis vs Linear, Jira and Asana",
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
    out = qa.answer(enterprise_id="ent", question="what are people saying about us online?",
                    dataset="acme")
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
    # HIGH_STAKES_SKILLS is the web-research pipelines now — the method-only
    # skills it used to name (prd-author, saas-metrics-diagnosis, …) are not
    # reachable from a chat turn at all.
    out = qa.answer(enterprise_id="ent", question="what are people saying about us online?",
                    dataset="acme")
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
    _seed_custom_skill(monkeypatch)
    out = qa.answer(
        enterprise_id="ent", question="score this the house way",
        dataset="acme", pinned_skill=CUSTOM_SKILL,
        on_route=lambda s, a: events.append(("route", s, a)),
    )
    assert events[0] == ("route", CUSTOM_SKILL, CUSTOM_SKILL)
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
        lambda dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k: {
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
    _seed_custom_skill(monkeypatch)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _answer_out())
    qa.answer(enterprise_id="ent", question="anything", dataset="acme",
              pinned_skill=CUSTOM_SKILL, on_route=lambda s, a: events.append((s, a)))
    assert events == [(CUSTOM_SKILL, CUSTOM_SKILL)]


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

    _seed_custom_skill(monkeypatch)
    out = qa.answer(enterprise_id="ent", question="score this the house way",
                    dataset="acme", pinned_skill=CUSTOM_SKILL, on_route=_boom)
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

    _seed_custom_skill(monkeypatch)
    qa.answer(enterprise_id="ent", question="score the billing epic", dataset="acme",
              pinned_skill=CUSTOM_SKILL, on_phase=phases.append)

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
    _seed_custom_skill(monkeypatch)
    qa.answer(enterprise_id="ent", question="anything", dataset="acme",
              pinned_skill=CUSTOM_SKILL, prd_id=7, on_phase=phases.append)

    assert phases == ["Writing the answer…"]


def test_phase_sink_failure_never_breaks_the_answer(monkeypatch):
    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda eid, q: None)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _answer_out())

    def _boom(_label):
        raise RuntimeError("stream closed")

    _seed_custom_skill(monkeypatch)
    out = qa.answer(enterprise_id="ent", question="score the billing epic",
                    dataset="acme", pinned_skill=CUSTOM_SKILL, on_phase=_boom)
    assert out["answer"] == "ok"


def test_answer_without_hooks_behaves_exactly_as_before(monkeypatch):
    """Both hooks are optional; every existing caller omits them."""
    _seed_custom_skill(monkeypatch)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _answer_out())
    out = qa.answer(enterprise_id="ent", question="score this the house way",
                    dataset="acme", pinned_skill=CUSTOM_SKILL)
    assert out["_skill"] == CUSTOM_SKILL


# ── PRD-tab grounding (prd_id) ───────────────────────────────────────────────

def test_answer_prd_id_grounds_skill_answer(monkeypatch):
    """A PRD-tab ask routed to a skill carries the CURRENT PRD CONTEXT block on
    the gateway's CACHEABLE user prefix (byte-stable across turns → prompt-cache
    reads) — NOT in the uncached input — and the PRD addendum in the system
    prompt."""
    _seed_custom_skill(monkeypatch)
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
        pinned_skill=CUSTOM_SKILL, prd_id=7,
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
    _seed_custom_skill(monkeypatch)
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
              pinned_skill=CUSTOM_SKILL, prd_id=7)
    assert retrievals == []  # PRD-grounded → no KG retrieval

    qa.answer(enterprise_id="ent", question="anything", dataset="acme",
              pinned_skill=CUSTOM_SKILL)
    assert len(retrievals) == 1  # non-PRD skill ask unchanged


def test_answer_prd_prefix_stable_across_turns(monkeypatch):
    """Turns 2+ of the same PRD conversation must send a byte-identical
    cacheable prefix (same PRD content → cache read), with only the question
    varying in the uncached input."""
    _seed_custom_skill(monkeypatch)
    calls = []
    monkeypatch.setattr(qa, "llm_call", lambda **k: calls.append(k) or _answer_out())
    import app.prd_context as prd_context_mod

    monkeypatch.setattr(
        prd_context_mod, "build_prd_context",
        lambda ent, prd_id: f"=== CURRENT PRD CONTEXT ===\nprd {prd_id}",
    )
    qa.answer(enterprise_id="ent", question="first question", dataset="acme",
              pinned_skill=CUSTOM_SKILL, prd_id=7)
    qa.answer(enterprise_id="ent", question="second question", dataset="acme",
              pinned_skill=CUSTOM_SKILL, prd_id=7)
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

    def _compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
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
        lambda dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k: {
            "answer": "plain", "key_points": [], "citations": [],
            "confidence": 0.5, "unanswered": "",
        },
    )
    out = qa.answer(
        enterprise_id="ent", question="what changed", dataset="acme", prd_id=404
    )
    assert out["answer"] == "plain"


# ── routing text split — attachment content must not steer the interceptors ──
# An attached document's own vocabulary ("board", "ticket") must never decide
# which interceptor claims a turn; only what the user actually typed does. See
# `ChatScreen.tsx` `submitAsk`, which inlines every attachment's extracted text
# after a literal `\n\n[Attached files]\n` marker before POSTing.

_ATTACHED_DOC_QUESTION = (
    "Can you get me a summary of how our roadmap compares to Productboard, "
    "based on this doc?"
    "\n\n[Attached files]\n"
    "--- Sprntly_vs_Productboard_Comparison.docx ---\n"
    "Sprntly focuses on evidence-linked PRDs; the Productboard board view "
    "groups initiatives by objective. Neither product auto-files a ticket "
    "from a customer call today.\n"
)


def test_answer_does_not_route_attached_document_to_tracker(monkeypatch):
    """T1 — RED-first, the headline regression.

    The attached comparison document contains "board" once and "ticket" once
    — measured live as enough to make `is_jira_lookup` fire on the question +
    attachment text and claim the turn for the tracker path, which then
    replies "connect Jira" without ever reading the document. No tracker is
    connected here, so a misroute is unambiguous.

    Asserted on the turn's ROUTING METADATA (`_skill_source`/`_skill_action`),
    never the refusal's prose (fragile to wording) and never by calling
    `is_jira_lookup` directly — the defect is that the interceptor CLAIMS the
    turn, so the test must observe the turn's fate."""
    import app.db as db

    monkeypatch.setattr(db, "get_connection", lambda cid, prov: None)
    monkeypatch.setattr(
        "app.connector_lookup.registry.connected_providers", lambda cid: []
    )
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    monkeypatch.setattr(
        qa, "compose_ask_answer",
        lambda dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k: {
            "answer": "grounded", "key_points": [], "citations": [],
            "confidence": 0.5, "unanswered": "",
        },
    )
    out = qa.answer(
        enterprise_id="ent", question=_ATTACHED_DOC_QUESTION, dataset="acme"
    )
    assert out.get("_skill_source") != "connector-lookup", out
    assert out.get("_skill_action") != "Tracker lookup", out


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="needs live model")
def test_named_document_question_reaches_route_in_scope(monkeypatch):
    """T5 (live half) — RED-first, ~80% nondeterministic refusal measured
    live (AC6). NOT exercised in the authoring session: both available keys
    (`ANTHROPIC_API_KEY` and `DESIGN_AGENT_ANTHROPIC_API_KEY`) returned
    "credit balance is too low" from the real Anthropic endpoint — a `route()`
    call that fails outright degrades to the direct/none path (by design,
    see `test_llm_router_failure_is_direct`), which would silently read as a
    false pass here. This AC's real-LLM behaviour therefore belongs to the
    ship-gate-verifier's live gate (PI12); `test_route_input_carries_a_
    document_name_from_the_measured_case` below is the deterministic, WIRING
    half of the same regression, run and verified this session.

    The mid-conversation case DR-02 shipped grounding for (LV3): a bare
    follow-up NAMING a document already in this workspace's index, with no
    attachment text riding along this turn. Before the AC5 filename fix the
    scope classifier saw only the bare words and this exact case (measured
    against the real router) refused out-of-scope roughly 4 times in 5. The
    fix hands the classifier the attached/uploaded FILENAMES — never their
    content — so the same question is unambiguously in-scope.

    Hits the REAL Anthropic haiku router (no `llm_call` mock) — nondeterministic
    by nature, so this asserts the STRONG majority the ticket measured (0/5
    refusals post-fix) across N runs, matching the root-cause section's own
    measurement methodology, not a single sample."""
    from app.document_sources import DocumentFileRef

    monkeypatch.setattr(
        "app.document_sources.list_company_files",
        lambda cid: [
            DocumentFileRef(
                id="f1", source_id="s1", source_name="uploads",
                filename="Sprntly_vs_Productboard_Comparison.docx",
                uploaded_at="2026-08-01",
            )
        ],
    )
    # Only the ROUTER call needs to be real; stub the (expensive, unrelated)
    # answer-generation call so an in-scope result returns fast.
    monkeypatch.setattr(
        qa, "compose_ask_answer",
        lambda dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k: {
            "answer": "grounded", "key_points": [], "citations": [],
            "confidence": 0.5, "unanswered": "",
        },
    )
    question = "summarize the Sprntly_vs_Productboard_Comparison.docx file"
    n = 5
    refusals = 0
    for _ in range(n):
        out = qa.answer(enterprise_id="ent", question=question, dataset="acme")
        if out.get("_skill_source") == "scope_gate":
            refusals += 1
    assert refusals == 0, f"{refusals}/{n} out-of-scope refusals for {question!r}"


def test_route_input_carries_a_document_name_from_the_measured_case(monkeypatch):
    """T5 (deterministic, WIRING half of AC6). The router's classifier INPUT
    must carry the workspace's uploaded filenames — the measured regression
    case is a question naming a document already in the index —, mocked here
    rather than judged by a real model. Complements (does not replace) the
    live half above, which is gated behind a real Anthropic key."""
    from app.document_sources import DocumentFileRef

    monkeypatch.setattr(
        qa, "list_company_files",
        lambda cid: [
            DocumentFileRef(
                id="f1", source_id="s1", source_name="uploads",
                filename="Sprntly_vs_Productboard_Comparison.docx",
                uploaded_at="2026-08-01",
            )
        ],
    )
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _route_out())
    monkeypatch.setattr(
        qa, "compose_ask_answer",
        lambda dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k: {
            "answer": "ok", "key_points": [], "citations": [],
            "confidence": 0.5, "unanswered": "",
        },
    )
    qa.answer(
        enterprise_id="ent",
        question="summarize the Sprntly_vs_Productboard_Comparison.docx file",
        dataset="acme",
    )
    assert "Sprntly_vs_Productboard_Comparison.docx" in captured["input"]


def test_routing_text_stops_at_marker_grounding_keeps_full_question(monkeypatch):
    """T2 — interceptors receive only the user's typed text (up to the
    marker); grounding still receives the FULL question, marker and all.
    Both halves asserted in one test."""
    seen_interceptor_arg = {}

    def _spy_is_call_digest(q):
        seen_interceptor_arg["text"] = q
        return False

    monkeypatch.setattr(qa, "is_call_digest", _spy_is_call_digest)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    seen_grounding_arg = {}

    def _compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        seen_grounding_arg["question"] = q
        return {"answer": "ok", "key_points": [], "citations": [],
                "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _compose)
    question = "what happened?" + qa._ATTACHED_FILES_MARKER + "body text here"
    qa.answer(enterprise_id="ent", question=question, dataset="acme")
    assert seen_interceptor_arg["text"] == "what happened?"
    assert "[Attached files]" not in seen_interceptor_arg["text"]
    assert "body text here" not in seen_interceptor_arg["text"]
    assert seen_grounding_arg["question"] == question
    assert "body text here" in seen_grounding_arg["question"]


def test_routing_text_equals_question_when_no_marker():
    """T3 — a question with no marker produces routing text IDENTICAL to the
    question, proved by equality (the no-attachment path is provably
    untouched)."""
    q = "what happened last week?"
    assert qa._routing_text(q) == q


def test_routing_text_not_truncated_by_a_bare_prose_mention():
    """T4 (AC4) — a question that merely MENTIONS the phrase in prose, with
    no attachment block actually present (no surrounding newlines), is not
    truncated. The matched literal is the two-newline-prefixed marker the
    composer emits, not the bare words."""
    q = "what does [Attached files] even mean in this UI?"
    assert qa._routing_text(q) == q


def test_route_input_carries_filenames_and_never_a_document_body(monkeypatch):
    """T4 — router input at `route()`'s call site carries the attached and
    workspace filenames, and explicitly never a document BODY."""
    from app.document_sources import DocumentFileRef

    monkeypatch.setattr(
        qa, "list_company_files",
        lambda cid: [
            DocumentFileRef(
                id="f1", source_id="s1", source_name="uploads",
                filename="Sprint Planning Board.docx", uploaded_at="2026-08-01",
            )
        ],
    )
    monkeypatch.setattr(
        qa, "active_conversation_attachment_names",
        lambda cid: ["Roadmap Notes.docx"],
    )
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _route_out())
    monkeypatch.setattr(
        qa, "compose_ask_answer",
        lambda dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k: {
            "answer": "ok", "key_points": [], "citations": [],
            "confidence": 0.5, "unanswered": "",
        },
    )
    qa.answer(enterprise_id="ent", question="what should we prioritize?", dataset="acme")
    assert "Sprint Planning Board.docx" in captured["input"]
    assert "Roadmap Notes.docx" in captured["input"]
    # No document BODY text — the DocumentFileRef type carries no body field
    # at all, and the conversation-attachment source above is mocked as
    # already name-only, matching what `active_conversation_attachment_names`
    # actually returns (see test_ask_runner.py's body-stripping proof).


def test_empty_document_index_leaves_routing_unchanged(monkeypatch):
    """T6 — an empty index (no workspace files, no conversation attachments)
    or a read failure degrades `route()`'s input to the plain routing text —
    routing behaves exactly as today."""
    monkeypatch.setattr(qa, "list_company_files", lambda cid: [])
    monkeypatch.setattr(qa, "active_conversation_attachment_names", lambda cid: [])
    captured = {}
    monkeypatch.setattr(qa, "llm_call", lambda **k: captured.update(k) or _route_out())
    monkeypatch.setattr(
        qa, "compose_ask_answer",
        lambda dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k: {
            "answer": "ok", "key_points": [], "citations": [],
            "confidence": 0.5, "unanswered": "",
        },
    )
    qa.answer(enterprise_id="ent", question="what happened last week?", dataset="acme")
    assert captured["input"].endswith("Question: what happened last week?")

    # Read failure degrades the same way.
    def _boom(cid):
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(qa, "list_company_files", _boom)
    captured.clear()
    qa.answer(enterprise_id="ent", question="what happened last week?", dataset="acme")
    assert captured["input"].endswith("Question: what happened last week?")


def test_no_interceptor_ever_sees_the_filename_augmented_string(monkeypatch):
    """T6a (AC5a) — the byte-identical AC1 routing text reaches every
    interceptor; NONE of them ever see the filename-augmented string built
    for `route()`. A filename like "Sprint Planning Board.docx" carries the
    same tracker nouns an attachment body does, so leaking it upward would
    reopen this exact bug through a new door."""
    from app.document_sources import DocumentFileRef

    monkeypatch.setattr(
        qa, "list_company_files",
        lambda cid: [
            DocumentFileRef(
                id="f1", source_id="s1", source_name="uploads",
                filename="Sprint Planning Board.docx", uploaded_at="2026-08-01",
            )
        ],
    )
    seen_by_interceptor = {}

    def _spy_is_call_digest(q):
        seen_by_interceptor["text"] = q
        return False

    monkeypatch.setattr(qa, "is_call_digest", _spy_is_call_digest)
    captured_router_input = {}
    monkeypatch.setattr(
        qa, "llm_call", lambda **k: captured_router_input.update(k) or _route_out()
    )
    monkeypatch.setattr(
        qa, "compose_ask_answer",
        lambda dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k: {
            "answer": "ok", "key_points": [], "citations": [],
            "confidence": 0.5, "unanswered": "",
        },
    )
    qa.answer(enterprise_id="ent", question="what should we prioritize?", dataset="acme")
    assert "Sprint Planning Board.docx" not in seen_by_interceptor["text"]
    # ...while route() itself DID get the augmented string — proving this is
    # a real split, not an accident of the interceptor never running.
    assert "Sprint Planning Board.docx" in captured_router_input["input"]


# ── Part 3 — capability gates (an interceptor may only claim what it can serve)

def test_tracker_lookup_requires_capability(monkeypatch):
    """T7 (Part 3) — with NO tracker connected, a bare PM-noun-plus-verb
    question (no attachment, no tracker named — the generic match AC9 calls
    out) must not reach the tracker path and must not be answered with
    "connect Jira". Driven through `qa_agent.answer`, not by calling
    `is_jira_lookup` directly — the defect is that the interceptor CLAIMS the
    turn."""
    import app.db as db

    monkeypatch.setattr(db, "get_connection", lambda cid, prov: None)
    monkeypatch.setattr(
        "app.connector_lookup.registry.connected_providers", lambda cid: []
    )
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    monkeypatch.setattr(
        qa, "compose_ask_answer",
        lambda dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k: {
            "answer": "a real answer about the launch", "key_points": [],
            "citations": [], "confidence": 0.5, "unanswered": "",
        },
    )
    out = qa.answer(
        enterprise_id="ent", question="show me the open tickets for the launch",
        dataset="acme",
    )
    assert out.get("_skill_source") != "connector-lookup", out
    assert out.get("_skill_action") != "Tracker lookup", out
    assert "connect" not in out["answer"].lower()


def test_data_analysis_requires_tabular_data(monkeypatch, tmp_path):
    """T8 — with NO tabular data uploaded, a data-analysis-shaped question
    does not reach the DS path (the exact defect that sent an attached PDF
    into a data-science refusal in 458ms with no model call)."""
    monkeypatch.setattr(qa.datasets, "raw_path", lambda slug: tmp_path / slug / "raw")
    monkeypatch.setattr(qa, "_ds_claude_enabled", lambda ent: False)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    monkeypatch.setattr(
        qa, "compose_ask_answer",
        lambda dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k: {
            "answer": "a real answer", "key_points": [], "citations": [],
            "confidence": 0.5, "unanswered": "",
        },
    )
    import app.ds.chat_analysis as ca

    def _must_not_run(**k):
        raise AssertionError("chat_analysis.answer must not run with no tabular data")

    monkeypatch.setattr(ca, "answer", _must_not_run)
    out = qa.answer(
        enterprise_id="ent", question="can you analyze our product usage data?",
        dataset="acme",
    )
    assert out["answer"] == "a real answer"


def test_call_index_listing_already_gates_on_call_source(monkeypatch):
    """T9 (AC11 — regression test only, no new gating logic). The call-index
    listing path is ALREADY correct: `answer_listing` returns None when the
    index isn't `usable`, and the call site already falls through on None."""
    import app.call_index as ci

    monkeypatch.setattr(ci, "ensure_fresh", lambda ent: ci.Freshness(connected=False))
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    monkeypatch.setattr(
        qa, "compose_ask_answer",
        lambda dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k: {
            "answer": "a real answer", "key_points": [], "citations": [],
            "confidence": 0.5, "unanswered": "",
        },
    )
    out = qa.answer(
        enterprise_id="ent", question="give me the 5 latest transcripts",
        dataset="acme",
    )
    assert out["answer"] == "a real answer"


def test_declined_precondition_falls_through_to_a_real_answer(monkeypatch):
    """T10 (AC12) — a declined precondition (no tracker connected, no
    tabular data) falls through to normal routing and produces a REAL
    answer. The user never sees a canned refusal caused by the decline —
    an interceptor that declines must be invisible to the user."""
    import app.db as db

    monkeypatch.setattr(db, "get_connection", lambda cid, prov: None)
    monkeypatch.setattr(
        "app.connector_lookup.registry.connected_providers", lambda cid: []
    )
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    monkeypatch.setattr(
        qa, "compose_ask_answer",
        lambda dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k: {
            "answer": "the launch board has 12 open items", "key_points": [],
            "citations": [], "confidence": 0.5, "unanswered": "",
        },
    )
    out = qa.answer(
        enterprise_id="ent", question="show me the open tickets for the launch",
        dataset="acme",
    )
    assert out["answer"] == "the launch board has 12 open items"
    assert "connect" not in out["answer"].lower()
    assert "no tracker is connected" not in out["answer"].lower()


def test_tracker_lookup_still_fires_when_connected(monkeypatch):
    """T11 — with a tracker CONNECTED, "check jira for open bugs" still
    reaches the tracker path. Guards against fixing the false positives
    (T7) by breaking the true ones."""
    import app.db as db

    monkeypatch.setattr(
        db, "get_connection",
        lambda cid, prov: {"token_json_encrypted": "enc"} if prov == "jira" else None,
    )
    from app.connector_lookup import tracker as tracker_mod

    monkeypatch.setattr(
        tracker_mod, "answer",
        lambda **k: {"answer": "jira answer", "_skill": None,
                     "_skill_action": "Tracker lookup", "_skill_source": "connector-lookup"},
    )
    out = qa.answer(
        enterprise_id="ent", question="check jira for open bugs", dataset="acme",
    )
    assert out["_skill_source"] == "connector-lookup"
    assert out["_skill_action"] == "Tracker lookup"


# ═══════ Direct-path retrieval sees the question, not the thread ═══════════
#
# `qa_agent.answer`'s direct path used to fold the whole rendered history onto
# the question and hand THAT string to `compose_ask_answer` — one string that
# then drove all four retrieval consumers (the shared embedding, KG theme
# kNN, the document catalog's lexical channel, and Stage N filename
# matching) plus the model's own view of "the question". A long thread turned
# each of those into a thread-wide search instead of a question-scoped one,
# and any filename the ASSISTANT happened to cite earlier in the thread
# permanently outranked whatever the current turn was actually about. History
# still reaches the model — it rides `compose_ask_answer`'s own `history`
# argument now, exactly as the skill-routed path (`_answer_single_shot`,
# already correct) has always done.


def test_direct_path_embeds_the_message_not_the_thread(monkeypatch, fake_llm):
    """AC1 — regression, RED today. The text embedded for retrieval on the
    direct path must be the user's current message, byte-identical to
    `question` — never the folded thread."""
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())  # → direct path
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }
    embedded_texts: list = []

    def _embed(texts, **kw):
        embedded_texts.append(list(texts))
        return [[0.03] * 1536 for _ in texts]

    monkeypatch.setattr("app.graph.embeddings.embed_texts", _embed)

    history = [
        {"role": "user", "content": "what did users say about the onboarding flow?"},
        {"role": "assistant", "content": "Most complaints were about the email step."},
    ]
    qa.answer(
        enterprise_id="ent", question="how do we price?", dataset="acme",
        history=history,
    )

    assert embedded_texts == [["how do we price?"]]


def test_prior_turn_filename_is_not_a_named_match(monkeypatch, fake_llm):
    """AC3 — regression, RED today, the sharpest one. History contains an
    ASSISTANT turn citing a filename; the current message names no document.
    Stage N must return zero named matches for that file — it must not be
    force-loaded ahead of topical rank."""
    from app.document_sources import DocumentFileRef

    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    monkeypatch.setattr(
        "app.ask_runner.list_company_files",
        lambda cid: [
            DocumentFileRef(
                id="f1", source_id="s1", source_name="uploads",
                filename="Pricing_2025.docx", uploaded_at="2026-08-01",
            )
        ],
    )
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }
    history = [
        {"role": "user", "content": "what's our latest pricing?"},
        {"role": "assistant", "content": "Per Pricing_2025.docx, tiers are..."},
    ]

    out = qa.answer(
        enterprise_id="ent", question="how many customers signed up last quarter?",
        dataset="acme", history=history,
    )

    named = [d for d in out.get("documents", []) if d.get("match") == "named"]
    assert named == [], f"Pricing_2025.docx force-loaded from a prior turn: {named}"


def test_direct_path_lexical_query_is_the_message_not_the_thread(monkeypatch, fake_llm):
    """AC2 — regression, RED today. `find_catalog_candidates`'s `query=`
    argument (the lexical channel) must be the bare message, not the folded
    thread."""
    from app.document_sources import DocumentFileRef

    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    # Stage T only runs once the index is non-empty (`document_grounding`
    # short-circuits to `("", [])` for a company with no uploads/attachments/
    # connected docs at all) — seed one upload so the lexical channel is
    # actually reached.
    monkeypatch.setattr(
        "app.ask_runner.list_company_files",
        lambda cid: [
            DocumentFileRef(
                id="f1", source_id="s1", source_name="uploads",
                filename="unrelated.docx", uploaded_at="2026-08-01",
            )
        ],
    )
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }
    seen_queries: list = []

    def _find_catalog_candidates(enterprise_id, *, query, **kw):
        seen_queries.append(query)
        return []

    monkeypatch.setattr(
        "app.ask_runner.find_catalog_candidates", _find_catalog_candidates
    )

    history = [
        {"role": "user", "content": "what did users say about the onboarding flow?"},
        {"role": "assistant", "content": "Most complaints were about the email step."},
    ]
    qa.answer(
        enterprise_id="ent", question="how do we price?", dataset="acme",
        history=history,
    )

    assert seen_queries == ["how do we price?"]


def test_history_still_reaches_the_prompt(monkeypatch, fake_llm):
    """AC5 — the rendered history is still present in the composed user
    content. This fix changes only what RETRIEVAL sees, not what the model
    sees."""
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }
    history = [
        {"role": "user", "content": "what did users say about the onboarding flow?"},
        {"role": "assistant", "content": "Most complaints were about the email step."},
    ]
    qa.answer(
        enterprise_id="ent", question="how do we price?", dataset="acme",
        history=history,
    )

    rendered = qa._render_history(history)
    assert rendered
    assert rendered in fake_llm["calls"][0]["user"]


def test_decision_log_question_is_the_bare_message(
    monkeypatch, fake_llm, isolated_settings
):
    """AC6 — the `agent_decision_log` row for decision_type='answer' records
    the bare message in factors['question'], not the folded thread."""
    import json

    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }
    history = [
        {"role": "user", "content": "what did users say about the onboarding flow?"},
        {"role": "assistant", "content": "Most complaints were about the email step."},
    ]
    qa.answer(
        enterprise_id="co-decision-log", question="how do we price?", dataset="acme",
        history=history,
    )

    rows = (
        isolated_settings["supabase"].table("agent_decision_log").select("*")
        .eq("decision_type", "answer").execute().data
    )
    assert len(rows) == 1
    factors = rows[0]["factors"]
    if isinstance(factors, str):
        factors = json.loads(factors)
    assert factors["question"] == "how do we price?"
    assert len(factors["question"]) < 200  # not thread-scale


def test_no_history_composition_is_byte_identical(monkeypatch, fake_llm):
    """AC8 — `history=None` and `history=[]` both produce identical retrieval
    inputs and an identical composed prompt."""
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }
    qa.answer(
        enterprise_id="ent", question="how do we price?", dataset="acme",
        history=None,
    )
    qa.answer(
        enterprise_id="ent", question="how do we price?", dataset="acme",
        history=[],
    )

    assert fake_llm["calls"][0]["user"] == fake_llm["calls"][1]["user"]
    assert (
        fake_llm["calls"][0]["kwargs"]["user_cacheable_prefix"]
        == fake_llm["calls"][1]["kwargs"]["user_cacheable_prefix"]
    )


def test_empty_message_with_long_history_retrieves_on_the_empty_message(
    monkeypatch, fake_llm
):
    """A whitespace-only message does not silently fall back to retrieving on
    the thread — retrieval sees exactly the (whitespace) message, stated
    explicitly rather than left emergent."""
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }
    embedded_texts: list = []

    def _embed(texts, **kw):
        embedded_texts.append(list(texts))
        return [[0.03] * 1536 for _ in texts]

    monkeypatch.setattr("app.graph.embeddings.embed_texts", _embed)

    history = [
        {"role": "user", "content": "what did users say about the onboarding flow?"},
        {"role": "assistant", "content": "Most complaints were about the email step."},
    ]
    qa.answer(
        enterprise_id="ent", question="   ", dataset="acme", history=history,
    )

    assert embedded_texts == [["   "]]


def test_history_render_failure_does_not_break_retrieval(monkeypatch, fake_llm):
    """`render_history_block` raising must not fail the answer — it degrades
    to no history block and retrieval still runs on the bare message."""
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    def _boom(history, **kw):
        raise RuntimeError("history render blew up")

    monkeypatch.setattr("app.ask_runner.render_history_block", _boom)
    embedded_texts: list = []

    def _embed(texts, **kw):
        embedded_texts.append(list(texts))
        return [[0.03] * 1536 for _ in texts]

    monkeypatch.setattr("app.graph.embeddings.embed_texts", _embed)

    history = [
        {"role": "user", "content": "what did users say about the onboarding flow?"},
    ]
    out = qa.answer(
        enterprise_id="ent", question="how do we price?", dataset="acme",
        history=history,
    )

    assert out["answer"] == "x"
    assert embedded_texts == [["how do we price?"]]


def test_no_turn_text_in_logs(monkeypatch, fake_llm, caplog):
    """AC10 — no log record emitted across a direct-path answer with history
    contains a turn body, a rendered-history fragment, a document title, or a
    filename."""
    from app.document_sources import DocumentFileRef

    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    monkeypatch.setattr(
        "app.ask_runner.list_company_files",
        lambda cid: [
            DocumentFileRef(
                id="f1", source_id="s1", source_name="uploads",
                filename="Pricing_2025.docx", uploaded_at="2026-08-01",
            )
        ],
    )
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }
    history = [
        {"role": "user", "content": "SECRET_TURN_MARKER what's our pricing?"},
        {"role": "assistant", "content": "Per Pricing_2025.docx, tiers are ANSWER_BODY_TEXT."},
    ]

    with caplog.at_level("DEBUG"):
        qa.answer(
            enterprise_id="ent", question="anything else?", dataset="acme",
            history=history,
        )

    for record in caplog.records:
        msg = record.getMessage()
        assert "SECRET_TURN_MARKER" not in msg
        assert "ANSWER_BODY_TEXT" not in msg
        assert "Pricing_2025.docx" not in msg


def test_skill_path_retrieval_inputs_unchanged(monkeypatch):
    """AC7 — the skill-routed path is byte-unchanged: `_kg_grounding` and
    `document_grounding` still receive the bare question, never the folded
    thread. It was already correct; this fix must not touch it."""
    _seed_custom_skill(monkeypatch)
    seen: dict = {}

    def _fake_kg(eid, q):
        seen["kg_question"] = q
        return "", False

    def _fake_docs(eid, q):
        seen["docs_question"] = q
        return "", []

    monkeypatch.setattr(qa, "_kg_grounding", _fake_kg)
    monkeypatch.setattr(qa, "document_grounding", _fake_docs)
    monkeypatch.setattr(qa, "llm_call", lambda **k: _answer_out())

    history = [
        {"role": "user", "content": "what did users say about onboarding?"},
        {"role": "assistant", "content": "Mostly about the email step."},
    ]
    qa.answer(
        enterprise_id="ent", question="turn that into a plan", dataset="acme",
        pinned_skill=CUSTOM_SKILL, history=history,
    )

    assert seen["kg_question"] == "turn that into a plan"
    assert seen["docs_question"] == "turn that into a plan"


# ── ★ typed scope-driven gate on the connector-lookup interceptors ───────────
# A project surface (private/group) hands `qa_agent.answer(scope=...)` a
# `SurfaceScope`. `_skip_project_connectors(scope, routing_text, history)`
# replaces the old `active_project_id()` ContextVar predicate: it SKIPS the
# tracker / named-source / document interceptors for a project surface UNLESS
# the question NAMES a source one of them can serve. So a named-source project
# question is ADMITTED (AC9); an UNNAMED PM-noun question ("what tasks are
# open?") is skipped and grounds in the folded project block (AC10); and for a
# MAIN ask (`scope is None`) the helper is always False, so the three guards
# behave byte-for-byte as before (AC11). All directions are proved.
from app.surface_scope import Surface, SurfaceScope

_PROJECT_GROUP_SCOPE = SurfaceScope(surface=Surface.project_group, project_id=1)


def _stub_answer_generation(monkeypatch):
    """Stub the router + the (expensive) answer-generation call so a
    fell-through turn returns fast with a recognisable answer."""
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())
    monkeypatch.setattr(
        qa, "compose_ask_answer",
        lambda dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k: {
            "answer": "grounded-from-project-block", "key_points": [], "citations": [],
            "confidence": 0.5, "unanswered": "",
        },
    )


def test_project_named_source_admits_connector_but_unnamed_skips(monkeypatch):
    """AC9/AC10: on a PROJECT surface a NAMED tracker question is ADMITTED to
    the tracker interceptor; an UNNAMED PM-noun question is SKIPPED and grounds
    in the project block. AC11: a MAIN ask (scope=None) fires as before."""
    import app.connector_lookup.tracker as tracker_mod

    _stub_answer_generation(monkeypatch)
    monkeypatch.setattr(qa, "is_jira_lookup", lambda text, history=None: True)
    monkeypatch.setattr(tracker_mod, "any_connected", lambda eid: True)
    # named_trackers is empty UNLESS the question actually names a tracker.
    monkeypatch.setattr(
        tracker_mod, "named_trackers",
        lambda text: ["jira"] if "jira" in text.lower() else [],
    )
    monkeypatch.setattr(
        tracker_mod, "answer",
        lambda **k: {
            "answer": "tracker result", "_skill_source": "connector-lookup",
            "_skill_action": "Tracker lookup",
        },
    )

    # MAIN (scope=None): interceptor fires exactly as before (AC11).
    out_main = qa.answer(enterprise_id="ent", question="what tasks are open?", dataset="acme")
    assert out_main.get("_skill_source") == "connector-lookup", out_main

    # PROJECT + NAMED source ("jira"): admitted → interceptor fires (AC9).
    out_named = qa.answer(
        enterprise_id="ent", question="what's the status of the jira ticket?",
        dataset="acme", scope=_PROJECT_GROUP_SCOPE,
    )
    assert out_named.get("_skill_source") == "connector-lookup", out_named

    # PROJECT + UNNAMED PM-noun: skipped → grounds in the project block (AC10).
    out_unnamed = qa.answer(
        enterprise_id="ent", question="what tasks are open?",
        dataset="acme", scope=_PROJECT_GROUP_SCOPE,
    )
    assert out_unnamed.get("_skill_source") != "connector-lookup", out_unnamed
    assert out_unnamed.get("answer") == "grounded-from-project-block", out_unnamed


def test_skip_connectors_forced_false_routes_pm_noun_to_tracker_is_red(monkeypatch):
    """MUTATION (AC10, PI13): force `_skip_project_connectors` to always return
    False (the pre-ticket blanket-off-for-project extreme) and the UNNAMED
    PM-noun question is hijacked into the tracker interceptor → RED. Restoring
    the real named-source decision → GREEN (proved by the test above)."""
    import app.connector_lookup.tracker as tracker_mod

    _stub_answer_generation(monkeypatch)
    monkeypatch.setattr(qa, "is_jira_lookup", lambda text, history=None: True)
    monkeypatch.setattr(tracker_mod, "any_connected", lambda eid: True)
    monkeypatch.setattr(tracker_mod, "named_trackers", lambda text: [])
    monkeypatch.setattr(
        tracker_mod, "answer",
        lambda **k: {
            "answer": "tracker result", "_skill_source": "connector-lookup",
            "_skill_action": "Tracker lookup",
        },
    )
    # The mutation: always admit (never skip) on a project surface.
    monkeypatch.setattr(qa, "_skip_project_connectors", lambda scope, rt, hist: False)

    out = qa.answer(
        enterprise_id="ent", question="what tasks are open?",
        dataset="acme", scope=_PROJECT_GROUP_SCOPE,
    )
    # With the gate defeated the unnamed PM-noun is wrongly hijacked to the
    # tracker — this is the RED the named-source decision prevents.
    assert out.get("_skill_source") == "connector-lookup", out


def test_project_named_connector_and_document_admit(monkeypatch):
    """AC9: a project ask naming a CONNECTOR reaches the named-source branch,
    and one naming a DOCUMENT provider reaches the document-lookup branch."""
    import app.connector_lookup.registry as registry_mod
    import app.connector_lookup.tracker as tracker_mod

    _stub_answer_generation(monkeypatch)
    monkeypatch.setattr(qa, "is_jira_lookup", lambda text, history=None: False)
    monkeypatch.setattr(tracker_mod, "named_trackers", lambda text: [])
    monkeypatch.setattr(
        qa, "is_connector_lookup",
        lambda text, history=None: {"slack"} if "slack" in text.lower() else None,
    )
    monkeypatch.setattr(
        qa, "document_lookup_candidates",
        lambda text: {"confluence"} if "runbook" in text.lower() else set(),
    )
    monkeypatch.setattr(registry_mod, "connected_providers", lambda eid: ["confluence"])
    monkeypatch.setattr(
        registry_mod, "answer_for_hints",
        lambda **k: {
            "answer": "read source", "_skill_source": "connector-lookup",
            "_skill_action": "Named source lookup",
        },
    )

    out_conn = qa.answer(
        enterprise_id="ent", question="what did slack say about pricing?",
        dataset="acme", scope=_PROJECT_GROUP_SCOPE,
    )
    assert out_conn.get("_skill_source") == "connector-lookup", out_conn

    out_doc = qa.answer(
        enterprise_id="ent", question="what does our onboarding runbook say?",
        dataset="acme", scope=_PROJECT_GROUP_SCOPE,
    )
    assert out_doc.get("_skill_source") == "connector-lookup", out_doc


def test_main_scope_none_connector_guards_byte_identical(monkeypatch):
    """AC11: for a MAIN ask (scope=None) each interceptor fires exactly as the
    pre-ticket `_project_scoped_ask()`-False path did — the helper returns
    False for `scope is None` and for `Surface.main`."""
    import app.connector_lookup.registry as registry_mod

    _stub_answer_generation(monkeypatch)
    monkeypatch.setattr(qa, "is_jira_lookup", lambda text, history=None: False)
    monkeypatch.setattr(qa, "is_connector_lookup", lambda text, history=None: {"slack"})
    monkeypatch.setattr(
        registry_mod, "answer_for_hints",
        lambda **k: {
            "answer": "read from slack", "_skill_source": "connector-lookup",
            "_skill_action": "Named source lookup",
        },
    )
    q = "what did slack say about the pricing change?"

    # scope=None and Surface.main both behave identically (interceptor fires).
    for scope in (None, SurfaceScope(surface=Surface.main)):
        out = qa.answer(enterprise_id="ent", question=q, dataset="acme", scope=scope)
        assert out.get("_skill_source") == "connector-lookup", (scope, out)

    # And `_skip_project_connectors` is False for both, directly.
    assert qa._skip_project_connectors(None, q, None) is False
    assert qa._skip_project_connectors(SurfaceScope(surface=Surface.main), q, None) is False


# ── ★ an explicit project-content ask beats a STALE connector mention ────────
# `is_connector_lookup` also fires on a sticky-thread follow-up (e.g. a bare
# "context") whenever `history` names a connector a few turns back, even
# though the CURRENT message names nothing. Left alone that stale hit vetoed
# the sixth-branch project loop for exactly the phrasing
# `is_project_content_request` exists to admit. `_skip_project_connectors`
# now re-checks `is_connector_lookup` history-free: a connector named in THIS
# message still wins outright; a connector that only shows up once history is
# added is stale, and an explicit content ask lifts the veto.
_PRIOR_SLACK_MENTION_HISTORY = [
    {"role": "user", "content": "check slack for the pricing decision"},
    {"role": "assistant", "content": "Here is what I found in Slack about pricing."},
]


def test_stale_connector_history_does_not_veto_explicit_context_ask():
    """A bare 'give me the context' names no connector itself; the sticky
    Slack mention lives only in `history`. The project branch must NOT be
    vetoed — `_skip_project_connectors` returns True (skip the connector
    interceptors, let the sixth branch claim the turn)."""
    assert qa._skip_project_connectors(
        _PROJECT_GROUP_SCOPE, "give me the context", _PRIOR_SLACK_MENTION_HISTORY,
    ) is True


def test_in_message_connector_still_wins_over_project_branch():
    """When the CURRENT message itself names a connector ('...from slack'),
    the connector must still win: `_skip_project_connectors` returns False
    (do NOT skip the connector interceptors — the project branch does not
    steal this turn)."""
    assert qa._skip_project_connectors(
        _PROJECT_GROUP_SCOPE,
        "give me the full context from slack",
        _PRIOR_SLACK_MENTION_HISTORY,
    ) is False


def test_context_ask_with_no_connector_anywhere_is_unchanged():
    """Control: no connector named in the message OR history — the project
    branch fires exactly as it did before this fix (unrelated history, or
    none at all, never triggers the stale-connector carve-out)."""
    assert qa._skip_project_connectors(
        _PROJECT_GROUP_SCOPE, "give me the context", [],
    ) is True
    assert qa._skip_project_connectors(
        _PROJECT_GROUP_SCOPE, "give me the context", None,
    ) is True


def test_stale_connector_ask_reaches_project_branch_end_to_end(monkeypatch):
    """End-to-end through `qa.answer()`: a project-surface turn with a prior
    Slack mention in history, asking a bare 'give me the context', reaches
    the sixth-branch project tool loop rather than falling through to the
    connector/company-wide path."""
    scope = SurfaceScope(
        surface=Surface.project_group, project_id=1,
        extra_tools=({"name": "get_project_memory"},),
    )

    def _fake_scoped_tool_answer(*, scope, question, history, enterprise_id, dataset):
        return {"answer": "project-scoped-context", "_skill_source": "project-tools"}

    monkeypatch.setattr(qa, "_try_scoped_tool_answer", _fake_scoped_tool_answer)

    out = qa.answer(
        enterprise_id="ent", question="give me the context",
        dataset="acme", scope=scope, history=_PRIOR_SLACK_MENTION_HISTORY,
    )
    assert out.get("_skill_source") == "project-tools", out
    assert out.get("answer") == "project-scoped-context", out
