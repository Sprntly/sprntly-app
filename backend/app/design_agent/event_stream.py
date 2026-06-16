"""In-process pub/sub for agent step events.

Single-worker asyncio fan-out: publish_step() pushes events to every active
subscriber queue for a given prototype; subscribe() yields events until the
stream is closed by close(). No external broker — the agent runs in the SAME
process and event loop as the FastAPI server (asyncio.create_task in
routes/design_agent.py), so an in-memory asyncio.Queue per subscriber is
correct and sufficient.

DEPLOYMENT NOTE: correct for the current single-worker EC2/uvicorn topology.
A multi-worker deployment would route generate/iterate to one worker and
/events to another, seeing no events — SSE degrades to poll (never breaks).
Multi-worker needs a shared broker (Redis pub/sub) — out of scope here, named
as the scaling follow-up.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

_subscribers: dict[int, set[asyncio.Queue]] = {}
_QUEUE_MAX = 64


def publish_step(prototype_id: int, event: dict[str, Any]) -> None:
    """Non-blocking fan-out to all live subscribers of this prototype.

    Drops the OLDEST frame from a full queue so the call never blocks the
    caller (step events are advisory; the poll fallback carries the real
    terminal state). A publish to a prototype with no subscribers is a no-op.
    Never raises.
    """
    queues = _subscribers.get(prototype_id)
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


async def subscribe(prototype_id: int) -> AsyncIterator[dict[str, Any]]:
    """Yield events for prototype_id until a terminal event (kind=done/error).

    Registers a fresh bounded queue on entry and deregisters it in the finally
    block — a disconnected client (CancelledError) never leaks a queue.
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    _subscribers.setdefault(prototype_id, set()).add(q)
    try:
        while True:
            item = await q.get()
            yield item
            if isinstance(item, dict) and item.get("kind") in ("done", "error"):
                return
    finally:
        buckets = _subscribers.get(prototype_id)
        if buckets is not None:
            buckets.discard(q)
            if not buckets:
                del _subscribers[prototype_id]


def close(prototype_id: int, *, kind: str = "done", summary: str = "") -> None:
    """Push a terminal sentinel event to all subscribers and clear the registry.

    Every active subscribe() generator yields the sentinel then completes.
    Mapping from _finish status: complete runs -> kind="done"; all other exits
    (max_iters, aborted, refused, error) -> kind="error". Safe to call when no
    subscribers exist (no-op).

    `summary` is the agent's natural-language change summary (the iterate run's
    final text block). It is attached as the `text` key on the sentinel ONLY for
    a non-empty done event; the error sentinel is unchanged. The frontend keeps
    its "Change applied" fallback for the absent/empty case.
    """
    queues = _subscribers.pop(prototype_id, set())
    sentinel: dict[str, Any] = {"kind": kind}
    if kind == "done" and summary:
        sentinel["text"] = summary
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
