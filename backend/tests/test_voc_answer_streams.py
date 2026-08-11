"""The voice-of-customer answers publish tokens as they generate.

These are the SLOWEST answers the product produces — a `max_tokens=12000`
synthesis over a full window — and until 2026-08-11 they had no live preview:
`qa_agent.answer` accepted `on_delta` and never passed it to
`call_digest.answer`. Measured on staging, 76.8s of an 83.6s turn was spent in
that call with a static spinner on screen the whole time.

THE RULE THE WHOLE CHANGE RESTS ON: **at most ONE generation per turn may
receive the sink.** `on_delta` is bound to a single
`app.ask_stream.AnswerFieldExtractor` that is never reset between generations,
so a second one's tokens are appended to the first's abandoned text and the
user reads one garbled answer assembled from two. A streamed call must
therefore be TERMINAL for the turn: if it can fall through on failure, it must
not receive the sink. That is the whole reason `call_digest._answer_query` and
the pinned `_answer_voc_report` are deliberately left unstreamed — not a
preference about which answers deserve a preview.

That rule is enforced here by the `gateway` fixture, which counts the
generations handed a live sink and asserts the count at TEARDOWN. Every test in
this file is checked whether or not it thinks to look, and so is every test
added later. Pinning the RULE is strictly stronger than pinning the two
instances of it: it does not care what the parameter is spelled, which helper
it is threaded through, or whether it is passed positionally.

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

Every one of those defeats is a re-spelling, and no source-text or AST guard
survives one. So: stub the gateway, drive `qa_agent.answer` down each route
with a RECORDING SINK, and assert on the TEXT THE CLIENT WOULD HAVE SEEN.

The routes are driven through `qa_agent.answer` — NOT `call_digest.answer` —
because the sink was dropped at the qa_agent CALL SITE, and a test that calls
the callee itself cannot see that.

The stub gateway INVOKES the sink it is handed. Without that, "the sink
received nothing" would pass for a route that forwards the sink perfectly, and
this file would be false-green a fourth time.

Decoding fragments is `app.ask_stream`'s own suite; the transport is the
gateway's.
"""
from __future__ import annotations

import contextlib

