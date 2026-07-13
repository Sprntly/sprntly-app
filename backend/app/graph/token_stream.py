"""In-process pub/sub for token-level generation streaming (SSE).

Generalises `app.design_agent.event_stream` to a STRING channel key so the
long-form doc generations (PRD, evidence, …) can token-stream to the client as
the model produces text. A generation publishes `{"kind":"delta","text":…}`
frames as tokens land and a terminal `{"kind":"done"|"error"}` at the end; an
SSE route subscribes and relays frames to the browser's EventSource.

Two transport realities shape this module:
  1. Deltas originate on the LLM WORKER THREAD (the gateway runs the blocking
     streamed call via asyncio.to_thread), but asyncio.Queue is not
     thread-safe — so cross-thread publishes MUST go through
     `publish_threadsafe`, which hops onto the loop via call_soon_threadsafe.
  2. SINGLE-WORKER ONLY, same as event_stream: a multi-worker deployment could
     route generation to one worker and /stream to another, seeing no frames —
     SSE then yields nothing and the client's poll fallback carries the real
     result. Multi-worker needs a shared broker (Redis). Named, out of scope.

Frames are advisory display only: the authoritative result is always the
persisted doc the client already polls for. So a full queue drops its OLDEST
frame rather than blocking, and a closed loop drops the publish.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable

# Token deltas are far more frequent than the design-agent's coarse step events,
# so the per-subscriber buffer is larger. A slow client that overruns it drops
# oldest frames (its text jumps) but the final poll still shows the whole doc.
_QUEUE_MAX = 512

_subscribers: dict[str, set[asyncio.Queue]] = {}


def publish(channel: str, event: dict[str, Any]) -> None:
    """Non-blocking fan-out to all live subscribers of `channel` (loop thread).

    Drops the oldest frame from a full queue so a slow subscriber never blocks
    the generation. A publish to a channel with no subscribers is a no-op.
    Never raises. Call this only from the event loop; from a worker thread use
    `publish_threadsafe`.
    """
    queues = _subscribers.get(channel)
    if not queues:
        return
    for q in list(queues):
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


def publish_threadsafe(
    loop: asyncio.AbstractEventLoop, channel: str, event: dict[str, Any]
) -> None:
    """Publish from a non-loop (worker) thread by hopping onto `loop`.

    on_delta fires on the blocking LLM call's worker thread; asyncio.Queue is
    not thread-safe, so we schedule the publish on the loop. A closed loop
    (generation outlived the request) drops the frame — the poll fallback still
    carries the persisted result. Never raises.
    """
    try:
        loop.call_soon_threadsafe(publish, channel, event)
    except RuntimeError:
        pass  # loop already closed


def delta_sink(
    loop: asyncio.AbstractEventLoop, channel: str
) -> Callable[[str], None]:
    """Build an `on_delta(text)` for `llm_call(on_delta=…)` that streams each
    text delta to `channel` from the worker thread. Empty deltas are skipped."""
    def _on_delta(text: str) -> None:
        if text:
            publish_threadsafe(loop, channel, {"kind": "delta", "text": text})
    return _on_delta


async def subscribe(channel: str) -> AsyncIterator[dict[str, Any]]:
    """Yield frames for `channel` until a terminal (kind=done/error) frame.

    Registers a fresh bounded queue on entry, deregisters in `finally` — a
    disconnected client (CancelledError) never leaks a queue.
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    _subscribers.setdefault(channel, set()).add(q)
    try:
        while True:
            item = await q.get()
            yield item
            if isinstance(item, dict) and item.get("kind") in ("done", "error"):
                return
    finally:
        buckets = _subscribers.get(channel)
        if buckets is not None:
            buckets.discard(q)
            if not buckets:
                del _subscribers[channel]


def close(channel: str, *, kind: str = "done") -> None:
    """Push a terminal sentinel to all subscribers of `channel` and clear it.

    Every active subscribe() generator yields the sentinel then completes. Safe
    when no subscribers exist (no-op). Call on the loop thread; from a worker
    thread use `close_threadsafe`.
    """
    queues = _subscribers.pop(channel, set())
    sentinel: dict[str, Any] = {"kind": kind}
    for q in queues:
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            q.put_nowait(sentinel)
        except asyncio.QueueFull:
            pass


def close_threadsafe(
    loop: asyncio.AbstractEventLoop, channel: str, *, kind: str = "done"
) -> None:
    """Terminal-close `channel` from a worker thread (see publish_threadsafe)."""
    try:
        loop.call_soon_threadsafe(lambda: close(channel, kind=kind))
    except RuntimeError:
        pass
