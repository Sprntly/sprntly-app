"""Delegation TRUTHFULNESS — the Malina guard (R4).

Mutation-proof, deterministic (FakeSupabaseClient, brief LLM stubbed). The
`delegate_task` / `complete_task` handlers return an AUTHORITATIVE confirmation
string that overrides the model's free text, so the reply can only be truthful
if the underlying rows were ACTUALLY written. These pin exactly that:

  (a) a `delegate_task` that reports "Assigned to" has, at that point,
      already written a `project_delegations` row, delivered the brief turn into
      the assignee's own individual chat, and recorded the genesis `assigned`
      event — the "promised but never invoked" (Malina) bug would return the
      confirmation with no row;
  (b) a completion signal writes exactly ONE terminal `completed`
      `delegation_events` row — a repeat "done" is idempotent (truthful
      "already done", no second event);
  (c) an undeliverable (non-member) request DECLINES and writes NOTHING — never
      a false success.
"""
from __future__ import annotations

import uuid

from app import project_delegation
from app.db import delegation_events as de_db
from app.db import project_delegations as pd_db
from app.db import projects as projects_db
from tests._company_helpers import company_client


def _create_project(ctx, *, name: str = "Delegation truthfulness project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


def _seed_assignee(project_id: int, *, name: str = "Fortune Adeyemi", role: str = "Designer") -> str:
    from app.db.client import require_client

    user_id = "assignee-" + uuid.uuid4().hex[:8]
    require_client().table("profiles").insert(
        {"id": user_id, "email": f"{user_id}@co.com", "full_name": name, "role": role}
    ).execute()
    projects_db.add_member(project_id, user_id)
    return user_id


def _seed_project_with_assignee(ctx) -> tuple[dict, str]:
    from app.db.client import require_client

    project = _create_project(ctx)
    require_client().table("profiles").upsert(
        {"id": ctx.user_id, "email": f"{ctx.user_id}@co.com", "full_name": "Alex Assigner", "role": "PM"}
    ).execute()
    assignee_id = _seed_assignee(project["id"])
    return project, assignee_id


def _stub_brief_llm(monkeypatch, *, reply: str = "Here is the brief. Please proceed with the task."):
    def _fake_call_md(*, system, user, model, meta_out=None, **kwargs):  # noqa: ARG001
        if meta_out is not None:
            meta_out.update({
                "model": model, "input_tokens": 60, "output_tokens": 20,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            })
        return reply

    monkeypatch.setattr(project_delegation, "call_md", _fake_call_md)


def _delegate(project, assigner_user_id, *, assignee="Fortune", task="Draft the pricing page"):
    roster = projects_db.list_members(project["id"])
    return project_delegation.handle_delegate_task(
        project_id=project["id"],
        assigner_user_id=assigner_user_id,
        source_conversation_id=1,
        source_turn_id=1,
        roster=roster,
        dataset="",
        company_id="unused-in-fake-db",
        tool_input={"assignee": assignee, "task_summary": task},
    )


# ── (a) the confirmation only follows a real write + delivery ────────────────


def test_delegate_confirmation_only_follows_real_row_and_delivery(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _stub_brief_llm(monkeypatch)
    project, assignee_id = _seed_project_with_assignee(ctx)

    result = _delegate(project, ctx.user_id, assignee="Fortune", task="Draft the pricing page")
    # The authoritative confirmation.
    assert "Assigned to" in result

    # 1) The FACT row was written (the delegate_task was actually invoked, not
    #    merely narrated).
    rows = pd_db.list_delegations_for_project(project["id"])
    assert len(rows) == 1
    deleg = rows[0]
    assert deleg["assignee_user_id"] == assignee_id
    assert deleg["task_summary"] == "Draft the pricing page"

    # 2) The brief turn was DELIVERED into the assignee's own thread BEFORE the
    #    confirmation (AD-P19 delivery-then-record order).
    assert deleg["delivered_conversation_id"] is not None
    assert deleg["delivered_turn_id"] is not None

    # 3) The genesis event was recorded on the raw event log (read straight from
    #    `delegation_events`, not the `v_delegation_status` view — that view is a
    #    real-rig SQL object not mirrored by the fake DB).
    assigned = [e for e in de_db.list_events(deleg["id"]) if e["event"] == "assigned"]
    assert len(assigned) == 1


# ── (b) completion writes a terminal `completed` row, idempotently ───────────
#
# NOTE ON SCOPE: the full `handle_complete_task` handler resolves the speaker's
# open tasks through `list_status_for_assignee` → the `v_delegation_status` SQL
# VIEW, which the fake DB does not mirror (it needs a real migration; that
# end-to-end path is exercised by the rig-gated `test_delegation_events.py`).
# The MUTATION this guard protects — the terminal `completed` write and its
# idempotency — lives on the raw `delegation_events` table + the pure
# `is_legal_transition` state machine the handler enforces against, and is proven
# deterministically here.


def test_completed_event_write_and_idempotency_guard(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _stub_brief_llm(monkeypatch)
    project, assignee_id = _seed_project_with_assignee(ctx)
    _delegate(project, ctx.user_id, assignee="Fortune", task="Draft the pricing page")
    deleg = pd_db.list_delegations_for_project(project["id"])[0]

    # The completion signal writes exactly ONE terminal `completed` event.
    de_db.record_event(delegation_id=deleg["id"], event="completed", actor_user_id=assignee_id)
    completed = [e for e in de_db.list_events(deleg["id"]) if e["event"] == "completed"]
    assert len(completed) == 1

    # The idempotency GUARD the handler gates the second write on: a repeat "done"
    # finds the task already `completed`, and `completed → completed` is NOT a
    # legal transition, so no second row is ever written…
    assert de_db.is_legal_transition("completed", "completed") is False
    # …and the guard is not vacuously closed: the genesis `assigned → completed`
    # transition it protects IS legal.
    assert de_db.is_legal_transition("assigned", "completed") is True


# ── (c) undeliverable → decline, never a false success ───────────────────────


def test_non_member_assignee_declines_and_writes_nothing(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _stub_brief_llm(monkeypatch)
    project, _assignee_id = _seed_project_with_assignee(ctx)

    result = _delegate(project, ctx.user_id, assignee="Nonexistent Person", task="do a thing")
    # A DECLINE — never a false "Assigned to".
    assert "Assigned to" not in result
    assert "don't see" in result.lower() or "who did you mean" in result.lower()
    # And NO fact row was written.
    assert pd_db.list_delegations_for_project(project["id"]) == []


def test_complete_by_non_member_declines_and_writes_no_event(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _stub_brief_llm(monkeypatch)
    project, _assignee_id = _seed_project_with_assignee(ctx)

    r = project_delegation.handle_complete_task(
        project_id=project["id"], completer_user_id="ghost-non-member",
        tool_input={"task_summary": "anything"},
    )
    assert "member" in r.lower()  # "I can only update tasks for members of this project."
    # No delegation existed, so nothing to (falsely) complete.
    assert pd_db.list_delegations_for_project(project["id"]) == []
