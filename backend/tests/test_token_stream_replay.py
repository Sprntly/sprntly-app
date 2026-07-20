"""Replay-buffer tests for app.graph.token_stream.

Brief-insight PRDs and evidence are warm-started server-side, so the client
usually opens its SSE stream mid-generation. The channel accumulates delta
text so a late subscriber is caught up with one {"kind":"replay"} frame before
the live deltas. These tests pin that contract: what is replayed, when nothing
is, and that the buffer's lifecycle is bounded by close().

Async tests run under pytest-asyncio auto mode (asyncio_mode = auto).
"""
from __future__ import annotations

import asyncio

import pytest

from app.graph import token_stream


@pytest.fixture(autouse=True)
def _clear_state():
    token_stream._subscribers.clear()
    token_stream._accum.clear()
    token_stream._accum_overflowed.clear()
    yield
    token_stream._subscribers.clear()
    token_stream._accum.clear()
    token_stream._accum_overflowed.clear()


def _delta(text: str) -> dict:
    return {"kind": "delta", "text": text}


async def _collect(channel: str, out: list[dict]) -> None:
    async for frame in token_stream.subscribe(channel):
        out.append(frame)


# ─── late join: replay then live, no duplication ─────────────────────────────


async def test_late_subscriber_gets_replay_then_live_deltas():
    """Deltas published before subscribe arrive as ONE replay frame; deltas
    published after arrive live — nothing duplicated, nothing lost."""
    token_stream.publish("prd:1", _delta("<!doctype html><body>Hea"))
    token_stream.publish("prd:1", _delta("d of the doc "))

    received: list[dict] = []
    task = asyncio.create_task(_collect("prd:1", received))
    await asyncio.sleep(0)  # consumer registers + emits its replay frame

    token_stream.publish("prd:1", _delta("then the tail"))
    token_stream.close("prd:1", kind="done")
    await task

    assert received[0] == {"kind": "replay", "text": "<!doctype html><body>Head of the doc "}
    assert received[1] == _delta("then the tail")
    assert received[2] == {"kind": "done"}


async def test_subscriber_from_start_gets_no_replay_frame():
    """A subscriber attached before the first delta sees only live frames —
    the pre-replay contract is unchanged for the chat/import path."""
    received: list[dict] = []
    task = asyncio.create_task(_collect("prd:2", received))
    await asyncio.sleep(0)

    token_stream.publish("prd:2", _delta("a"))
    token_stream.close("prd:2", kind="done")
    await task

    assert received == [_delta("a"), {"kind": "done"}]


async def test_two_late_subscribers_each_get_their_own_replay():
    """The replay frame is per-subscriber (each joiner catches up from its own
    join point), not consumed by whoever connects first."""
    token_stream.publish("evidence:7", _delta("start "))

    first: list[dict] = []
    t1 = asyncio.create_task(_collect("evidence:7", first))
    await asyncio.sleep(0)

    token_stream.publish("evidence:7", _delta("middle "))

    second: list[dict] = []
    t2 = asyncio.create_task(_collect("evidence:7", second))
    await asyncio.sleep(0)

    token_stream.close("evidence:7", kind="done")
    await asyncio.gather(t1, t2)

    assert first[0] == {"kind": "replay", "text": "start "}
    assert first[1] == _delta("middle ")
    assert second[0] == {"kind": "replay", "text": "start middle "}
    assert [f["kind"] for f in second] == ["replay", "done"]


# ─── lifecycle: close() bounds the buffer ────────────────────────────────────


async def test_close_drops_buffer_so_post_close_subscriber_replays_nothing():
    """After the terminal frame the buffer is gone: a very late subscriber gets
    no frames at all (the poll serves the persisted doc), and memory doesn't
    accumulate across generations."""
    token_stream.publish("prd:3", _delta("whole doc"))
    token_stream.close("prd:3", kind="done")
    assert token_stream._accum == {}

    received: list[dict] = []
    task = asyncio.create_task(_collect("prd:3", received))
    await asyncio.sleep(0)
    assert received == []  # nothing replayed, stream just waits
    task.cancel()


async def test_error_close_also_drops_buffer():
    token_stream.publish("prd:4", _delta("partial"))
    token_stream.close("prd:4", kind="error")
    assert token_stream._accum == {}
    assert "prd:4" not in token_stream._accum_overflowed


async def test_next_generation_on_same_channel_accumulates_fresh():
    """A retry/regeneration reuses the channel id: the new run's buffer starts
    empty rather than replaying the previous attempt's text."""
    token_stream.publish("prd:5", _delta("attempt one"))
    token_stream.close("prd:5", kind="error")
    token_stream.publish("prd:5", _delta("attempt two "))

    received: list[dict] = []
    task = asyncio.create_task(_collect("prd:5", received))
    await asyncio.sleep(0)
    token_stream.close("prd:5", kind="done")
    await task

    assert received[0] == {"kind": "replay", "text": "attempt two "}


# ─── overflow: degrade to live-only, never a truncated replay ────────────────


async def test_overflow_disables_replay_but_live_relay_survives(monkeypatch):
    """Past _ACCUM_MAX the buffer is dropped for the rest of the run — a late
    joiner degrades to live-only (old behavior) instead of receiving a
    truncated head that later deltas would glue wrongly onto."""
    monkeypatch.setattr(token_stream, "_ACCUM_MAX", 10)
    token_stream.publish("prd:6", _delta("0123456789"))  # fits exactly
    token_stream.publish("prd:6", _delta("X"))           # overflows → dropped
    assert "prd:6" not in token_stream._accum
    assert "prd:6" in token_stream._accum_overflowed
    token_stream.publish("prd:6", _delta("Y"))           # stays dropped
    assert "prd:6" not in token_stream._accum

    received: list[dict] = []
    task = asyncio.create_task(_collect("prd:6", received))
    await asyncio.sleep(0)

    token_stream.publish("prd:6", _delta("live"))
    token_stream.close("prd:6", kind="done")
    await task

    assert received == [_delta("live"), {"kind": "done"}]
    # close() cleared the overflow mark: the channel can buffer again next run.
    assert "prd:6" not in token_stream._accum_overflowed


# ─── non-delta frames don't pollute the buffer ───────────────────────────────


async def test_only_delta_text_is_buffered():
    token_stream.publish("prd:8", {"kind": "delta"})           # no text
    token_stream.publish("prd:8", {"kind": "progress", "text": "nope"})
    assert token_stream._accum.get("prd:8", "") == ""
