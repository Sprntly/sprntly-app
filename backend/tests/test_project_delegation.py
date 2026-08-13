"""Tests for `app/project_delegation.py` — the `delegate_task` tool, the
bounded best-effort brief, and gated cross-user delivery (walking
skeleton) — and its wiring into `routes/projects.py::_respond_as_group_agent`.

This is the FIRST cross-user write in the product: the agent writes an
`assistant` turn into a conversation owned by ANOTHER member. A miss here
is a cross-user IDOR. The double-membership gate in
`handle_delegate_task` (AD-P16/AD-P18) is the load-bearing invariant this
file mutation-proofs.

Covers:
  - tool-description + brief-prompt property tests (AC9, AC7/AC8)
  - delivery: one assistant turn in the assignee's OWN individual
    conversation + one `project_delegations` fact row (AC1/AC2)
  - authorization / IDOR (mutation-proofed): the gate is flipped to
    always-True and the write occurs, restored and it doesn't (AC3)
  - never writes a `user` turn as another person (AC4)
  - fail-closed resolution (no_match/ambiguous) and fail-closed brief
    (AC5/AC6)
  - cost/observability: one `projects.delegation.brief` line per
    delivered hand-off, none otherwise; no brief/task text in any log
    line (AC10/AC11)

Most of this file drives `project_delegation.handle_delegate_task`
directly against `FakeSupabaseClient` (`isolated_settings`) with
`project_delegation.call_md` stubbed — fast and deterministic, proving the
handler's CONTRACT (gates, ordering, never-raises) rather than exercising
a real LLM tool-call decision.

Two tests need the REAL classifier decision AND the REAL brief LLM call —
a stub masks whether `delegate_task` actually gets called end to end
([[feedback_stubbed-e2e-masks-loop-behaviour]]) — driven over the real
`/v1/projects/{id}/group/turns` route against a real local Supabase:

  - `test_delegation_delivers_turn_and_records_fact`
  - `test_brief_uses_assigner_context_and_no_trailing_question`

Both are gated behind `RUN_DELEGATE_TASK_LIVE=1` PLUS a real
`ANTHROPIC_API_KEY`, mirroring `test_group_chat_turns_live.py`'s /
`test_project_group_gate.py`'s rig-gating shape. Run them with:

    RUN_DELEGATE_TASK_LIVE=1 \\
        pytest tests/test_project_delegation.py -m integration
"""
from __future__ import annotations

import logging
import os
import re
import time
import uuid

import jwt as pyjwt
import pytest

from app import project_delegation
from app.db import projects as projects_db
from tests._company_helpers import company_client


def _create_project(ctx, *, name: str = "Delegation project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


def _seed_assignee(project_id: int, *, name: str = "Fortune Adeyemi", role: str = "Designer") -> str:
    """Add a second project member (the delegation target) with a
    resolvable name/role and return their user_id."""
    from app.db.client import require_client

    user_id = "assignee-" + uuid.uuid4().hex[:8]
    require_client().table("profiles").insert(
        {"id": user_id, "email": f"{user_id}@co.com", "full_name": name, "role": role}
    ).execute()
    projects_db.add_member(project_id, user_id)
    return user_id


def _seed_project_with_assignee(ctx, *, assigner_role: str = "PM") -> tuple[dict, str]:
    from app.db.client import require_client

    project = _create_project(ctx)
    require_client().table("profiles").upsert(
        {"id": ctx.user_id, "email": f"{ctx.user_id}@co.com", "full_name": "Alex Assigner", "role": assigner_role}
    ).execute()
    assignee_id = _seed_assignee(project["id"])
    return project, assignee_id


def _stub_brief_llm(monkeypatch, *, reply: str = "Here is the brief. Please proceed with the task."):
    """Stub the ONE LLM call site `_build_brief` uses
    (`app.project_delegation.call_md`) so no test in this fast lane ever
    hits Anthropic. Returns the list of calls made."""
    calls: list[dict] = []

    def _fake_call_md(*, system, user, model, meta_out=None, **kwargs):  # noqa: ARG001
        calls.append({"system": system, "user": user})
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model,
                    "input_tokens": 60,
                    "output_tokens": 20,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                }
            )
        return reply

    monkeypatch.setattr(project_delegation, "call_md", _fake_call_md)
    return calls


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


