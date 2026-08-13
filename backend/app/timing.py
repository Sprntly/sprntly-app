"""[timing] instrumentation for the chat ask flow.

One purpose: make every block of an ask's life measurable from the logs alone,
so latency work starts from numbers instead of guesses. Every line follows one
greppable contract:

    [timing] block=<name> event=start <k=v ...>
    [timing] block=<name> event=end dur_ms=<int> <k=v ...>

`grep '\\[timing\\]'` over journalctl/docker logs reconstructs the whole
request: route → planner → worker → each gather leg → the answer stream.
Blocks are namespaced by layer (`route:*`, `worker:*`, `qa:*`, `gather:*`,
`llm:<purpose>`), and the LLM lines ride the gateway so every model call in
the app reports itself without per-callsite edits.

Deliberately dumb: stdlib logging at INFO, monotonic clocks, no state, no
sampling. The cost of a line is microseconds against blocks measured in
seconds. When the latency work lands, these stay — they are the regression
alarm for it.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Callable, TypeVar

logger = logging.getLogger("app.timing")

T = TypeVar("T")


def _ctx(ctx: dict) -> str:
    parts = [f"{k}={v}" for k, v in ctx.items() if v is not None and v != ""]
    return (" " + " ".join(parts)) if parts else ""


@contextmanager
def timed(block: str, **ctx: Any):
    """Log start and end (with dur_ms) around a block. Never raises from the
    logging itself; the wrapped block's own exceptions pass through — with the
    end line still emitted, so a failed block reports how long failing took."""
    extra = _ctx(ctx)
    t0 = time.monotonic()
    logger.info("[timing] block=%s event=start%s", block, extra)
    try:
        yield
    finally:
        dur_ms = int((time.monotonic() - t0) * 1000)
        logger.info("[timing] block=%s event=end dur_ms=%d%s", block, dur_ms, extra)


def timed_def(block: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator form for whole functions (the composer, the worker, the
    planner) — start/end around every entry and every exit path without
    re-indenting a 500-line body."""

    def _deco(fn: Callable[..., T]) -> Callable[..., T]:
        def _wrapped(*args: Any, **kwargs: Any) -> T:
            with timed(block):
                return fn(*args, **kwargs)

        _wrapped.__name__ = getattr(fn, "__name__", block)
        _wrapped.__doc__ = fn.__doc__
        _wrapped.__wrapped__ = fn  # type: ignore[attr-defined]
        return _wrapped

    return _deco


def timed_fn(block: str, fn: Callable[[], T], **ctx: Any) -> Callable[[], T]:
    """Wrap a zero-arg callable so its execution is a timed block — the shape
    `ask_runner._gather` submits, which is what makes every gather leg report
    itself under its own name with no per-leg edits."""

    def _wrapped() -> T:
        with timed(block, **ctx):
            return fn()

    return _wrapped
