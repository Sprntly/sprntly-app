"""Tests for the member-add post-insert side-effect deferral
(`app/routes/projects.py::_dispatch_member_add_side_effects`).

Both member-add mutation surfaces (`add_member`'s TIER_WORKSPACE/
TIER_COMPANY branch, `tag_candidate_route`'s TIER_WORKSPACE branch) used to
run three best-effort side effects — a realtime publish, the on-join
greeting (a narrative LLM call), and the "added to project" email —
SYNCHRONOUSLY before returning. They are now deferred off the request
thread in production; under pytest (`"pytest" in sys.modules`) they still
run inline so every existing test that asserts on the greeting turn /
member.added publish / notify email — driven through the HTTP route
(`test_project_join_greeting.py`, `test_project_added_notifications.py`) —
keeps passing unchanged. This file adds direct coverage of the deferral
mechanism itself: the route calls the DISPATCHER rather than the three
side effects directly, the production branch genuinely runs off-thread, it
carries the calling thread's contextvars into that thread, and a raising
side effect never surfaces past the dispatcher.
"""
from __future__ import annotations

import sys
import threading

from app.db import projects as projects_db
from app.routes import projects as projects_routes
from app import usage_context
from tests._company_helpers import company_client


def _new_project(ctx, name: str = "Growing team") -> dict:
    r = ctx.client.post("/v1/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _pin_workspace_resolver(monkeypatch, *, user_id: str, email: str, name: str) -> None:
    monkeypatch.setattr(
        projects_db,
        "resolve_candidate",
        lambda pid, needle: {  # noqa: ARG005
            "tier": projects_db.TIER_WORKSPACE,
            "user_id": user_id,
            "email": email,
            "name": name,
        },
    )


def _no_pytest_sys_modules() -> dict:
    """A copy of `sys.modules` with the `"pytest"` key dropped — flips
    `_dispatch_member_add_side_effects`'s `"pytest" in sys.modules` check to
    False so a test can drive the production (off-thread) branch, without
    actually clearing the real module cache (a bare `{}` swap would risk
    breaking any fresh import the worker thread happens to trigger)."""
    return {k: v for k, v in sys.modules.items() if k != "pytest"}


# ── The route calls the dispatcher, not the three side effects directly ────


def test_add_member_calls_the_dispatcher(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    _pin_workspace_resolver(monkeypatch, user_id="added-uid", email="peer@acme.example", name="Peer")

    calls: list[dict] = []
    monkeypatch.setattr(
        projects_routes,
        "_dispatch_member_add_side_effects",
        lambda **kw: calls.append(kw),
    )

    r = ctx.client.post(f"/v1/projects/{project['id']}/members", json={"email": "peer@acme.example"})
    assert r.status_code == 200, r.text
    assert len(calls) == 1
    assert calls[0]["project_id"] == project["id"]
    assert calls[0]["user_id"] == "added-uid"
    assert calls[0]["email"] == "peer@acme.example"
    assert calls[0]["recipient_name"] == "Peer"


def test_tag_workspace_calls_the_dispatcher(isolated_settings, monkeypatch):
    from app.db.workspaces import upsert_workspace_member

    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    upsert_workspace_member(project["workspace_id"], "tagged-uid", "member")
    _pin_workspace_resolver(monkeypatch, user_id="tagged-uid", email="wanda@acme.example", name="Wanda")

    calls: list[dict] = []
    monkeypatch.setattr(
        projects_routes,
        "_dispatch_member_add_side_effects",
        lambda **kw: calls.append(kw),
    )

    r = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "Wanda"})
    assert r.status_code == 200, r.text
    assert len(calls) == 1
    assert calls[0]["user_id"] == "tagged-uid"


# ── Under pytest: inline, in order (existing greeting/publish/email tests
# already prove this end to end; this pins the dispatcher's own contract) ──