# ── Tool-description + brief-prompt property tests (AC7/AC8/AC9) ────────


def test_delegate_tool_description_when_and_negative_space():
    desc = project_delegation.DELEGATE_TASK_TOOL["description"]
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", desc.strip()) if s]
    assert len(sentences) >= 3, "tool description must be >= 3 sentences"

    lower = desc.lower()
    assert any(w in lower for w in ("hand", "send", "assign", "route")), (
        "description must contain a hand/send/assign cue"
    )
    assert "do not" in lower, "description must explicitly say when NOT to call it"
    assert "question" in lower or "fyi" in lower
    assert "human-to-human" in lower or "talking to each other" in lower
    assert "roster" in lower, "description must instruct picking the assignee from the roster"

    assert project_delegation.DELEGATE_TASK_TOOL["input_schema"]["required"] == [
        "assignee", "task_summary",
    ]
    assert project_delegation.DELEGATE_TASK_TOOL["input_schema"]["additionalProperties"] is False

    # Negative-space: the checks themselves must actually catch a
    # description that DOESN'T carry these rules — proves this isn't vacuous.
    weak = "Call this tool to delegate work to a teammate."
    assert "do not" not in weak.lower()
    assert "roster" not in weak.lower()


def test_brief_prompt_forbids_trailing_question_and_requires_fields():
    system = project_delegation._BRIEF_SYSTEM.lower()
    assert "never end" in system
    assert "question" in system and "offer" in system
    assert "task" in system
    assert "who assigned" in system or "assigned it" in system
    assert "artifact" in system
    assert "never invent" in system

    weak = "Write a brief for the assignee about the task."
    assert "never end" not in weak.lower()
    assert "never invent" not in weak.lower()


# ── Authorization / IDOR (mutation-proofed — AC3) ────────────────────────


