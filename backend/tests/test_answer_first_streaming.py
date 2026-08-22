"""Answer-first streaming (behind `ANSWER_FIRST_STREAMING_ENABLED`, default OFF).

Covers the five contracts the feature must hold:
  (a) flag OFF  -> the forced-JSON path is used, unchanged.
  (b) flag ON   -> the answer streams as PLAIN text BEFORE the structured fields
                   are produced (and the streamed prompt has no JSON envelope).
  (c) the structured fields are still returned in the final payload (and the
      answer survives a failed metadata pass).
  (d) the terminal/reset invariant fires on decline -> fall-through.
  (e) a Group-B call (background warm) is unaffected by the flag.

The LLM primitives are faked; no network.
"""
from __future__ import annotations

import pytest

from app import answer_first
from app.ask_stream import AnswerFieldExtractor
from app.prompts import (
    ASK_SYSTEM,
    ASK_USER_TEMPLATE_QUESTION_ONLY,
)


# ── Prompt derivation ───────────────────────────────────────────────────────


def test_answer_only_system_strips_json_envelope_keeps_formatting():
    out = answer_first.answer_only_system(ASK_SYSTEM)
    # The JSON-envelope directive is gone...
    assert "STRICT JSON" not in out
    assert "citations` array in the JSON" not in out
    # ...but the inline-source discipline and the chart/formatting rules stay.
    assert "[Source:" in out
    assert "chart" in out
    assert "## Finding" in out


def test_answer_only_user_strips_json_template_keeps_question():
    user = ASK_USER_TEMPLATE_QUESTION_ONLY.format(question="What is our pricing?")
    out = answer_first.answer_only_user(user)
    assert "Return JSON of this shape" not in out
    assert '"citations"' not in out
    # The question still rides through.
    assert "What is our pricing?" in out


def test_answer_only_user_is_noop_without_template():
    # The skill / call-digest sites build a bare user turn (no JSON scaffold).
    user = "Conversation so far:\n...\n\nQuestion: how many signals?"
    assert answer_first.answer_only_user(user) == user


# ── Core orchestration: stream first, structure after ───────────────────────


def _make_fakes(events, streamed, *, structured_result=None, structured_raises=False):
    systems = {}

    def stream_text_fn(system, user, sink):
        systems["answer"] = system
        # The answer streams as plain text via the display sink...
        sink("The pricing is usage-based. ")
        sink("[Source: pm_manual]")
        events.append("answer_streamed")
        return "The pricing is usage-based. [Source: pm_manual]"

    def structured_fn(system, user, schema):
        systems["meta"] = system
        systems["meta_user"] = user
        events.append("structured")
        if structured_raises:
            raise RuntimeError("metadata call boom")
        return structured_result

    def sink(text):
        streamed.append(text)

    return stream_text_fn, structured_fn, sink, systems


def test_run_streams_answer_before_structured_and_returns_all_fields():
    events: list[str] = []
    streamed: list[str] = []
    meta = {
        "key_points": ["usage-based", "no free tier"],
        "citations": [{"source": "pm_manual", "evidence": "usage-based"}],
        "confidence": 0.77,
        "unanswered": "",
    }
    stream_text_fn, structured_fn, sink, _ = _make_fakes(
        events, streamed, structured_result=meta
    )

    payload = answer_first.run(
        question="What is our pricing?",
        forced_system=ASK_SYSTEM,
        forced_user="Question: What is our pricing?",
        on_delta=sink,
        default_confidence=0.5,
        stream_text_fn=stream_text_fn,
        structured_fn=structured_fn,
    )

    # (b) the answer streamed as plain text BEFORE the structured pass ran.
    assert events == ["answer_streamed", "structured"]
    assert "".join(streamed) == "The pricing is usage-based. [Source: pm_manual]"
    # (c) every structured field is present in the final payload.
    assert payload["answer"] == "The pricing is usage-based. [Source: pm_manual]"
    assert payload["key_points"] == ["usage-based", "no free tier"]
    assert payload["citations"] == [{"source": "pm_manual", "evidence": "usage-based"}]
    assert payload["confidence"] == 0.77
    assert payload["unanswered"] == ""


