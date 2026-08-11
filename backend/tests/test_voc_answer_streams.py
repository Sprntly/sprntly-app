"""The voice-of-customer answers publish tokens as they generate.

These are the SLOWEST answers the product produces — a `max_tokens=12000`
synthesis over a full window — and until 2026-08-11 they were the only ones
with no live preview: `qa_agent.answer` accepted `on_delta` and simply did not
pass it on to `call_digest.answer` or `_answer_voc_report`. Measured on
staging, 76.8s of an 83.6s turn was spent in that call with a static spinner
on screen the whole time.

What these tests pin is the WIRING — that the sink reaches the `llm_call` —
plus the one path we deliberately left unstreamed. Decoding the fragments is
`app.ask_stream`'s own suite; the transport is the gateway's.
"""
from __future__ import annotations

import inspect

import app.call_digest as cd
import app.graph.gateway as gateway_mod
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


def _capture_report_call(monkeypatch):
    """Drive `cd.answer` down the REPORT branch, returning the captured kwargs.

    Same stubbing recipe as `test_answer_report_failure_degrades_gracefully`:
    a key, one call, and a report-shaped question.
    """
    seen: dict = {}

    def fake_llm_call(**kw):
        seen.setdefault(kw.get("purpose"), kw)
        return _Result(_payload())

    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1)])
    monkeypatch.setattr(gateway_mod, "llm_call", fake_llm_call)
    return seen


def test_report_path_forwards_the_sink_to_the_gateway(monkeypatch):
    """`call_digest.answer(on_delta=sink)` reaches the report `llm_call`."""
    seen = _capture_report_call(monkeypatch)
    sink = lambda _t: None  # noqa: E731

    cd.answer(
        enterprise_id="co", question="summarize customer calls", on_delta=sink,
    )

    assert "voc_report" in seen, f"report call never ran; saw {list(seen)}"
    assert seen["voc_report"].get("on_delta") is sink, (
        "the report llm_call must receive the caller's sink — passing None "
        "here is exactly the defect this test exists for"
    )


def test_report_path_without_a_sink_is_unchanged(monkeypatch):
    """Callers that omit `on_delta` must behave exactly as before."""
    seen = _capture_report_call(monkeypatch)

    cd.answer(enterprise_id="co", question="summarize customer calls")

    assert "voc_report" in seen
    assert seen["voc_report"].get("on_delta") is None


def test_query_path_is_deliberately_not_streamed():
    """The query path must NOT gain a sink — see the comment at its call site.

    It is followed by a fall-through to the report, so streaming it would
    publish an abandoned attempt's partial text and then append a second,
    different generation to it. Whoever wires it up should have to come here
    and say why.
    """
    src = inspect.getsource(cd._answer_query)
    assert "on_delta" not in src, (
        "the query path was wired to stream — read the call-site comment about "
        "the report fallback before changing this test"
    )


def test_every_qa_agent_digest_call_site_forwards_the_sink():
    """`qa_agent` is where the sink was dropped; pin EVERY call site.

    The bug was a route that simply did not pass the sink, so a NEW route added
    later without it is the same defect and must fail here.

    PARSED, NOT COUNTED. The first version of this test compared
    `src.count("call_digest.answer(")` against `src.count("on_delta=on_delta,")`
    and was VACUOUS: the module has 4 call sites but 10 `on_delta=on_delta,`
    forwards (most at unrelated routes), so deleting a sink from a call_digest
    site left 9 >= 4 and the guard still passed. It could not fail on the
    defect it exists for. Walking the AST names the actual call sites, so a
    bare one is unmissable.
    """
    import ast

    from app import qa_agent

    tree = ast.parse(inspect.getsource(qa_agent))
    bare: list[int] = []
    sites = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (
            isinstance(fn, ast.Attribute)
            and fn.attr == "answer"
            and isinstance(fn.value, ast.Name)
            and fn.value.id == "call_digest"
        ):
            continue
        sites += 1
        if not any(kw.arg == "on_delta" for kw in node.keywords):
            bare.append(node.lineno)

    assert sites >= 4, (
        f"expected at least the 4 known call_digest.answer call sites, found "
        f"{sites} — they moved; update this guard rather than deleting it"
    )
    assert not bare, (
        f"call_digest.answer called without on_delta at qa_agent.py line(s) "
        f"{bare} — that route publishes no tokens and its answer will land in "
        "one lump after a minute of spinner"
    )


def test_voc_from_kg_forwards_the_sink():
    """The PINNED voice-of-customer route streams too (`max_tokens=12000`)."""
    from app import qa_agent

    src = inspect.getsource(qa_agent._answer_voc_report)
    assert "on_delta=on_delta" in src, (
        "_answer_voc_report accepts a sink but does not pass it to its "
        "llm_call — the pinned VoC route would stay spinner-only"
    )
