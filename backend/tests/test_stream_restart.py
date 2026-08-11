"""A mid-generation gateway retry must not show attempt 1 glued to attempt 2.

When `app.llm` retries a streamed call, the model re-emits the WHOLE answer
from zero on the same token_stream channel. Rewinding only the JSON parse state
(`AnswerFieldExtractor.reset`) leaves the accumulated text in place: the
channel's replay buffer and the browser's accumulator both keep attempt 1 and
append attempt 2 to it, so the reader sees a truncated sentence followed by the
full answer.

The HTML generations (PRD / evidence) survived this because the frontend spots
a second `<!doctype` and slices. Chat answers are plain MARKDOWN and have no
such marker, so nothing cleared them. These tests pin the explicit signal that
replaces that heuristic for the markdown case: `reset()` publishes
`{"kind":"restart"}`, which drops the replay buffer server-side and the
accumulator client-side.

Everything here is DISPLAY ONLY — the authoritative answer is the persisted job
row the client polls — so the tests also pin that every new path fails soft: a
restart callback that raises must not break the answer or the retry.

Async tests run under pytest-asyncio auto mode (asyncio_mode = auto).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import anthropic
import pytest

from app import llm
from app.ask_stream import AnswerFieldExtractor
from app.graph import token_stream


@pytest.fixture(autouse=True)
def _clear_state():
    for _d in (token_stream._subscribers, token_stream._accum,
               token_stream._pending_drains):
        _d.clear()
    token_stream._accum_overflowed.clear()
    yield
    for _d in (token_stream._subscribers, token_stream._accum,
               token_stream._pending_drains):
        _d.clear()
    token_stream._accum_overflowed.clear()


def _delta(text: str) -> dict:
    return {"kind": "delta", "text": text}


async def _collect(channel: str, out: list[dict]) -> None:
    async for frame in token_stream.subscribe(channel):
        out.append(frame)


# ─── AnswerFieldExtractor: reset announces, construction does not ────────────


def test_reset_notifies_the_restart_callback():
    """The extractor is the only object that knows the stream restarted (the
    retry layer calls its reset), so it has to be the one that says so."""
    beats: list[str] = []
    ex = AnswerFieldExtractor(lambda t: None, on_restart=lambda: beats.append("restart"))

    ex.feed('{"answer": "attempt one par')
    assert beats == [], "no restart announced while a single attempt streams"

    ex.reset()
    assert beats == ["restart"]


def test_construction_does_not_announce_a_restart():
    """`__init__` sets up the same initial state `reset()` does. If it went
    through `reset()`, every ask would publish a restart frame before emitting
    a single character — a claim about text that never existed."""
    beats: list[str] = []
    AnswerFieldExtractor(lambda t: None, on_restart=lambda: beats.append("restart"))
    assert beats == []


def test_restart_callback_is_optional_and_the_sink_stays_positional():
    """`ask_job_runner` and the existing suites build this with one argument;
    that contract must keep working."""
    out: list[str] = []
    ex = AnswerFieldExtractor(out.append)
    ex.feed('{"answer": "hello", "key_points": []}')
    ex.reset()  # must not raise with no callback wired
    assert "".join(out) == "hello"


def test_a_failing_restart_callback_never_breaks_the_answer():
    """Display-only path: a broken notification must not propagate into the
    retry loop that is trying to salvage the generation."""
    out: list[str] = []

    def _boom() -> None:
        raise RuntimeError("sink is gone")

    ex = AnswerFieldExtractor(out.append, on_restart=_boom)
    ex.feed('{"answer": "att')
    ex.reset()  # must not raise
    ex.feed('{"answer": "attempt two", "key_points": []}')
    assert "".join(out) == "attattempt two", "streaming continues after the failure"


# ─── token_stream: a restart frame drops the replay buffer ──────────────────


def test_restart_clears_the_channel_accumulator():
    token_stream.publish("ask:1", _delta("The top theme is per"))
    assert token_stream._accum["ask:1"] == "The top theme is per"

    token_stream.publish("ask:1", {"kind": "restart"})
    assert "ask:1" not in token_stream._accum

    token_stream.publish("ask:1", _delta("The top theme is performance."))
    assert token_stream._accum["ask:1"] == "The top theme is performance."


def test_restart_clears_the_overflow_flag():
    """Attempt 2 starts from zero, so whatever made attempt 1 overrun the cap
    no longer holds — buffering must resume rather than stay off for the run."""
    token_stream._accum_overflowed.add("ask:2")

    token_stream.publish("ask:2", {"kind": "restart"})
    assert "ask:2" not in token_stream._accum_overflowed

    token_stream.publish("ask:2", _delta("fresh"))
    assert token_stream._accum["ask:2"] == "fresh", "buffering resumed"


async def test_late_joiner_after_a_restart_replays_attempt_two_only():
    """The bug, from the reader's seat: someone who opens the tab after the
    retry must be caught up with the fresh answer, not both attempts."""
    token_stream.publish("ask:3", _delta("The top theme is per"))
    token_stream.publish("ask:3", {"kind": "restart"})
    token_stream.publish("ask:3", _delta("The top theme is "))

    received: list[dict] = []
    task = asyncio.create_task(_collect("ask:3", received))
    await asyncio.sleep(0)  # subscriber registers and takes its replay snapshot

    token_stream.publish("ask:3", _delta("performance."))
    token_stream.close("ask:3", kind="done")
    await task

    assert received[0] == {"kind": "replay", "text": "The top theme is "}
    assert "per" not in received[0]["text"].removeprefix("The top theme is ")
    assert "".join(f.get("text", "") for f in received) == "The top theme is performance."


async def test_a_live_subscriber_is_told_about_the_restart():
    """The frame is relayed, not swallowed — a client already watching has its
    own accumulator to drop."""
    received: list[dict] = []
    task = asyncio.create_task(_collect("ask:4", received))
    await asyncio.sleep(0)

    token_stream.publish("ask:4", _delta("attempt one par"))
    token_stream.publish("ask:4", {"kind": "restart"})
    token_stream.publish("ask:4", _delta("attempt two, whole"))
    token_stream.close("ask:4", kind="done")
    await task

    kinds = [f["kind"] for f in received]
    assert kinds == ["delta", "restart", "delta", "done"]


# ─── delta_sink.reset(): discard the buffered tail, then announce ────────────


async def test_sink_reset_discards_the_buffered_tail_it_has_not_published_yet():
    """The coalescing buffer holds attempt 1's tail on the worker thread until
    a flush threshold or `close()`. If reset only announced the restart, that
    tail would flush afterwards and land at the HEAD of attempt 2."""
    loop = asyncio.get_running_loop()
    received: list[dict] = []
    task = asyncio.create_task(_collect("ask:5", received))
    await asyncio.sleep(0)

    sink = token_stream.delta_sink(loop, "ask:5")
    sink("attempt one tail")     # under both thresholds: buffered, not published
    sink.reset()
    await asyncio.sleep(0)       # let the threadsafe hop deliver the restart
    sink("attempt two")
    token_stream.close("ask:5", kind="done")   # flushes whatever is buffered
    await task

    texts = [f.get("text", "") for f in received if f["kind"] == "delta"]
    assert "".join(texts) == "attempt two", "the discarded tail never reached a viewer"
    assert {"kind": "restart"} in received


async def test_sink_reset_clears_a_tail_that_already_flushed():
    """The other half of the same scenario: attempt 1 was long enough to cross
    a flush threshold, so its text is already in the replay buffer. The restart
    frame is what removes it."""
    loop = asyncio.get_running_loop()
    sink = token_stream.delta_sink(loop, "ask:6")

    sink("x" * (token_stream._FLUSH_BYTES + 1))   # crosses the byte threshold
    await asyncio.sleep(0)
    assert len(token_stream._accum.get("ask:6", "")) > token_stream._FLUSH_BYTES

    sink.reset()
    await asyncio.sleep(0)
    assert "ask:6" not in token_stream._accum, "attempt 1 dropped from the replay buffer"


async def test_sink_reset_survives_a_closed_loop():
    """A generation that outlived its request must not raise out of reset."""
    dead = asyncio.new_event_loop()
    dead.close()
    sink = token_stream.delta_sink(dead, "ask:7")
    sink("buffered")
    sink.reset()  # must not raise


# ─── app.llm: both callback shapes are rewound between attempts ─────────────


class _Resettable:
    """A stream callback that records feeds and rewinds like the real ones."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.resets = 0

    def __call__(self, text: str) -> None:
        self.seen.append(text)

    def reset(self) -> None:
        self.resets += 1
        self.seen.clear()


