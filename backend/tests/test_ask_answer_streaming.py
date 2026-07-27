"""Chat-answer token streaming: extractor + llm hook + worker publishing.

The Ask answer is generated via forced tool use, so its streamed deltas are
PARTIAL JSON fragments. `app.ask_stream.AnswerFieldExtractor` decodes just the
`answer` string's content out of those fragments; `app.llm.call_json` forwards
the fragments via `on_json_delta`; `app.ask_job_runner` wires the two onto the
`ask:<id>` token_stream channel that GET /v1/ask/{id}/stream relays.

Never hits the network: stub clients mimic the SDK stream protocol, and the
worker test fakes qa_agent.answer. Async tests run under pytest-asyncio auto.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import llm
from app.ask_stream import AnswerFieldExtractor
from app.graph import token_stream


@pytest.fixture(autouse=True)
def _clear_token_stream_state():
    token_stream._subscribers.clear()
    token_stream._accum.clear()
    token_stream._accum_overflowed.clear()
    yield
    token_stream._subscribers.clear()
    token_stream._accum.clear()
    token_stream._accum_overflowed.clear()


def _extract(fragments: list[str]) -> list[str]:
    out: list[str] = []
    ex = AnswerFieldExtractor(out.append)
    for f in fragments:
        ex.feed(f)
    return out


# ─── AnswerFieldExtractor ────────────────────────────────────────────────────


def test_extracts_answer_text_from_one_fragment():
    assert "".join(_extract(['{"answer": "Hello world", "key_points": []}'])) == "Hello world"


def test_fragments_split_anywhere_including_mid_key():
    frags = ['{"ans', 'wer"', ' : ', '"Top churn dri', "ver is onboarding", '", "confidence": 1}']
    assert "".join(_extract(frags)) == "Top churn driver is onboarding"


def test_stops_at_closing_quote_and_ignores_later_fields():
    chunks = _extract(['{"answer": "done", "key_points": ["never streamed"]}', '{"answer": "again"}'])
    assert "".join(chunks) == "done"


def test_simple_escapes_decoded():
    frags = ['{"answer": "line1\\nline2 \\"quoted\\" back\\\\slash\\ttab"}']
    assert "".join(_extract(frags)) == 'line1\nline2 "quoted" back\\slash\ttab'


def test_escape_split_across_fragments():
    # The backslash lands in one fragment, its selector in the next.
    frags = ['{"answer": "a\\', 'nb"}']
    assert "".join(_extract(frags)) == "a\nb"


def test_unicode_escape_split_across_fragments():
    frags = ['{"answer": "caf\\u', '00e9"}']
    assert "".join(_extract(frags)) == "café"


def test_surrogate_pair_split_across_fragments():
    # 🎉 = 🎉 with the pair split across feeds.
    frags = ['{"answer": "party \\ud83c', '\\udf89 time"}']
    assert "".join(_extract(frags)) == "party 🎉 time"


def test_lone_high_surrogate_becomes_replacement_char():
    frags = ['{"answer": "x\\ud83c y"}']
    assert "".join(_extract(frags)) == "x� y"


def test_no_answer_key_streams_nothing():
    assert _extract(['{"other": "field", "no": 1}']) == []


def test_reset_rewinds_for_a_retried_stream():
    out: list[str] = []
    ex = AnswerFieldExtractor(out.append)
    ex.feed('{"answer": "first att')
    ex.reset()  # transient failure → the stream restarts from zero
    ex.feed('{"answer": "second"}')
    assert "".join(out) == "first attsecond"  # display-only; poll is authoritative
    # After the closing quote of the retried stream, nothing more is emitted.
    ex.feed('{"answer": "third"}')
    assert "".join(out) == "first attsecond"


def test_sink_exception_never_propagates():
    def _boom(_: str) -> None:
        raise RuntimeError("display sink died")

    ex = AnswerFieldExtractor(_boom)
    ex.feed('{"answer": "text"}')  # must not raise


def test_extractor_is_callable_and_exposes_reset():
    out: list[str] = []
    ex = AnswerFieldExtractor(out.append)
    ex('{"answer": "via __call__"}')
    assert "".join(out) == "via __call__"
    assert callable(getattr(ex, "reset"))


# ─── call_json → on_json_delta hook ──────────────────────────────────────────


class _JsonStreamCtx:
    """Mimics `client.messages.stream(...)` for a forced-tool call: iterating
    the stream yields `input_json` events; the final message carries the
    assembled tool_use block."""

    def __init__(self, fragments, final_input):
        self._fragments = fragments
        self._final_input = final_input

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for f in self._fragments:
            yield SimpleNamespace(type="input_json", partial_json=f)

    @property
    def text_stream(self):  # pragma: no cover — must not be used on this path
        raise AssertionError("tool-use streaming must iterate events, not text_stream")

    def get_final_message(self):
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="tool_use", name="submit_response", input=self._final_input
                )
            ],
            usage=SimpleNamespace(input_tokens=1, output_tokens=2),
            stop_reason="tool_use",
        )


class _JsonStreamStubClient:
    def __init__(self, fragments, final_input):
        outer = self

        class _Messages:
            def stream(self, **kwargs):
                outer.stream_kwargs = kwargs
                return _JsonStreamCtx(fragments, final_input)

        self.messages = _Messages()


def test_call_json_forwards_partial_json_fragments(monkeypatch, isolated_settings):
    frags = ['{"answer": "str', 'eamed"', ', "key_points": []}']
    final = {"answer": "streamed", "key_points": [], "citations": [], "confidence": 1, "unanswered": ""}
    monkeypatch.setattr(llm, "get_client", lambda: _JsonStreamStubClient(frags, final))
    seen: list[str] = []

    out = llm.call_json(
        system="s", user="u", schema={"type": "object"},
        stream=True, on_json_delta=seen.append,
    )

    assert seen == frags, "every partial-JSON fragment forwarded in order"
    assert out == final, "assembled tool input still returned"


def test_call_json_with_extractor_streams_answer_text(monkeypatch, isolated_settings):
    frags = ['{"answer": "Hello ', '**world**", "key_points": ["a"]}']
    final = {"answer": "Hello **world**", "key_points": ["a"], "citations": [], "confidence": 1, "unanswered": ""}
    monkeypatch.setattr(llm, "get_client", lambda: _JsonStreamStubClient(frags, final))
    chunks: list[str] = []

    out = llm.call_json(
        system="s", user="u", schema={"type": "object"},
        stream=True, on_json_delta=AnswerFieldExtractor(chunks.append),
    )

    assert "".join(chunks) == "Hello **world**"
    assert out["answer"] == "Hello **world**"


# ─── worker → token_stream channel ───────────────────────────────────────────


async def test_run_ask_job_streams_answer_text_then_done(monkeypatch):
    from app import ask_job_runner as ajr

    frags = ['{"answer": "Hi ', 'there", "key_points": []}']

    def fake_answer(**kwargs):
        on_delta = kwargs["on_delta"]
        for f in frags:
            on_delta(f)
        return {"answer": "Hi there", "key_points": [], "citations": [],
                "confidence": 1, "unanswered": ""}

    completed: dict = {}
    monkeypatch.setattr(ajr.qa_agent, "answer", fake_answer)
    monkeypatch.setattr(ajr, "complete_ask_job", lambda i, p: completed.setdefault(i, p))
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)

    received: list[dict] = []

    async def _collect():
        async for frame in token_stream.subscribe(ajr.ask_channel(7)):
            received.append(frame)

    task = asyncio.create_task(_collect())
    await asyncio.sleep(0)  # subscriber registers before the job starts

    await ajr.run_ask_job(ask_id=7, enterprise_id="e1", question="q", dataset="d")
    await task

    streamed = "".join(f["text"] for f in received if f["kind"] == "delta")
    assert streamed == "Hi there", "answer text (not raw JSON) reaches the channel"
    assert received[-1] == {"kind": "done"}
    assert completed[7]["answer"] == "Hi there", "persisted payload untouched"


async def test_run_ask_job_failure_closes_channel_with_error(monkeypatch):
    from app import ask_job_runner as ajr

    def fake_answer(**kwargs):
        raise RuntimeError("model exploded")

    failed: dict = {}
    monkeypatch.setattr(ajr.qa_agent, "answer", fake_answer)
    monkeypatch.setattr(ajr, "fail_ask_job", lambda i, m: failed.setdefault(i, m))
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)

    received: list[dict] = []

    async def _collect():
        async for frame in token_stream.subscribe(ajr.ask_channel(9)):
            received.append(frame)

    task = asyncio.create_task(_collect())
    await asyncio.sleep(0)

    await ajr.run_ask_job(ask_id=9, enterprise_id="e1", question="q", dataset="d")
    await task

    assert received[-1] == {"kind": "error"}, "subscriber released on failure"
    assert 9 in failed
