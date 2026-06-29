"""Tests for the process-wide LLM concurrency cap (app.llm._llm_semaphore).

The small prod box stalls at 4+ concurrent Anthropic streams, so model calls
are bounded by a module-level threading.BoundedSemaphore acquired/released
around the single chokepoint (`_create_with_retries`). These tests prove:

  * no more than N Anthropic calls are ever in flight at once (extra calls
    QUEUE, they don't fail),
  * all queued calls eventually complete,
  * the LLM_MAX_CONCURRENCY env var changes the cap,
  * a single call still works unchanged,
  * the PRD 2-part path runs both parts within the cap,
  * a slot is released even when the Anthropic call raises (no slot leak /
    deadlock).

Anthropic is mocked; the mock BLOCKS on a threading.Event so we can observe how
many calls overlap. Nothing hits the network.
"""
from __future__ import annotations

import importlib
import threading
from types import SimpleNamespace

import pytest


def _reload_llm_with_cap(monkeypatch, cap: str | None, bg: str | None = None):
    """Reload app.llm so its module-level gate picks up LLM_MAX_CONCURRENCY
    (and optionally LLM_BG_CAP).

    The gate is created at import time from settings, so to exercise a specific
    cap we set the env, reload app.config (so settings re-reads it), then reload
    app.llm. `bg=None` clears LLM_BG_CAP so the default bg_cap (1) applies —
    matching the pre-existing tests. Returns the freshly reloaded llm module.
    """
    import app.config as config_mod

    if cap is None:
        monkeypatch.delenv("LLM_MAX_CONCURRENCY", raising=False)
    else:
        monkeypatch.setenv("LLM_MAX_CONCURRENCY", cap)
    if bg is None:
        monkeypatch.delenv("LLM_BG_CAP", raising=False)
    else:
        monkeypatch.setenv("LLM_BG_CAP", bg)
    importlib.reload(config_mod)
    import app.llm as llm_mod

    importlib.reload(llm_mod)
    return llm_mod


class _ConcurrencyProbe:
    """A fake Anthropic client whose messages.create blocks until released.

    Tracks the number of simultaneously in-flight calls and records the high
    watermark, so a test can assert the cap was never exceeded.
    """

    def __init__(self, *, response, release_event: threading.Event):
        self._response = response
        self._release = release_event
        self._lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0
        self.started = threading.Semaphore(0)  # +1 each time a call enters
        outer = self

        class _Messages:
            def create(self, **kwargs):
                with outer._lock:
                    outer.in_flight += 1
                    outer.max_in_flight = max(outer.max_in_flight, outer.in_flight)
                outer.started.release()
                try:
                    # Hold the slot until the test lets calls drain.
                    outer._release.wait(timeout=10)
                    return outer._response
                finally:
                    with outer._lock:
                        outer.in_flight -= 1

        self.messages = _Messages()


def _msg(text="ok"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=1, output_tokens=1,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
        stop_reason="end_turn",
    )


def _run_concurrent_calls(llm_mod, probe, n_calls):
    """Fire n_calls call_md's, each on its own thread (mirrors how heavy
    callers dispatch the sync chokepoint via asyncio.to_thread / threads).

    Returns (threads, errors) — errors is a thread-safe list of exceptions.
    """
    errors: list[BaseException] = []
    err_lock = threading.Lock()

    def _one():
        try:
            llm_mod.call_md(system="s", user="u")
        except BaseException as exc:  # noqa: BLE001 — recorded for the assert
            with err_lock:
                errors.append(exc)

    threads = [threading.Thread(target=_one) for _ in range(n_calls)]
    for t in threads:
        t.start()
    return threads, errors


# ---------------------------------------------------------------------------


def test_concurrency_never_exceeds_cap(isolated_settings, monkeypatch):
    """Launch N+3 concurrent calls against a blocking mock; the number of
    simultaneously in-flight Anthropic calls must never exceed N, and all
    N+3 must eventually complete."""
    cap = 3
    llm = _reload_llm_with_cap(monkeypatch, str(cap))

    release = threading.Event()
    probe = _ConcurrencyProbe(response=_msg(), release_event=release)
    monkeypatch.setattr(llm, "get_client", lambda: probe)

    n_calls = cap + 3
    threads, errors = _run_concurrent_calls(llm, probe, n_calls)

    # Let exactly `cap` calls enter; the rest must be blocked on the semaphore.
    for _ in range(cap):
        assert probe.started.acquire(timeout=5), "expected a call to start"
    # Give any (incorrectly) un-capped extra calls a chance to slip through.
    assert not probe.started.acquire(timeout=0.5), (
        "a call started while the cap should have been saturated"
    )
    assert probe.in_flight == cap
    assert probe.max_in_flight == cap

    # Release everything; all N+3 must drain and complete.
    release.set()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive()

    assert errors == []
    assert probe.in_flight == 0
    # The high watermark across the whole run never exceeded the cap.
    assert probe.max_in_flight == cap


