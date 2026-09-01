"""Tests for `app/delegation_followup.py::run_task_followup_cycle` — the
autonomous task follow-up sweep — plus the `TASK_FOLLOWUP` scheduler
registration and the non-breakage grep/compile checks.

Drives the sweep directly against `FakeSupabaseClient` (`isolated_settings`)
with `delegation_followup.call_json` and `.send_followup_email` stubbed —
fast and deterministic, proving the sweep's CONTRACT (pre-filter, step-0
soft-done short-circuit, cap/quiet-hours guards, decision application,
idempotency, email-after-two-unanswered-DMs, escalation targeting,
per-task isolation, cost logging) rather than a real LLM decision.
`list_due_followups` itself (a real `v_delegation_status` view read,
`FakeSupabaseClient` cannot evaluate) is monkeypatched to a canned due-row
list per test — its real behaviour is proven against a real local
Supabase in `test_delegation_followup_sends.py`.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import delegation_followup as followup_mod
from app import project_delegation
from app.db import conversations as conversations_db
from app.db import delegation_events as delegation_events_db
from app.db import delegation_followup_sends as sends_db
from app.db import delegation_followups as delegation_followups_db
from app.db.project_delegations import record_delegation
from tests._company_helpers import company_client

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── Frozen clock (see test_invite_reminders.py's identical rationale:
# calendar-day-dependent quiet-hours/weekend math must not depend on which
# day the suite happens to run). Wednesday, safely outside quiet hours. ──

_FROZEN_NOW = datetime(2026, 8, 12, 12, 0, 0, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    _pinned: datetime = _FROZEN_NOW

    @classmethod
    def now(cls, tz=None):  # noqa: D102 - mirrors datetime.now
        return cls._pinned if tz is not None else cls._pinned.replace(tzinfo=None)


def _freeze(monkeypatch, pinned: datetime = _FROZEN_NOW) -> None:
    frozen = type("_Frozen", (_FrozenDatetime,), {"_pinned": pinned})
    monkeypatch.setattr(followup_mod, "datetime", frozen)
    # Every `record_send` issued during the test (by the sweep or by test
    # setup) lands at this same frozen instant — otherwise a real-wallclock
    # `sent_at` would always lexicographically compare as "within the last
    # 24h/7d" of a frozen-past `now`, making the per-person cap guard fire
    # unpredictably in tests that aren't about the cap guard at all.
    monkeypatch.setattr(sends_db, "utc_now", lambda: pinned.isoformat())


def _bypass_cap(monkeypatch) -> None:
    """Neutralize the per-person cap guard for tests whose subject is a
    LATER step (idempotency, decision application, email, escalation) —
    `test_cycle_skips_capped_person_no_llm` is the one test that exercises
    the cap guard itself."""
    monkeypatch.setattr(followup_mod, "is_capped", lambda **kwargs: False)


# ── Fixtures / helpers ────────────────────────────────────────────────────


def _create_project(ctx, *, name: str = "Followup project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


def _seed_assignee(project_id: int, *, name: str = "Fortune Adeyemi") -> str:
    from app.db.client import require_client
    from app.db.projects import add_member

    user_id = "assignee-" + uuid.uuid4().hex[:8]
    require_client().table("profiles").insert(
        {"id": user_id, "email": f"{user_id}@co.com", "full_name": name, "role": "Designer"}
    ).execute()
    add_member(project_id, user_id)
    return user_id


def _seed_delegation(ctx, project_id: int, assignee_id: str, *, task_summary: str) -> tuple[int, int]:
    conv = conversations_db.create_individual_project_chat(project_id, assignee_id)
    turn = conversations_db.post_individual_turn(conv["id"], "assistant", "brief")
    deleg = record_delegation(
        project_id=project_id,
        assigner_user_id=ctx.user_id,
        assignee_user_id=assignee_id,
        task_summary=task_summary,
        source_conversation_id=None,
        source_turn_id=None,
        delivered_conversation_id=conv["id"],
        delivered_turn_id=turn["id"],
    )
    return deleg["id"], conv["id"]


def _due_row(
    *,
    deleg_id: int,
    project_id: int,
    conv_id: int,
    assigner_id: str,
    assignee_id: str,
    task_summary: str,
    next_check_in: datetime | None = None,
    last_checked_in: datetime | None = None,
    expected_completion: datetime | None = None,
    pending_done_since: datetime | None = None,
    status: str = "assigned",
) -> dict:
    ncki = next_check_in or (_FROZEN_NOW - timedelta(hours=1))
    return {
        "delegation_id": deleg_id,
        "project_id": project_id,
        "assigner_user_id": assigner_id,
        "assignee_user_id": assignee_id,
        "task_summary": task_summary,
        "delivered_conversation_id": conv_id,
        "next_check_in": ncki.isoformat(),
        "last_checked_in": last_checked_in.isoformat() if last_checked_in else None,
        "expected_completion": expected_completion.isoformat() if expected_completion else None,
        "pending_done_since": pending_done_since.isoformat() if pending_done_since else None,
        "status": status,
    }


def _install_due(monkeypatch, rows: list[dict]) -> None:
    monkeypatch.setattr(
        followup_mod.delegation_followups_db, "list_due_followups", lambda now: rows
    )


def _stub_decision_llm(monkeypatch, **overrides):
    """Stub the ONE decision LLM call site (`delegation_followup.call_json`).
    `state["calls"]` is the no-LLM-call assertion point. `raise_for` (a
    substring) makes the stub raise only when that substring appears in
    the rendered user prompt — used to isolate one task's failure from
    another's in the per-task error-isolation test."""
    state: dict = {
        "calls": [],
        "decision": "reschedule",
        "next_check_in": None,
        "dm_text": None,
        "raise_error": False,
        "raise_for": None,
    }
    state.update(overrides)

    def _fake_call_json(*, system, user, model, schema=None, meta_out=None, **kwargs):  # noqa: ARG001
        state["calls"].append({"system": system, "user": user})
        if state["raise_error"] or (state["raise_for"] and state["raise_for"] in user):
            raise RuntimeError("simulated decision failure")
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model, "input_tokens": 50, "output_tokens": 20,
                    "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                }
            )
        return {
            "decision": state["decision"],
            "next_check_in": state["next_check_in"],
            "dm_text": state["dm_text"],
        }

    monkeypatch.setattr(followup_mod, "call_json", _fake_call_json)
    return state


