"""Fast-lane fake-DB tests for the delegation-ledger endpoint: the pure
state-machine engine, the FOUR fail-closed authz gates (mutation-proofed —
the load-bearing tests), and the party-filtered reads/counts.

This is the ledger's IDOR-critical surface: a miss at gate 2 (delegation-
in-project) or gate 3 (party-role) lets one project member mutate another
member's accountability state (complete a task assigned to someone else,
cancel a task they did not assign).

`v_delegation_status` (and therefore `current_status`/
`list_status_for_assignee`/`list_status_for_assigner`) is a real Postgres
`left join lateral` view — `FakeSupabaseClient` is an in-memory store with
no SQL engine behind it and cannot evaluate one (see
`db/delegation_events.py`'s own docstring, and
`test_delegation_events.py`'s identical caveat for the same reason).
`_install_fake_ledger_views` below stands in a data-driven equivalent —
"latest `delegation_events` row wins, else `assigned`" — that reacts to
real `record_event` inserts made through the fast-lane fixtures, so a
route call that appends an event and then re-derives status round-trips
correctly within one test. `load_delegation_for_authz` (gate 2) needs no
such patch: it reads `project_delegations` directly, a table the fake DB
does support.

The real end-to-end proof that these reads correctly evaluate the ACTUAL
Postgres view lives in `test_project_ledger_live.py` (env-gated, real
local Supabase) and `test_delegation_events.py` (the migration's own
view-derivation proof).
"""
from __future__ import annotations

import logging
import uuid

import pytest

from app.db import delegation_events as delegation_events_db
from app.db import projects as projects_db
from app.db.project_delegations import record_delegation
from tests._company_helpers import company_client
from tests._project_helpers import seed_same_tenant_non_member


# ── Fixtures / helpers ──────────────────────────────────────────────────