def test_env_override_changes_cap(isolated_settings, monkeypatch):
    """LLM_MAX_CONCURRENCY=2 must lower the cap to 2 in-flight calls."""
    llm = _reload_llm_with_cap(monkeypatch, "2")
    assert llm._resolve_max_concurrency() == 2

    release = threading.Event()
    probe = _ConcurrencyProbe(response=_msg(), release_event=release)
    monkeypatch.setattr(llm, "get_client", lambda: probe)

    threads, errors = _run_concurrent_calls(llm, probe, 5)
    for _ in range(2):
        assert probe.started.acquire(timeout=5)
    assert not probe.started.acquire(timeout=0.5)
    assert probe.max_in_flight == 2

    release.set()
    for t in threads:
        t.join(timeout=10)
    assert errors == []
    assert probe.max_in_flight == 2


def test_invalid_or_zero_cap_falls_back_to_default(isolated_settings, monkeypatch):
    """A 0 / negative / unset cap must fall back to the default (never 0,
    which would deadlock every call)."""
    llm = _reload_llm_with_cap(monkeypatch, "0")
    assert llm._resolve_max_concurrency() == llm._DEFAULT_MAX_CONCURRENCY

    llm = _reload_llm_with_cap(monkeypatch, "-5")
    assert llm._resolve_max_concurrency() == llm._DEFAULT_MAX_CONCURRENCY

    llm = _reload_llm_with_cap(monkeypatch, None)
    assert llm._resolve_max_concurrency() == llm._DEFAULT_MAX_CONCURRENCY


def test_single_call_still_works(isolated_settings, monkeypatch):
    """A lone call returns its content unchanged — the cap is invisible at
    low concurrency."""
    llm = _reload_llm_with_cap(monkeypatch, "3")
    release = threading.Event()
    release.set()  # don't block — single call returns immediately
    probe = _ConcurrencyProbe(response=_msg("hello"), release_event=release)
    monkeypatch.setattr(llm, "get_client", lambda: probe)

    out = llm.call_md(system="s", user="u")
    assert out == "hello"
    assert probe.max_in_flight == 1


def test_prd_two_part_path_runs_both_within_cap(isolated_settings, monkeypatch):
    """The PRD path runs two parallel parts via asyncio.gather over to_thread.
    Both must run (2 <= default cap of 3) and the in-flight count peaks at 2."""
    import asyncio

    llm = _reload_llm_with_cap(monkeypatch, "3")
    release = threading.Event()
    probe = _ConcurrencyProbe(response=_msg("part"), release_event=release)
    monkeypatch.setattr(llm, "get_client", lambda: probe)

    # A watcher releases the mock once BOTH parts are in flight, proving the two
    # parts overlap (and fit) under the cap rather than running serially.
    def _release_when_both_started():
        assert probe.started.acquire(timeout=5)
        assert probe.started.acquire(timeout=5)
        release.set()

    watcher = threading.Thread(target=_release_when_both_started)
    watcher.start()

    def _call_part_a():
        return llm.call_md(system="A", user="a")

    def _call_part_b():
        return llm.call_md(system="B", user="b")

    async def _run():
        return await asyncio.gather(
            asyncio.to_thread(_call_part_a),
            asyncio.to_thread(_call_part_b),
        )

    a, b = asyncio.run(_run())
    watcher.join(timeout=5)
    assert a == "part" and b == "part"
    assert probe.max_in_flight == 2  # both parts overlapped, capped at 2


def test_background_lane_capped_at_one(isolated_settings, monkeypatch):
    """With cap=3, three concurrent BACKGROUND calls must run one at a time
    (bg_cap=1) while an interactive call still enters alongside."""
    llm = _reload_llm_with_cap(monkeypatch, "3")
    release = threading.Event()
    probe = _ConcurrencyProbe(response=_msg(), release_event=release)
    monkeypatch.setattr(llm, "get_client", lambda: probe)

    errors: list[BaseException] = []

    def _bg():
        try:
            llm.call_md(system="s", user="u", background=True)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_bg) for _ in range(3)]
    for t in threads:
        t.start()

    # Exactly ONE background call may be in flight.
    assert probe.started.acquire(timeout=5)
    assert not probe.started.acquire(timeout=0.5), (
        "a second background call ran concurrently — bg lane uncapped"
    )
    assert probe.in_flight == 1

    # An interactive call enters immediately despite the queued background work.
    t_int = threading.Thread(target=lambda: llm.call_md(system="i", user="u"))
    t_int.start()
    assert probe.started.acquire(timeout=5), "interactive call blocked behind bg lane"
    assert probe.in_flight == 2

    release.set()
    for t in [*threads, t_int]:
        t.join(timeout=10)
        assert not t.is_alive()
    assert errors == []