def _stub_email(monkeypatch, *, ok: bool = True):
    state = {"calls": []}

    def _fake_send(*, to_email, first_name, project_id):
        state["calls"].append({"to_email": to_email, "first_name": first_name, "project_id": project_id})
        return ok

    monkeypatch.setattr(followup_mod, "send_followup_email", _fake_send)
    return state


# ── Cap / quiet-hours guards (AC7, AC8) ───────────────────────────────────


def test_cycle_skips_capped_person_no_llm(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _freeze(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    deleg_id, conv_id = _seed_delegation(ctx, project["id"], assignee_id, task_summary="Capped task")

    sends_db.record_send(
        delegation_id=deleg_id, company_id="co-1", assignee_user_id=assignee_id,
        check_key="prior-1", channel="dm",
    )
    row = _due_row(
        deleg_id=deleg_id, project_id=project["id"], conv_id=conv_id,
        assigner_id=ctx.user_id, assignee_id=assignee_id, task_summary="Capped task",
    )
    _install_due(monkeypatch, [row])
    state = _stub_decision_llm(monkeypatch)

    summary = followup_mod.run_task_followup_cycle()

    assert state["calls"] == []
    assert summary["rescheduled"] == 1
    assert summary["pinged"] == 0
    turns = conversations_db.list_individual_turns(conv_id, assignee_id)
    assert len(turns) == 1  # only the original delivered brief turn — no ping
    followup = delegation_followups_db.get_followup(deleg_id)
    assert followup["next_check_in"] is not None


def test_cycle_skips_quiet_hours_no_llm(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    quiet_now = _FROZEN_NOW.replace(hour=22)  # 22:00 UTC — within 20:00-08:00
    _freeze(monkeypatch, quiet_now)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    deleg_id, conv_id = _seed_delegation(ctx, project["id"], assignee_id, task_summary="Quiet-hours task")

    row = _due_row(
        deleg_id=deleg_id, project_id=project["id"], conv_id=conv_id,
        assigner_id=ctx.user_id, assignee_id=assignee_id, task_summary="Quiet-hours task",
        next_check_in=quiet_now - timedelta(hours=1),
    )
    _install_due(monkeypatch, [row])
    state = _stub_decision_llm(monkeypatch)

    summary = followup_mod.run_task_followup_cycle()

    assert state["calls"] == []
    assert summary["rescheduled"] == 1
    turns = conversations_db.list_individual_turns(conv_id, assignee_id)
    assert len(turns) == 1


# ── Decision application (AC9, AC10) ──────────────────────────────────────


def test_ping_posts_dm_records_and_reschedules(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _freeze(monkeypatch)
    _bypass_cap(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    deleg_id, conv_id = _seed_delegation(ctx, project["id"], assignee_id, task_summary="Ping task")

    prior_next = _FROZEN_NOW - timedelta(hours=1)
    row = _due_row(
        deleg_id=deleg_id, project_id=project["id"], conv_id=conv_id,
        assigner_id=ctx.user_id, assignee_id=assignee_id, task_summary="Ping task",
        next_check_in=prior_next,
    )
    _install_due(monkeypatch, [row])
    _stub_decision_llm(monkeypatch, decision="ping", dm_text="Checking in on this — any update?")

    summary = followup_mod.run_task_followup_cycle()

    assert summary["pinged"] == 1
    turns = conversations_db.list_individual_turns(conv_id, assignee_id)
    assert len(turns) == 2  # the original brief + the new ping
    assert turns[-1]["content"] == "Checking in on this — any update?"
    assert turns[-1]["role"] == "assistant"

    sends = sends_db.sends_for_delegation(deleg_id, channel="dm")
    assert len(sends) == 1
    assert sends[0]["status"] == "sent"

    followup = delegation_followups_db.get_followup(deleg_id)
    assert followup["last_checked_in"] is not None
    assert followup["next_check_in"] > prior_next.isoformat()  # floor-clamped, must lengthen


def test_ping_publishes_brief_delivered_to_the_assignee(isolated_settings, monkeypatch):
    """Check-in liveness (part 2, spec 3a): a scheduled ping is a turn posted
    into the assignee's own individual chat, the same shape a fresh brief
    delivery already is — it must live-append the same way, reusing the
    exact `brief.delivered` mechanic (no new channel/event)."""
    ctx = company_client(monkeypatch)
    _freeze(monkeypatch)
    _bypass_cap(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    deleg_id, conv_id = _seed_delegation(ctx, project["id"], assignee_id, task_summary="Ping task")

    row = _due_row(
        deleg_id=deleg_id, project_id=project["id"], conv_id=conv_id,
        assigner_id=ctx.user_id, assignee_id=assignee_id, task_summary="Ping task",
        next_check_in=_FROZEN_NOW - timedelta(hours=1),
    )
    _install_due(monkeypatch, [row])
    _stub_decision_llm(monkeypatch, decision="ping", dm_text="Checking in on this — any update?")
    published: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        project_delegation, "publish_broadcast",
        lambda topic, event, payload: published.append((topic, event, payload)),
    )

    summary = followup_mod.run_task_followup_cycle()

    assert summary["pinged"] == 1
    notices = [p for p in published if p[1] == "brief.delivered"]
    assert len(notices) == 1, published
    assert notices[0][0] == f"project:{project['id']}:user:{assignee_id}"
    assert notices[0][2]["content"] == "Checking in on this — any update?"


def test_reschedule_sends_nothing(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _freeze(monkeypatch)
    _bypass_cap(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    deleg_id, conv_id = _seed_delegation(ctx, project["id"], assignee_id, task_summary="Reschedule task")

    model_next = _FROZEN_NOW + timedelta(days=3)
    row = _due_row(
        deleg_id=deleg_id, project_id=project["id"], conv_id=conv_id,
        assigner_id=ctx.user_id, assignee_id=assignee_id, task_summary="Reschedule task",
    )
    _install_due(monkeypatch, [row])
    _stub_decision_llm(monkeypatch, decision="reschedule", next_check_in=model_next.isoformat())

    summary = followup_mod.run_task_followup_cycle()

    assert summary["rescheduled"] == 1
    turns = conversations_db.list_individual_turns(conv_id, assignee_id)
    assert len(turns) == 1  # no new turn
    assert sends_db.sends_for_delegation(deleg_id) == []
    followup = delegation_followups_db.get_followup(deleg_id)
    assert followup["next_check_in"].startswith(model_next.isoformat()[:19])


# ── Email escalation gate (AC11) ──────────────────────────────────────────


def test_email_only_after_two_unanswered_dms(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _freeze(monkeypatch)
    _bypass_cap(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    deleg_id, conv_id = _seed_delegation(ctx, project["id"], assignee_id, task_summary="Email-gate task")

    # One prior unanswered DM — below the >=2 threshold.
    sends_db.record_send(
        delegation_id=deleg_id, company_id="co-1", assignee_user_id=assignee_id,
        check_key="prior-1", channel="dm",
    )
    row = _due_row(
        deleg_id=deleg_id, project_id=project["id"], conv_id=conv_id,
        assigner_id=ctx.user_id, assignee_id=assignee_id, task_summary="Email-gate task",
    )
    _install_due(monkeypatch, [row])
    _stub_decision_llm(monkeypatch, decision="ping", dm_text="Second check-in")
    email_state = _stub_email(monkeypatch)

    followup_mod.run_task_followup_cycle()

    assert email_state["calls"] == []
    assert sends_db.sends_for_delegation(deleg_id, channel="email") == []
    assert len(sends_db.sends_for_delegation(deleg_id, channel="dm")) == 2  # prior + this cycle's


def test_email_sent_after_two_unanswered_dms(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _freeze(monkeypatch)
    _bypass_cap(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    deleg_id, conv_id = _seed_delegation(ctx, project["id"], assignee_id, task_summary="Email-gate task 2")

    # Two prior unanswered DMs — meets the >=2 threshold.
    sends_db.record_send(
        delegation_id=deleg_id, company_id="co-1", assignee_user_id=assignee_id,
        check_key="prior-1", channel="dm",
    )
    sends_db.record_send(
        delegation_id=deleg_id, company_id="co-1", assignee_user_id=assignee_id,
        check_key="prior-2", channel="dm",
    )
    row = _due_row(
        deleg_id=deleg_id, project_id=project["id"], conv_id=conv_id,
        assigner_id=ctx.user_id, assignee_id=assignee_id, task_summary="Email-gate task 2",
    )
    _install_due(monkeypatch, [row])
    _stub_decision_llm(monkeypatch, decision="ping", dm_text="Third check-in")
    email_state = _stub_email(monkeypatch)

    summary = followup_mod.run_task_followup_cycle()

    assert summary["emailed"] == 1
    assert len(email_state["calls"]) == 1
    assert email_state["calls"][0]["to_email"] == f"{assignee_id}@co.com"
    email_rows = sends_db.sends_for_delegation(deleg_id, channel="email")
    assert len(email_rows) == 1
    assert email_rows[0]["status"] == "sent"


# ── Escalation targeting (AC12) ────────────────────────────────────────────


def test_escalate_posts_to_requester_not_assignee(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _freeze(monkeypatch)
    _bypass_cap(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    deleg_id, conv_id = _seed_delegation(ctx, project["id"], assignee_id, task_summary="Escalate task")

    # One prior send with no status-changing event after it -> cycles_since_status=1.
    sends_db.record_send(
        delegation_id=deleg_id, company_id="co-1", assignee_user_id=assignee_id,
        check_key="prior-1", channel="dm",
    )
    row = _due_row(
        deleg_id=deleg_id, project_id=project["id"], conv_id=conv_id,
        assigner_id=ctx.user_id, assignee_id=assignee_id, task_summary="Escalate task",
        expected_completion=_FROZEN_NOW - timedelta(days=3),
    )
    _install_due(monkeypatch, [row])
    _stub_decision_llm(monkeypatch, decision="escalate")

    summary = followup_mod.run_task_followup_cycle()

    assert summary["escalated"] == 1
    assignee_turns = conversations_db.list_individual_turns(conv_id, assignee_id)
    assert len(assignee_turns) == 1  # unchanged — no DM to the assignee

    requester_conv = conversations_db.get_individual_project_chat(project["id"], ctx.user_id)
    assert requester_conv is not None
    requester_turns = conversations_db.list_individual_turns(requester_conv["id"], ctx.user_id)
    assert len(requester_turns) == 1
    assert "Escalate task" in requester_turns[0]["content"]

    escalation_rows = sends_db.sends_for_delegation(deleg_id, channel="escalation")
    assert len(escalation_rows) == 1
    # Only the pre-seeded "prior-1" dm row — the escalate path adds no NEW dm.
    assert len(sends_db.sends_for_delegation(deleg_id, channel="dm")) == 1


def test_escalate_publishes_brief_delivered_to_the_requester(isolated_settings, monkeypatch):
    """Same check-in liveness as the ping test above, on the requester's own
    channel this time — the escalation notice is their version of a
    check-in."""
    ctx = company_client(monkeypatch)
    _freeze(monkeypatch)
    _bypass_cap(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    deleg_id, conv_id = _seed_delegation(
        ctx, project["id"], assignee_id, task_summary="Escalate task",
    )
    sends_db.record_send(
        delegation_id=deleg_id, company_id="co-1", assignee_user_id=assignee_id,
        check_key="prior-1", channel="dm",
    )
    row = _due_row(
        deleg_id=deleg_id, project_id=project["id"], conv_id=conv_id,
        assigner_id=ctx.user_id, assignee_id=assignee_id, task_summary="Escalate task",
        expected_completion=_FROZEN_NOW - timedelta(days=3),
    )
    _install_due(monkeypatch, [row])
    _stub_decision_llm(monkeypatch, decision="escalate")
    published: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        project_delegation, "publish_broadcast",
        lambda topic, event, payload: published.append((topic, event, payload)),
    )

    summary = followup_mod.run_task_followup_cycle()

    assert summary["escalated"] == 1
    notices = [p for p in published if p[1] == "brief.delivered"]
    assert len(notices) == 1, published
    assert notices[0][0] == f"project:{project['id']}:user:{ctx.user_id}"
    assert "Escalate task" in notices[0][2]["content"]


# ── Step-0 soft-done short-circuit (AC13) ─────────────────────────────────


def test_soft_done_finalized_without_llm_or_ping(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _freeze(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    deleg_id, conv_id = _seed_delegation(ctx, project["id"], assignee_id, task_summary="Soft-done task")

    row = _due_row(
        deleg_id=deleg_id, project_id=project["id"], conv_id=conv_id,
        assigner_id=ctx.user_id, assignee_id=assignee_id, task_summary="Soft-done task",
        pending_done_since=_FROZEN_NOW - timedelta(hours=2),
    )
    _install_due(monkeypatch, [row])
    state = _stub_decision_llm(monkeypatch)

    summary = followup_mod.run_task_followup_cycle()

    assert summary["finalized"] == 1
    assert state["calls"] == []  # zero call_json calls for this task

    events = delegation_events_db.list_events(deleg_id)
    assert [e["event"] for e in events] == ["completed"]

    followup = delegation_followups_db.get_followup(deleg_id)
    assert followup["pending_done_since"] is None

    turns = conversations_db.list_individual_turns(conv_id, assignee_id)
    assert len(turns) == 1  # no ping turn posted

    assert sends_db.sends_for_delegation(deleg_id) == []


def test_soft_done_finalize_publishes_the_completion_notice(isolated_settings, monkeypatch):
    """The finalize step calls `notify_requester_task_completed`
    (`delegation_status_ingest.py`) — the same helper the inbound
    `done_explicit` reply path uses — so it inherits that path's own
    completion-notice publish for free. This proves the wiring survives
    reaching it from the OTHER caller (the outbound sweep, not an inbound
    reply)."""
    ctx = company_client(monkeypatch)
    _freeze(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    deleg_id, conv_id = _seed_delegation(ctx, project["id"], assignee_id, task_summary="Soft-done task")

    row = _due_row(
        deleg_id=deleg_id, project_id=project["id"], conv_id=conv_id,
        assigner_id=ctx.user_id, assignee_id=assignee_id, task_summary="Soft-done task",
        pending_done_since=_FROZEN_NOW - timedelta(hours=2),
    )
    _install_due(monkeypatch, [row])
    _stub_decision_llm(monkeypatch)
    published: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        project_delegation, "publish_broadcast",
        lambda topic, event, payload: published.append((topic, event, payload)),
    )

    summary = followup_mod.run_task_followup_cycle()

    assert summary["finalized"] == 1
    notices = [p for p in published if p[1] == "brief.delivered"]
    assert len(notices) == 1, published
    assert notices[0][0] == f"project:{project['id']}:user:{ctx.user_id}"
    assert "finished" in notices[0][2]["content"]


# ── Idempotency (AC14) ─────────────────────────────────────────────────────


def test_double_fire_is_noop(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    _freeze(monkeypatch)
    _bypass_cap(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    deleg_id, conv_id = _seed_delegation(ctx, project["id"], assignee_id, task_summary="Double-fire task")

    row = _due_row(
        deleg_id=deleg_id, project_id=project["id"], conv_id=conv_id,
        assigner_id=ctx.user_id, assignee_id=assignee_id, task_summary="Double-fire task",
    )
    _install_due(monkeypatch, [row])  # same due row served both cycles
    _stub_decision_llm(monkeypatch, decision="ping", dm_text="Only once")

    followup_mod.run_task_followup_cycle()
    summary_2 = followup_mod.run_task_followup_cycle()

    assert summary_2["skipped"] == 1
    assert summary_2["pinged"] == 0
    turns = conversations_db.list_individual_turns(conv_id, assignee_id)
    assert len(turns) == 2  # original brief + exactly one ping (not two)
    assert len(sends_db.sends_for_delegation(deleg_id, channel="dm")) == 1


# ── Never auto-invites (AC15) ──────────────────────────────────────────────


def test_sweep_never_invites():
    """AC15 — grep the module for an actual membership/invite WRITE call,
    not merely the words (which legitimately appear in this file's own
    docstrings explaining what it does NOT do)."""
    source = (REPO_ROOT / "backend" / "app" / "delegation_followup.py").read_text()
    forbidden_patterns = (
        '.table("project_members")',
        '.table("workspace_invites")',
        "add_member(",
        "add_member (",
    )
    for pattern in forbidden_patterns:
        assert pattern not in source, f"found forbidden write call: {pattern!r}"


# ── Cost logging (AC16) ─────────────────────────────────────────────────────


def test_cycle_emits_one_cost_line_per_task(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    _freeze(monkeypatch)
    _bypass_cap(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    deleg_id, conv_id = _seed_delegation(ctx, project["id"], assignee_id, task_summary="Cost-log task")

    row = _due_row(
        deleg_id=deleg_id, project_id=project["id"], conv_id=conv_id,
        assigner_id=ctx.user_id, assignee_id=assignee_id, task_summary="Cost-log task",
    )
    _install_due(monkeypatch, [row])
    _stub_decision_llm(monkeypatch, decision="reschedule")

    with caplog.at_level(logging.INFO):
        followup_mod.run_task_followup_cycle()

    cost_lines = [
        r for r in caplog.records
        if r.getMessage().startswith("projects.delegation.followup")
    ]
    assert len(cost_lines) == 1
    msg = cost_lines[0].getMessage()
    assert f"delegation_id={deleg_id}" in msg
    assert f"project_id={project['id']}" in msg
    assert "Cost-log task" not in msg  # identifiers only, no task content


# ── Per-task error isolation (AC17) ─────────────────────────────────────────


def test_cycle_per_task_error_isolation(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    _freeze(monkeypatch)
    _bypass_cap(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    boom_id, boom_conv = _seed_delegation(ctx, project["id"], assignee_id, task_summary="Boom task")
    ok_id, ok_conv = _seed_delegation(ctx, project["id"], assignee_id, task_summary="Fine task")

    rows = [
        _due_row(
            deleg_id=boom_id, project_id=project["id"], conv_id=boom_conv,
            assigner_id=ctx.user_id, assignee_id=assignee_id, task_summary="Boom task",
        ),
        _due_row(
            deleg_id=ok_id, project_id=project["id"], conv_id=ok_conv,
            assigner_id=ctx.user_id, assignee_id=assignee_id, task_summary="Fine task",
        ),
    ]
    _install_due(monkeypatch, rows)
    _stub_decision_llm(monkeypatch, decision="reschedule", raise_for="Boom task")

    with caplog.at_level(logging.ERROR):
        summary = followup_mod.run_task_followup_cycle()

    assert summary["due"] == 2
    assert summary["rescheduled"] == 1  # only the fine task got through
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any(str(boom_id) in r.getMessage() for r in error_records)

    ok_followup = delegation_followups_db.get_followup(ok_id)
    assert ok_followup is not None and ok_followup["next_check_in"] is not None


# ── Scheduler registration (AC5) ──────────────────────────────────────────


class _FakeScheduler:
    def __init__(self):
        self.jobs: list[dict] = []
        self.started = False

    def add_job(self, func, *, trigger=None, id=None, name=None, replace_existing=False):
        self.jobs.append({"id": id, "name": name})

    def start(self):
        self.started = True

    def shutdown(self, wait=False):
        pass


def _run_start_scheduler(monkeypatch, *, scheduler_enabled: bool, task_followup_enabled: bool):
    from app import scheduler as sched_mod

    monkeypatch.setattr(sched_mod.settings, "scheduler_enabled", scheduler_enabled)
    monkeypatch.setattr(sched_mod.settings, "pipeline_interval_hours", 6)
    monkeypatch.setattr(sched_mod.settings, "task_followup_enabled", task_followup_enabled)
    monkeypatch.setattr(sched_mod.settings, "task_followup_interval_hours", 1)
    fake = _FakeScheduler()
    monkeypatch.setattr(sched_mod, "AsyncIOScheduler", lambda **kw: fake)
    sched_mod.start_scheduler()
    sched_mod.shutdown_scheduler()
    return fake


def test_job_registered_only_when_both_flags_on(monkeypatch):
    fake = _run_start_scheduler(monkeypatch, scheduler_enabled=True, task_followup_enabled=True)
    assert "task_followup" in {j["id"] for j in fake.jobs}


def test_job_absent_when_task_followup_flag_off(monkeypatch):
    fake = _run_start_scheduler(monkeypatch, scheduler_enabled=True, task_followup_enabled=False)
    assert "task_followup" not in {j["id"] for j in fake.jobs}


def test_job_absent_when_scheduler_disabled_even_if_task_followup_on(monkeypatch):
    """The process-level SCHEDULER_ENABLED gate wins even if the feature's
    own flag is on — start_scheduler returns before registering ANY job."""
    from app import scheduler as sched_mod

    monkeypatch.setattr(sched_mod.settings, "scheduler_enabled", False)
    monkeypatch.setattr(sched_mod.settings, "task_followup_enabled", True)
    fake = _FakeScheduler()
    monkeypatch.setattr(sched_mod, "AsyncIOScheduler", lambda **kw: fake)
    sched_mod.start_scheduler()
    assert fake.jobs == []
    assert fake.started is False


# ── Email (pure) ────────────────────────────────────────────────────────────


def test_email_skipped_without_key(isolated_settings, monkeypatch):
    from app import delegation_followup_email as email_mod
    from app import config as config_mod

    monkeypatch.setattr(config_mod.settings, "resend_api_key", "", raising=False)
    ok = email_mod.send_followup_email(to_email="a@b.com", first_name="Fortune", project_id=1)
    assert ok is False


def test_email_body_has_cta_no_task_content():
    from app import delegation_followup_email as email_mod

    subject, body_text, body_html = email_mod.render_followup_email(
        first_name="Fortune", project_id=42,
    )
    link = email_mod.project_chat_link(42)
    assert link in body_text
    assert link in body_html.replace("&amp;", "&")  # html escapes '&' -> '&amp;'
    assert "Draft the pricing page" not in body_text  # never task content
    assert "Draft the pricing page" not in body_html
    assert "chat=individual" in link


# ── Non-breakage (AC19) ─────────────────────────────────────────────────────


_PRE_EXISTING_JOB_IDS = {
    "brief_tick", "refresh_connectors", "skill_source_sync", "drip_emails",
    "brief_nudges", "invite_reminders", "signin_health_monitor",
    "connector_health_monitor", "ticket_sync", "extraction_eval",
    "jira_personal_data_report", "orphan_ask_job_sweep",
}


def test_scheduler_preexisting_jobs_intact():
    source = (REPO_ROOT / "backend" / "app" / "scheduler.py").read_text()
    ids_found = set(re.findall(r'id="([a-z_]+)"', source))
    assert _PRE_EXISTING_JOB_IDS <= ids_found, (
        f"missing pre-existing job ids: {_PRE_EXISTING_JOB_IDS - ids_found}"
    )
    assert "task_followup" in ids_found

    result = subprocess.run(
        [
            sys.executable, "-m", "py_compile",
            "app/scheduler.py", "app/config.py", "app/db/delegation_followups.py",
        ],
        cwd=REPO_ROOT / "backend",
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_delegation_followups_writers_unchanged(isolated_settings, monkeypatch):
    """the cadence-spine `upsert_followup`/`get_followup` still round-trip untouched
    by the appended `list_due_followups`/`timezones_for_user_ids` — the
    same partial-merge contract `test_delegation_followups.py`'s real-DB
    suite proves, checked here fast-lane against `FakeSupabaseClient`."""
    from app.db.delegation_followups import get_followup, upsert_followup
    from app.db.project_delegations import record_delegation

    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    deleg = record_delegation(
        project_id=project["id"],
        assigner_user_id="assigner-writers-check",
        assignee_user_id="assignee-writers-check",
        task_summary="writers-unchanged smoke",
        source_conversation_id=None,
        source_turn_id=None,
        delivered_conversation_id=None,
        delivered_turn_id=None,
    )
    t = _FROZEN_NOW
    first = upsert_followup(deleg["id"], next_check_in=t)
    assert first["next_check_in"].startswith(t.isoformat()[:19])
    assert first["muted"] is False

    reread = get_followup(deleg["id"])
    assert reread["next_check_in"].startswith(t.isoformat()[:19])
    assert get_followup(-999) is None
