"""Coalescing tests for app.graph.token_stream.delta_sink.

`on_delta` fires once per model text delta — thousands of times for a PRD.
Each one used to become its own `call_soon_threadsafe` hop onto the single
event loop that also serves every HTTP request. The sink now buffers on the
worker thread and publishes one frame per ~100ms / 8KB.

The contract these tests pin:
  - the published text is byte-identical to the concatenation of the deltas,
  - the TAIL (whatever arrived after the last threshold crossing) is flushed
    by close(), and arrives BEFORE the terminal sentinel,
  - a late joiner's replay still sees everything.

The ordering test is the load-bearing one. `close()` runs ON the loop, so a
flush that went through `publish_threadsafe` would be scheduled for the next
loop iteration — landing after the sentinel and into an already-cleared
channel, silently truncating every document. That is the bug this file exists
to prevent regressing.

Async tests run under pytest-asyncio auto mode (asyncio_mode = auto).
"""
from __future__ import annotations

import asyncio

import pytest

from app.graph import token_stream


@pytest.fixture(autouse=True)
def _clear_state():
    for d in (token_stream._subscribers, token_stream._accum,
              token_stream._pending_drains):
        d.clear()
    token_stream._accum_overflowed.clear()
    yield
    for d in (token_stream._subscribers, token_stream._accum,
              token_stream._pending_drains):
        d.clear()
    token_stream._accum_overflowed.clear()


async def _collect(channel: str, out: list[dict]) -> None:
    async for frame in token_stream.subscribe(channel):
        out.append(frame)


def _deltas(frames: list[dict]) -> list[str]:
    return [f["text"] for f in frames if f.get("kind") == "delta"]


# ─── coalescing ──────────────────────────────────────────────────────────────


async def test_small_deltas_below_thresholds_produce_no_frames_until_close():
    """The whole point: N tiny deltas must not become N loop hops."""
    ch = "prd:1"
    received: list[dict] = []
    task = asyncio.create_task(_collect(ch, received))
    await asyncio.sleep(0)

    sink = token_stream.delta_sink(asyncio.get_running_loop(), ch)
    for _ in range(500):
        sink("x")                      # 500 bytes total, well under 8192
    await asyncio.sleep(0.01)

    assert received == [], "sub-threshold deltas should still be buffered"

    token_stream.close(ch)
    await asyncio.wait_for(task, timeout=1)
    assert _deltas(received) == ["x" * 500], "one coalesced frame, not 500"


