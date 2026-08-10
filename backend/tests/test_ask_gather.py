"""`ask_runner._gather` — the concurrent retrieval fan-out.

The bug this file exists for shipped and reached a live request. `_gather` took
ONE `contextvars.copy_context()` and handed it to every task; a `Context` can
only be entered once at a time, so whichever task started second raised

    RuntimeError: cannot enter context: <Context ...> is already entered

and `_gather` — which is deliberately fail-open, so one bad leg never loses the
ask — swallowed it. The corpus, the live connector read and the KG bundle all
degraded to None, the answer was composed with no grounding at all, and the job
still logged "Ask job succeeded". Silent, and invisible from the outside.

So the tests below are mostly about the failure modes rather than the happy
path: that concurrency actually works, that ContextVars survive the hop, and
that a slow or broken leg costs only itself.
"""
from __future__ import annotations

import contextvars
import threading
import time

import app.ask_runner as ar

_probe: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "test_gather_probe", default=None
)


def test_two_tasks_both_succeed():
    """The regression. With one shared Context this returned {'a': 1, 'b': None}
    (or vice versa) and logged a swallowed RuntimeError for the loser."""
    out = ar._gather({"a": lambda: 1, "b": lambda: 2})
    assert out == {"a": 1, "b": 2}


def test_many_tasks_all_succeed():
    """Wave 1 can carry three legs (live + corpus + facts); one shared context
    failed all but one of them."""
    tasks = {f"t{i}": (lambda i=i: i) for i in range(5)}
    assert ar._gather(tasks) == {f"t{i}": i for i in range(5)}


def test_tasks_actually_run_concurrently():
    """The whole point. Two legs that each sleep 0.3s must finish in ~0.3s, not
    ~0.6s — otherwise this is a fan-out in name only."""
    def _slow():
        time.sleep(0.3)
        return "done"

    started = time.monotonic()
    out = ar._gather({"a": _slow, "b": _slow})
    elapsed = time.monotonic() - started

    assert out == {"a": "done", "b": "done"}
    assert elapsed < 0.55, f"legs ran serially ({elapsed:.2f}s)"


def test_contextvars_reach_the_tasks():
    """The reason tasks run in a copied context at all.

    `document_grounding` resolves conversation-scoped documents through
    `_active_conversation_id` / `_active_conversation_user_id`. A bare
    `ThreadPoolExecutor.submit` does not carry those across, and the failure is
    silent: a conversation's own documents just vanish from grounding."""
    token = _probe.set("carried")
    try:
        out = ar._gather({"a": _probe.get, "b": _probe.get})
    finally:
        _probe.reset(token)
    assert out == {"a": "carried", "b": "carried"}


def test_each_task_gets_its_own_context():
    """A task setting a ContextVar must not leak into a sibling or the caller.

    Copies are independent by construction; this pins it, because a shared
    context would make one leg's write visible to the others and — worse — the
    embedding memoisation would start crossing between asks."""
    def _writer():
        _probe.set("mine")
        return _probe.get()

    out = ar._gather({"w": _writer, "r": _probe.get})
    assert out["w"] == "mine"
    assert out["r"] is None          # sibling unaffected
    assert _probe.get() is None      # caller unaffected


def test_one_failing_leg_never_takes_the_others_down():
    """Every retrieval already has its own fail-open contract (live_read "never
    raises", the KG bundle is best-effort). Fan-out must preserve them."""
    def _boom():
        raise RuntimeError("kg exploded")

    out = ar._gather({"good": lambda: "ok", "bad": _boom})
    assert out == {"good": "ok", "bad": None}


def test_a_leg_that_overruns_the_deadline_yields_none():
    """The backstop. It should never fire in practice — every leg owns a shorter
    timeout — but when it does the ask still composes, minus that leg."""
    def _hang():
        time.sleep(5)
        return "never"

    started = time.monotonic()
    out = ar._gather({"fast": lambda: "ok", "slow": _hang}, deadline_s=0.2)
    elapsed = time.monotonic() - started

    assert out["fast"] == "ok"
    assert out["slow"] is None
    assert elapsed < 2.0, "the deadline did not bound the wait"


def test_no_tasks_is_a_no_op():
    """The PRD branch can leave a wave empty; that must not spin up a pool."""
    assert ar._gather({}) == {}


def test_tasks_do_not_run_on_the_calling_thread():
    """Sanity: this is real concurrency, not a serial loop wearing a pool."""
    caller = threading.get_ident()
    out = ar._gather({"a": threading.get_ident, "b": threading.get_ident})
    assert out["a"] != caller
    assert out["b"] != caller