def test_run_degrades_to_answer_with_defaults_when_metadata_fails():
    events: list[str] = []
    streamed: list[str] = []
    stream_text_fn, structured_fn, sink, _ = _make_fakes(
        events, streamed, structured_raises=True
    )

    payload = answer_first.run(
        question="What is our pricing?",
        forced_system=ASK_SYSTEM,
        forced_user="Question: What is our pricing?",
        on_delta=sink,
        default_confidence=0.42,
        stream_text_fn=stream_text_fn,
        structured_fn=structured_fn,
    )

    # The prose already shipped; metadata is advisory.
    assert payload["answer"] == "The pricing is usage-based. [Source: pm_manual]"
    assert payload["key_points"] == []
    assert payload["citations"] == []
    assert payload["confidence"] == 0.42  # caller default
    assert payload["unanswered"] == ""


def test_run_feeds_answer_only_prompt_to_the_streamed_call():
    events: list[str] = []
    streamed: list[str] = []
    stream_text_fn, structured_fn, sink, systems = _make_fakes(
        events, streamed, structured_result={"confidence": 0.9}
    )
    answer_first.run(
        question="q",
        forced_system=ASK_SYSTEM,
        forced_user="Question: q",
        on_delta=sink,
        default_confidence=0.5,
        stream_text_fn=stream_text_fn,
        structured_fn=structured_fn,
    )
    # The system prompt the STREAMED call saw had the JSON envelope stripped.
    assert "STRICT JSON" not in systems["answer"]


def test_metadata_pass_receives_question_context_and_answer_not_prose_alone():
    """Calibration lock-in: the confidence/metadata pass must see the question +
    grounding context + the produced answer, so it judges GROUNDING (the way the
    baseline self-assessed while generating) rather than rating finished prose in
    a vacuum. Regressing this reintroduces the 0.90->0.15 miscalibration.
    """
    events: list[str] = []
    streamed: list[str] = []
    stream_text_fn, structured_fn, sink, systems = _make_fakes(
        events, streamed, structured_result={"confidence": 0.9}
    )
    grounded_user = (
        "LIVE CONTEXT FROM CONNECTED SOURCES: pricing is usage-based per chunk.\n\n"
        "Question: What is our pricing?"
    )
    answer_first.run(
        question="What is our pricing?",
        forced_system=ASK_SYSTEM,
        forced_user=grounded_user,
        on_delta=sink,
        default_confidence=0.5,
        stream_text_fn=stream_text_fn,
        structured_fn=structured_fn,
    )
    meta_user = systems["meta_user"]
    # The question and the retrieved context both reach the confidence pass...
    assert "What is our pricing?" in meta_user
    assert "usage-based per chunk" in meta_user
    # ...alongside the produced answer.
    assert "The pricing is usage-based. [Source: pm_manual]" in meta_user
    # And the metadata system prompt carries the grounding-based confidence rubric.
    assert "SOURCE MATERIAL" in systems["meta"]
    assert "grounded" in systems["meta"].lower()


# ── The raw-text sink bypass (emit_text vs feed) ────────────────────────────


def test_emit_text_forwards_raw_markdown_but_feed_would_drop_it():
    out_emit: list[str] = []
    AnswerFieldExtractor(out_emit.append).emit_text("## Finding\nPlain markdown")
    assert out_emit == ["## Finding\nPlain markdown"]

    # feed() expects partial-JSON tool input; raw markdown has no `"answer":`
    # key, so it decodes to nothing — which is exactly why answer-first must use
    # emit_text, not feed.
    out_feed: list[str] = []
    AnswerFieldExtractor(out_feed.append).feed("## Finding\nPlain markdown")
    assert out_feed == []


def test_text_sink_uses_emit_text_on_an_extractor():
    out: list[str] = []
    extractor = AnswerFieldExtractor(out.append)
    sink = answer_first._text_sink(extractor)
    sink("hello world")
    assert out == ["hello world"]


def test_text_sink_passthrough_for_plain_callable_and_none():
    out: list[str] = []
    sink = answer_first._text_sink(out.append)
    sink("x")
    assert out == ["x"]
    assert answer_first._text_sink(None) is None


# ── (d) terminal / reset-on-fall-through ────────────────────────────────────


