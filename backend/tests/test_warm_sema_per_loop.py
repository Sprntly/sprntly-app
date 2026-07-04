"""Regression test: the warm-concurrency semaphore must be per-event-loop.

`warm_synthesis_drilldowns` runs the warm fan-out via a fresh `asyncio.run(...)`
loop on every scheduler/startup pass. A module-level `asyncio.Semaphore` binds
to the first loop that awaits it and then raised
  RuntimeError: <Semaphore> is bound to a different event loop
on the next pass — every cached-Ask/evidence warm failed (seen in prod logs as
"app.ask_runner: Cached Ask warming failed"). `_warm_sema()` returns a semaphore
bound to the CURRENT loop, so consecutive asyncio.run passes each get their own.
"""
from __future__ import annotations

import asyncio


def test_warm_sema_reusable_across_consecutive_asyncio_run_loops():
    from app.brief_runner import _warm_sema

    async def use_it():
        sema = _warm_sema()
        async with sema:  # binds the semaphore to the current loop
            return sema

    # Two separate event loops (mirrors two scheduler passes). Pre-fix the second
    # `async with` raised "bound to a different event loop"; now each pass gets a
    # fresh loop-bound semaphore and neither raises. Hold BOTH objects alive and
    # compare identity, not id(): once the first loop is GC'd its semaphore is
    # freed, and the allocator can hand the second run the same address, so an
    # `id(first) != id(second)` check flakes (identical ids for distinct objects).
    first = asyncio.run(use_it())
    second = asyncio.run(use_it())
    assert first is not second  # distinct semaphore object per loop


def test_warm_sema_is_stable_within_one_loop():
    from app.brief_runner import _warm_sema

    async def twice():
        return _warm_sema() is _warm_sema()

    assert asyncio.run(twice()) is True