async def test_byte_threshold_flushes_mid_stream():
    ch = "prd:2"
    received: list[dict] = []
    task = asyncio.create_task(_collect(ch, received))
    await asyncio.sleep(0)

    sink = token_stream.delta_sink(asyncio.get_running_loop(), ch)
    chunk = "y" * (token_stream._FLUSH_BYTES // 4)
    for _ in range(4):
        sink(chunk)
    await asyncio.sleep(0.01)

    assert _deltas(received) == ["y" * token_stream._FLUSH_BYTES]

    token_stream.close(ch)
    await asyncio.wait_for(task, timeout=1)


async def test_time_threshold_flushes_without_hitting_byte_cap(monkeypatch):
    """A slow generation must still stream — it can't wait for 8KB."""
    monkeypatch.setattr(token_stream, "_FLUSH_INTERVAL_S", 0.0)
    ch = "prd:3"
    received: list[dict] = []
    task = asyncio.create_task(_collect(ch, received))
    await asyncio.sleep(0)

    sink = token_stream.delta_sink(asyncio.get_running_loop(), ch)
    sink("tiny")
    await asyncio.sleep(0.01)

    assert _deltas(received) == ["tiny"]
    token_stream.close(ch)
    await asyncio.wait_for(task, timeout=1)


async def test_no_text_is_lost_across_many_flushes():
    """Concatenated output must equal concatenated input, exactly."""
    ch = "prd:4"
    received: list[dict] = []
    task = asyncio.create_task(_collect(ch, received))
    await asyncio.sleep(0)

    sink = token_stream.delta_sink(asyncio.get_running_loop(), ch)
    parts = [f"<p>chunk {i}</p>" * 40 for i in range(60)]   # crosses 8KB often
    for p in parts:
        sink(p)
        await asyncio.sleep(0)          # let scheduled publishes run
    token_stream.close(ch)
    await asyncio.wait_for(task, timeout=1)

    assert "".join(_deltas(received)) == "".join(parts)


async def test_empty_deltas_are_skipped():
    ch = "prd:5"
    sink = token_stream.delta_sink(asyncio.get_running_loop(), ch)
    sink("")
    sink("")
    received: list[dict] = []
    task = asyncio.create_task(_collect(ch, received))
    await asyncio.sleep(0)
    token_stream.close(ch)
    await asyncio.wait_for(task, timeout=1)
    assert _deltas(received) == []


# ─── the tail, and its ordering ──────────────────────────────────────────────


async def test_close_flushes_the_tail():
    """Text after the last threshold crossing is the end of the document."""
    ch = "prd:6"
    received: list[dict] = []
    task = asyncio.create_task(_collect(ch, received))
    await asyncio.sleep(0)

    sink = token_stream.delta_sink(asyncio.get_running_loop(), ch)
    sink("y" * token_stream._FLUSH_BYTES)     # forces one flush
    await asyncio.sleep(0.01)
    sink("</body></html>")                    # tail, below both thresholds
    token_stream.close(ch)
    await asyncio.wait_for(task, timeout=1)

    assert "".join(_deltas(received)).endswith("</body></html>")


async def test_tail_arrives_before_the_done_sentinel():
    """Regression guard for the ordering trap.

    If the tail flush ever goes through publish_threadsafe from close(), it is
    scheduled for the NEXT loop tick — after this sentinel, into a channel
    whose subscribers have already been popped. The frame vanishes and every
    document loses its ending. Frame ORDER is the only thing that catches it.
    """
    ch = "prd:7"
    received: list[dict] = []
    task = asyncio.create_task(_collect(ch, received))
    await asyncio.sleep(0)

    sink = token_stream.delta_sink(asyncio.get_running_loop(), ch)
    sink("the tail")
    token_stream.close(ch)
    await asyncio.wait_for(task, timeout=1)

    kinds = [f["kind"] for f in received]
    assert kinds == ["delta", "done"], f"tail must precede the sentinel, got {kinds}"


async def test_close_without_a_sink_is_safe():
    """Channels closed with no delta_sink registered must not raise."""
    ch = "prd:8"
    received: list[dict] = []
    task = asyncio.create_task(_collect(ch, received))
    await asyncio.sleep(0)
    token_stream.close(ch)
    await asyncio.wait_for(task, timeout=1)
    assert received == [{"kind": "done"}]


async def test_error_close_also_flushes_the_tail():
    """A failed generation still shows what it managed to produce."""
    ch = "prd:9"
    received: list[dict] = []
    task = asyncio.create_task(_collect(ch, received))
    await asyncio.sleep(0)

    sink = token_stream.delta_sink(asyncio.get_running_loop(), ch)
    sink("partial output")
    token_stream.close(ch, kind="error")
    await asyncio.wait_for(task, timeout=1)

    assert [f["kind"] for f in received] == ["delta", "error"]
    assert _deltas(received) == ["partial output"]


# ─── interaction with the replay buffer ──────────────────────────────────────


async def test_late_joiner_replays_coalesced_text():
    """Coalescing must not change what a mid-generation joiner is caught up on."""
    ch = "prd:10"
    sink = token_stream.delta_sink(asyncio.get_running_loop(), ch)
    sink("z" * token_stream._FLUSH_BYTES)     # flushes into the replay buffer
    await asyncio.sleep(0.01)

    received: list[dict] = []
    task = asyncio.create_task(_collect(ch, received))
    await asyncio.sleep(0)

    sink(" and the rest")
    token_stream.close(ch)
    await asyncio.wait_for(task, timeout=1)

    assert received[0]["kind"] == "replay"
    assert received[0]["text"] == "z" * token_stream._FLUSH_BYTES
    assert "".join(_deltas(received)) == " and the rest"


@pytest.mark.parametrize("bytes_before_join", [0, 10, 8192, 20000])
async def test_late_joiner_never_loses_text_whatever_the_flush_boundary(
    bytes_before_join,
):
    """The invariant coalescing must preserve, at every join point.

    Buffering moves the boundary between what is REPLAYED and what arrives
    live — a joiner can now land mid-buffer and get its first text as a delta
    instead of a replay frame. That is fine (appending to empty and replacing
    empty are the same thing) as long as replay + deltas still reconstructs the
    document exactly. This is the contract; the replay/live SPLIT is not.
    """
    ch = f"prd:join{bytes_before_join}"
    sink = token_stream.delta_sink(asyncio.get_running_loop(), ch)

    head = "H" * bytes_before_join
    if head:
        sink(head)
    await asyncio.sleep(0.01)

    received: list[dict] = []
    task = asyncio.create_task(_collect(ch, received))
    await asyncio.sleep(0)

    tail = "T" * 5000
    sink(tail)
    token_stream.close(ch)
    await asyncio.wait_for(task, timeout=1)

    seen = "".join(f["text"] for f in received
                   if f.get("kind") in ("replay", "delta"))
    assert seen == head + tail, (
        f"late joiner lost text at boundary {bytes_before_join}: "
        f"got {len(seen)}B, expected {len(head + tail)}B"
    )


async def test_close_deregisters_the_drain():
    """A finished generation must not leave its buffer behind for the next one."""
    ch = "prd:11"
    token_stream.delta_sink(asyncio.get_running_loop(), ch)
    assert ch in token_stream._pending_drains
    token_stream.close(ch)
    assert ch not in token_stream._pending_drains