def _create_project(ctx, *, name: str = "Ledger project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


def _seed_foreign_project(*, name: str = "Foreign project") -> dict:
    """A project scoped to a company/workspace that never resolves through
    `require_workspace` for the caller under test — mirrors
    `test_projects_routes.py`'s own `_seed_foreign_project` (the tenant-
    gate probe)."""
    return projects_db.create_project(
        company_id="foreign-co", workspace_id="foreign-ws", name=name, created_by="someone-else",
    )


def _seed_member(ctx, *project_ids: int) -> tuple[str, dict]:
    """A same-tenant user with company/workspace access
    (`seed_same_tenant_non_member`), added to every `project_ids` given.
    Returns `(user_id, headers)`."""
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
    """Stand-ins for `current_status`/`list_status_for_assignee`/
    `list_status_for_assigner` — see module docstring for why the real
    ones (a Postgres view) don't work against `FakeSupabaseClient`."""
    from app.db.client import require_client

    def _all_delegations() -> list[dict]:
        return require_client().table("project_delegations").select("*").execute().data or []

    def _current_status(delegation_id):
        rows = [d for d in _all_delegations() if d["id"] == delegation_id]
        return _fake_status_row(rows[0])["status"] if rows else None

    def _list_for_assignee(project_id, user_id):
        return [
            _fake_status_row(d)
            for d in _all_delegations()
            if d["project_id"] == project_id and d["assignee_user_id"] == user_id
        ]

    def _list_for_assigner(project_id, user_id):
        return [
            _fake_status_row(d)
            for d in _all_delegations()
            if d["project_id"] == project_id and d["assigner_user_id"] == user_id
        ]

    monkeypatch.setattr(delegation_events_db, "current_status", _current_status)
    monkeypatch.setattr(delegation_events_db, "list_status_for_assignee", _list_for_assignee)
    monkeypatch.setattr(delegation_events_db, "list_status_for_assigner", _list_for_assigner)


# ── State machine (pure — AC1, AC4, AC5, AC9) ────────────────────────────


def test_legal_edges_accepted():
    for current, events in delegation_events_db.TRANSITIONS.items():
        for event in events:
            assert delegation_events_db.is_legal_transition(current, event), (current, event)


@pytest.mark.parametrize(
    "current,event",
    [
        ("completed", "accepted"),
        ("cancelled", "completed"),
        ("assigned", "reopened"),
        ("assigned", "completed"),
        ("completed", "completed"),
    ],
)
def test_illegal_edges_rejected(current, event):
    assert not delegation_events_db.is_legal_transition(current, event)


def test_event_party_map_excludes_server_event():
    assert "assigned" not in delegation_events_db.EVENT_PARTY
    assignee_events = {e for e, p in delegation_events_db.EVENT_PARTY.items() if p == "assignee"}
    assigner_events = {e for e, p in delegation_events_db.EVENT_PARTY.items() if p == "assigner"}
    assert assignee_events == {"accepted", "in_progress", "completed", "declined"}
    assert assigner_events == {"cancelled", "reopened"}
    assert delegation_events_db.EVENT_PARTY["reopened"] == "assigner"


def test_open_closed_state_partition():
    assert delegation_events_db.OPEN_STATES == {"assigned", "accepted", "in_progress", "reopened"}
    assert delegation_events_db.CLOSED_STATES == {"completed", "declined", "cancelled"}
    assert delegation_events_db.OPEN_STATES.isdisjoint(delegation_events_db.CLOSED_STATES)
    assert delegation_events_db.OPEN_STATES | delegation_events_db.CLOSED_STATES == {
        "assigned", "accepted", "in_progress", "reopened", "completed", "declined", "cancelled",
    }


# ── Four-gate authz (fake-DB, mutation-proofed — the load-bearing tests) ──


def test_non_member_403_no_write(isolated_settings, monkeypatch):
    """The assignee has same-tenant company/workspace access but is NOT a
    `project_members` row — they would pass gates 2-4 (correct party,
    legal edge) but gate 1 blocks them independently. Flipping gate 1
    (`is_project_member`) to allow-all then lets the write through,
    proving gate 1 (not something else) was the blocker (AC2, AC6)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, assignee_headers = seed_same_tenant_non_member(ctx)
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id)
    _install_fake_ledger_views(monkeypatch)

    r = ctx.client.post(_events_url(project["id"], deleg["id"]), json={"event": "accepted"}, headers=assignee_headers)
    assert r.status_code == 403
    assert delegation_events_db.list_events(deleg["id"]) == []

    monkeypatch.setattr(projects_db, "is_project_member", lambda *a, **kw: True)
    r2 = ctx.client.post(_events_url(project["id"], deleg["id"]), json={"event": "accepted"}, headers=assignee_headers)
    assert r2.status_code == 200
    assert len(delegation_events_db.list_events(deleg["id"])) == 1


def test_cross_tenant_404(isolated_settings, monkeypatch):
    """A caller whose company does not own the project at all — gate 1's
    other half (`_require_project`) 404s before gate 2 is ever reached,
    so a foreign delegation id's existence is never disclosed (AC2)."""
    ctx = company_client(monkeypatch)
    foreign = _seed_foreign_project()

    r = ctx.client.post(_events_url(foreign["id"], 999999), json={"event": "accepted"})
    assert r.status_code == 404


def test_delegation_not_in_project_404_no_write(isolated_settings, monkeypatch):
    """The delegation is real and the caller IS a member of the project
    named in the URL — but the delegation itself belongs to a DIFFERENT
    project. Opaque 404, no write. Flipping gate 2's `project_id` compare
    to always-equal (via a forced `load_delegation_for_authz`) then lets
    the write through (AC3, AC6)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    other_project = _create_project(ctx, name="Other project")
    assignee_id, assignee_headers = _seed_member(ctx, project["id"], other_project["id"])
    deleg = _seed_delegation(other_project["id"], ctx.user_id, assignee_id)
    _install_fake_ledger_views(monkeypatch)

    r = ctx.client.post(_events_url(project["id"], deleg["id"]), json={"event": "accepted"}, headers=assignee_headers)
    assert r.status_code == 404
    assert delegation_events_db.list_events(deleg["id"]) == []

    orig_load = delegation_events_db.load_delegation_for_authz

    def _forced_equal_project(delegation_id):
        row = orig_load(delegation_id)
        return {**row, "project_id": project["id"]} if row is not None else None

    monkeypatch.setattr(delegation_events_db, "load_delegation_for_authz", _forced_equal_project)
    r2 = ctx.client.post(_events_url(project["id"], deleg["id"]), json={"event": "accepted"}, headers=assignee_headers)
    assert r2.status_code == 200
    assert len(delegation_events_db.list_events(deleg["id"])) == 1


def test_wrong_party_assigner_emits_assignee_event_403(isolated_settings, monkeypatch):
    """The assigner (ctx, the caller who created the delegation) tries to
    emit an assignee-only event. Flipping gate 3's party compare to
    always-true (forcing `load_delegation_for_authz` to report the caller
    as both parties) then lets the write through (AC4, AC6)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, _ = _seed_member(ctx, project["id"])
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id)
    # Prime the delegation into "accepted" so "completed" is a LEGAL edge —
    # this test isolates gate 3 (party), not gate 4 (legality).
    delegation_events_db.record_event(delegation_id=deleg["id"], event="accepted", actor_user_id=assignee_id)
    _install_fake_ledger_views(monkeypatch)

    r = ctx.client.post(_events_url(project["id"], deleg["id"]), json={"event": "completed"})
    assert r.status_code == 403
    assert len(delegation_events_db.list_events(deleg["id"])) == 1  # only the seeded "accepted"

    orig_load = delegation_events_db.load_delegation_for_authz

    def _forced_always_party(delegation_id):
        row = orig_load(delegation_id)
        if row is None:
            return None
        return {**row, "assigner_user_id": ctx.user_id, "assignee_user_id": ctx.user_id}

    monkeypatch.setattr(delegation_events_db, "load_delegation_for_authz", _forced_always_party)
    r2 = ctx.client.post(_events_url(project["id"], deleg["id"]), json={"event": "completed"})
    assert r2.status_code == 200
    assert len(delegation_events_db.list_events(deleg["id"])) == 2


