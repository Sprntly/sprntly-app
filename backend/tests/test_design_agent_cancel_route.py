"""Tests for POST /v1/design-agent/{prototype_id}/cancel — the true-abort
escape hatch from the prototype generating screen.

Reuses the in-memory FakeSupabaseClient harness from test_design_agent_routes
(the `env` / `client` / `unauth` fixtures + `_seed_prd` / `_stub_generate`
helpers) so the route binds to the fake-Supabase-wired DB helpers. The cancel
endpoint mirrors the DELETE route's guards + cleanup and adds a best-effort
abort of the in-flight generation task via a SEPARATE, generation-only registry
(`_inflight_generation_tasks`) — the shared `_inflight_tasks` set (used by the
SIGTERM drain across five task types) is left untouched.
"""
from __future__ import annotations

import asyncio

import pytest

from app.auth import CompanyContext
from tests.conftest import _TEST_COMPANY_ID, _TEST_USER_ID
from tests.test_design_agent_routes import (  # noqa: F401 — reused pytest fixtures
    _seed_prd,
    _stub_generate,
    client,
    env,
    unauth,
)


class _StubTask:
    """Minimal stand-in for an in-flight asyncio.Task registered in the
    generation registry. Records whether cancel() was called without needing a
    running event loop or a real coroutine."""

    def __init__(self, *, done: bool = False) -> None:
        self._done = done
        self.cancel_calls = 0

    def done(self) -> bool:
        return self._done

    def cancel(self) -> bool:
        self.cancel_calls += 1
        return True


def _seed_generating_prototype(env, prd_id: int) -> int:
    return env.proto.start_prototype(
        prd_id=prd_id, workspace_id=_TEST_COMPANY_ID, template_version=1,
    )


# ─── Core: delete + reset-to-draft + 204 ───────────────────────────────────


def test_cancel_deletes_inflight_prototype_and_resets_prd_to_draft(env, client):
    prd_id = _seed_prd(env.db)
    assert env.db.get_prd(prd_id)["status"] == "ready"
    pid = _seed_generating_prototype(env, prd_id)

    resp = client.post(f"/v1/design-agent/{pid}/cancel")

    assert resp.status_code == 204, resp.text
    # Row deleted (workspace-scoped lookup now returns None).
    assert (
        env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
        is None
    )
    # PRD flipped back to draft — the clean-slate "undo it" outcome.
    assert env.db.get_prd(prd_id)["status"] == "draft"


# ─── Best-effort abort of the local task ───────────────────────────────────


def test_cancel_aborts_local_task_and_still_cleans_up(env, client):
    prd_id = _seed_prd(env.db)
    pid = _seed_generating_prototype(env, prd_id)
    stub = _StubTask()
    env.routes._inflight_generation_tasks[pid] = stub

    resp = client.post(f"/v1/design-agent/{pid}/cancel")

    assert resp.status_code == 204, resp.text
    # The in-process task was cancelled to stop future LLM turns.
    assert stub.cancel_calls == 1
    # Cleanup still ran (delete + reset), independent of the abort.
    assert (
        env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
        is None
    )
    assert env.db.get_prd(prd_id)["status"] == "draft"


def test_cancel_cleans_up_when_no_local_task_registered(env, client):
    # Multi-worker path: the task holding this generation lives in another
    # process, so nothing is registered here. Cleanup must still run.
    prd_id = _seed_prd(env.db)
    pid = _seed_generating_prototype(env, prd_id)
    assert pid not in env.routes._inflight_generation_tasks

    resp = client.post(f"/v1/design-agent/{pid}/cancel")

    assert resp.status_code == 204, resp.text
    assert (
        env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
        is None
    )
    assert env.db.get_prd(prd_id)["status"] == "draft"


# ─── Workspace isolation / gating / idempotency ────────────────────────────


def test_cancel_cross_workspace_returns_404(env, client):
    # Prototype under a FOREIGN workspace: the company caller filters by its own
    # company_id and must not see it (404, never 403 — no cross-tenant leak).
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id="demo", template_version=1,
    )

    resp = client.post(f"/v1/design-agent/{pid}/cancel")

    assert resp.status_code == 404
    # The foreign row was not deleted.
    assert (
        env.proto.get_prototype(prototype_id=pid, workspace_id="demo") is not None
    )


