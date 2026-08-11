"""The voice-of-customer answers publish tokens as they generate.

These are the SLOWEST answers the product produces — a `max_tokens=12000`
synthesis over a full window — and until 2026-08-11 they had no live preview:
`qa_agent.answer` accepted `on_delta` and never passed it to
`call_digest.answer`. Measured on staging, 76.8s of an 83.6s turn was spent in
that call with a static spinner on screen the whole time.

WHY THIS FILE IS ALL BEHAVIOUR AND NO SPELLING. Three earlier versions pinned
how the code was WRITTEN and every one of them was false-green:

  1. `src.count("call_digest.answer(") <= src.count("on_delta=on_delta,")` —
     vacuous. The module has 4 digest call sites and ~10 `on_delta=on_delta,`
     forwards at unrelated routes, so deleting a sink left 9 >= 4 and the
     guard passed on the very defect it existed for.
  2. An AST walk asserting each `call_digest.answer` call carried a keyword
     NAMED `on_delta`. It never looked at the VALUE: `on_delta=None` at the
     unpinned VoC route — the exact route this PR fixes — kept it green. It
     also matched only `Attribute(value=Name('call_digest'))`, so
     `from app import call_digest as digest; digest.answer(...)` was invisible
     to it, and `qa_agent` already does that function-local import in four
     places.
  3. `"on_delta" not in inspect.getsource(callee)` for the two routes that
     must NOT stream. Moving the `llm_call` into a module-level helper and
     passing the sink positionally satisfies the substring while the route
     starts streaming again.

The lesson is that a guard which reads source text or an AST is checking
SPELLING, and every mutation above is a re-spelling. So: stub the gateway,
drive `qa_agent.answer` down each route with a RECORDING SINK, and assert on
the TEXT THE CLIENT WOULD HAVE SEEN.

Three properties are pinned here, all of them observable:

  * The four routes that reach `call_digest.answer` publish text. They are
    driven through `qa_agent.answer` — NOT `call_digest.answer` directly —
    because the sink was dropped at the qa_agent CALL SITE, and a test that
    calls the callee itself cannot see that.
  * `call_digest._answer_query` publishes NOTHING. It is followed by
    `except -> fall through to the report`, so a mid-generation failure runs a
    SECOND full generation into the same never-reset extractor; streaming both
    yields one garbled preview out of two coherent answers.
  * The PINNED `voice-of-customer-report` publishes NOTHING, for the same
    reason: `_answer_voc_report` returns None on failure and None does not end
    the turn — control falls through to `_answer_single_shot`.

The stub gateway INVOKES the sink it is handed (see `_stub_gateway`). Without
that, "the sink received nothing" would pass for a route that forwards the
sink perfectly, and this file would be false-green a fourth time.

Decoding fragments is `app.ask_stream`'s own suite; the transport is the
gateway's.
"""
from __future__ import annotations

import app.call_digest as cd
import app.call_index as call_index
import app.graph.gateway as gateway_mod
import app.qa_agent as qa
import app.skill_router as sr
from app.kg_ingest.pullers.fireflies import CallTranscript


class _Result:
    def __init__(self, output):
        self.output = output


def _payload():
    return {
        "answer": "ok", "key_points": [], "citations": [],
        "confidence": 0.9, "unanswered": "",
    }


def _call(i):
    """Verbatim from tests/test_call_digest.py, so the two cannot drift."""
    return CallTranscript(
        external_id=f"c{i}", title=f"Call {i}", date="2026-06-20",
        participants=["p@x.com"], overview=f"overview {i}",
        quotes=[{"speaker": "Cust", "text": f"quote {i}"}],
    )


# What a streaming generation publishes. Two fragments rather than one so a
# route that somehow forwards only the first still reads as wrong text.
_FRAGMENTS = ("customers keep asking ", "for a CSV export")
_STREAMED = "".join(_FRAGMENTS)


class _RecordingSink:
    """The Ask worker's token sink, recording what the client would have seen.

    Stands in for `app.ask_stream.AnswerFieldExtractor`, which is the real
    thing `on_delta` is bound to. Assertions in this file are about `.text` —
    never about how the sink was passed, spelled or named.
    """

    def __init__(self):
        self.fragments: list[str] = []

    def __call__(self, fragment: str) -> None:
        self.fragments.append(fragment)

    @property
    def text(self) -> str:
        return "".join(self.fragments)


def _stub_gateway(monkeypatch) -> list[str | None]:
    """One recording gateway for both namespaces; returns the purposes seen.

    Patched in TWO places on purpose. `call_digest` imports `llm_call` inside
    each function, so it resolves through `app.graph.gateway` at call time;
    `qa_agent` aliased it into its own namespace at import. Patching only one
    leaves a live gateway on the other half of the route under test.

    The stub CALLS the sink it is handed before returning. That is what makes
    a negative assertion mean something: with a gateway that ignores `on_delta`
    every "publishes nothing" test would pass no matter how the route is wired.
    """
    purposes: list[str | None] = []

    def fake_llm_call(**kw):
        purposes.append(kw.get("purpose"))
        sink = kw.get("on_delta")
        if sink is not None:
            for fragment in _FRAGMENTS:
                sink(fragment)
        return _Result(_payload())

    monkeypatch.setattr(gateway_mod, "llm_call", fake_llm_call)
    monkeypatch.setattr(qa, "llm_call", fake_llm_call)
    return purposes