class _FlakyStreamCtx:
    """First use raises a retryable error partway; later uses stream cleanly."""

    def __init__(self, outer, fragments, final_input, text_mode: bool):
        self._outer = outer
        self._fragments = fragments
        self._final = final_input
        self._text_mode = text_mode
        self._fail = outer.calls == 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _emit(self):
        if self._fail:
            yield self._fragments[0]
            raise anthropic.APIConnectionError(request=SimpleNamespace())
        yield from self._fragments

    def __iter__(self):
        for f in self._emit():
            yield SimpleNamespace(type="input_json", partial_json=f)

    @property
    def text_stream(self):
        return self._emit()

    def get_final_message(self):
        if self._text_mode:
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="".join(self._fragments))],
                usage=SimpleNamespace(input_tokens=1, output_tokens=2),
                stop_reason="end_turn",
            )
        return SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", name="submit_response",
                                     input=self._final)],
            usage=SimpleNamespace(input_tokens=1, output_tokens=2),
            stop_reason="tool_use",
        )


class _FlakyStubClient:
    def __init__(self, fragments, final_input=None, text_mode: bool = False):
        self.calls = 0
        outer = self

        class _Messages:
            def stream(self, **kwargs):
                outer.calls += 1
                return _FlakyStreamCtx(outer, fragments, final_input, text_mode)

        self.messages = _Messages()


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    monkeypatch.setattr(llm, "_attempt_delay", lambda attempt: 0.0)


