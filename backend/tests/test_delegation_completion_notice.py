"""Tests for the requester-facing completion notice
(`app.delegation_status_ingest.notify_requester_task_completed`) and its
wiring into all three delegation-completion sites: the inbound classifier's
`done_explicit` branch, the outbound sweep's soft-done finalize, and the
manual ledger `emit_delegation_event_route`.

Drives each site directly (`_apply_classification`, `_process_one`, the
FastAPI route via `TestClient`) against `FakeSupabaseClient`
(`isolated_settings`), with the notify helper's own DB/routing calls
(`_route_to_requester` / `_post_to_own_chat` / `_display_first_name`)
monkeypatched at the helper-level tests — mirrors the fake/monkeypatch
style of `test_delegation_status_ingest.py` / `test_delegation_followup.py`
/ `test_delegation_events_api.py`.

AC1 (a real "done" reply -> exactly one requester notice, end to end
through the real classifier LLM) is NOT a unit test here — it is the
ship-gate's live real-DB + real-classifier check.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app import delegation_followup as followup_mod
from app import delegation_status_ingest as ingest
from app.db import delegation_events as delegation_events_db
from app.db.project_delegations import record_delegation
from app.routes import projects as projects_routes
from tests._company_helpers import company_client
from tests._project_helpers import seed_same_tenant_non_member

_NOW = datetime(2026, 8, 12, 12, 0, 0, tzinfo=timezone.utc)


# ── Shared fixtures ───────────────────────────────────────────────────────


def _create_project(ctx, *, name: str = "Completion-notice project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


def _seed_member(ctx, *project_ids: int) -> tuple[str, dict]:
    from app.db import projects as projects_db

    user_id, headers = seed_same_tenant_non_member(ctx)
    for pid in project_ids:
        projects_db.add_member(pid, user_id)
    return user_id, headers


def _seed_delegation(
    project_id: int, assigner_user_id: str, assignee_user_id: str, *, task_summary: str = "Draft the brief"
) -> dict:
    return record_delegation(
        project_id=project_id,
        assigner_user_id=assigner_user_id,
        assignee_user_id=assignee_user_id,
        task_summary=task_summary,
        source_conversation_id=None,
        source_turn_id=None,
        delivered_conversation_id=None,
        delivered_turn_id=None,
    )


def _events_url(project_id, delegation_id) -> str:
    return f"/v1/projects/{project_id}/delegations/{delegation_id}/events"


def _fake_status_row(deleg_row: dict) -> dict:
    events = delegation_events_db.list_events(deleg_row["id"])
    latest = events[-1] if events else None
    return {
        "delegation_id": deleg_row["id"],
        "project_id": deleg_row["project_id"],
        "assigner_user_id": deleg_row["assigner_user_id"],
        "assignee_user_id": deleg_row["assignee_user_id"],
        "task_summary": deleg_row["task_summary"],
        "delivered_conversation_id": deleg_row.get("delivered_conversation_id"),
        "delivered_turn_id": deleg_row.get("delivered_turn_id"),
        "status": latest["event"] if latest else "assigned",
        "status_at": latest["created_at"] if latest else deleg_row["created_at"],
    }


def _install_fake_ledger_views(monkeypatch) -> None:
    """Stand-in for `current_status` / `status_dto` — see
    `test_delegation_events_api.py`'s identical helper for why the real
    Postgres view (`v_delegation_status`) can't evaluate against
    `FakeSupabaseClient`."""
    from app.db.client import require_client

    def _all_delegations() -> list[dict]:
        return require_client().table("project_delegations").select("*").execute().data or []

    def _current_status(delegation_id):
        rows = [d for d in _all_delegations() if d["id"] == delegation_id]
        return _fake_status_row(rows[0])["status"] if rows else None

    def _status_dto(delegation_id):
        rows = [d for d in _all_delegations() if d["id"] == delegation_id]
        if not rows:
            return None
        row = _fake_status_row(rows[0])
        return {
            "delegation_id": row["delegation_id"],
            "status": row["status"],
            "status_at": row["status_at"],
            "task_summary": row["task_summary"],
        }

    monkeypatch.setattr(delegation_events_db, "current_status", _current_status)
    monkeypatch.setattr(delegation_events_db, "status_dto", _status_dto)


# ── notify_requester_task_completed — recipient / format / non-fatal ─────
# (helper-level, pure — monkeypatch the routing seam only)


def _install_notify_seam(monkeypatch, *, assigner_id: str = "U_req"):
    """Stubs the two calls `notify_requester_task_completed` makes through
    `_route_to_requester`: `load_delegation_for_authz` (source of the
    assigner id) and `_post_to_own_chat` (the actual post, spied on)."""
    posts: list[tuple] = []

    monkeypatch.setattr(
        ingest.delegation_events_db,
        "load_delegation_for_authz",
        lambda delegation_id: {"assigner_user_id": assigner_id},
    )
    monkeypatch.setattr(
        ingest, "_post_to_own_chat",
        lambda project_id, user_id, text: posts.append((project_id, user_id, text)),
    )
    monkeypatch.setattr(ingest, "_display_first_name", lambda project_id, user_id: "Ada")
    return posts


def test_notify_routes_to_assigner_not_assignee(monkeypatch):
    posts = _install_notify_seam(monkeypatch, assigner_id="U_req")

    ingest.notify_requester_task_completed(
        1, 5, assignee_user_id="U_do", task_summary="Ship the deck",
    )

    assert len(posts) == 1
    assert posts[0][1] == "U_req"
    assert posts[0][1] != "U_do"


def test_notify_text_with_summary(monkeypatch):
    posts = _install_notify_seam(monkeypatch)

    ingest.notify_requester_task_completed(
        1, 5, assignee_user_id="U_do", task_summary="Ship the deck",
    )

    assert posts[0][2] == "✓ Ada finished: Ship the deck"


def test_notify_text_empty_summary_fallback(monkeypatch):
    posts = _install_notify_seam(monkeypatch)

    ingest.notify_requester_task_completed(
        1, 5, assignee_user_id="U_do", task_summary="",
    )

    assert posts[0][2] == "✓ Ada finished the task."


def test_notify_swallows_post_failure(monkeypatch):
    monkeypatch.setattr(ingest, "_display_first_name", lambda project_id, user_id: "Ada")

    def _boom(project_id, delegation_id, text):  # noqa: ARG001
        raise RuntimeError("simulated post failure")

    monkeypatch.setattr(ingest, "_route_to_requester", _boom)

    result = ingest.notify_requester_task_completed(
        1, 5, assignee_user_id="U_do", task_summary="Ship the deck",
    )
    assert result is None


# ── Site 1 — classifier `done_explicit` branch (guard + notify) ──────────


def _out(delegation_id: int, *, intent: str = "done_explicit") -> dict:
    return {
        "delegation_id": delegation_id,
        "intent": intent,
        "stated_completion": None,
        "proposed_next_check_in": None,
    }


def test_done_explicit_on_completed_row_records_and_notifies_zero_times(isolated_settings, monkeypatch):
    """Regression — on unfixed code `record_event` fires unconditionally
    on an already-`completed` row (AC4)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, _ = _seed_member(ctx, project["id"])
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id, task_summary="Ship the deck")

    record_calls: list = []
    notify_calls: list = []
    monkeypatch.setattr(
        ingest.delegation_events_db, "record_event",
        lambda **kwargs: record_calls.append(kwargs),
    )
    monkeypatch.setattr(
        ingest, "notify_requester_task_completed",
        lambda *a, **kw: notify_calls.append((a, kw)),
    )

    open_map = {deleg["id"]: {"status": "completed", "task_summary": "Ship the deck"}}
    ingest._apply_classification(
        project_id=project["id"], replier_user_id=assignee_id, open_map=open_map,
        out=_out(deleg["id"]),
    )

    assert record_calls == []
    assert notify_calls == []