def test_delivery_only_into_assignee_own_individual_thread(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project, assignee_id = _seed_project_with_assignee(ctx)
    _stub_brief_llm(monkeypatch)

    result = _delegate(project, ctx.user_id)
    assert "Sent the brief" in result

    from app.db.client import require_client

    delivered = (
        require_client()
        .table("conversations")
        .select("*")
        .eq("project_id", project["id"])
        .eq("kind", "individual")
        .execute()
        .data
    )
    assert len(delivered) == 1, "exactly one individual conversation must exist"
    assert delivered[0]["user_id"] == assignee_id
    assert delivered[0]["project_id"] == project["id"]

    turns = (
        require_client()
        .table("conversation_turns")
        .select("*")
        .eq("conversation_id", delivered[0]["id"])
        .execute()
        .data
    )
    assert len(turns) == 1
    assert turns[0]["role"] == "assistant"
    assert turns[0]["author_user_id"] is None

    delegations = (
        require_client()
        .table("project_delegations")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert len(delegations) == 1
    d = delegations[0]
    assert d["assigner_user_id"] == ctx.user_id
    assert d["assignee_user_id"] == assignee_id
    assert d["delivered_conversation_id"] == delivered[0]["id"]
    assert d["delivered_turn_id"] == turns[0]["id"]

    # No group-chat write and no OTHER user's conversation was touched.
    other_convs = (
        require_client()
        .table("conversations")
        .select("id")
        .eq("project_id", project["id"])
        .neq("id", delivered[0]["id"])
        .execute()
        .data
    )
    assert other_convs == []


def test_non_member_assignee_no_write(isolated_settings, monkeypatch):
    """A resolved id that fails the server-side `is_project_member`
    re-check must produce NO turn and NO delegation row — then flipping
    the gate to always-True (forced) proves the gate is load-bearing by
    making the write occur."""
    ctx = company_client(monkeypatch)
    project, _ = _seed_project_with_assignee(ctx)
    _stub_brief_llm(monkeypatch)

    outsider = {"user_id": "outsider-" + uuid.uuid4().hex[:8], "name": "Outsider", "job_role": None}
    monkeypatch.setattr(
        project_delegation, "resolve_member",
        lambda project_id, needle: {"status": "resolved", "member": outsider},
    )

    from app.db.client import require_client

    result = _delegate(project, ctx.user_id, assignee="Outsider")
    assert "only hand tasks between members" in result

    assert require_client().table("project_delegations").select("id").execute().data == []
    assert (
        require_client()
        .table("conversations")
        .select("id")
        .eq("kind", "individual")
        .execute()
        .data
        == []
    )

    # RED->GREEN mutation proof: flip the gate to always-True — the write
    # now occurs, proving the gate (not something else) was blocking it.
    monkeypatch.setattr(project_delegation, "is_project_member", lambda *a, **kw: True)
    result2 = _delegate(project, ctx.user_id, assignee="Outsider")
    assert "Sent the brief" in result2
    assert len(require_client().table("project_delegations").select("id").execute().data) == 1
    assert (
        len(
            require_client()
            .table("conversations")
            .select("id")
            .eq("kind", "individual")
            .execute()
            .data
        )
        == 1
    )


def test_cross_project_id_rejected(isolated_settings, monkeypatch):
    """A resolved id that IS a member of a DIFFERENT project fails the
    re-check scoped to project X — no write."""
    ctx = company_client(monkeypatch)
    project, _ = _seed_project_with_assignee(ctx)
    other_project = _create_project(ctx, name="Another project")
    other_member_id = _seed_assignee(other_project["id"], name="Someone Else")
    _stub_brief_llm(monkeypatch)

    forced = {"user_id": other_member_id, "name": "Someone Else", "job_role": None}
    monkeypatch.setattr(
        project_delegation, "resolve_member",
        lambda project_id, needle: {"status": "resolved", "member": forced},
    )

    result = _delegate(project, ctx.user_id, assignee="Someone Else")
    assert "only hand tasks between members" in result

    from app.db.client import require_client

    assert require_client().table("project_delegations").select("id").execute().data == []
    delivered = (
        require_client()
        .table("conversations")
        .select("id")
        .eq("kind", "individual")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert delivered == []


def test_never_writes_user_turn_as_other_person(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project, assignee_id = _seed_project_with_assignee(ctx)
    _stub_brief_llm(monkeypatch)

    calls: list[dict] = []
    from app.db import conversations as conversations_db

    original = conversations_db.post_individual_turn

    def _spy(conversation_id, role, content):
        calls.append({"conversation_id": conversation_id, "role": role})
        return original(conversation_id, role, content)

    monkeypatch.setattr(project_delegation, "post_individual_turn", _spy)

    _delegate(project, ctx.user_id)

    assert len(calls) == 1
    assert calls[0]["role"] == "assistant"

    from app.db.client import require_client

    turn = (
        require_client()
        .table("conversation_turns")
        .select("author_user_id, role")
        .eq("conversation_id", calls[0]["conversation_id"])
        .execute()
        .data[0]
    )
    assert turn["role"] == "assistant"
    assert turn["author_user_id"] is None


# ── Fail-closed (AC5/AC6) ─────────────────────────────────────────────────


def test_no_match_asks_group_no_write(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project, _ = _seed_project_with_assignee(ctx)
    calls = _stub_brief_llm(monkeypatch)

    result = _delegate(project, ctx.user_id, assignee="Nobody Here")
    assert "?" in result
    assert "Members:" in result or "members" in result.lower()
    assert calls == [], "no_match must never reach the brief call"

    from app.db.client import require_client

    assert require_client().table("project_delegations").select("id").execute().data == []
    assert (
        require_client().table("conversations").select("id").eq("kind", "individual").execute().data
        == []
    )


def test_ambiguous_asks_which_no_write(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project, _ = _seed_project_with_assignee(ctx)
    _seed_assignee(project["id"], name="Designer Two", role="Designer")
    _seed_assignee(project["id"], name="Designer Three", role="Designer")
    calls = _stub_brief_llm(monkeypatch)

    result = _delegate(project, ctx.user_id, assignee="designer")
    assert "which" in result.lower()
    assert calls == [], "ambiguous must never reach the brief call"

    from app.db.client import require_client

    assert require_client().table("project_delegations").select("id").execute().data == []


def test_brief_failure_no_partial_delivery(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project, assignee_id = _seed_project_with_assignee(ctx)

    def _boom(**kwargs):  # noqa: ARG001
        raise RuntimeError("simulated LLM failure")

    monkeypatch.setattr(project_delegation, "call_md", _boom)

    result = _delegate(project, ctx.user_id)
    assert "couldn't build the brief" in result

    from app.db.client import require_client

    assert require_client().table("project_delegations").select("id").execute().data == []
    assert (
        require_client().table("conversations").select("id").eq("kind", "individual").execute().data
        == []
    )


def test_handle_delegate_task_never_raises(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project, assignee_id = _seed_project_with_assignee(ctx)
    _stub_brief_llm(monkeypatch)

    def _boom(**kwargs):  # noqa: ARG001
        raise RuntimeError("simulated downstream DB failure")

    monkeypatch.setattr(project_delegation, "record_delegation", _boom)

    result = _delegate(project, ctx.user_id)  # must not raise
    assert "hit a problem" in result


# ── Genesis event (AC9/AC10 — the delegation-events genesis hook's blast radius on this file) ─────────


def test_delegation_emits_single_assigned_genesis(isolated_settings, monkeypatch):
    """A successful hand-off writes exactly one `assigned` `delegation_events`
    row for the new delegation, with `actor_user_id == assigner_user_id`."""
    ctx = company_client(monkeypatch)
    project, assignee_id = _seed_project_with_assignee(ctx)
    _stub_brief_llm(monkeypatch)

    result = _delegate(project, ctx.user_id)
    assert "Sent the brief" in result

    from app.db.client import require_client

    deleg = (
        require_client()
        .table("project_delegations")
        .select("id")
        .eq("project_id", project["id"])
        .execute()
        .data[0]
    )
    events = (
        require_client()
        .table("delegation_events")
        .select("*")
        .eq("delegation_id", deleg["id"])
        .execute()
        .data
    )
    assert len(events) == 1
    assert events[0]["event"] == "assigned"
    assert events[0]["actor_user_id"] == ctx.user_id


def test_genesis_failure_does_not_rollback_delegation(isolated_settings, monkeypatch):
    """A forced failure of the genesis `record_event` call must NOT roll
    back the delegation — the `project_delegations` row and the delivered
    brief turn still exist, and `handle_delegate_task` still returns its
    normal confirmation string."""
    ctx = company_client(monkeypatch)
    project, assignee_id = _seed_project_with_assignee(ctx)
    _stub_brief_llm(monkeypatch)

    def _boom(**kwargs):  # noqa: ARG001
        raise RuntimeError("simulated genesis-event failure")

    monkeypatch.setattr(project_delegation, "record_event", _boom)

    result = _delegate(project, ctx.user_id)
    assert "Sent the brief" in result, "the delegation must still succeed and report normally"

    from app.db.client import require_client

    delegations = (
        require_client()
        .table("project_delegations")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert len(delegations) == 1

    turns = (
        require_client()
        .table("conversation_turns")
        .select("*")
        .eq("conversation_id", delegations[0]["delivered_conversation_id"])
        .execute()
        .data
    )
    assert len(turns) == 1
    assert turns[0]["role"] == "assistant"

    events = (
        require_client()
        .table("delegation_events")
        .select("id")
        .eq("delegation_id", delegations[0]["id"])
        .execute()
        .data
    )
    assert events == [], "the forced-failing genesis event must never have been written"


# ── Ledger-create liveness (publish delegation.event on genesis) ──────────
# The emit route publishes a `delegation.event` on every later status change so
# the Task ledger updates live. Creation is the one transition that route never
# sees, so a fresh hand-off used to appear in the ledger only on the recipient's
# next refetch. `handle_delegate_task` now mirrors that publish on the genesis
# `assigned` — best-effort/no-rollback (AD-P22) — to BOTH parties' per-user
# channels. These tests prove the publish fires on create.


def test_delegation_create_publishes_event_to_both_parties(isolated_settings, monkeypatch):
    """A successful hand-off publishes exactly one `delegation.event` to the
    assigner's AND the assignee's per-user channel on creation — never the
    group channel — carrying only the shaped status-DTO whitelist."""
    ctx = company_client(monkeypatch)
    project, assignee_id = _seed_project_with_assignee(ctx)
    _stub_brief_llm(monkeypatch)

    # Shape the status DTO deterministically (the view's SQL is proven by the
    # real-DB delegation-events round-trip; here we prove the publish WIRING).
    monkeypatch.setattr(
        project_delegation, "status_dto",
        lambda did: {
            "delegation_id": did, "status": "assigned",
            "status_at": "2026-08-10T00:00:00Z", "task_summary": "Draft the pricing page",
        },
    )
    published: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        project_delegation, "publish_broadcast",
        lambda topic, event, payload: published.append((topic, event, payload)),
    )

    result = _delegate(project, ctx.user_id)
    assert "Sent the brief" in result

    events = [p for p in published if p[1] == "delegation.event"]
    assert len(events) == 2, published
    topics = {t for t, _e, _p in events}
    assert f"project:{project['id']}:user:{ctx.user_id}" in topics    # assigner
    assert f"project:{project['id']}:user:{assignee_id}" in topics    # assignee
    # A create is private to the two parties — never the group channel.
    assert f"project:{project['id']}" not in topics
    for _t, _e, payload in events:
        assert set(payload) == {"delegation_id", "status", "status_at", "task_summary"}
        assert payload["status"] == "assigned"


def test_delegation_self_assign_publishes_event(isolated_settings, monkeypatch):
    """Self-assign (assigner == assignee): the genesis publish still fires to
    that user's own per-user channel — the one-channel case."""
    ctx = company_client(monkeypatch)
    project, _ = _seed_project_with_assignee(ctx)
    _stub_brief_llm(monkeypatch)

    monkeypatch.setattr(
        project_delegation, "status_dto",
        lambda did: {
            "delegation_id": did, "status": "assigned",
            "status_at": "2026-08-10T00:00:00Z", "task_summary": "x",
        },
    )
    published: list[tuple[str, str]] = []
    monkeypatch.setattr(
        project_delegation, "publish_broadcast",
        lambda topic, event, payload: published.append((topic, event)),
    )

    # The assigner (full_name "Alex Assigner") is a member of their own project,
    # so a hand-off to "Alex" resolves to self.
    result = _delegate(project, ctx.user_id, assignee="Alex")
    assert "Sent the brief" in result

    event_topics = [t for t, e in published if e == "delegation.event"]
    assert event_topics, published
    assert all(t == f"project:{project['id']}:user:{ctx.user_id}" for t in event_topics)


def test_delegation_create_publish_failure_does_not_rollback(isolated_settings, monkeypatch):
    """The create-publish is best-effort (AD-P22): a raising `status_dto` or
    `publish_broadcast` must NOT roll back the delivered hand-off — the
    delegation row still exists and the handler still returns normally."""
    ctx = company_client(monkeypatch)
    project, _ = _seed_project_with_assignee(ctx)
    _stub_brief_llm(monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("simulated status_dto failure")

    monkeypatch.setattr(project_delegation, "status_dto", _boom)

    result = _delegate(project, ctx.user_id)
    assert "Sent the brief" in result, "a create-publish hiccup must never break the hand-off"

    from app.db.client import require_client

    delegations = (
        require_client()
        .table("project_delegations")
        .select("id")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert len(delegations) == 1


# ── Cost / observability (AC10/AC11) ──────────────────────────────────────


def test_delegation_emits_one_brief_cost_line(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project, assignee_id = _seed_project_with_assignee(ctx)
    _stub_brief_llm(monkeypatch)

    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        result = _delegate(project, ctx.user_id)
    assert "Sent the brief" in result

    brief_lines = [
        r.getMessage() for r in caplog.records if "projects.delegation.brief" in r.getMessage()
    ]
    assert len(brief_lines) == 1
    assert "est_cost_usd=" in brief_lines[0]

    caplog.clear()
    # A non-delegation outcome (no_match) never reaches the brief call —
    # no cost line at all.
    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        _delegate(project, ctx.user_id, assignee="Nobody Here")
    brief_lines2 = [
        r.getMessage() for r in caplog.records if "projects.delegation.brief" in r.getMessage()
    ]
    assert brief_lines2 == []


def test_no_brief_text_in_logs(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project, assignee_id = _seed_project_with_assignee(ctx)
    secret_task = "SECRET_TASK_DO_NOT_LOG the pricing revamp"
    _stub_brief_llm(monkeypatch, reply="SECRET_BRIEF_TEXT_DO_NOT_LOG the plan is set.")

    with caplog.at_level(logging.INFO):
        _delegate(project, ctx.user_id, task=secret_task)

    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRET_TASK_DO_NOT_LOG" not in joined
    assert "SECRET_BRIEF_TEXT_DO_NOT_LOG" not in joined


# ── Real-LLM / real-DB live tier ─────────────────────────────────────────
#
# Gated behind RUN_DELEGATE_TASK_LIVE=1 PLUS a real ANTHROPIC_API_KEY.
# Mutates real rows against a real (company, workspace, user) already
# seeded in the local rig — mirrors test_group_chat_turns_live.py's /
# test_project_group_gate.py's fixture shape.

_RUN_LIVE = os.getenv("RUN_DELEGATE_TASK_LIVE") == "1" and bool(os.getenv("ANTHROPIC_API_KEY"))

_LIVE_SKIP_REASON = (
    "needs a real local Supabase + a real ANTHROPIC_API_KEY — set "
    "RUN_DELEGATE_TASK_LIVE=1 with SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY/"
    "SUPABASE_JWT_SECRET/ANTHROPIC_API_KEY pointed at the local rig, the "
    "projects/chat/memory/delegations migrations applied"
)


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live delegation round-trip against a non-loopback "
        f"SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    if not _RUN_LIVE:
        pytest.skip("live tier disabled")
    return _sb()


@pytest.fixture(scope="module")
def fixture_ids(sb):
    companies = sb.table("companies").select("id").limit(1).execute().data
    assert companies, "no company row in the local rig — seed one before running this test"
    company_id = companies[0]["id"]

    workspaces = (
        sb.table("workspaces").select("id").eq("company_id", company_id).limit(1).execute().data
    )
    assert workspaces, f"no workspace for company {company_id}"
    workspace_id = workspaces[0]["id"]

    owners = (
        sb.table("company_members")
        .select("user_id, role")
        .eq("company_id", company_id)
        .in_("role", ["owner", "admin"])
        .limit(2)
        .execute()
        .data
    )
    assert len(owners) >= 2, (
        f"need >=2 owner/admin company_members rows for company {company_id} "
        "(one assigner, one assignee)"
    )
    assigner_id, assignee_id = owners[0]["user_id"], owners[1]["user_id"]

    sb.table("profiles").upsert(
        {"id": assigner_id, "email": f"{assigner_id}@example.invalid", "full_name": "Alexis Assigner", "role": "PM"}
    ).execute()
    sb.table("profiles").upsert(
        {
            "id": assignee_id,
            "email": f"{assignee_id}@example.invalid",
            # First token ("Fortune") is what the roster block + resolve_member's
            # name-match key both key off of — must match the name used to
            # address the delegation in the live turns below.
            "full_name": "Fortune Adeyemi",
            "role": "Designer",
        }
    ).execute()

    yield {
        "company_id": company_id,
        "workspace_id": workspace_id,
        "assigner_id": assigner_id,
        "assignee_id": assignee_id,
    }


def _bearer(user_id: str) -> dict[str, str]:
    from app.config import settings

    now = int(time.time())
    token = pyjwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": now + 3600},
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def client(fixture_ids):
    import app.main as main_mod
    from app.config import settings
    from fastapi.testclient import TestClient

    headers = _bearer(fixture_ids["assigner_id"])
    headers["X-Workspace-Id"] = fixture_ids["workspace_id"]
    headers["Origin"] = settings.origins_list[0]
    return TestClient(main_mod.app, headers=headers)


@pytest.fixture
def project_ids(sb):
    created: list[int] = []
    yield created
    for pid in created:
        sb.table("project_delegations").delete().eq("project_id", pid).execute()
        sb.table("projects").delete().eq("id", pid).execute()


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_delegation_delivers_turn_and_records_fact(client, sb, fixture_ids, project_ids):
    """(a) "@Sprntly send the pricing task to <assignee>" over the REAL
    route -> the REAL model calls delegate_task -> one real assistant turn
    lands in the assignee's individual conversation + one
    project_delegations row (AC1); the group gets one confirmation turn
    (AC2)."""
    project = client.post(
        "/v1/projects", json={"name": f"Live delegation {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])
    sb.table("project_members").upsert(
        {"project_id": project["id"], "user_id": fixture_ids["assignee_id"]}
    ).execute()

    first_name = "Fortune"  # matches fixture profile "Fortune Adeyemi"
    r = client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": f"@Sprntly please send the pricing task to {first_name}"},
    )
    assert r.status_code == 200, r.text

    turns = client.get(f"/v1/projects/{project['id']}/group/turns").json()["turns"]
    assert [t["role"] for t in turns] == ["user", "assistant"], (
        f"expected exactly one group confirmation turn — got {[t['role'] for t in turns]}"
    )
    assert turns[-1]["author_user_id"] is None

    delegations = (
        sb.table("project_delegations")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert len(delegations) == 1, (
        f"expected exactly one delegation fact — got {len(delegations)}. Group reply was: "
        f"{turns[-1]['content']!r}"
    )
    d = delegations[0]
    assert d["assigner_user_id"] == fixture_ids["assigner_id"]
    assert d["assignee_user_id"] == fixture_ids["assignee_id"]

    delivered = (
        sb.table("conversations")
        .select("*")
        .eq("id", d["delivered_conversation_id"])
        .execute()
        .data[0]
    )
    assert delivered["kind"] == "individual"
    assert delivered["user_id"] == fixture_ids["assignee_id"]
    assert delivered["project_id"] == project["id"]

    delivered_turn = (
        sb.table("conversation_turns")
        .select("*")
        .eq("id", d["delivered_turn_id"])
        .execute()
        .data[0]
    )
    assert delivered_turn["role"] == "assistant"
    assert delivered_turn["author_user_id"] is None
    assert delivered_turn["content"].strip() != ""


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_brief_uses_assigner_context_and_no_trailing_question(client, sb, fixture_ids, project_ids):
    """(AC7/AC8) The delivered brief names the assigner (role) and never
    ends on a question — proven against the REAL model's actual output,
    not a stub."""
    project = client.post(
        "/v1/projects", json={"name": f"Live brief content {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])
    sb.table("project_members").upsert(
        {"project_id": project["id"], "user_id": fixture_ids["assignee_id"]}
    ).execute()

    r = client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "@Sprntly hand the onboarding-flow redesign to Fortune"},
    )
    assert r.status_code == 200, r.text

    delegations = (
        sb.table("project_delegations")
        .select("*")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert len(delegations) == 1
    delivered_turn = (
        sb.table("conversation_turns")
        .select("content")
        .eq("id", delegations[0]["delivered_turn_id"])
        .execute()
        .data[0]
    )
    brief = delivered_turn["content"].strip()
    assert brief != ""
    assert not brief.endswith("?"), f"brief ended on a question: {brief!r}"
    assert "PM" in brief or "Assigner" in brief or "Alexis" in brief, (
        f"brief does not appear to name the assigner: {brief!r}"
    )


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_no_match_asks_group_no_dm_live(client, sb, fixture_ids, project_ids):
    """(c) An unresolvable assignee name -> the real model either declines
    to call the tool or the tool returns no_match -> no delegation row,
    no second individual conversation created for this project."""
    project = client.post(
        "/v1/projects", json={"name": f"Live no-match {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])

    r = client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "@Sprntly please send the pricing task to Zzyzx Nonexistent"},
    )
    assert r.status_code == 200, r.text

    delegations = (
        sb.table("project_delegations")
        .select("id")
        .eq("project_id", project["id"])
        .execute()
        .data
    )
    assert delegations == [], "an unresolvable assignee must never write a delegation fact"

    individual_convs = (
        sb.table("conversations")
        .select("id")
        .eq("project_id", project["id"])
        .eq("kind", "individual")
        .execute()
        .data
    )
    assert individual_convs == [], "an unresolvable assignee must never deliver a DM"