def test_retry_rewinds_a_json_delta_callback(monkeypatch, isolated_settings):
    frags = ['{"answer": "The top theme is per', 'formance.", "key_points": []}']
    final = {"answer": "The top theme is performance.", "key_points": [],
             "citations": [], "confidence": 1, "unanswered": ""}
    monkeypatch.setattr(llm, "get_client",
                        lambda: _FlakyStubClient(frags, final))
    cb = _Resettable()

    out = llm.call_json(system="s", user="u", schema={"type": "object"},
                        stream=True, on_json_delta=cb)

    assert cb.resets == 1, "the surviving attempt announced its restart"
    assert cb.seen == frags, "attempt 1's fragment was dropped, not prepended"
    assert out == final, "the authoritative result is unaffected"


def test_retry_rewinds_a_plain_text_delta_callback(monkeypatch, isolated_settings):
    """The raw-text sink accumulates downstream exactly like the tool-use one,
    so it needs the same rewind — this is the shape the long VoC generations
    stream through."""
    frags = ["## Themes\n\nperfor", "mance and onboarding."]
    monkeypatch.setattr(llm, "get_client",
                        lambda: _FlakyStubClient(frags, text_mode=True))
    cb = _Resettable()

    llm.call_md(system="s", user="u", stream=True, on_delta=cb)

    assert cb.resets == 1
    assert cb.seen == frags


def test_a_reset_that_raises_does_not_fail_the_retry(monkeypatch, isolated_settings):
    """Streaming is advisory: a broken rewind must never turn a recoverable
    stream failure into a failed generation."""
    frags = ['{"answer": "hi', ' there", "key_points": []}']
    final = {"answer": "hi there", "key_points": [], "citations": [],
             "confidence": 1, "unanswered": ""}
    monkeypatch.setattr(llm, "get_client", lambda: _FlakyStubClient(frags, final))

    class _BadReset(_Resettable):
        def reset(self) -> None:
            raise RuntimeError("sink is gone")

    out = llm.call_json(system="s", user="u", schema={"type": "object"},
                        stream=True, on_json_delta=_BadReset())
    assert out == final


# ─── end to end: worker → channel ───────────────────────────────────────────


async def test_run_ask_job_streams_only_the_surviving_attempt(monkeypatch):
    """The whole chain, from the retry layer's reset down to what a reader is
    caught up with: a retry mid-answer leaves attempt 2 alone on the channel."""
    from app import ask_job_runner as ajr

    attempt_one = '{"answer": "The top theme is per'
    attempt_two = ['{"answer": "The top theme is ', 'performance.", "key_points": []}']

    def fake_answer(**kwargs):
        extractor = kwargs["on_delta"]
        extractor(attempt_one)
        # What app.llm does between attempts when the stream drops.
        extractor.reset()
        for f in attempt_two:
            extractor(f)
        return {"answer": "The top theme is performance.", "key_points": [],
                "citations": [], "confidence": 1, "unanswered": ""}

    completed: dict = {}
    monkeypatch.setattr(ajr.qa_agent, "answer", fake_answer)
    monkeypatch.setattr(ajr, "complete_ask_job", lambda i, p: completed.setdefault(i, p))
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)

    received: list[dict] = []
    task = asyncio.create_task(_collect(ajr.ask_channel(42), received))
    await asyncio.sleep(0)

    await ajr.run_ask_job(ask_id=42, enterprise_id="e1", question="q", dataset="d")
    await task

    streamed = "".join(f["text"] for f in received if f["kind"] == "delta")
    assert streamed == "The top theme is performance.", (
        "the reader sees the fresh answer, not the truncated attempt glued to it"
    )
    assert {"kind": "restart"} in received
    assert completed[42]["answer"] == "The top theme is performance.", (
        "the persisted payload — the authoritative answer — is untouched"
    )