def test_wrong_party_assignee_emits_assigner_event_403(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, assignee_headers = _seed_member(ctx, project["id"])
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id)
    _install_fake_ledger_views(monkeypatch)

    r = ctx.client.post(_events_url(project["id"], deleg["id"]), json={"event": "cancelled"}, headers=assignee_headers)
    assert r.status_code == 403
    assert delegation_events_db.list_events(deleg["id"]) == []


def test_neither_party_member_403(isolated_settings, monkeypatch):
    """A project member who witnessed the delegation (e.g. in group chat)
    but is neither the assigner nor the assignee — 403 on any event."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, _ = _seed_member(ctx, project["id"])
    _, witness_headers = _seed_member(ctx, project["id"])
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id)
    _install_fake_ledger_views(monkeypatch)

    for event in ("accepted", "cancelled"):
        r = ctx.client.post(
            _events_url(project["id"], deleg["id"]), json={"event": event}, headers=witness_headers
        )
        assert r.status_code == 403, event
    assert delegation_events_db.list_events(deleg["id"]) == []


def test_server_event_and_unknown_422(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, _ = _seed_member(ctx, project["id"])
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id)
    _install_fake_ledger_views(monkeypatch)

    for event in ("assigned", "bogus"):
        r = ctx.client.post(_events_url(project["id"], deleg["id"]), json={"event": event})
        assert r.status_code == 422, event
    assert delegation_events_db.list_events(deleg["id"]) == []


def test_illegal_transition_409_no_write(isolated_settings, monkeypatch):
    """The correct party (the assignee), but the delegation is already
    `completed` — `completed -> accepted` is not a legal edge. Flipping
    gate 4 (`is_legal_transition`) to always-legal then lets the write
    through (AC5, AC6)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, assignee_headers = _seed_member(ctx, project["id"])
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id)
    delegation_events_db.record_event(delegation_id=deleg["id"], event="completed", actor_user_id=assignee_id)
    _install_fake_ledger_views(monkeypatch)

    r = ctx.client.post(_events_url(project["id"], deleg["id"]), json={"event": "accepted"}, headers=assignee_headers)
    assert r.status_code == 409
    assert len(delegation_events_db.list_events(deleg["id"])) == 1  # only the seeded "completed"

    monkeypatch.setattr(delegation_events_db, "is_legal_transition", lambda *a, **kw: True)
    r2 = ctx.client.post(_events_url(project["id"], deleg["id"]), json={"event": "accepted"}, headers=assignee_headers)
    assert r2.status_code == 200
    assert len(delegation_events_db.list_events(deleg["id"])) == 2


