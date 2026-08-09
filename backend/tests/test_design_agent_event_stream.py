"""Tests for the in-process SSE pub/sub module (event_stream.py).

Uses asyncio.create_task + asyncio.sleep(0) to let subscribers register their
queues before events are published synchronously.  All async tests run under
pytest-asyncio auto mode (asyncio_mode = auto in pytest.ini).
"""
from __future__ import annotations

import asyncio

import pytest

from app.design_agent import event_stream


# ─── fixture: clear module-level state between tests ─────────────────────────


@pytest.fixture(autouse=True)
def _clear_subscribers():
    event_stream._subscribers.clear()
    yield
    event_stream._subscribers.clear()


# ─── publish to no subscriber is a no-op ─────────────────────────────────────


def test_publish_no_subscribers_is_noop():
    """publish_step to a prototype with no active subscribers never raises."""
    event_stream.publish_step(100, {"kind": "step", "text": "hello"})


def test_close_no_subscribers_is_noop():
    """close() on an unregistered prototype never raises."""
    event_stream.close(101, kind="done")


# ─── basic subscribe / publish / close ───────────────────────────────────────


async def test_subscribe_receives_published_events():
    """Events published while a subscriber is live arrive in order."""
    received: list[dict] = []

    async def _consume():
        async for ev in event_stream.subscribe(1):
            received.append(ev)

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0)          # let consumer register its queue

    event_stream.publish_step(1, {"kind": "step", "text": "a"})
    event_stream.publish_step(1, {"kind": "step", "text": "b"})
    event_stream.close(1, kind="done")

    await task

    assert received[0] == {"kind": "step", "text": "a"}
    assert received[1] == {"kind": "step", "text": "b"}
    assert received[2] == {"kind": "done"}


async def test_subscribe_terminates_on_done_sentinel():
    """subscribe() exits as soon as a kind=done event is received."""
    results: list[dict] = []

    async def _consume():
        async for ev in event_stream.subscribe(2):
            results.append(ev)

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0)
    event_stream.close(2, kind="done")
    await task

    assert results == [{"kind": "done"}]


async def test_subscribe_terminates_on_error_sentinel():
    """subscribe() also exits on kind=error (error path is terminal)."""
    results: list[dict] = []

    async def _consume():
        async for ev in event_stream.subscribe(3):
            results.append(ev)

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0)
    event_stream.close(3, kind="error")
    await task

    assert results == [{"kind": "error"}]


# ─── done sentinel carries the agent's change summary ─────────────────────────


async def test_done_sentinel_carries_summary_text():
    """A non-empty summary on a done close() lands as the `text` key on the
    terminal sentinel (the agent's natural-language change description)."""
    results: list[dict] = []

    async def _consume():
        async for ev in event_stream.subscribe(20):
            results.append(ev)

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0)
    event_stream.close(20, kind="done", summary="Renamed the CTA button.")
    await task

    assert results == [{"kind": "done", "text": "Renamed the CTA button."}]


async def test_done_sentinel_omits_text_when_summary_empty():
    """An empty summary leaves the done sentinel shape unchanged (no `text` key)
    — the frontend keeps its 'Change applied' fallback."""
    results: list[dict] = []

    async def _consume():
        async for ev in event_stream.subscribe(21):
            results.append(ev)

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0)
    event_stream.close(21, kind="done", summary="")
    await task

    assert results == [{"kind": "done"}]


async def test_error_sentinel_never_carries_text():
    """The error sentinel is unchanged even if a summary is somehow passed —
    `text` is a done-only key."""
    results: list[dict] = []

    async def _consume():
        async for ev in event_stream.subscribe(22):
            results.append(ev)

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0)
    event_stream.close(22, kind="error", summary="should be ignored")
    await task

    assert results == [{"kind": "error"}]


# ─── multi-subscriber fan-out ─────────────────────────────────────────────────


