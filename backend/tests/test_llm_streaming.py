"""on_delta token-streaming hook (app.llm) + token_stream SSE pub/sub.

Never hits the network: a stub client mimics the SDK's
`with client.messages.stream(**kw) as s: s.text_stream / s.get_final_message()`
protocol, yielding pre-recorded text deltas.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import llm
from app.graph import token_stream as ts


class _StreamCtx:
    def __init__(self, deltas, final_text):
        self._deltas = deltas
        self._final_text = final_text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        return iter(self._deltas)

    def get_final_message(self):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self._final_text)],
            usage=SimpleNamespace(input_tokens=1, output_tokens=2),
            stop_reason="end_turn",
        )


class _StreamStubClient:
    def __init__(self, deltas):
        self._deltas = deltas
        outer = self

        class _Messages:
            def stream(self, **kwargs):
                outer.stream_kwargs = kwargs
                return _StreamCtx(outer._deltas, "".join(outer._deltas))

            def create(self, **kwargs):  # non-stream path
                outer.created = True
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="".join(outer._deltas))],
                    usage=SimpleNamespace(input_tokens=1, output_tokens=2),
                    stop_reason="end_turn",
                )

        self.messages = _Messages()


@pytest.fixture
def stream_client(monkeypatch):
    def _factory(deltas):
        c = _StreamStubClient(deltas)
        monkeypatch.setattr(llm, "get_client", lambda: c)
        return c
    return _factory


def test_call_md_streaming_forwards_each_delta(stream_client, isolated_settings):
    stream_client(["Hello ", "streamed ", "world"])
    seen: list[str] = []

    out = llm.call_md(
        system="s", user="u", stream=True, on_delta=seen.append,
    )

    assert seen == ["Hello ", "streamed ", "world"], "every text delta forwarded in order"
    assert out == "Hello streamed world", "final assembled text still returned"


def test_call_md_without_on_delta_is_unchanged(stream_client, isolated_settings):
    c = stream_client(["a", "b"])
    out = llm.call_md(system="s", user="u", stream=True)  # no on_delta
    assert out == "ab"
    # Non-stream path never touches on_delta and never iterates text_stream.
    out2 = llm.call_md(system="s", user="u", stream=False)
    assert out2 == "ab" and getattr(c, "created", False)


# ── token_stream pub/sub ─────────────────────────────────────────────────────

def test_token_stream_delivers_frames_then_terminates():
    async def _run():
        chan = "prd:1"
        agen = ts.subscribe(chan)
        # Prime the subscriber (registers its queue) before publishing.
        task = asyncio.ensure_future(_collect(agen))
        await asyncio.sleep(0)  # let subscribe register its queue
        ts.publish(chan, {"kind": "delta", "text": "A"})
        ts.publish(chan, {"kind": "delta", "text": "B"})
        ts.close(chan)
        return await task

    frames = asyncio.run(_run())
    assert [f.get("text") for f in frames if f["kind"] == "delta"] == ["A", "B"]
    assert frames[-1]["kind"] == "done"


async def _collect(agen):
    out = []
    async for f in agen:
        out.append(f)
    return out


def test_publish_to_no_subscribers_is_noop():
    ts.publish("nobody-home", {"kind": "delta", "text": "x"})  # must not raise


def test_delta_sink_skips_empty_and_wraps_text():
    async def _run():
        chan = "ev:9"
        task = asyncio.ensure_future(_collect(ts.subscribe(chan)))
        await asyncio.sleep(0)
        loop = asyncio.get_running_loop()
        sink = ts.delta_sink(loop, chan)
        sink("chunk")
        sink("")  # empty → skipped
        await asyncio.sleep(0)  # let call_soon_threadsafe run
        ts.close(chan)
        return await task

    frames = asyncio.run(_run())
    deltas = [f for f in frames if f["kind"] == "delta"]
    assert [d["text"] for d in deltas] == ["chunk"], "empty delta skipped"
