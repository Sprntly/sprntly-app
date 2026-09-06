"""Tests for `app/context_assembler_project.py::_insession_task_check_block`
— the in-session "are you done?" check (request-time flag-gated).

`v_delegation_status` is a real Postgres view `FakeSupabaseClient` cannot
evaluate (see `db/delegation_events.py`'s own docstring and
`test_delegation_status_ingest.py`'s identical caveat) —
`list_status_for_assignee` is monkeypatched to a data-driven equivalent that
reacts to real `record_delegation`/`record_event` inserts, mirroring
`test_delegation_status_ingest.py::_install_fake_assignee_view`.

Four groups, in file order:
  1. Injection — flag/open-task/assignee/throttle gating (AC: injection).
  2. Property tests on the injected instruction (LLM-facing text) — names the
     task, forbids marking/assuming done, bounded length.
  3. Throttle — `last_insession_ask_at` gates re-injection within the window,
     upsert records on inject, re-injects after the window.
  4. Regression — main-chat isolation (mutation-proofed reasoning, mirrors
     `test_project_instructions.py`); `maybe_ingest_status`/the completion
     path are untouched by this ticket (not re-tested here — proven by this
     module never importing `delegation_status_ingest` at all, plus the
     existing `test_delegation_status_ingest.py` suite staying green
     unmodified in the same blast-radius run).
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import context_assembler_project as cap
from app.db import delegation_events as delegation_events_db
from app.db import delegation_followups as delegation_followups_db
from app.db.project_delegations import record_delegation
from tests._company_helpers import company_client


# ── Fixtures / helpers ──────────────────────────────────────────────────────


def _create_project(ctx, *, name: str = "In-session check project") -> dict:
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


def _fake_status_row(deleg_row: dict) -> dict:
    events = delegation_events_db.list_events(deleg_row["id"])
    latest = events[-1] if events else None
    return {
        "delegation_id": deleg_row["id"],
        "project_id": deleg_row["project_id"],
        "assigner_user_id": deleg_row["assigner_user_id"],
        "assignee_user_id": deleg_row["assignee_user_id"],
        "task_summary": deleg_row["task_summary"],
        "status": latest["event"] if latest else "assigned",
        "status_at": latest["created_at"] if latest else deleg_row["created_at"],
    }


def _install_fake_assignee_view(monkeypatch) -> None:
    """Stand-in for `list_status_for_assignee` — see module docstring.
    Mirrors `test_delegation_status_ingest.py::_install_fake_assignee_view`."""
    from app.db.client import require_client

    def _list_for_assignee(project_id, user_id):
        rows = (
            require_client()
            .table("project_delegations")
            .select("*")
            .eq("project_id", project_id)
            .eq("assignee_user_id", user_id)
            .execute()
            .data
            or []
        )
        return [_fake_status_row(d) for d in rows]

    monkeypatch.setattr(delegation_events_db, "list_status_for_assignee", _list_for_assignee)


def _seed_open_delegation(
    ctx, project_id, assignee_id, *, task_summary: str = "Draft the pricing page"
) -> int:
    deleg = record_delegation(
        project_id=project_id,
        assigner_user_id=ctx.user_id,
        assignee_user_id=assignee_id,
        task_summary=task_summary,
        source_conversation_id=None,
        source_turn_id=None,
        delivered_conversation_id=None,
        delivered_turn_id=None,
    )
    return deleg["id"]


def _enable(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "insession_task_check_enabled", True)


# ── 1. Injection gating ─────────────────────────────────────────────────────


def test_injects_when_flag_on_open_task_assignee_cold(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    _seed_open_delegation(ctx, project["id"], assignee_id, task_summary="Draft the pricing page")
    _enable(monkeypatch)

    block = cap._insession_task_check_block(project["id"], assignee_id)
    assert "Draft the pricing page" in block
    assert "do NOT mark it done" in block.lower() or "do not mark it done" in block.lower()


def test_no_injection_when_flag_off(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    _seed_open_delegation(ctx, project["id"], assignee_id)
    # Flag deliberately left at its default (False) — NOT calling _enable().

    block = cap._insession_task_check_block(project["id"], assignee_id)
    assert block == ""


def test_no_injection_when_no_open_task(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    _enable(monkeypatch)
    # No delegation seeded at all.

    block = cap._insession_task_check_block(project["id"], assignee_id)
    assert block == ""


def test_no_injection_for_non_assignee(isolated_settings, monkeypatch):
    """A project member who is NOT the assignee of the open task gets
    nothing — this only ever surfaces the CALLER's own open delegations."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"], name="Assignee Person")
    other_member_id = _seed_assignee(project["id"], name="Other Person")
    _install_fake_assignee_view(monkeypatch)
    _seed_open_delegation(ctx, project["id"], assignee_id)
    _enable(monkeypatch)

    block = cap._insession_task_check_block(project["id"], other_member_id)
    assert block == ""