def test_done_explicit_records_completed_then_notifies_requester(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, _ = _seed_member(ctx, project["id"])
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id, task_summary="Ship the deck")

    record_calls: list = []
    notify_calls: list = []
    monkeypatch.setattr(
        ingest.delegation_events_db, "record_event",
        lambda **kwargs: record_calls.append(kwargs),
    )
    monkeypatch.setattr(
        ingest, "notify_requester_task_completed",
        lambda *a, **kw: notify_calls.append((a, kw)),
    )

    open_map = {deleg["id"]: {"status": "in_progress", "task_summary": "Ship the deck"}}
    ingest._apply_classification(
        project_id=project["id"], replier_user_id=assignee_id, open_map=open_map,
        out=_out(deleg["id"]),
    )

    assert len(record_calls) == 1
    assert record_calls[0]["event"] == "completed"
    assert record_calls[0]["delegation_id"] == deleg["id"]
    assert record_calls[0]["actor_user_id"] == assignee_id

    assert len(notify_calls) == 1
    args, kwargs = notify_calls[0]
    assert args == (project["id"], deleg["id"])
    assert kwargs["assignee_user_id"] == assignee_id
    assert kwargs["task_summary"] == "Ship the deck"


# ── Site 2 — sweep's soft-done finalize (`_process_one`) ─────────────────