def test_bg_cap_env_override_allows_more_concurrent_background(
    isolated_settings, monkeypatch
):
    """LLM_BG_CAP=2 (with cap=4) lets TWO background calls run concurrently —
    the warm-parallelism knob — while a third queues and interactive still has
    a slot."""
    llm = _reload_llm_with_cap(monkeypatch, "4", bg="2")
    assert llm._resolve_bg_cap() == 2

    release = threading.Event()
    probe = _ConcurrencyProbe(response=_msg(), release_event=release)
    monkeypatch.setattr(llm, "get_client", lambda: probe)

    errors: list[BaseException] = []

    def _bg():
        try:
            llm.call_md(system="s", user="u", background=True)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_bg) for _ in range(3)]
    for t in threads:
        t.start()

    # TWO background calls may now be in flight at once (bg_cap=2)...
    assert probe.started.acquire(timeout=5)
    assert probe.started.acquire(timeout=5)
    # ...but not a third — the bg lane is capped at 2.
    assert not probe.started.acquire(timeout=0.5), (
        "a third background call ran — bg_cap=2 not enforced"
    )
    assert probe.in_flight == 2

    release.set()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive()
    assert errors == []


def test_resolve_bg_cap_fallbacks(isolated_settings, monkeypatch):
    """A 0 / negative / unset LLM_BG_CAP falls back to the default (1)."""
    llm = _reload_llm_with_cap(monkeypatch, "4", bg="0")
    assert llm._resolve_bg_cap() == llm._DEFAULT_BG_CAP
    llm = _reload_llm_with_cap(monkeypatch, "4", bg="-3")
    assert llm._resolve_bg_cap() == llm._DEFAULT_BG_CAP
    llm = _reload_llm_with_cap(monkeypatch, "4", bg=None)
    assert llm._resolve_bg_cap() == llm._DEFAULT_BG_CAP


def test_bg_cap_clamped_below_capacity(isolated_settings, monkeypatch):
    """The gate clamps bg_cap to capacity-1 so background can never occupy
    every slot (an interactive caller always has a reachable slot)."""
    llm = _reload_llm_with_cap(monkeypatch, "3", bg="9")
    # _resolve_bg_cap reflects the raw env; the gate clamps on construction.
    assert llm._llm_gate._bg_cap == 2  # capacity(3) - 1


def test_interactive_waiter_jumps_background_queue(isolated_settings, monkeypatch):
    """With cap=1 and a held slot, a background waiter that queued FIRST must
    still run AFTER an interactive waiter that queued later."""
    llm = _reload_llm_with_cap(monkeypatch, "1")

    order: list[str] = []
    order_lock = threading.Lock()
    first_entered = threading.Event()
    release_first = threading.Event()

    class _OrderProbe:
        class _Messages:
            def create(self, **kwargs):
                label = kwargs.get("system")
                with order_lock:
                    order.append(label)
                if label == "first":
                    first_entered.set()
                    release_first.wait(timeout=10)
                return _msg()

        messages = _Messages()

    monkeypatch.setattr(llm, "get_client", lambda: _OrderProbe())

    t_first = threading.Thread(target=lambda: llm.call_md(system="first", user="u"))
    t_first.start()
    assert first_entered.wait(timeout=5)

    # Queue a background waiter FIRST...
    t_bg = threading.Thread(
        target=lambda: llm.call_md(system="bg", user="u", background=True)
    )
    t_bg.start()
    _time_buffer = threading.Event()
    _time_buffer.wait(0.3)  # let the bg waiter actually park in the gate
    # ...then an interactive waiter.
    t_int = threading.Thread(target=lambda: llm.call_md(system="int", user="u"))
    t_int.start()
    _time_buffer.wait(0.3)

    release_first.set()
    for t in (t_first, t_bg, t_int):
        t.join(timeout=10)
        assert not t.is_alive()

    assert order == ["first", "int", "bg"], (
        f"interactive did not jump the background queue: {order}"
    )


def test_slot_released_on_exception_no_leak(isolated_settings, monkeypatch):
    """If the Anthropic call raises, the slot must be released (finally) so a
    later call can still acquire it — i.e. no slot leak / deadlock. With cap=1,
    a failing call followed by a succeeding call proves the slot freed."""
    llm = _reload_llm_with_cap(monkeypatch, "1")

    class _Boom:
        class _Messages:
            def create(self, **kwargs):
                raise ValueError("non-retryable boom")

        messages = _Messages()

    monkeypatch.setattr(llm, "get_client", lambda: _Boom())
    with pytest.raises(ValueError):
        llm.call_md(system="s", user="u")

    # If the slot leaked, this second call would block forever on the cap=1
    # semaphore. It must acquire and return promptly.
    ok = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: _msg("after")))
    monkeypatch.setattr(llm, "get_client", lambda: ok)

    result_box: dict = {}

    def _second():
        result_box["out"] = llm.call_md(system="s", user="u")

    t = threading.Thread(target=_second)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "second call deadlocked — a slot was leaked"
    assert result_box["out"] == "after"
