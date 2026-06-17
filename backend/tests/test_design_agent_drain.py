"""Tier 0 — graceful drain on shutdown.

On a deploy/restart SIGTERM the lifespan teardown:
  1. marks the process draining (``request_shutdown``) so POST /generate returns
     503 instead of starting work a pending SIGKILL would abandon mid-build; and
  2. awaits in-flight generation up to a tunable deadline (``drain_inflight``),
     never cancelling on timeout (the vite thread is uncancellable) and never
     raising (a drain error must not block shutdown).

The startup ``invalidate_orphan_generating_prototypes`` sweep recovers any
'generating' row left behind by a drain timeout on the next boot, so no extra
checkpoint code is needed here.

These tests mock the heavy work — no real generation runs.
"""
from __future__ import annotations

import asyncio
import importlib
import logging

import pytest
from fastapi import HTTPException


@pytest.fixture
def routes(isolated_settings, monkeypatch):
    """Feature flag ON + a freshly reloaded design-agent routes module so its
    module-level ``_shutting_down`` flag and ``_inflight_tasks`` set start clean
    for this test (reload resets module globals)."""
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    import app.config as _config_mod
    importlib.reload(_config_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    return routes_mod


# ── drain_inflight ───────────────────────────────────────────────────────────


async def test_drain_awaits_inflight_task_to_completion(routes):
    """A long-running task registered in _inflight_tasks is awaited by the drain
    coroutine (within the deadline) and the drain returns after it finishes."""
    finished = asyncio.Event()

    async def _work():
        await asyncio.sleep(0.05)
        finished.set()
        return "done"

    task = asyncio.create_task(_work())
    routes._inflight_tasks.add(task)
    task.add_done_callback(routes._inflight_tasks.discard)

    assert not task.done()
    await routes.drain_inflight(5.0)  # deadline far exceeds the 0.05s task

    assert task.done(), "drain returned before the in-flight task completed"
    assert finished.is_set()
    assert routes._shutting_down is True, "drain must mark the process draining"
    await task  # surface any task exception


async def test_drain_sets_shutting_down_and_request_shutdown_too(routes):
    """Both request_shutdown() and drain_inflight() flip the draining flag."""
    assert routes._shutting_down is False
    routes.request_shutdown()
    assert routes._shutting_down is True


async def test_drain_returns_on_empty_set_without_error(routes):
    """asyncio.wait raises on an empty set — the empty-inflight case must be
    guarded and return cleanly."""
    assert not routes._inflight_tasks
    await routes.drain_inflight(5.0)  # must NOT raise
    assert routes._shutting_down is True


async def test_drain_times_out_without_cancelling_or_raising(routes, caplog):
    """When a task outruns the deadline, drain returns (does NOT raise), does NOT
    cancel the task, and logs a warning naming the still-running work. The task
    keeps running; the next-boot orphan sweep recovers its DB row."""
    started = asyncio.Event()

    async def _slow():
        started.set()
        await asyncio.sleep(0.30)
        return "eventually"

    task = asyncio.create_task(_slow())
    task.set_name("proto-99")
    routes._inflight_tasks.add(task)
    task.add_done_callback(routes._inflight_tasks.discard)

    await started.wait()
    with caplog.at_level(logging.WARNING, logger="app.routes.design_agent"):
        # Deadline well under the task's 0.30s runtime → timeout path.
        await routes.drain_inflight(0.02)

    assert not task.cancelled(), "drain must NOT cancel an uncancellable heavy task"
    assert not task.done(), "task should still be running after the drain deadline"
    msgs = [r.getMessage() for r in caplog.records]
    assert any("drain_timeout" in m for m in msgs), "deadline elapse must log a warning"
    assert any("proto-99" in m for m in msgs), "warning must name the still-running task"

    # Let it finish so the test loop has no orphaned task.
    result = await task
    assert result == "eventually"


async def test_drain_never_raises_on_internal_error(routes, monkeypatch, caplog):
    """A failure inside the drain (e.g. asyncio.wait blowing up) is swallowed so
    shutdown is never blocked."""
    task = asyncio.create_task(asyncio.sleep(0.01))
    routes._inflight_tasks.add(task)
    task.add_done_callback(routes._inflight_tasks.discard)

    async def _boom(*a, **k):
        raise RuntimeError("simulated wait failure")

    monkeypatch.setattr(routes.asyncio, "wait", _boom)
    with caplog.at_level(logging.WARNING, logger="app.routes.design_agent"):
        await routes.drain_inflight(5.0)  # must NOT propagate

    msgs = [r.getMessage() for r in caplog.records]
    assert any("drain_error" in m for m in msgs)
    await task  # cleanup


# ── /generate 503 while draining ─────────────────────────────────────────────


async def test_generate_returns_503_when_draining(routes):
    """POST /generate raises HTTPException(503, 'service is draining, retry
    shortly') once the process is marked draining — before any DB work."""
    from app.auth import CompanyContext

    routes.request_shutdown()
    body = routes.GenerateRequest(prd_id=1)
    company = CompanyContext(company_id="app", role="owner", user_id="u1")

    with pytest.raises(HTTPException) as exc_info:
        await routes.generate(body=body, company=company)

    assert exc_info.value.status_code == 503
    assert "draining" in exc_info.value.detail


async def test_generate_passes_drain_gate_when_not_draining(routes, monkeypatch):
    """Sanity: when NOT draining, the 503 gate is inert — the handler proceeds
    past it (we stub the DB dedupe to short-circuit before real generation, so
    no heavy work runs)."""
    from app.auth import CompanyContext

    assert routes._shutting_down is False

    # Short-circuit at the find_existing dedupe so we never start a real task:
    # returning an existing row makes generate() return early with that status.
    monkeypatch.setattr(
        routes, "find_existing_prototype",
        lambda **k: {"id": 7, "status": "ready"},
    )

    body = routes.GenerateRequest(prd_id=1)
    company = CompanyContext(company_id="app", role="owner", user_id="u1")
    resp = await routes.generate(body=body, company=company)

    # Passed the drain gate (no 503) and hit the dedupe early-return.
    assert resp.prototype_id == 7
    assert resp.status == "ready"