def _stub_live_calls(monkeypatch):
    """A company with one fetchable call — the digest's happy path.

    Same recipe as `test_call_digest.test_answer_report_failure_degrades_
    gracefully`, plus the capability gate the qa_agent interceptors consult.
    """
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1)])
    monkeypatch.setattr(cd, "has_call_source", lambda cid: True)


def _route_voc(monkeypatch):
    """Route every question to `voice-of-customer-report`, as the haiku router
    does for the reported phrasings. Keeps the routing model out of the test
    while landing on the dispatch the sink was dropped at."""
    monkeypatch.setattr(
        qa, "route",
        lambda q, **k: qa.RouteDecision("voice-of-customer-report", 0.9, "llm"),
    )


# A question that declines EVERY pre-routing interceptor (asserted in the test
# below, not assumed) and is report-shaped for the digest, so it reaches the
# VoC dispatch and then the streaming report pass.
_UNPINNED_REPORT_Q = "what are the themes in customer feedback"


# ── the routes that MUST stream ──────────────────────────────────────────────


def test_unpinned_voc_dispatch_publishes_the_answer_as_it_generates(monkeypatch):
    """THE route this PR fixes: no pinned skill, routed to VoC, report-shaped.

    Driven through `qa_agent.answer`, because the sink was dropped in
    `qa_agent` — a test that called `call_digest.answer` itself would pass with
    the caller handing over nothing at all.
    """
    # The route, pinned by behaviour rather than by line number: every
    # interceptor ahead of the VoC dispatch declines this phrasing.
    assert sr.is_call_digest(_UNPINNED_REPORT_Q) is False
    assert sr.is_voc_report_request(_UNPINNED_REPORT_Q) is False
    assert call_index.is_listing_request(_UNPINNED_REPORT_Q) is False
    assert cd.is_voc_query(_UNPINNED_REPORT_Q) is False  # → the report pass

    purposes = _stub_gateway(monkeypatch)
    _stub_live_calls(monkeypatch)
    _route_voc(monkeypatch)
    sink = _RecordingSink()

    out = qa.answer(
        enterprise_id="ent", question=_UNPINNED_REPORT_Q, dataset="acme",
        on_delta=sink,
    )

    assert out["_skill_source"] == "call-digest", (
        f"expected the merged digest report, got {out.get('_skill_source')!r} "
        f"— the turn took a different route and this test is not testing what "
        f"it says it does (purposes seen: {purposes})"
    )
    assert sink.text == _STREAMED, (
        "the unpinned voice-of-customer answer published NOTHING to the "
        f"client (sink saw {sink.fragments!r}). This is the slowest answer in "
        "the product — 76.8s of measured spinner — and it is the exact defect "
        "this PR exists to fix. Whatever is between qa_agent.answer and the "
        "report llm_call is no longer carrying the caller's sink."
    )


def test_call_digest_interception_publishes_the_answer_as_it_generates(monkeypatch):
    """"summarize the customer calls from last week" — the pre-routing
    interception, a different call site from the VoC dispatch above. A sink
    dropped at any ONE site is a route that spins with no preview, so each is
    driven separately."""
    question = "summarize the customer calls from last week"
    assert sr.is_call_digest(question) is True
    assert cd.is_voc_query(question) is False

    _stub_gateway(monkeypatch)
    _stub_live_calls(monkeypatch)
    sink = _RecordingSink()

    out = qa.answer(
        enterprise_id="ent", question=question, dataset="acme", on_delta=sink,
    )

    assert out["_skill_source"] == "call-digest"
    assert sink.text == _STREAMED, (
        f"the call-digest interception published nothing (saw {sink.fragments!r})"
    )


def test_bare_voc_report_request_publishes_the_answer_as_it_generates(monkeypatch):
    """"give me a voice of customer report" — the third call site, reached by
    `is_voc_report_request` when a call source is connected."""
    question = "give me a voice of customer report"
    assert sr.is_voc_report_request(question) is True
    assert cd.is_voc_query(question) is False

    _stub_gateway(monkeypatch)
    _stub_live_calls(monkeypatch)
    sink = _RecordingSink()

    out = qa.answer(
        enterprise_id="ent", question=question, dataset="acme", on_delta=sink,
    )

    assert out["_skill_source"] == "call-digest"
    assert sink.text == _STREAMED, (
        f"the bare VoC-report route published nothing (saw {sink.fragments!r})"
    )