def test_correct_party_legal_edge_writes(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, assignee_headers = _seed_member(ctx, project["id"])
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id)
    _install_fake_ledger_views(monkeypatch)

    r = ctx.client.post(_events_url(project["id"], deleg["id"]), json={"event": "accepted"}, headers=assignee_headers)
    assert r.status_code == 200
    assert r.json() == {"delegation_id": deleg["id"], "status": "accepted"}
    events = delegation_events_db.list_events(deleg["id"])
    assert len(events) == 1
    assert events[0]["event"] == "accepted"
    assert events[0]["actor_user_id"] == assignee_id

    # assigned -> accepted -> completed is legal end to end (AC1).
    r2 = ctx.client.post(_events_url(project["id"], deleg["id"]), json={"event": "completed"}, headers=assignee_headers)
    assert r2.status_code == 200
    assert r2.json() == {"delegation_id": deleg["id"], "status": "completed"}


# ── Read isolation (fake-DB, AC7/AC8/AC9) ─────────────────────────────────


def test_assigned_to_me_returns_only_my_assignee_rows(isolated_settings, monkeypatch):
    _install_fake_ledger_views(monkeypatch)
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, assignee_headers = _seed_member(ctx, project["id"])
    other_id, _ = _seed_member(ctx, project["id"])
    mine = _seed_delegation(project["id"], ctx.user_id, assignee_id, task_summary="mine")
    _seed_delegation(project["id"], ctx.user_id, other_id, task_summary="not mine")

    r = ctx.client.get(
        f"/v1/projects/{project['id']}/delegations", params={"view": "assigned_to_me"}, headers=assignee_headers
    )
    assert r.status_code == 200
    rows = r.json()
    assert [row["delegation_id"] for row in rows] == [mine["id"]]
    assert rows[0]["other_party_user_id"] == ctx.user_id


def test_waiting_on_returns_only_my_assigner_rows(isolated_settings, monkeypatch):
    _install_fake_ledger_views(monkeypatch)
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, _ = _seed_member(ctx, project["id"])
    other_assigner_id, _ = _seed_member(ctx, project["id"])
    mine = _seed_delegation(project["id"], ctx.user_id, assignee_id, task_summary="mine")
    _seed_delegation(project["id"], other_assigner_id, assignee_id, task_summary="not mine")

    r = ctx.client.get(f"/v1/projects/{project['id']}/delegations", params={"view": "waiting_on"})
    assert r.status_code == 200
    rows = r.json()
    assert [row["delegation_id"] for row in rows] == [mine["id"]]
    assert rows[0]["other_party_user_id"] == assignee_id


def test_neither_party_sees_empty_ledger(isolated_settings, monkeypatch):
    _install_fake_ledger_views(monkeypatch)
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, _ = _seed_member(ctx, project["id"])
    _, witness_headers = _seed_member(ctx, project["id"])
    _seed_delegation(project["id"], ctx.user_id, assignee_id)

    r1 = ctx.client.get(
        f"/v1/projects/{project['id']}/delegations", params={"view": "assigned_to_me"}, headers=witness_headers
    )
    r2 = ctx.client.get(
        f"/v1/projects/{project['id']}/delegations", params={"view": "waiting_on"}, headers=witness_headers
    )
    assert r1.json() == []
    assert r2.json() == []