import pytest

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
    thing `on_delta` is bound to — including its defining property, that it is
    ONE object for the whole turn and is never reset between generations.
    Assertions in this file are about `.text`, never about how the sink was
    passed, spelled or named.
    """

    def __init__(self):
        self.fragments: list[str] = []

    def __call__(self, fragment: str) -> None:
        self.fragments.append(fragment)

    @property
    def text(self) -> str:
        return "".join(self.fragments)


class _Gateway:
    """Recording stand-in for `app.graph.gateway.llm_call`.

    Records every generation's `purpose`, and separately the purposes of the
    generations that were handed a LIVE sink — the quantity the turn-level
    invariant is about.

    A generation can be made to go wrong in the THREE shapes that make a turn
    continue — and all three are needed, because each is invisible to the
    others' tests:

      * `raises_on`   — it throws.
      * `returns_none_on` is not a stub setting; it is what
        `_answer_voc_report` does with a failed generation, reached via
        `raises_on("voc_from_kg")`.
      * `returns_empty_on` — it returns NORMALLY with a schema-valid but
        DEGENERATE payload (empty `answer`). No exception, no None. This is
        the shape that survives an assertion battery built only around the
        first two: a caller that retries on a blank synthesis, or treats a
        blank one as "declined" and falls through, runs a second generation
        without anything ever raising.

    In every case the fragments are published BEFORE the failure. That is the
    point: a model that dies (or comes back empty) after publishing half an
    answer is the case where a non-terminal streamed path does damage, and a
    stub that failed before streaming would hide exactly the defect.
    """

    def __init__(self):
        self.purposes: list[str | None] = []
        self.streamed: list[str | None] = []
        self._failing: set[str] = set()
        self._empty: set[str] = set()

    def raises_on(self, purpose: str) -> None:
        self._failing.add(purpose)

    def returns_empty_on(self, purpose: str) -> None:
        """Come back schema-valid but with nothing in `answer` — the model
        produced tokens and none of them survived extraction."""
        self._empty.add(purpose)

    def __call__(self, **kw):
        purpose = kw.get("purpose")
        self.purposes.append(purpose)
        sink = kw.get("on_delta")
        if sink is not None:
            self.streamed.append(purpose)
            for fragment in _FRAGMENTS:
                sink(fragment)
        if purpose in self._failing:
            raise RuntimeError(f"{purpose}: model died mid-generation")
        if purpose in self._empty:
            return _Result(dict(_payload(), answer=""))
        return _Result(_payload())


@pytest.fixture
def gateway(monkeypatch):
    """The recording gateway, plus the turn-level invariant, checked for every
    test in this file — including ones written after this comment.

    Patched in TWO places on purpose. `call_digest` imports `llm_call` inside
    each function, so it resolves through `app.graph.gateway` at call time;
    `qa_agent` aliased it into its own namespace at import. Patching only one
    leaves a live gateway on the other half of the route under test.

    The teardown assertion is the design rule itself, and it is deliberately
    NOT raised from inside `__call__`: the production ladder is full of
    `except Exception` handlers that would swallow it and degrade to a
    fallback, turning a real violation into a green run.

    A test that legitimately drives more than one TURN must reset
    `gw.streamed` between them — the budget is per turn, not per test.

    KNOWN BLIND SPOT, RECORDED SO THE NEXT PERSON DOES NOT WALK INTO IT. This
    budget observes `app.graph.gateway.llm_call` and nothing else. The
    skill-less DIRECT answer path does not generate through the gateway at
    all: `app.ask_runner` (line ~1741) calls `app.llm.call_json(...,
    on_json_delta=on_delta)`, imported at its module top and never patched
    here. A full answer's worth of text can therefore reach a sink on that
    path while `gw.streamed` stays empty. That is OUT OF SCOPE for this file,
    which is about the voice-of-customer routes — but anyone extending the
    budget to cover the direct path must patch `app.llm.call_json` and count
    `on_json_delta` too, or they will get a green run that proves nothing.
    """
    gw = _Gateway()
    monkeypatch.setattr(gateway_mod, "llm_call", gw)
    monkeypatch.setattr(qa, "llm_call", gw)
    yield gw
    assert len(gw.streamed) <= 1, (
        f"{len(gw.streamed)} generations in ONE turn were handed the caller's "
        f"sink ({gw.streamed}). At most ONE may be. The sink is a single "
        "AnswerFieldExtractor that is never reset between generations, so the "
        "second one's tokens are appended to the first's ABANDONED text and "
        "the user reads one garbled answer assembled from two — and "
        "token_stream._accum replays it to anyone who reloads. A streamed "
        "call must be TERMINAL for the turn: if it can fall through on "
        "failure, it must not receive the sink."
    )


def _stub_live_calls(monkeypatch):
    """A company with one fetchable call — the digest's happy path.

    Same recipe as `test_call_digest.test_answer_report_failure_degrades_
    gracefully`, plus the capability gate the qa_agent interceptors consult.
    """
    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1)])
    monkeypatch.setattr(cd, "has_call_source", lambda cid: True)


def _stub_kg(monkeypatch):
    """A populated knowledge graph, so the pinned VoC path runs its generation
    instead of declining before it (returning None with nothing generated)."""
    monkeypatch.setattr(qa, "_retrieve_kg_bundle", lambda eid, q: {"signals": [1], "themes": []})
    import app.graph.retrieval as retrieval
    monkeypatch.setattr(retrieval, "render_context_section", lambda b: "KG SIGNAL")


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


def test_unpinned_voc_dispatch_publishes_the_answer_as_it_generates(gateway, monkeypatch):
    """THE route this PR fixes: no pinned skill, routed to VoC, report-shaped."""
    # The route, pinned by behaviour rather than by line number: every
    # interceptor ahead of the VoC dispatch declines this phrasing.
    assert sr.is_call_digest(_UNPINNED_REPORT_Q) is False
    assert sr.is_voc_report_request(_UNPINNED_REPORT_Q) is False
    assert call_index.is_listing_request(_UNPINNED_REPORT_Q) is False
    assert cd.is_voc_query(_UNPINNED_REPORT_Q) is False  # → the report pass

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
        f"it says it does (generations: {gateway.purposes})"
    )
    assert sink.text == _STREAMED, (
        "the unpinned voice-of-customer answer published NOTHING to the "
        f"client (sink saw {sink.fragments!r}). This is the slowest answer in "
        "the product — 76.8s of measured spinner — and it is the exact defect "
        "this PR exists to fix. Whatever is between qa_agent.answer and the "
        "report llm_call is no longer carrying the caller's sink."
    )
    # Terminal: the streamed generation IS the answer, nothing ran after it.
    assert gateway.purposes == ["voc_report"]


def test_call_digest_interception_publishes_the_answer_as_it_generates(gateway, monkeypatch):
    """"summarize the customer calls from last week" — the pre-routing
    interception, a different call site from the VoC dispatch above. A sink
    dropped at any ONE site is a route that spins with no preview, so each is
    driven separately."""
    question = "summarize the customer calls from last week"
    assert sr.is_call_digest(question) is True
    assert cd.is_voc_query(question) is False

    _stub_live_calls(monkeypatch)
    sink = _RecordingSink()

    out = qa.answer(
        enterprise_id="ent", question=question, dataset="acme", on_delta=sink,
    )

    assert out["_skill_source"] == "call-digest"
    assert sink.text == _STREAMED, (
        f"the call-digest interception published nothing (saw {sink.fragments!r})"
    )
    assert gateway.purposes == ["voc_report"]


def test_bare_voc_report_request_publishes_the_answer_as_it_generates(gateway, monkeypatch):
    """"give me a voice of customer report" — the third call site, reached by
    `is_voc_report_request` when a call source is connected."""
    question = "give me a voice of customer report"
    assert sr.is_voc_report_request(question) is True
    assert cd.is_voc_query(question) is False

    _stub_live_calls(monkeypatch)
    sink = _RecordingSink()

    out = qa.answer(
        enterprise_id="ent", question=question, dataset="acme", on_delta=sink,
    )

    assert out["_skill_source"] == "call-digest"
    assert sink.text == _STREAMED, (
        f"the bare VoC-report route published nothing (saw {sink.fragments!r})"
    )
    assert gateway.purposes == ["voc_report"]


def test_windowed_call_question_publishes_the_answer_as_it_generates(gateway, monkeypatch):
    """The fourth call site: a question the call INDEX resolves to a window.

    Reached with the index stubbed to claim the window, which is what it does
    in production for "what did customers say last Tuesday"-shaped asks.
    """
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
    assert gateway.purposes == ["voc_report"]


# ── the routes that MUST NOT stream ──────────────────────────────────────────


def test_query_shaped_voc_publishes_nothing(gateway, monkeypatch):
    """`call_digest._answer_query` must stay silent.

    The caller hands a sink all the way down (the same sink the report route
    streams with), so this pins the QUERY PATH's own decision, not the absence
    of a sink upstream.
    """
    question = "what are customers feedback"
    assert cd.is_voc_query(question) is True

    _stub_live_calls(monkeypatch)
    _route_voc(monkeypatch)
    sink = _RecordingSink()

    out = qa.answer(
        enterprise_id="ent", question=question, dataset="acme", on_delta=sink,
    )

    assert out["_skill_source"] == "voc-query", (
        f"expected the query pass, got {out.get('_skill_source')!r} — the turn "
        f"never reached the path under test (generations: {gateway.purposes})"
    )
    assert "voc_query" in gateway.purposes  # the generation really did run
    assert sink.fragments == [], (
        f"the query path published {sink.fragments!r} to the client. On failure "
        "it falls through to a second, different generation into the same "
        "extractor, so the user would see an abandoned attempt's text with the "
        "real answer appended to it. Read the call-site comment before wiring "
        "this up."
    )


def test_pinned_voc_report_publishes_nothing(gateway, monkeypatch):
    """The PINNED `voice-of-customer-report` (KG-only) must stay silent.

    `_answer_voc_report` returns None on failure, and None does NOT end the
    turn: control falls out of the block into `_answer_single_shot`.
    """
    _stub_live_calls(monkeypatch)
    _stub_kg(monkeypatch)
    sink = _RecordingSink()

    out = qa.answer(
        enterprise_id="ent", question=_UNPINNED_REPORT_Q, dataset="acme",
        pinned_skill="voice-of-customer-report", on_delta=sink,
    )

    assert out["_skill"] == "voice-of-customer-report"
    assert gateway.purposes == ["voc_from_kg"], (
        f"expected exactly the KG-only generation, saw {gateway.purposes} — a "
        "second purpose means the turn fell through to another answer path and "
        "the sink assertion below would be measuring the wrong thing"
    )
    assert sink.fragments == [], (
        f"the pinned VoC report published {sink.fragments!r} to the client. It "
        "returns None on failure and falls through to a SECOND generation into "
        "the same never-reset extractor. Read the call-site comment first."
    )


# ── the fall-through branches: where a second generation actually happens ────
#
# The tests above pin which routes stream on the HAPPY path. These pin the rule
# that makes streaming safe at all, on the paths where a turn really does run a
# second generation. Each forces the first generation to publish fragments and
# THEN fail, which is the only case where a non-terminal streamed path does
# damage — a stub that failed before streaming would hide exactly the defect.


def test_a_failed_pinned_voc_generation_does_not_stream_twice(gateway, monkeypatch):
    """FIRST GENERATION RETURNS NONE, and None does not end the turn.

    The documented hazard, executed end to end: `_answer_voc_report`'s
    generation dies, it returns None, and control falls through to
    `_answer_single_shot` — a SECOND full generation into the SAME extractor.
    Only the generation that actually answers may reach the client.
    """
    gateway.raises_on("voc_from_kg")
    _stub_live_calls(monkeypatch)
    _stub_kg(monkeypatch)
    sink = _RecordingSink()

    out = qa.answer(
        enterprise_id="ent", question=_UNPINNED_REPORT_Q, dataset="acme",
        pinned_skill="voice-of-customer-report", on_delta=sink,
    )

    # The fall-through really happened — otherwise this test proves nothing.
    assert gateway.purposes == ["voc_from_kg", "skill_answer"], (
        f"expected the KG generation to fail and the turn to fall through to "
        f"the single-shot answer, saw {gateway.purposes}"
    )
    assert gateway.streamed == ["skill_answer"], (
        f"the abandoned KG generation streamed to the client ({gateway.streamed}). "
        "It returned None and the turn carried on, so its partial text is now "
        "sitting in the extractor with the real answer appended to it."
    )
    assert sink.text == _STREAMED, (
        f"the client saw {sink.text!r} — one turn must publish ONE answer's "
        f"worth of text, not an abandoned attempt plus the real one"
    )
    assert out["answer"]


def test_a_failed_query_generation_does_not_stream_twice(gateway, monkeypatch):
    """FIRST GENERATION RAISES, and the turn continues to the report pass.

    `_answer_query` fails mid-generation and `call_digest.answer` degrades to
    the full report — a second generation, which DOES stream. If the query
    pass had also streamed, the user would read the abandoned attempt with the
    report appended to it.
    """
    gateway.raises_on("voc_query")
    _stub_live_calls(monkeypatch)
    _route_voc(monkeypatch)
    sink = _RecordingSink()

    out = qa.answer(
        enterprise_id="ent", question="what are customers feedback",
        dataset="acme", on_delta=sink,
    )

    assert gateway.purposes == ["voc_query", "voc_report"], (
        f"expected the query pass to fail and degrade to the report, saw "
        f"{gateway.purposes}"
    )
    assert gateway.streamed == ["voc_report"], (
        f"the abandoned query generation streamed to the client "
        f"({gateway.streamed}) before the report replaced it"
    )
    assert sink.text == _STREAMED
    assert out["_skill_source"] == "call-digest"


def test_a_failed_report_generation_ends_the_turn(gateway, monkeypatch):
    """The streamed route must be TERMINAL — that is what licenses streaming it.

    Its generation dies mid-answer; the digest returns its own error payload
    and the turn STOPS. If it instead fell through to another answer path, the
    partial text already on screen would be joined by a second generation's.
    """
    gateway.raises_on("voc_report")
    _stub_live_calls(monkeypatch)
    _route_voc(monkeypatch)
    sink = _RecordingSink()

    out = qa.answer(
        enterprise_id="ent", question=_UNPINNED_REPORT_Q, dataset="acme",
        on_delta=sink,
    )

    assert gateway.purposes == ["voc_report"], (
        f"a generation ran after the streamed report failed ({gateway.purposes}) "
        "— the streamed route is no longer terminal, so the abandoned partial "
        "answer on screen is about to have a second one appended to it"
    )
    assert "error" in out["answer"].lower()
    assert out["_skill_source"] == "call-digest"


def test_a_failure_before_any_generation_still_answers_once(gateway, monkeypatch):
    """The fetch itself explodes, before anything has been published.

    Nothing has reached the client yet, so a fall-through here would be
    harmless — what must still hold is the budget: whatever ends up answering
    the turn, exactly one generation may stream. Pins the invariant on the
    branch where the digest raises OUT of `call_digest.answer` instead of
    degrading inside it.
    """
    def _boom(*a, **k):
        raise RuntimeError("fireflies unreachable")

    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "has_call_source", lambda cid: True)
    monkeypatch.setattr(cd, "build_corpus", _boom)
    _route_voc(monkeypatch)
    sink = _RecordingSink()

    # Whether the turn dies or degrades is the ladder's business; the sink
    # budget is not, and it holds either way.
    with contextlib.suppress(Exception):
        qa.answer(
            enterprise_id="ent", question=_UNPINNED_REPORT_Q, dataset="acme",
            on_delta=sink,
        )

    assert len(gateway.streamed) <= 1
    assert sink.text in ("", _STREAMED), (
        f"the client saw {sink.text!r} — a turn publishes one answer's worth "
        "of text or none, never two"
    )


def test_an_empty_synthesis_does_not_start_a_second_generation(gateway, monkeypatch):
    """THE GENERATION SUCCEEDS AND SAYS NOTHING — the third failure shape.

    No exception and no None: a schema-valid payload whose `answer` is empty.
    Nothing above this test ever produces that input, and two plausible
    hardenings survive everything else in this file because of it:

      * retry the synthesis once when `answer` comes back blank, forwarding
        the same sink — two streaming calls in one turn;
      * treat a blank synthesis as "this route declined" and fall through to
        the generic answer — a streamed route made non-terminal.

    Both publish the abandoned attempt's fragments and then a second
    generation's on top, into the same never-reset extractor. Traced with the
    real `AnswerFieldExtractor`, the client ends up reading the first
    attempt's partial text with a stray brace from the second attempt's JSON
    stuck to it, never converging on the answer that was actually stored.

    An empty answer is a legitimate terminal outcome. It must be RETURNED, not
    retried into the same sink.
    """
    gateway.returns_empty_on("voc_report")
    _stub_live_calls(monkeypatch)
    _route_voc(monkeypatch)
    sink = _RecordingSink()

    out = qa.answer(
        enterprise_id="ent", question=_UNPINNED_REPORT_Q, dataset="acme",
        on_delta=sink,
    )

    assert gateway.purposes == ["voc_report"], (
        f"a blank synthesis started a SECOND generation ({gateway.purposes}). "
        "Whether it retried the same call or fell through to another answer "
        "path, the fragments already published are now the prefix of a "
        "different generation's text and the client never sees either answer "
        "whole."
    )
    assert out["_skill_source"] == "call-digest"
    assert out["answer"] == "", (
        "the empty synthesis must come back as the answer it is, not be "
        "papered over by a second run"
    )
    assert sink.text == _STREAMED


# ── callers that pass no sink at all ─────────────────────────────────────────


def test_a_caller_without_a_sink_still_gets_the_answer(gateway, monkeypatch):
    """`on_delta` is optional and advisory: omitting it must behave exactly as
    before the streaming change, on the same route that streams."""
    _stub_live_calls(monkeypatch)
    _route_voc(monkeypatch)

    out = qa.answer(
        enterprise_id="ent", question=_UNPINNED_REPORT_Q, dataset="acme",
    )

    assert out["_skill_source"] == "call-digest"
    assert out["answer"]
    assert gateway.purposes == ["voc_report"]
    assert gateway.streamed == []


def test_call_digest_answer_without_a_sink_is_unchanged(gateway, monkeypatch):
    """The callee's own contract, one level below the routes above: no sink in,
    no sink out, and the payload is still authoritative.

    ON THE `gateway` FIXTURE DELIBERATELY. This test used to build its own
    stub, which made it the one test in the file not covered by the turn-level
    budget — harmless in itself (it passes no sink) but a working template for
    opting out of the invariant without saying so. There is now no stub in
    this file that the budget does not see. `gateway.streamed == []` is also a
    stricter statement of what the bespoke stub checked: not "the kwarg was
    None" but "nothing was ever published".
    """
    _stub_live_calls(monkeypatch)

    out = cd.answer(enterprise_id="co", question="summarize customer calls")

    assert gateway.purposes == ["voc_report"], (
        f"report call never ran; saw {gateway.purposes}"
    )
    assert gateway.streamed == []
    assert out["_skill_source"] == "call-digest"
    assert out["answer"]