def test_windowed_call_question_publishes_the_answer_as_it_generates(monkeypatch):
    """The fourth call site: a question the call INDEX resolves to a window.

    Reached with the index stubbed to claim the window, which is what it does
    in production for "what did customers say last Tuesday"-shaped asks.
    """
    _stub_gateway(monkeypatch)
    _stub_live_calls(monkeypatch)
    monkeypatch.setattr(
        call_index, "windowed_call_question",
        lambda eid, text: cd.parse_window("calls from last week"),
    )
    sink = _RecordingSink()

    out = qa.answer(
        enterprise_id="ent", question=_UNPINNED_REPORT_Q, dataset="acme",
        on_delta=sink,
    )

    assert out["_skill_source"] == "call-digest"
    assert sink.text == _STREAMED, (
        f"the windowed-call route published nothing (saw {sink.fragments!r})"
    )


# ── the routes that MUST NOT stream ──────────────────────────────────────────


def test_query_shaped_voc_publishes_nothing(monkeypatch):
    """`call_digest._answer_query` must stay silent.

    It is followed by `except -> fall through to the report`: a mid-generation
    failure runs a SECOND full generation into the SAME never-reset extractor,
    so streaming both publishes the abandoned attempt's partial text and then
    appends the report's — one garbled preview out of two coherent answers.

    The caller hands a sink all the way down (the same sink the report route
    streams with), so this pins the QUERY PATH's own decision, not the absence
    of a sink upstream.
    """
    question = "what are customers feedback"
    assert cd.is_voc_query(question) is True

    purposes = _stub_gateway(monkeypatch)
    _stub_live_calls(monkeypatch)
    _route_voc(monkeypatch)
    sink = _RecordingSink()

    out = qa.answer(
        enterprise_id="ent", question=question, dataset="acme", on_delta=sink,
    )

    assert out["_skill_source"] == "voc-query", (
        f"expected the query pass, got {out.get('_skill_source')!r} — the turn "
        f"never reached the path under test (purposes seen: {purposes})"
    )
    assert "voc_query" in purposes  # the generation really did run
    assert sink.fragments == [], (
        f"the query path published {sink.fragments!r} to the client. On failure "
        "it falls through to a second, different generation into the same "
        "extractor, so the user would see an abandoned attempt's text with the "
        "real answer appended to it. Read the call-site comment before wiring "
        "this up."
    )


def test_pinned_voc_report_publishes_nothing(monkeypatch):
    """The PINNED `voice-of-customer-report` (KG-only) must stay silent.

    `_answer_voc_report` returns None on failure, and None does NOT end the
    turn: control falls out of the block into `_answer_single_shot` — a second
    full generation into the same extractor, which then replays the abandoned
    text to anyone who reloads. Strictly worse than the spinner it replaced.
    """
    purposes = _stub_gateway(monkeypatch)
    _stub_live_calls(monkeypatch)
    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda eid, q: {"signals": [1], "themes": []})
    import app.graph.retrieval as retrieval
    monkeypatch.setattr(retrieval, "render_context_section", lambda b: "KG SIGNAL")
    sink = _RecordingSink()

    out = qa.answer(
        enterprise_id="ent", question=_UNPINNED_REPORT_Q, dataset="acme",
        pinned_skill="voice-of-customer-report", on_delta=sink,
    )

    assert out["_skill"] == "voice-of-customer-report"
    assert purposes == ["voc_from_kg"], (
        f"expected exactly the KG-only generation, saw {purposes} — a second "
        "purpose means the turn fell through to another answer path and the "
        "sink assertion below would be measuring the wrong thing"
    )
    assert sink.fragments == [], (
        f"the pinned VoC report published {sink.fragments!r} to the client. It "
        "returns None on failure and falls through to a SECOND generation into "
        "the same never-reset extractor. Read the call-site comment first."
    )


# ── callers that pass no sink at all ─────────────────────────────────────────


def test_a_caller_without_a_sink_still_gets_the_answer(monkeypatch):
    """`on_delta` is optional and advisory: omitting it must behave exactly as
    before the streaming change, on the same route that streams."""
    purposes = _stub_gateway(monkeypatch)
    _stub_live_calls(monkeypatch)
    _route_voc(monkeypatch)

    out = qa.answer(
        enterprise_id="ent", question=_UNPINNED_REPORT_Q, dataset="acme",
    )

    assert out["_skill_source"] == "call-digest"
    assert out["answer"]
    assert "voc_report" in purposes


def test_call_digest_answer_without_a_sink_is_unchanged(monkeypatch):
    """The callee's own contract, one level below the routes above: no sink in,
    no sink out, and the payload is still authoritative."""
    seen: dict = {}

    def fake_llm_call(**kw):
        seen.setdefault(kw.get("purpose"), kw)
        return _Result(_payload())

    monkeypatch.setattr(gateway_mod, "llm_call", fake_llm_call)
    _stub_live_calls(monkeypatch)

    out = cd.answer(enterprise_id="co", question="summarize customer calls")

    assert "voc_report" in seen, f"report call never ran; saw {list(seen)}"
    assert seen["voc_report"].get("on_delta") is None
    assert out["_skill_source"] == "call-digest"