def test_no_injection_when_closed(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id = _seed_open_delegation(ctx, project["id"], assignee_id)
    delegation_events_db.record_event(
        delegation_id=deleg_id, event="completed", actor_user_id=assignee_id,
    )
    _enable(monkeypatch)

    block = cap._insession_task_check_block(project["id"], assignee_id)
    assert block == ""


def test_no_injection_when_throttle_warm(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id = _seed_open_delegation(ctx, project["id"], assignee_id)
    delegation_followups_db.upsert_followup(
        deleg_id, last_insession_ask_at=datetime.now(timezone.utc)
    )
    _enable(monkeypatch)

    block = cap._insession_task_check_block(project["id"], assignee_id)
    assert block == ""


def test_no_injection_when_no_user_id(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _install_fake_assignee_view(monkeypatch)
    _enable(monkeypatch)

    assert cap._insession_task_check_block(project["id"], None) == ""


# ── 2. Property tests on the injected instruction (LLM-facing) ─────────────


def test_instruction_names_task_and_forbids_marking_done(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    _seed_open_delegation(ctx, project["id"], assignee_id, task_summary="Ship the onboarding flow")
    _enable(monkeypatch)

    block = cap._insession_task_check_block(project["id"], assignee_id)
    lowered = block.lower()

    assert "ship the onboarding flow" in lowered
    assert "do not mark it done" in lowered
    assert "do not assume it's done" in lowered or "do not assume it is done" in lowered
    assert "only ask" in lowered
    assert "already asked about this task earlier in this conversation" in lowered

    sentences = [s for s in block.split(".") if s.strip()]
    assert len(sentences) >= 3, "instruction must be substantive, not a one-liner"

    weak = "The user has a task."
    assert "do not mark it done" not in weak.lower()


def test_instruction_bounded_length(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    # Three open tasks with an over-cap-length summary each — the cap (3
    # tasks) and the per-summary char truncation together bound the total.
    for i in range(4):
        _seed_open_delegation(
            ctx, project["id"], assignee_id, task_summary=("x" * 500) + f"-{i}"
        )
    _enable(monkeypatch)

    block = cap._insession_task_check_block(project["id"], assignee_id)
    # 3 tasks (the cap) * (160-char summary cap + quoting/ellipsis overhead)
    # + the fixed instruction boilerplate — generously bounded well under 1000.
    assert len(block) < 1000, f"instruction is not bounded: {len(block)} chars"
    quoted_summaries = re.findall(r"'(x+…?)'", block)
    assert len(quoted_summaries) == 3, "expected exactly 3 quoted (capped) task summaries"
    for summary in quoted_summaries:
        assert len(summary) <= cap._INSESSION_TASK_SUMMARY_CHARS + 1  # + ellipsis char


def test_instruction_caps_at_three_tasks(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    for i in range(5):
        _seed_open_delegation(ctx, project["id"], assignee_id, task_summary=f"Task {i}")
    _enable(monkeypatch)

    block = cap._insession_task_check_block(project["id"], assignee_id)
    named = sum(1 for i in range(5) if f"Task {i}" in block)
    assert named == 3


# ── 3. Throttle ──────────────────────────────────────────────────────────────


def test_throttle_upsert_records_on_inject(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id = _seed_open_delegation(ctx, project["id"], assignee_id)
    _enable(monkeypatch)

    assert delegation_followups_db.get_followup(deleg_id) is None

    block = cap._insession_task_check_block(project["id"], assignee_id)
    assert block != ""

    followup = delegation_followups_db.get_followup(deleg_id)
    assert followup is not None
    assert followup["last_insession_ask_at"] is not None


def test_throttle_reinjects_after_window(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id = _seed_open_delegation(ctx, project["id"], assignee_id, task_summary="Old-session task")
    stale = datetime.now(timezone.utc) - timedelta(
        hours=cap._INSESSION_ASK_WINDOW_HOURS + 1
    )
    delegation_followups_db.upsert_followup(deleg_id, last_insession_ask_at=stale)
    _enable(monkeypatch)

    block = cap._insession_task_check_block(project["id"], assignee_id)
    assert "Old-session task" in block


# ── 4. Main-chat isolation (mutation-proofed) ───────────────────────────────


def test_main_chat_scope_none_no_insession_block(monkeypatch):
    """Main chat's assembled system prompt never carries the A5 instruction,
    for BOTH `scope is None` and `SurfaceScope(surface=Surface.main)` — even
    when the flag is on and `list_status_for_assignee` would return an open
    task for ANY project/user (the strongest form of "even when the caller
    genuinely has an open task somewhere": no code on the main path calls
    `context_assembler_project` at all, so patching the assignee reader to
    always return a sentinel task and asserting it never reaches the LLM
    call directly proves the seam, not just fixture absence).

    Mutation-proofed manually (not shipped as a second producer in this
    file): temporarily calling `_insession_task_check_block` unconditionally
    from `qa_agent._fold_project_context`'s main-path branch turns this test
    RED; reverting turns it back GREEN."""
    import app.qa_agent as qa
    from app.config import settings
    from app.surface_scope import Surface, SurfaceScope

    monkeypatch.setattr(settings, "insession_task_check_enabled", True)
    monkeypatch.setattr(
        delegation_events_db,
        "list_status_for_assignee",
        lambda project_id, user_id: [
            {
                "delegation_id": 999,
                "status": "assigned",
                "task_summary": "DO-NOT-LEAK-SENTINEL-TASK",
            }
        ],
    )

    systems: list[str] = []

    def _fake_llm_call(**k):
        from types import SimpleNamespace

        if k.get("purpose") == "route":
            return SimpleNamespace(
                output={"skill_id": None, "confidence": 0.0, "action": None}
            )
        systems.append(k.get("system") or "")
        return SimpleNamespace(
            output={
                "answer": "ok", "key_points": [], "citations": [],
                "confidence": 0.9, "unanswered": "",
            }
        )

    monkeypatch.setattr(qa, "llm_call", _fake_llm_call)
    monkeypatch.setattr(qa, "route", lambda *a, **k: qa.RouteDecision("call-digest-like", 0.0, "none"))

    common = dict(
        enterprise_id="ent", question="anything at all", dataset="acme", pinned_skill="__builtin_none__",
    )
    qa.answer(**common)
    qa.answer(**common, scope=None)
    qa.answer(**common, scope=SurfaceScope(surface=Surface.main))

    assert len(systems) == 3
    for system in systems:
        assert "DO-NOT-LEAK-SENTINEL-TASK" not in system
        assert "open task" not in system.lower()