def test_unknown_view_422(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    r = ctx.client.get(f"/v1/projects/{project['id']}/delegations", params={"view": "whatever"})
    assert r.status_code == 422


def test_ledger_row_dto_shape_and_bucket(isolated_settings, monkeypatch):
    _install_fake_ledger_views(monkeypatch)
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, assignee_headers = _seed_member(ctx, project["id"])

    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": ctx.user_id, "email": f"{ctx.user_id}@co.com", "full_name": "Alexis Assigner", "role": "PM"}
    ).execute()

    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id, task_summary="Draft the pricing page")
    delegation_events_db.record_event(delegation_id=deleg["id"], event="accepted", actor_user_id=assignee_id)
    delegation_events_db.record_event(delegation_id=deleg["id"], event="completed", actor_user_id=assignee_id)

    r = ctx.client.get(
        f"/v1/projects/{project['id']}/delegations", params={"view": "assigned_to_me"}, headers=assignee_headers
    )
    row = r.json()[0]
    assert set(row.keys()) == {
        "delegation_id", "task_summary", "status", "status_at", "bucket",
        "other_party_user_id", "other_party_name", "delivered_conversation_id", "delivered_turn_id",
    }
    assert row["status"] == "completed"
    assert row["bucket"] == "done"
    assert row["other_party_user_id"] == ctx.user_id
    assert row["other_party_name"] == "Alexis Assigner"


def test_counts_open_only(isolated_settings, monkeypatch):
    _install_fake_ledger_views(monkeypatch)
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, assignee_headers = _seed_member(ctx, project["id"])

    _seed_delegation(project["id"], ctx.user_id, assignee_id, task_summary="open")
    reopened = _seed_delegation(project["id"], ctx.user_id, assignee_id, task_summary="reopened")
    delegation_events_db.record_event(delegation_id=reopened["id"], event="completed", actor_user_id=assignee_id)
    delegation_events_db.record_event(delegation_id=reopened["id"], event="reopened", actor_user_id=ctx.user_id)
    closed = _seed_delegation(project["id"], ctx.user_id, assignee_id, task_summary="closed")
    delegation_events_db.record_event(delegation_id=closed["id"], event="declined", actor_user_id=assignee_id)

    r = ctx.client.get(f"/v1/projects/{project['id']}/delegations/counts", headers=assignee_headers)
    assert r.json() == {"assigned_to_me_open": 2, "waiting_on_open": 0}

    r2 = ctx.client.get(f"/v1/projects/{project['id']}/delegations/counts")
    assert r2.json() == {"assigned_to_me_open": 0, "waiting_on_open": 2}


# ── Cost / observability (AC11) ────────────────────────────────────────────


def test_no_llm_cost_line_emitted(isolated_settings, monkeypatch, caplog):
    _install_fake_ledger_views(monkeypatch)
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, assignee_headers = _seed_member(ctx, project["id"])
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id)

    with caplog.at_level(logging.INFO):
        r = ctx.client.post(
            _events_url(project["id"], deleg["id"]), json={"event": "accepted"}, headers=assignee_headers
        )
    assert r.status_code == 200
    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "est_cost_usd=" not in joined


def test_no_note_text_in_logs(isolated_settings, monkeypatch, caplog):
    _install_fake_ledger_views(monkeypatch)
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id, assignee_headers = _seed_member(ctx, project["id"])
    deleg = _seed_delegation(project["id"], ctx.user_id, assignee_id)

    with caplog.at_level(logging.INFO):
        r = ctx.client.post(
            _events_url(project["id"], deleg["id"]),
            json={"event": "accepted", "note": "SECRET_NOTE_DO_NOT_LOG"},
            headers=assignee_headers,
        )
    assert r.status_code == 200
    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "SECRET_NOTE_DO_NOT_LOG" not in joined


# ── CI-lane registry backstop ──────────────────────────────────────────────


def test_ci_lane_registry_has_project_ledger_live():
    from tests.test_ci_lane_coverage import _KNOWN_UNRUNNABLE

    assert ("test_project_ledger_live.py", "RUN_PROJECT_LEDGER_LIVE") in _KNOWN_UNRUNNABLE
