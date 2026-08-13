"""Fast-lane tests for the ledger's LIVE dual per-user publish —
`_publish_delegation_event` and its wiring into `emit_delegation_event_route`.

Spies `publish_broadcast` (patched on `project_delegation`, the module the
route calls through) so no real Realtime traffic is made, and stands in a
data-driven `current_status`/`status_dto` for `v_delegation_status` (a real
Postgres view `FakeSupabaseClient` cannot evaluate — same rationale and
shape as `test_delegation_events_api.py::_install_fake_ledger_views`).

The load-bearing gate: a decline/cancel is PRIVATE (AD-P30) — the two
publish topics are the assigner's and assignee's per-user channels ONLY,
NEVER the group channel `project:{id}`. A publish failure never fails an
already-recorded event (AD-P22 best-effort).
"""
from __future__ import annotations

from app import project_delegation
from app.db import delegation_events as delegation_events_db
from app.db import projects as projects_db
from app.db.project_delegations import record_delegation
from tests._company_helpers import company_client
from tests._project_helpers import seed_same_tenant_non_member


# ── Fixtures / helpers ──────────────────────────────────────────────────


def _create_project(ctx, *, name: str = "Ledger liveness project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


def _seed_member(ctx, *project_ids: int) -> tuple[str, dict]:
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
    """The `status_dto` client shape derived from the latest event — mirrors
    the four columns `v_delegation_status` exposes to this DTO."""
    events = delegation_events_db.list_events(deleg_row["id"])
    latest = events[-1] if events else None
    return {
        "delegation_id": deleg_row["id"],
        "status": latest["event"] if latest else "assigned",
        "status_at": latest["created_at"] if latest else deleg_row["created_at"],
        "task_summary": deleg_row["task_summary"],
    }


def _install_fake_status(monkeypatch) -> None:
    """Stand-ins for `current_status`/`status_dto` — the real ones read a
    Postgres view `FakeSupabaseClient` cannot evaluate (see module docstring)."""
    from app.db.client import require_client

    def _all_delegations() -> list[dict]:
        return require_client().table("project_delegations").select("*").execute().data or []

    def _current_status(delegation_id):
        rows = [d for d in _all_delegations() if d["id"] == delegation_id]
        return _fake_status_row(rows[0])["status"] if rows else None

    def _status_dto(delegation_id):
        rows = [d for d in _all_delegations() if d["id"] == delegation_id]
        return _fake_status_row(rows[0]) if rows else None

    monkeypatch.setattr(delegation_events_db, "current_status", _current_status)
    monkeypatch.setattr(delegation_events_db, "status_dto", _status_dto)


def _spy_publish(monkeypatch, *, raises: bool = False) -> list[dict]:
    """Record every `publish_broadcast(topic, event, payload)` the route makes
    through `_publish_delegation_event`; optionally force it to raise to prove
    the route's best-effort guard (AC-3)."""
    calls: list[dict] = []

    def _fake(topic, event, payload):
        calls.append({"topic": topic, "event": event, "payload": payload})
        if raises:
            raise RuntimeError("realtime down")

    monkeypatch.setattr(project_delegation, "publish_broadcast", _fake)
    return calls


# ── AC-1: dual per-user publish, never the group channel ─────────────────


def test_emit_dual_publishes_to_both_parties(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, assignee_headers = _seed_member(ctx, project["id"])
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id)
    _install_fake_status(monkeypatch)
    calls = _spy_publish(monkeypatch)

    # The assignee accepts — an assignee-owned, legal edge from `assigned`.
    r = ctx.client.post(
        _events_url(project["id"], deleg["id"]), json={"event": "accepted"}, headers=assignee_headers
    )
    assert r.status_code == 200

    # Exactly one publish per party, both `delegation.event`.
    assert len(calls) == 2
    assert {c["topic"] for c in calls} == {
        f"project:{project['id']}:user:{ctx.user_id}",   # assigner
        f"project:{project['id']}:user:{assignee_id}",   # assignee
    }
    assert all(c["event"] == "delegation.event" for c in calls)


def test_emit_never_publishes_to_group_channel(isolated_settings, monkeypatch):
    """Privacy gate (AD-P30): a decline is the private-est event — its status
    must never leak to the group channel `project:{id}`."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, assignee_headers = _seed_member(ctx, project["id"])
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id)
    _install_fake_status(monkeypatch)
    calls = _spy_publish(monkeypatch)

    r = ctx.client.post(
        _events_url(project["id"], deleg["id"]), json={"event": "declined"}, headers=assignee_headers
    )
    assert r.status_code == 200

    group_topic = f"project:{project['id']}"
    assert all(c["topic"] != group_topic for c in calls)
    assert len(calls) == 2  # both publishes landed on per-user channels


# ── AC-2: shaped read-DTO, never a raw row ───────────────────────────────


def test_publish_payload_is_shaped_dto(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, assignee_headers = _seed_member(ctx, project["id"])
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id, task_summary="Draft the pricing page")
    _install_fake_status(monkeypatch)
    calls = _spy_publish(monkeypatch)

    r = ctx.client.post(
        _events_url(project["id"], deleg["id"]), json={"event": "accepted"}, headers=assignee_headers
    )
    assert r.status_code == 200

    assert calls, "expected a publish"
    for c in calls:
        assert set(c["payload"].keys()) == {"delegation_id", "status", "status_at", "task_summary"}
        # No internal-only / raw-row column ever leaks.
        for leaked in ("assigner_user_id", "assignee_user_id", "project_id",
                       "delivered_conversation_id", "delivered_turn_id"):
            assert leaked not in c["payload"], leaked
        assert c["payload"]["delegation_id"] == deleg["id"]
        assert c["payload"]["status"] == "accepted"
        assert c["payload"]["task_summary"] == "Draft the pricing page"


# ── AC-3: best-effort — a publish failure never fails the emit ────────────


def test_publish_failure_does_not_fail_emit(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, assignee_headers = _seed_member(ctx, project["id"])
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id)
    _install_fake_status(monkeypatch)
    _spy_publish(monkeypatch, raises=True)  # forced-raise publish

    r = ctx.client.post(
        _events_url(project["id"], deleg["id"]), json={"event": "accepted"}, headers=assignee_headers
    )
    # Identical response body whether the publish succeeds or fails.
    assert r.status_code == 200
    assert r.json() == {"delegation_id": deleg["id"], "status": "accepted"}
    # The event is still durably recorded.
    events = delegation_events_db.list_events(deleg["id"])
    assert len(events) == 1
    assert events[0]["event"] == "accepted"