def test_sweep_inferred_done_finalize_notifies_requester(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, _ = _seed_member(ctx, project["id"])
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id, task_summary="Draft the onboarding flow")

    record_calls: list = []
    notify_calls: list = []
    monkeypatch.setattr(
        followup_mod.delegation_events_db, "record_event",
        lambda **kwargs: record_calls.append(kwargs),
    )
    monkeypatch.setattr(
        followup_mod, "notify_requester_task_completed",
        lambda *a, **kw: notify_calls.append((a, kw)),
    )

    row = {
        "delegation_id": deleg["id"],
        "project_id": project["id"],
        "assigner_user_id": ctx.user_id,
        "assignee_user_id": assignee_id,
        "task_summary": "Draft the onboarding flow",
        "delivered_conversation_id": None,
        "next_check_in": _NOW.isoformat(),
        "last_checked_in": None,
        "expected_completion": None,
        "pending_done_since": (_NOW - timedelta(hours=2)).isoformat(),
        "status": "in_progress",
    }
    summary: dict = defaultdict(int)

    followup_mod._process_one(row, now=_NOW, tz_map={}, summary=summary)

    assert summary["finalized"] == 1
    assert len(record_calls) == 1
    assert record_calls[0]["event"] == "completed"
    assert record_calls[0]["delegation_id"] == deleg["id"]

    assert len(notify_calls) == 1
    args, kwargs = notify_calls[0]
    assert args == (project["id"], deleg["id"])
    assert kwargs["assignee_user_id"] == assignee_id
    assert kwargs["task_summary"] == "Draft the onboarding flow"


# ── Site 3 — manual ledger route (`emit_delegation_event_route`) ─────────


def test_route_completed_event_notifies_requester(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, assignee_headers = _seed_member(ctx, project["id"])
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id)
    _install_fake_ledger_views(monkeypatch)

    notify_calls: list = []
    monkeypatch.setattr(
        projects_routes, "notify_requester_task_completed",
        lambda *a, **kw: notify_calls.append((a, kw)),
    )

    r1 = ctx.client.post(
        _events_url(project["id"], deleg["id"]), json={"event": "in_progress"}, headers=assignee_headers,
    )
    assert r1.status_code == 200
    assert notify_calls == []

    r2 = ctx.client.post(
        _events_url(project["id"], deleg["id"]), json={"event": "completed"}, headers=assignee_headers,
    )
    assert r2.status_code == 200

    assert len(notify_calls) == 1
    args, kwargs = notify_calls[0]
    assert args == (project["id"], deleg["id"])
    assert kwargs["assignee_user_id"] == assignee_id


def test_route_noncompleted_event_does_not_notify(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, assignee_headers = _seed_member(ctx, project["id"])
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id)
    _install_fake_ledger_views(monkeypatch)

    notify_calls: list = []
    monkeypatch.setattr(
        projects_routes, "notify_requester_task_completed",
        lambda *a, **kw: notify_calls.append((a, kw)),
    )

    r = ctx.client.post(
        _events_url(project["id"], deleg["id"]), json={"event": "in_progress"}, headers=assignee_headers,
    )
    assert r.status_code == 200
    assert notify_calls == []


def test_route_second_completed_emit_409s_no_notify(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, assignee_headers = _seed_member(ctx, project["id"])
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id)
    delegation_events_db.record_event(
        delegation_id=deleg["id"], event="completed", actor_user_id=assignee_id,
    )
    _install_fake_ledger_views(monkeypatch)

    notify_calls: list = []
    monkeypatch.setattr(
        projects_routes, "notify_requester_task_completed",
        lambda *a, **kw: notify_calls.append((a, kw)),
    )

    r = ctx.client.post(
        _events_url(project["id"], deleg["id"]), json={"event": "completed"}, headers=assignee_headers,
    )
    assert r.status_code == 409
    assert notify_calls == []
    assert len(delegation_events_db.list_events(deleg["id"])) == 1  # only the seeded "completed"