def test_cancel_returns_404_when_flag_unset(env, client, monkeypatch):
    prd_id = _seed_prd(env.db)
    pid = _seed_generating_prototype(env, prd_id)
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)

    resp = client.post(f"/v1/design-agent/{pid}/cancel")

    assert resp.status_code == 404
    # Flag-gated invisibility must not have performed the cleanup.
    assert (
        env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
        is not None
    )


def test_cancel_rejects_foreign_origin(env, client):
    prd_id = _seed_prd(env.db)
    pid = _seed_generating_prototype(env, prd_id)

    resp = client.post(
        f"/v1/design-agent/{pid}/cancel",
        headers={"Origin": "https://evil.example.com"},
    )

    assert resp.status_code == 403


def test_cancel_without_auth_returns_401(env, unauth):
    resp = unauth.post("/v1/design-agent/1/cancel")
    assert resp.status_code == 401


def test_cancel_already_absent_returns_404_not_500(env, client):
    # Already finished-and-deleted, or never existed → 404, never a 500.
    resp = client.post("/v1/design-agent/999999/cancel")
    assert resp.status_code == 404


# ─── Shared-set contract stays intact ──────────────────────────────────────


@pytest.mark.asyncio
async def test_generation_task_registered_in_both_registries_and_cleared(
    env, monkeypatch
):
    """The generate task is strong-ref'd in the shared `_inflight_tasks` SET
    (drain contract) AND keyed by prototype_id in `_inflight_generation_tasks`
    (cancel lookup); the done-callback removes it from BOTH. The shared set must
    remain a set of Tasks so the SIGTERM drain's `{t for t in _inflight_tasks
    if not t.done()}` never iterates int keys."""
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)

    resp = await env.routes.generate(
        body=env.routes.GenerateRequest(prd_id=prd_id),
        company=CompanyContext(
            company_id=_TEST_COMPANY_ID, role="owner", user_id=_TEST_USER_ID
        ),
    )
    pid = resp.prototype_id

    # Shared registry: still a SET, holding the one Task.
    assert isinstance(env.routes._inflight_tasks, set)
    assert len(env.routes._inflight_tasks) == 1
    (task,) = tuple(env.routes._inflight_tasks)
    # Generation-only registry: a dict keyed by prototype_id → the SAME task.
    assert isinstance(env.routes._inflight_generation_tasks, dict)
    assert env.routes._inflight_generation_tasks.get(pid) is task
    # The drain's set-comprehension over Tasks still works (no int keys leaked).
    pending = {t for t in env.routes._inflight_tasks if not t.done()}
    assert all(hasattr(t, "done") for t in pending)

    # Drain: the done-callback removes the finished task from BOTH registries.
    for _ in range(1000):
        if not env.routes._inflight_tasks and not env.routes._inflight_generation_tasks:
            break
        await asyncio.sleep(0)
    assert env.routes._inflight_tasks == set()
    assert env.routes._inflight_generation_tasks == {}


# ─── _run_generation_bg cancel-safety ──────────────────────────────────────


@pytest.mark.asyncio
async def test_run_generation_bg_cancel_does_not_write_terminal_status(
    env, monkeypatch
):
    """When the generation task is cancelled, `_run_generation_bg` must NOT
    write a ready/failed status for the (now-deleted) id — no resurrection. The
    CancelledError propagates instead of being swallowed by the `except
    Exception` failure path."""
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    _stub_generate(monkeypatch, env.routes, raises=asyncio.CancelledError())
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id=_TEST_COMPANY_ID, template_version=1,
    )

    complete_calls: list[dict] = []
    fail_calls: list[dict] = []
    monkeypatch.setattr(
        env.routes, "complete_prototype", lambda **kw: complete_calls.append(kw)
    )
    monkeypatch.setattr(
        env.routes, "fail_prototype", lambda **kw: fail_calls.append(kw)
    )

    with pytest.raises(asyncio.CancelledError):
        await env.routes._run_generation_bg(
            prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prd_id=prd_id,
            target_platform="both", instructions="", figma_file_key=None,
        )

    # No terminal status write on the cancel path.
    assert complete_calls == []
    assert fail_calls == []