def test_dispatch_runs_inline_under_pytest():
    order: list[str] = []
    monkeypatch_targets = {
        "_publish_member_added": lambda *a, **k: order.append("publish"),  # noqa: ARG005
        "post_join_greeting": lambda *a, **k: order.append("greeting"),  # noqa: ARG005
    }
    orig_publish = projects_routes.project_delegation._publish_member_added
    orig_greeting = projects_routes.project_join_greeting.post_join_greeting
    orig_notify = projects_routes._notify_added_to_project
    try:
        projects_routes.project_delegation._publish_member_added = monkeypatch_targets["_publish_member_added"]
        projects_routes.project_join_greeting.post_join_greeting = monkeypatch_targets["post_join_greeting"]
        projects_routes._notify_added_to_project = lambda **k: order.append("notify")  # noqa: ARG005

        projects_routes._dispatch_member_add_side_effects(
            project_id=1, user_id="u1", project_name="P", dataset="ds",
            company_id="co", email="e@x.co", recipient_name="E",
        )
    finally:
        projects_routes.project_delegation._publish_member_added = orig_publish
        projects_routes.project_join_greeting.post_join_greeting = orig_greeting
        projects_routes._notify_added_to_project = orig_notify

    # Inline (pytest) — all three ran, in the pre-deferral order, by the
    # time the dispatcher call returns (no future to wait on).
    assert order == ["publish", "greeting", "notify"]


# ── Production: genuinely off the calling thread, contextvars carried ──────


def test_dispatch_runs_off_thread_in_production(monkeypatch):
    main_thread_id = threading.get_ident()
    calls: list[dict] = []

    def _record(**kwargs):
        calls.append({"thread_id": threading.get_ident(), **kwargs})

    monkeypatch.setattr(projects_routes, "_run_member_add_side_effects", _record)
    monkeypatch.setattr(projects_routes.sys, "modules", _no_pytest_sys_modules())

    projects_routes._dispatch_member_add_side_effects(
        project_id=1, user_id="u1", project_name="P", dataset="ds",
        company_id="co", email="e@x.co", recipient_name="E",
    )
    for fut in list(projects_routes._inflight_member_add_futures):
        fut.result(timeout=5)

    assert len(calls) == 1
    assert calls[0]["user_id"] == "u1"
    assert calls[0]["thread_id"] != main_thread_id, (
        "expected the side effects to run off the request thread in production"
    )


def test_dispatch_carries_contextvars_into_the_background_thread(monkeypatch):
    """`post_join_greeting` makes an LLM call that reads ambient
    company-scoped bindings (the LLM key bound by `CompanyLLMKeyMiddleware`,
    any `usage_scope`). A bare `.submit(...)` would lose them — this proves
    `contextvars.copy_context().run(...)` carries a scope opened on the
    calling thread into the worker thread that actually runs the side
    effects."""
    seen: list[usage_context.UsageScope] = []

    def _record(**kwargs):  # noqa: ARG001
        seen.append(usage_context.current_scope())

    monkeypatch.setattr(projects_routes, "_run_member_add_side_effects", _record)
    monkeypatch.setattr(projects_routes.sys, "modules", _no_pytest_sys_modules())

    with usage_context.usage_scope(feature="member_add_deferral_test", user_id="u1"):
        projects_routes._dispatch_member_add_side_effects(
            project_id=1, user_id="u1", project_name="P", dataset="ds",
            company_id="co", email="e@x.co", recipient_name="E",
        )
    for fut in list(projects_routes._inflight_member_add_futures):
        fut.result(timeout=5)

    assert len(seen) == 1
    assert seen[0].feature == "member_add_deferral_test"
    assert seen[0].user_id == "u1"


def test_dispatch_swallows_a_raising_side_effect_without_surfacing_it(monkeypatch):
    """A side effect that breaks its own best-effort contract (raises) must
    never propagate past the dispatcher — in production the done-callback
    logs it (see `_on_member_add_side_effects_done`); the caller (the
    already-returned HTTP response) never sees it either way."""

    def _boom(**kwargs):  # noqa: ARG001
        raise RuntimeError("simulated side-effect failure")

    monkeypatch.setattr(projects_routes, "_run_member_add_side_effects", _boom)
    monkeypatch.setattr(projects_routes.sys, "modules", _no_pytest_sys_modules())

    # Must not raise here — the future's exception is only observable via
    # future.exception()/the done-callback, never by calling the dispatcher.
    projects_routes._dispatch_member_add_side_effects(
        project_id=1, user_id="u1", project_name="P", dataset="ds",
        company_id="co", email="e@x.co", recipient_name="E",
    )
    futures = list(projects_routes._inflight_member_add_futures)
    assert len(futures) == 1
    # The pool discards + logs it via the done-callback; wait for that.
    import time

    for _ in range(50):
        if futures[0] not in projects_routes._inflight_member_add_futures:
            break
        time.sleep(0.05)
    assert isinstance(futures[0].exception(timeout=5), RuntimeError)