def test_reset_stream_announces_restart_downstream():
    restarts: list[int] = []
    extractor = AnswerFieldExtractor(
        lambda _t: None, on_restart=lambda: restarts.append(1)
    )
    answer_first.reset_stream(extractor)
    assert restarts == [1]


def test_reset_stream_is_a_noop_on_non_extractor_sinks():
    # A plain callable and None must not raise (display-only, never break answer).
    answer_first.reset_stream(lambda _t: None)
    answer_first.reset_stream(None)


def test_declined_stream_is_superseded_not_glued_on_fallthrough():
    """The fall-through contract: a streamed attempt that declines, then a second
    generation into the SAME sink, must not glue attempt 2 onto attempt 1.

    A downstream accumulator (the token_stream replay buffer / browser) rewinds
    on the restart frame `reset_stream` publishes, so the final visible text is
    attempt 2 alone.
    """
    accumulated: list[str] = []

    def on_restart():
        accumulated.clear()  # what the real replay buffer / browser accumulator does

    extractor = AnswerFieldExtractor(accumulated.append, on_restart=on_restart)

    # Attempt 1 streams partial prose, then the pipeline declines (returns None).
    extractor.emit_text("PARTIAL abandoned answer")
    assert accumulated == ["PARTIAL abandoned answer"]

    # Caller resets the sink on fall-through (the wiring qa_agent.answer runs
    # when `_answer_voc_report` returns None under answer-first).
    answer_first.reset_stream(extractor)

    # Attempt 2 streams the real answer into the same sink.
    extractor.emit_text("REAL answer")
    assert accumulated == ["REAL answer"]  # attempt 1 was superseded, not glued


# ── (a)/(e) site branching: flag OFF unchanged, Group-B unaffected ──────────


def _fake_route_decision(skill_id):
    from app.qa_agent import RouteDecision

    return RouteDecision(skill_id, 0.8, "pinned", skill_id or "")


def _stub_voc_deps(monkeypatch):
    import app.graph.retrieval as retrieval_mod
    import app.qa_agent as qa

    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda *a, **k: {"signals": [1]})
    monkeypatch.setattr(retrieval_mod, "render_context_section", lambda *a, **k: "CTX")
    monkeypatch.setattr(qa, "today_line", lambda *a, **k: "", raising=False)
    monkeypatch.setattr(
        qa, "connected_sources_line", lambda *a, **k: "", raising=False
    )
    monkeypatch.setattr(qa, "_render_history", lambda *a, **k: "", raising=False)


def test_voc_report_flag_off_uses_forced_json(monkeypatch):
    """(a) Flag OFF: the pinned-VoC Group-A site takes the forced-JSON path."""
    monkeypatch.delenv("ANSWER_FIRST_STREAMING_ENABLED", raising=False)
    import app.qa_agent as qa

    _stub_voc_deps(monkeypatch)
    seen: dict = {}

    def fake_llm_call(**kwargs):
        seen["json_schema"] = kwargs.get("json_schema")
        seen["forced"] = True

        class _R:
            output = {"answer": "a", "key_points": [], "citations": [],
                      "confidence": 0.8, "unanswered": ""}

        return _R()

    af_called = {"n": 0}
    monkeypatch.setattr(qa, "llm_call", fake_llm_call)
    monkeypatch.setattr(
        answer_first, "gateway",
        lambda **k: af_called.__setitem__("n", af_called["n"] + 1),
    )

    qa._answer_voc_report(
        _fake_route_decision("voice-of-customer-report"),
        "ent-1", "what are customers saying?", [], on_delta=None,
    )

    assert seen.get("forced") is True
    assert seen["json_schema"] is not None  # forced structured output
    assert af_called["n"] == 0  # answer-first NOT used


