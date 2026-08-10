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


# ─── phase frames (which leg is running) ─────────────────────────────────────
# A long answer — a competitive sweep is minutes of web research — used to emit
# exactly the same signal as a two-second one. Phase frames ride the SAME
# channel as the deltas; they must reach subscribers without ever entering the
# replay buffer, which is answer TEXT and nothing else.


def test_phase_frame_never_enters_the_replay_buffer():
    """publish() accumulates only `kind == "delta"`. If a phase leaked into
    `_accum`, a late joiner's catch-up would be glued into the answer markdown
    and the persisted answer would visibly disagree with the preview."""
    token_stream.publish("ask:1", {"kind": "delta", "text": "The answer so far"})
    token_stream.publish("ask:1", {"kind": "phase", "label": "Writing the answer…"})
    token_stream.publish("ask:1", {"kind": "delta", "text": " and the rest"})

    assert token_stream._accum["ask:1"] == "The answer so far and the rest"


def test_phase_frame_with_text_key_still_never_accumulates():
    """Defence in depth: accumulation is keyed on the frame KIND, so even a
    malformed phase frame carrying `text` can't pollute the buffer."""
    token_stream.publish("ask:2", {"kind": "phase", "label": "x", "text": "LEAK"})
    assert "ask:2" not in token_stream._accum


async def test_phase_sink_publishes_to_subscribers():
    loop = asyncio.get_running_loop()
    received: list[dict] = []

    async def _collect():
        async for frame in token_stream.subscribe("ask:3"):
            received.append(frame)

    task = asyncio.create_task(_collect())
    await asyncio.sleep(0)

    sink = token_stream.phase_sink(loop, "ask:3")
    sink("Researching 3 competitors on the web…")
    sink("")  # empty labels are skipped
    await asyncio.sleep(0)
    token_stream.close("ask:3", kind="done")
    await task

    assert received == [
        {"kind": "phase", "label": "Researching 3 competitors on the web…"},
        {"kind": "done"},
    ]


async def test_late_joiner_replays_no_phase_by_design(monkeypatch):
    """A client that reloads mid-generation gets the answer text it missed and
    NO phase. That is the accepted degradation: re-publishing a label whose leg
    may already be over would be a claim with no signal behind it, so the client
    falls back to its generic resumed copy."""
    loop = asyncio.get_running_loop()
    # delta_sink coalesces (see token_stream), so text reaches the replay buffer
    # on a flush rather than instantly. A real generation runs for seconds and
    # crosses the interval many times before anyone reloads; this test would
    # otherwise be asserting against an instantaneous generation that cannot
    # happen. Force the interval to 0 so the first delta flushes, which is what
    # production does for anything lasting longer than _FLUSH_INTERVAL_S.
    monkeypatch.setattr(token_stream, "_FLUSH_INTERVAL_S", 0.0)
    token_stream.phase_sink(loop, "ask:4")("Searching your connected sources…")
    token_stream.delta_sink(loop, "ask:4")("Partial answer")
    await asyncio.sleep(0)

    received: list[dict] = []

    async def _collect():
        async for frame in token_stream.subscribe("ask:4"):
            received.append(frame)

    task = asyncio.create_task(_collect())
    await asyncio.sleep(0)
    token_stream.close("ask:4", kind="done")
    await task

    assert received[0] == {"kind": "replay", "text": "Partial answer"}
    assert not any(f.get("kind") == "phase" for f in received)


async def test_run_ask_job_publishes_phases_and_records_the_route(monkeypatch):
    """The worker wires both hooks: phases onto the SSE channel, the routed
    skill onto the job row (durable, so a reload's poll still finds it)."""
    from app import ask_job_runner as ajr

    def fake_answer(**kwargs):
        kwargs["on_route"]("competitive-intelligence-review", "Competitive intelligence")
        kwargs["on_phase"]("Researching 2 competitors on the web…")
        kwargs["on_delta"]('{"answer": "Report", "key_points": []}')
        return {"answer": "Report", "key_points": [], "citations": [],
                "confidence": 1, "unanswered": ""}

    routes: list[tuple] = []
    monkeypatch.setattr(ajr.qa_agent, "answer", fake_answer)
    monkeypatch.setattr(ajr, "complete_ask_job", lambda i, p: None)
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)
    monkeypatch.setattr(
        ajr, "set_ask_job_route", lambda i, s, a: routes.append((i, s, a))
    )

    received: list[dict] = []

    async def _collect():
        async for frame in token_stream.subscribe(ajr.ask_channel(11)):
            received.append(frame)

    task = asyncio.create_task(_collect())
    await asyncio.sleep(0)

    await ajr.run_ask_job(ask_id=11, enterprise_id="e1", question="q", dataset="d")
    await task

    assert routes == [(11, "competitive-intelligence-review", "Competitive intelligence")]
    assert {"kind": "phase", "label": "Researching 2 competitors on the web…"} in received
    # The answer text is untouched by the phase riding the same channel.
    assert "".join(f["text"] for f in received if f["kind"] == "delta") == "Report"
    assert received[-1] == {"kind": "done"}


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