async def test_close_sends_sentinel_to_all_active_subscribers():
    """close() fans the terminal sentinel to every concurrent subscriber."""
    got_a: list[dict] = []
    got_b: list[dict] = []

    async def _consume_a():
        async for ev in event_stream.subscribe(4):
            got_a.append(ev)

    async def _consume_b():
        async for ev in event_stream.subscribe(4):
            got_b.append(ev)

    t_a = asyncio.create_task(_consume_a())
    t_b = asyncio.create_task(_consume_b())
    await asyncio.sleep(0)          # both register before close()

    event_stream.close(4, kind="done")
    await t_a
    await t_b

    assert got_a == [{"kind": "done"}]
    assert got_b == [{"kind": "done"}]


# ─── registry cleanup ────────────────────────────────────────────────────────


async def test_subscriber_deregisters_after_terminal_event():
    """After a subscribe() generator exhausts, its queue is removed."""
    pid = 5

    async def _consume():
        async for _ in event_stream.subscribe(pid):
            pass

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0)
    event_stream.close(pid, kind="done")
    await task

    assert pid not in event_stream._subscribers


async def test_close_clears_registry_entry():
    """close() removes the prototype entry so a later subscribe starts fresh."""
    pid = 6

    gen = event_stream.subscribe(pid)
    # Advance the generator once to register the queue.
    task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)

    assert pid in event_stream._subscribers

    event_stream.close(pid, kind="done")
    try:
        await task
    except StopAsyncIteration:
        pass

    assert pid not in event_stream._subscribers


# ─── Publishes between subscribe and close are never lost ─────────────────────
#
# event_stream.py's own pub/sub logic is unchanged by the premature-stream-close
# fix — the bug was never here. `close()` has always popped the registry entry
# (by design: it is the terminal), and `publish_step` has always no-op'd against
# a missing entry (by design: "a publish to a prototype with no subscribers is a
# no-op"). The defect was `runner._finish` calling `close()` PREMATURELY —
# 15-22s before the prototype was actually ready — which made every LATER
# publish_step call (routes/design_agent.py's FINISHING_STEP / VITE_PHASE_STEP,
# already wired) a guaranteed no-op purely because the registry entry it needed
# was already gone. This test documents the invariant the fix now relies on: as
# long as close() has NOT yet been called, publishes made after a subscriber
# registers keep landing, no matter how many arrive first — the registry entry
# survives until something explicitly closes it.


async def test_publishes_before_close_all_reach_the_subscriber():
    """Multiple publish_step calls made while a subscriber is live, followed by
    a LATE close(), all land — the registry entry is never popped early."""
    received: list[dict] = []

    async def _consume():
        async for ev in event_stream.subscribe(23):
            received.append(ev)

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0)

    for i in range(4):
        event_stream.publish_step(23, {"kind": "step", "n": i})
    event_stream.close(23, kind="done")

    await task

    assert [ev.get("n") for ev in received[:4]] == [0, 1, 2, 3]
    assert received[-1] == {"kind": "done"}


# ─── queue-full: drops oldest, keeps newest ───────────────────────────────────


async def test_queue_full_drops_oldest_not_newest(monkeypatch):
    """When a subscriber queue fills up, the oldest frame is dropped so the
    newest can land (advisory progress events are expendable)."""
    import app.design_agent.event_stream as es

    # Shrink the max so we don't need 64 publishes in a unit test.
    monkeypatch.setattr(es, "_QUEUE_MAX", 2)

    pid = 7
    received: list[dict] = []

    async def _consume():
        async for ev in es.subscribe(pid):
            received.append(ev)

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0)          # consumer registers, blocks on get()

    # maxsize=2 — fill the queue while the consumer is suspended
    es.publish_step(pid, {"n": 0})  # queue: [n=0]         size 1
    es.publish_step(pid, {"n": 1})  # queue: [n=0, n=1]    size 2  FULL
    es.publish_step(pid, {"n": 2})  # drops n=0, adds n=2 → [n=1, n=2]  FULL
    es.close(pid)                   # drops n=1, adds sentinel → [n=2, done]

    await task

    ns = [e["n"] for e in received if "n" in e]
    assert 0 not in ns, "oldest frame should have been dropped"
    assert 2 in ns,     "newest frame must survive"
    assert received[-1] == {"kind": "done"}