def test_voc_report_flag_on_uses_answer_first(monkeypatch):
    """(b/e-inverse) Flag ON: the same Group-A site routes through answer-first."""
    monkeypatch.setenv("ANSWER_FIRST_STREAMING_ENABLED", "1")
    import app.qa_agent as qa

    _stub_voc_deps(monkeypatch)
    forced_called = {"n": 0}
    af_seen: dict = {}

    def fake_llm_call(**kwargs):
        forced_called["n"] += 1

        class _R:
            output = {}

        return _R()

    def fake_gateway(**kwargs):
        af_seen["purpose"] = kwargs.get("purpose")
        return {"answer": "streamed", "key_points": ["k"], "citations": [],
                "confidence": 0.7, "unanswered": ""}

    monkeypatch.setattr(qa, "llm_call", fake_llm_call)
    monkeypatch.setattr(answer_first, "gateway", fake_gateway)

    out = qa._answer_voc_report(
        _fake_route_decision("voice-of-customer-report"),
        "ent-1", "what are customers saying?", [], on_delta=None,
    )

    assert af_seen.get("purpose") == "voc_from_kg"  # answer-first used
    assert forced_called["n"] == 0  # forced-JSON NOT used
    assert out["answer"] == "streamed"


def test_group_b_warm_path_ignores_the_flag(monkeypatch):
    """(e) A Group-B call (background warm) stays on forced-JSON even flag ON."""
    monkeypatch.setenv("ANSWER_FIRST_STREAMING_ENABLED", "1")
    import app.ask_runner as ar

    class _Corpus:
        def joined(self):
            return "corpus text"

    monkeypatch.setattr(ar, "load_corpus", lambda dataset: _Corpus())
    monkeypatch.setattr(ar, "connected_sources_line", lambda *a, **k: "", raising=False)
    monkeypatch.setattr(ar, "today_line", lambda *a, **k: "", raising=False)

    seen: dict = {}

    def fake_call_json(**kwargs):
        seen["schema"] = kwargs.get("schema")
        return {"answer": "a", "key_points": [], "citations": [],
                "confidence": 0.8, "unanswered": ""}

    af_called = {"n": 0}
    monkeypatch.setattr(ar, "call_json", fake_call_json)
    monkeypatch.setattr(
        answer_first, "direct",
        lambda **k: af_called.__setitem__("n", af_called["n"] + 1),
    )

    ar._generate_one_sync("some-dataset", "What is our pricing?")

    assert seen.get("schema") is ar._ASK_RESPONSE_SCHEMA  # forced structured
    assert af_called["n"] == 0  # answer-first never touched Group B


# ── direct() wrapper: real two-step over the app.llm transport ──────────────


def test_direct_wrapper_streams_then_structures(monkeypatch):
    """(b/c) The compose_ask_answer wrapper streams the answer via call_md, then
    fills metadata via call_json, and returns the merged payload."""
    import app.llm as llm_mod

    events: list[str] = []
    streamed: list[str] = []
    seen_systems: dict = {}

    def fake_call_md(*, system, user, on_delta=None, **kwargs):
        seen_systems["answer"] = system
        assert on_delta is not None
        on_delta("Answer prose ")
        on_delta("[Source: x]")
        events.append("call_md")
        return "Answer prose [Source: x]"

    def fake_call_json(*, system, user, schema=None, user_cacheable_prefix=None, **kwargs):
        events.append("call_json")
        seen_systems["meta_prefix"] = user_cacheable_prefix
        seen_systems["meta_user"] = user
        return {"key_points": ["kp"], "citations": [{"source": "x", "evidence": "e"}],
                "confidence": 0.88, "unanswered": "nothing"}

    monkeypatch.setattr(llm_mod, "call_md", fake_call_md, raising=False)
    monkeypatch.setattr(llm_mod, "call_json", fake_call_json, raising=False)

    out = answer_first.direct(
        question="q?",
        forced_system=ASK_SYSTEM,
        forced_user="Question: q?",
        cacheable="CORPUS",
        enterprise_id=None,
        on_delta=streamed.append,
        default_confidence=0.5,
    )

    assert events == ["call_md", "call_json"]
    assert "".join(streamed) == "Answer prose [Source: x]"
    assert "STRICT JSON" not in seen_systems["answer"]
    # The confidence pass re-attaches the SAME corpus prefix the answer saw (cache
    # hit) and includes the produced answer — so it judges grounding, not prose.
    assert seen_systems["meta_prefix"] == "CORPUS"
    assert "Answer prose [Source: x]" in seen_systems["meta_user"]
    assert out["answer"] == "Answer prose [Source: x]"
    assert out["key_points"] == ["kp"]
    assert out["confidence"] == 0.88
    assert out["unanswered"] == "nothing"
