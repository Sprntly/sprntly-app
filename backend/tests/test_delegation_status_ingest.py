"""Tests for `app/delegation_status_ingest.py::maybe_ingest_status` — the
inbound reply classifier + writer, and its wiring into `ask_job_runner.py`'s
`_run_sync` (the seam beside `maybe_promote_turn`).

Drives `maybe_ingest_status` directly against `FakeSupabaseClient`
(`isolated_settings`) with `delegation_status_ingest.call_json` stubbed —
fast and deterministic, proving the writer's CONTRACT (pre-filter, the
per-intent application, the soft-done contradiction rule, never-raises, one
cost line per call) rather than a real LLM classification decision.
`v_delegation_status` is a real Postgres view `FakeSupabaseClient` cannot
evaluate (see `db/delegation_events.py`'s own docstring and
`test_delegation_events_api.py`'s identical caveat) — `list_status_for_assignee`
is monkeypatched to a data-driven equivalent that reacts to real
`record_event` inserts, mirroring `test_delegation_events_api.py`'s
`_install_fake_ledger_views`.
"""
from __future__ import annotations

import logging
import re
import uuid

import pytest

from app import ask_job_runner as ajr
from app import delegation_status_ingest as ingest
from app import project_delegation
from app.db import conversations as conversations_db
from app.db import delegation_events as delegation_events_db
from app.db import delegation_followups as delegation_followups_db
from app.db.project_delegations import record_delegation
from tests._company_helpers import company_client


# ── Fixtures / helpers ──────────────────────────────────────────────────


def _create_project(ctx, *, name: str = "Ingest project") -> dict:
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
        "delivered_conversation_id": deleg_row.get("delivered_conversation_id"),
        "delivered_turn_id": deleg_row.get("delivered_turn_id"),
        "status": latest["event"] if latest else "assigned",
        "status_at": latest["created_at"] if latest else deleg_row["created_at"],
    }


def _install_fake_assignee_view(monkeypatch) -> None:
    """Stand-in for `list_status_for_assignee` — see module docstring for
    why the real one (a Postgres view) can't run against
    `FakeSupabaseClient`. Mirrors `test_delegation_events_api.py`'s
    `_install_fake_ledger_views`."""
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
) -> tuple[int, int]:
    """A delegation whose brief was delivered into the assignee's own
    individual chat — the pre-filter needs `delivered_conversation_id` to
    match the conversation the reply arrived in. Returns
    `(delegation_id, conversation_id)`."""
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


def _stub_classify_llm(monkeypatch, **overrides):
    """Stub the ONE LLM call site `maybe_ingest_status` uses
    (`app.delegation_status_ingest.call_json`). `state["calls"]` is the
    no-LLM-call assertion point (AC10)."""
    state: dict = {
        "calls": [],
        "delegation_id": None,
        "intent": "none",
        "stated_completion": None,
        "proposed_next_check_in": None,
        "raise_error": False,
    }
    state.update(overrides)

    def _fake_call_json(*, system, user, model, schema=None, meta_out=None, **kwargs):  # noqa: ARG001
        state["calls"].append({"system": system, "user": user})
        if state["raise_error"]:
            raise RuntimeError("simulated classifier failure")
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model, "input_tokens": 40, "output_tokens": 15,
                    "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                }
            )
        return {
            "delegation_id": state["delegation_id"],
            "intent": state["intent"],
            "stated_completion": state["stated_completion"],
            "proposed_next_check_in": state["proposed_next_check_in"],
        }

    monkeypatch.setattr(ingest, "call_json", _fake_call_json)
    return state


# ── Prompt/schema property tests (LLM-facing) ────────────────────────────


def test_classify_prompt_names_every_intent_and_negative_space():
    system = ingest._CLASSIFY_SYSTEM.lower()
    for intent in (
        "in_progress", "done_explicit", "done_inferred", "timeline", "blocked", "cant_do", "none",
    ):
        assert f'"{intent}"' in system, f"prompt must name the {intent} intent explicitly"

    sentences = [s for s in re.split(r"(?<=[.!?])\s+", ingest._CLASSIFY_SYSTEM.strip()) if s]
    assert len(sentences) >= 6, "prompt must be substantive, not a one-liner"

    # Negative-space: "none" must be explicitly defined as the catch-all for
    # an unrelated/off-topic reply, not merely listed alongside the others.
    assert "not about any open task" in system or "not about any task" in system
    assert "never invent a delegation_id" in system or "never invent" in system

    weak_prompt = "Classify the reply as in_progress, done, or blocked."
    assert '"done_inferred"' not in weak_prompt
    assert "never invent" not in weak_prompt.lower()


def test_classify_schema_shape():
    props = ingest._CLASSIFY_SCHEMA["properties"]
    assert set(props) == {
        "delegation_id", "intent", "stated_completion", "proposed_next_check_in",
    }
    assert ingest._CLASSIFY_SCHEMA["required"] == list(props)
    assert ingest._CLASSIFY_SCHEMA["additionalProperties"] is False
    assert set(props["intent"]["enum"]) == {
        "in_progress", "done_explicit", "done_inferred", "timeline", "blocked", "cant_do", "none",
    }


# ── Cost guard / pre-filter (AC10) ───────────────────────────────────────


def test_ingest_no_open_delegation_skips_llm(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    state = _stub_classify_llm(monkeypatch)
    _install_fake_assignee_view(monkeypatch)

    ingest.maybe_ingest_status(project["id"], 12345, ctx.user_id, "sounds good")
    assert state["calls"] == [], "no open delegation delivered into this conversation -> zero LLM calls"


def test_ingest_no_open_delegation_wrong_conversation_skips_llm(isolated_settings, monkeypatch):
    """An open delegation exists, but was delivered into a DIFFERENT
    conversation than the one the reply arrived in — still zero LLM calls.
    (Regression: this is the strict match a real, non-null
    `delivered_conversation_id` must still enforce — the NULL-linkage
    relaxation below must never widen THIS case.)"""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    state = _stub_classify_llm(monkeypatch)

    _deleg_id, conv_id = _seed_open_delegation(ctx, project["id"], assignee_id)

    ingest.maybe_ingest_status(project["id"], conv_id + 999, assignee_id, "working on it")
    assert state["calls"] == []


# ── Link-less delegations (`delivered_conversation_id IS NULL`) ──────────
#
# Pre-existing gap (found in a later ticket's live-verify, this feature's
# own scope): a delegation whose delivered conversation row was deleted
# (FK `on delete set null`) and recreated goes link-less forever under a
# strict `== conversation_id` match — a real "done" reply then produces
# ZERO classifier activity, silently. See the module docstring's "Linkage
# gap" note for the mechanism and why relaxing this one case is safe.


def _seed_open_delegation_no_linkage(
    ctx, project_id, assignee_id, *, task_summary: str = "Draft the pricing page"
) -> tuple[int, int]:
    """Same as `_seed_open_delegation`, except the row's
    `delivered_conversation_id` is NULL — simulating the FK `on delete set
    null` firing after the originally-delivered-into conversation was
    deleted. Still returns the assignee's CURRENT individual project chat
    id, since that is what `ask_job_runner.py` always passes as
    `conversation_id` for a project-scoped ask."""
    conv = conversations_db.create_individual_project_chat(project_id, assignee_id)
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
    return deleg["id"], conv["id"]


def test_ingest_done_explicit_classifies_when_delivered_conversation_id_is_null(
    isolated_settings, monkeypatch,
):
    """The bug this ticket fixes: a real 'done' reply on an open delegation
    with a link-less `delivered_conversation_id` must still classify and
    record completion — mirrors `test_ingest_done_explicit_emits_completed`
    exactly, except the seeded row has no linkage."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation_no_linkage(ctx, project["id"], assignee_id)
    state = _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent="done_explicit")

    ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "yes, I've finished it")

    assert len(state["calls"]) == 1, "a link-less open delegation must still reach the classifier"
    events = delegation_events_db.list_events(deleg_id)
    assert [e["event"] for e in events] == ["completed"]


def test_ingest_fast_path_still_zero_llm_calls_with_no_open_delegation(
    isolated_settings, monkeypatch,
):
    """AC10 regression: the NULL-linkage relaxation must never widen the
    pre-filter beyond THIS assignee's OPEN rows in THIS project — an
    assignee with no open delegation at all in this project still short-
    circuits before any LLM call, exactly like the pre-existing fast path."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    state = _stub_classify_llm(monkeypatch)

    conv = conversations_db.create_individual_project_chat(project["id"], assignee_id)
    ingest.maybe_ingest_status(project["id"], conv["id"], assignee_id, "yes, I've finished it")

    assert state["calls"] == [], "no open delegation for this assignee -> zero LLM calls"


def test_ingest_requester_own_reply_does_not_complete_their_outbound_delegation(
    isolated_settings, monkeypatch,
):
    """No cross-user broadening: a link-less delegation is scoped to the
    ASSIGNEE by `list_status_for_assignee` (`assignee_user_id == user_id`)
    — the REQUESTER replying in their own individual project chat must
    never match it, even though the row has no `delivered_conversation_id`
    to strictly rule it out by conversation."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, _assignee_conv_id = _seed_open_delegation_no_linkage(ctx, project["id"], assignee_id)
    state = _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent="done_explicit")

    requester_conv = conversations_db.create_individual_project_chat(project["id"], ctx.user_id)
    ingest.maybe_ingest_status(
        project["id"], requester_conv["id"], ctx.user_id, "yes, I've finished it"
    )

    assert state["calls"] == [], (
        "the requester is not the assignee of any open delegation -> zero LLM calls, "
        "regardless of the assignee's row having a null delivered_conversation_id"
    )
    assert delegation_events_db.list_events(deleg_id) == []


# ── Application — in_progress (AC11) ─────────────────────────────────────


def test_ingest_in_progress_emits_event_and_reschedules(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation(ctx, project["id"], assignee_id)
    _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent="in_progress")

    ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "started on it")

    events = delegation_events_db.list_events(deleg_id)
    assert [e["event"] for e in events] == ["in_progress"]
    assert events[0]["actor_user_id"] == assignee_id

    followup = delegation_followups_db.get_followup(deleg_id)
    assert followup is not None
    assert followup["pending_done_since"] is None
    assert followup["next_check_in"] is not None  # floor-clamped, non-null


# ── Application — done_explicit / done_inferred (AC12) ───────────────────


def test_ingest_done_explicit_emits_completed(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation(ctx, project["id"], assignee_id)
    _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent="done_explicit")

    ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "all done")

    events = delegation_events_db.list_events(deleg_id)
    assert [e["event"] for e in events] == ["completed"]


def test_ingest_skips_already_completed_row_no_double_fire(isolated_settings, monkeypatch):
    """No-double-completion guarantee. Once the deterministic `complete_task`
    tool path has written `completed`, the row is no longer in `OPEN_STATES`,
    so this background classifier's pre-filter drops it and NEVER reaches the
    LLM — the two completion paths can't both fire on the same message (which
    would double-complete and double-email)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation(ctx, project["id"], assignee_id)
    # Simulate the `complete_task` tool path having already completed it.
    delegation_events_db.record_event(
        delegation_id=deleg_id, event="completed", actor_user_id=assignee_id
    )
    state = _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent="done_explicit")

    ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "yep, all done")

    assert state["calls"] == []  # pre-filter dropped the completed row; zero LLM calls
    # And no SECOND completed event was recorded by this path.
    events = [e["event"] for e in delegation_events_db.list_events(deleg_id)]
    assert events == ["completed"]


def test_ingest_done_inferred_is_soft(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation(ctx, project["id"], assignee_id)
    _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent="done_inferred")

    ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "shipped that")

    assert delegation_events_db.list_events(deleg_id) == [], "done_inferred must never emit an event"

    followup = delegation_followups_db.get_followup(deleg_id)
    assert followup is not None
    assert followup["pending_done_since"] is not None

    # One assistant turn posted into the REPLIER's own individual chat.
    turns = conversations_db.list_individual_turns(conv_id, assignee_id)
    confirm_turns = [t for t in turns if t["content"] == ingest._SOFT_CONFIRM_TEXT]
    assert len(confirm_turns) == 1


# ── Application — timeline (AC13) ─────────────────────────────────────────


def test_ingest_timeline_sets_expected_and_next(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation(ctx, project["id"], assignee_id)
    # A stated timeline sets next_check_in = that instant, but only when it is
    # beyond the MIN_INTERVAL (24h) re-ping floor — `respect_stated_timeline`
    # clamps anything sooner up to the floor. Compute the stated instant well
    # past that floor RELATIVE to today so the test doesn't rot as real time
    # approaches a hardcoded date (the original fixed "2026-08-21T09:00" fell
    # inside the 24h floor once the clock reached 2026-08-20).
    from datetime import datetime, timedelta, timezone

    stated_dt = (datetime.now(timezone.utc) + timedelta(days=30)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    stated = stated_dt.isoformat()
    _prefix = stated_dt.strftime("%Y-%m-%dT09:00:00")
    _stub_classify_llm(
        monkeypatch, delegation_id=deleg_id, intent="timeline", stated_completion=stated,
    )

    ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "I'll have it Friday")

    assert delegation_events_db.list_events(deleg_id) == []
    followup = delegation_followups_db.get_followup(deleg_id)
    assert followup["expected_completion"].startswith(_prefix)
    assert followup["next_check_in"].startswith(_prefix)
    assert followup["pending_done_since"] is None


# ── Application — blocked / cant_do route-back (AC14) ────────────────────


def test_ingest_blocked_routes_to_requester(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation(
        ctx, project["id"], assignee_id, task_summary="Draft the onboarding flow"
    )
    _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent="blocked")

    ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "I'm stuck on this")

    assert delegation_events_db.list_events(deleg_id) == []
    followup = delegation_followups_db.get_followup(deleg_id)
    assert followup["next_check_in"] is not None

    requester_conv = conversations_db.get_individual_project_chat(project["id"], ctx.user_id)
    assert requester_conv is not None
    turns = conversations_db.list_individual_turns(requester_conv["id"], ctx.user_id)
    assert len(turns) == 1
    assert "blocked on: Draft the onboarding flow" in turns[0]["content"]
    assert not turns[0]["content"].strip().endswith("?")


def test_ingest_cant_do_routes_to_requester(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation(ctx, project["id"], assignee_id)
    _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent="cant_do")

    ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "I can't take this on")

    assert delegation_events_db.list_events(deleg_id) == []
    requester_conv = conversations_db.get_individual_project_chat(project["id"], ctx.user_id)
    turns = conversations_db.list_individual_turns(requester_conv["id"], ctx.user_id)
    assert len(turns) == 1
    assert "can't take this on" in turns[0]["content"]


# ── Realtime publish on lifecycle change (part 2, spec 3a) ────────────────
#
# The creation path already publishes `delegation.event` + `brief.delivered`
# (`project_delegation.handle_delegate_task`, proven in
# `test_project_delegation.py::test_delegation_create_publishes_event_to_both_parties`).
# This module's own writes — a reply moving the derived status, or routing a
# note back to the requester — published NOTHING before this change, so the
# Task ledger and the requester's own chat only ever moved on their next
# reconcile/refetch. These tests prove the SAME wiring now fires here,
# reusing the exact `project_delegation.publish_broadcast` interception
# point the creation-path tests already use — no new channel, no new event
# name.


def _capture_broadcasts(monkeypatch) -> list[tuple[str, str, dict]]:
    published: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        project_delegation, "publish_broadcast",
        lambda topic, event, payload: published.append((topic, event, payload)),
    )
    return published


def test_ingest_in_progress_publishes_delegation_event_to_both_parties(
    isolated_settings, monkeypatch,
):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation(ctx, project["id"], assignee_id)
    _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent="in_progress")
    # `v_delegation_status` is a real Postgres view `FakeSupabaseClient` cannot
    # evaluate (module docstring) — shape the DTO deterministically, the same
    # way `test_project_delegation.py`'s own create-publish test does.
    monkeypatch.setattr(
        delegation_events_db, "status_dto",
        lambda did: {
            "delegation_id": did, "status": "in_progress",
            "status_at": "2026-08-21T00:00:00Z", "task_summary": "Draft the pricing page",
        },
    )
    published = _capture_broadcasts(monkeypatch)

    ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "started on it")

    events = [p for p in published if p[1] == "delegation.event"]
    assert len(events) == 2, published
    topics = {t for t, _e, _p in events}
    assert f"project:{project['id']}:user:{ctx.user_id}" in topics    # assigner
    assert f"project:{project['id']}:user:{assignee_id}" in topics    # assignee
    assert f"project:{project['id']}" not in topics                  # never the group channel
    for _t, _e, payload in events:
        assert payload["status"] == "in_progress"


def test_ingest_done_explicit_publishes_delegation_event_and_brief_delivered(
    isolated_settings, monkeypatch,
):
    """Completion is BOTH a status change (the ledger DTO) AND a completion
    notice (the "✓ … finished" turn posted into the requester's own chat) —
    both must go out live."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation(
        ctx, project["id"], assignee_id, task_summary="Draft the pricing page",
    )
    _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent="done_explicit")
    monkeypatch.setattr(
        delegation_events_db, "status_dto",
        lambda did: {
            "delegation_id": did, "status": "completed",
            "status_at": "2026-08-21T00:00:00Z", "task_summary": "Draft the pricing page",
        },
    )
    published = _capture_broadcasts(monkeypatch)

    ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "all done")

    status_events = [p for p in published if p[1] == "delegation.event"]
    assert len(status_events) == 2, published
    assert all(p[2]["status"] == "completed" for p in status_events)

    # The completion NOTICEs live-append too, reusing the exact
    # `brief.delivered` mechanic a fresh brief delivery already uses: ONE into
    # the requester's own chat (`notify_requester_task_completed`) AND ONE into
    # the ASSIGNEE's own chat (the safety-net confirmation — so a completion
    # the deterministic `complete_task` gate misses still confirms back to the
    # person who reported it done).
    notices = [p for p in published if p[1] == "brief.delivered"]
    assert len(notices) == 2, published
    by_topic = {p[0]: p[2]["content"] for p in notices}
    assert "finished" in by_topic[f"project:{project['id']}:user:{ctx.user_id}"]
    assignee_notice = by_topic[f"project:{project['id']}:user:{assignee_id}"]
    assert "Marked done" in assignee_notice


def test_ingest_done_explicit_confirms_to_the_assignee(isolated_settings, monkeypatch):
    """Safety net: the background `done_explicit` path (which catches the
    completion phrasings the deterministic `complete_task` gate misses) posts a
    confirmation into the ASSIGNEE's OWN chat, carrying the task summary — so
    the person who reported the task done always sees it registered, not just
    the requester."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation(
        ctx, project["id"], assignee_id, task_summary="Draft the pricing page",
    )
    _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent="done_explicit")
    monkeypatch.setattr(
        delegation_events_db, "status_dto",
        lambda did: {
            "delegation_id": did, "status": "completed",
            "status_at": "2026-08-21T00:00:00Z", "task_summary": "Draft the pricing page",
        },
    )
    published = _capture_broadcasts(monkeypatch)

    ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "wrapped that up")

    notices = [p for p in published if p[1] == "brief.delivered"]
    assignee_notices = [
        p for p in notices if p[0] == f"project:{project['id']}:user:{assignee_id}"
    ]
    assert len(assignee_notices) == 1, published
    content = assignee_notices[0][2]["content"]
    assert "Marked done" in content
    assert "Draft the pricing page" in content  # carries the task summary


def test_ingest_blocked_publishes_brief_delivered_to_requester(isolated_settings, monkeypatch):
    """No status transition on `blocked` (the row stays `assigned`/
    `in_progress`) — only the route-back TURN needs to go out live, reusing
    the same `brief.delivered` mechanic a fresh brief delivery uses."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation(ctx, project["id"], assignee_id)
    _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent="blocked")
    published = _capture_broadcasts(monkeypatch)

    ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "I'm stuck on this")

    assert [p[1] for p in published if p[1] == "delegation.event"] == []
    notices = [p for p in published if p[1] == "brief.delivered"]
    assert len(notices) == 1, published
    assert notices[0][0] == f"project:{project['id']}:user:{ctx.user_id}"
    assert "blocked" in notices[0][2]["content"]


def test_ingest_done_inferred_soft_confirm_publishes_to_the_replier(
    isolated_settings, monkeypatch,
):
    """The soft-confirm turn lands in the REPLIER's own chat (not the
    requester's) — the live-append must follow the same party."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation(ctx, project["id"], assignee_id)
    _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent="done_inferred")
    published = _capture_broadcasts(monkeypatch)

    ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "shipped that")

    notices = [p for p in published if p[1] == "brief.delivered"]
    assert len(notices) == 1, published
    assert notices[0][0] == f"project:{project['id']}:user:{assignee_id}"
    assert notices[0][2]["content"] == ingest._SOFT_CONFIRM_TEXT


def test_status_publish_failure_never_breaks_the_write(isolated_settings, monkeypatch, caplog):
    """Best-effort (AD-P22), same posture as the creation path's own
    `test_delegation_create_publish_failure_does_not_rollback`: a raising
    `status_dto` must not stop the `delegation_events` write or the
    `delegation_followups` upsert that follows it."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation(ctx, project["id"], assignee_id)
    _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent="in_progress")

    def _boom(_did):
        raise RuntimeError("simulated status_dto failure")

    monkeypatch.setattr(delegation_events_db, "status_dto", _boom)

    with caplog.at_level(logging.WARNING):
        ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "started on it")

    events = delegation_events_db.list_events(deleg_id)
    assert [e["event"] for e in events] == ["in_progress"], (
        "a publish hiccup must never roll back the already-committed event write"
    )
    followup = delegation_followups_db.get_followup(deleg_id)
    assert followup is not None and followup["next_check_in"] is not None


# ── none / unknown delegation_id (AC15) ──────────────────────────────────


def test_ingest_none_or_unknown_id_noops(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation(ctx, project["id"], assignee_id)

    # Preset a pending_done_since to prove "none" leaves it alone.
    from datetime import datetime, timezone

    preset = datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)
    delegation_followups_db.upsert_followup(deleg_id, pending_done_since=preset)

    _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent="none")
    ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "morning!")
    assert delegation_events_db.list_events(deleg_id) == []
    followup = delegation_followups_db.get_followup(deleg_id)
    assert followup["pending_done_since"] is not None, "'none' must never clear pending_done_since"

    # Unknown delegation_id (not in the replier's open set).
    _stub_classify_llm(monkeypatch, delegation_id=999_999_999, intent="in_progress")
    ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "on it")
    assert delegation_events_db.list_events(deleg_id) == []
    followup2 = delegation_followups_db.get_followup(deleg_id)
    assert followup2["pending_done_since"] is not None, (
        "an unmatched delegation_id must never touch this delegation's row"
    )


# ── Soft-done contradiction rule (AC15b) ──────────────────────────────────


def _preset_pending_done(deleg_id: int) -> None:
    from datetime import datetime, timezone

    delegation_followups_db.upsert_followup(
        deleg_id, pending_done_since=datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)
    )


@pytest.mark.parametrize("intent", ["in_progress", "timeline", "blocked", "cant_do", "done_explicit"])
def test_ingest_status_changing_intent_clears_pending_done(isolated_settings, monkeypatch, intent):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation(ctx, project["id"], assignee_id)
    _preset_pending_done(deleg_id)

    kwargs = {}
    if intent == "timeline":
        kwargs["stated_completion"] = "2026-08-21T09:00:00+00:00"
    _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent=intent, **kwargs)

    ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "a status-changing reply")

    followup = delegation_followups_db.get_followup(deleg_id)
    assert followup["pending_done_since"] is None, f"{intent} must clear pending_done_since"


def test_ingest_done_inferred_sets_not_clears_pending_done(isolated_settings, monkeypatch):
    """The one exception: `done_inferred` is what SETS the marker in the
    first place, never clears an existing one to None."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation(ctx, project["id"], assignee_id)
    _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent="done_inferred")

    ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "shipped it")

    followup = delegation_followups_db.get_followup(deleg_id)
    assert followup["pending_done_since"] is not None


# ── Never raises (AC16) ───────────────────────────────────────────────────


def test_ingest_never_raises_on_llm_error(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    _deleg_id, conv_id = _seed_open_delegation(ctx, project["id"], assignee_id)
    _stub_classify_llm(monkeypatch, raise_error=True)

    with caplog.at_level(logging.WARNING):
        result = ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "hi")
    assert result is None  # must not raise

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    ingest_warnings = [r for r in warnings if "delegation_status_ingest" in r.getMessage()]
    assert len(ingest_warnings) == 1
    assert f"project_id={project['id']}" in ingest_warnings[0].getMessage()


def test_ingest_never_raises_on_record_event_error(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation(ctx, project["id"], assignee_id)
    _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent="in_progress")

    def _boom(**kwargs):  # noqa: ARG001
        raise RuntimeError("simulated db failure")

    monkeypatch.setattr(delegation_events_db, "record_event", _boom)

    result = ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, "on it")
    assert result is None  # must not raise


def test_ingest_never_raises_on_null_replier(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    state = _stub_classify_llm(monkeypatch)
    _install_fake_assignee_view(monkeypatch)

    result = ingest.maybe_ingest_status(project["id"], 1, None, "hi")
    assert result is None
    assert state["calls"] == []


# ── Cost log (AC18) ────────────────────────────────────────────────────────


def test_ingest_emits_one_cost_line(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    assignee_id = _seed_assignee(project["id"])
    _install_fake_assignee_view(monkeypatch)
    deleg_id, conv_id = _seed_open_delegation(ctx, project["id"], assignee_id)
    secret_reply = "SECRET_REPLY_DO_NOT_LOG working on it"
    _stub_classify_llm(monkeypatch, delegation_id=deleg_id, intent="in_progress")

    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        ingest.maybe_ingest_status(project["id"], conv_id, assignee_id, secret_reply)

    cost_lines = [
        r.getMessage() for r in caplog.records if "projects.delegation.status_ingest" in r.getMessage()
    ]
    assert len(cost_lines) == 1
    assert f"project_id={project['id']}" in cost_lines[0]
    assert f"delegation_id={deleg_id}" in cost_lines[0]
    assert "status=complete" in cost_lines[0]

    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRET_REPLY_DO_NOT_LOG" not in joined


# ── Seam wiring (AC17) ─────────────────────────────────────────────────────


def _payload(answer: str) -> dict:
    return {"answer": answer, "key_points": [], "citations": [], "confidence": 0.8, "unanswered": ""}


def test_hook_skips_for_non_project_ask(isolated_settings, monkeypatch):
    """`project_id=None` invokes neither `maybe_promote_turn` nor
    `maybe_ingest_status`; `project_id` set invokes both, with
    `maybe_ingest_status` wired to `(project_id, conversation_id, user_id,
    question)` — byte-for-byte unchanged path for the non-project ask."""
    import app.project_memory as pm

    promote_calls: list = []
    ingest_calls: list = []
    monkeypatch.setattr(pm, "maybe_promote_turn", lambda *a, **kw: promote_calls.append(a) or None)
    monkeypatch.setattr(ajr, "complete_ask_job", lambda i, p: None)
    monkeypatch.setattr(ajr, "is_ask_cancelled", lambda i: False)
    monkeypatch.setattr(ajr.qa_agent, "answer", lambda **kw: _payload("hi"))
    monkeypatch.setattr(ingest, "maybe_ingest_status", lambda *a, **kw: ingest_calls.append(a) or None)
    # Both hooks re-key off context_source.params.project_id now (not the legacy
    # top-level project_id). Scope resolution is orthogonal to the hook keying
    # here, and would 404 on this synthetic project — stub it out.
    monkeypatch.setattr(ajr, "resolve_context_scope", lambda *a, **k: None)

    import asyncio

    asyncio.run(
        ajr.run_ask_job(
            ask_id=1, enterprise_id="c1", question="q", dataset="d", conversation_id=5,
        )
    )
    assert promote_calls == [], "default project_id=None must fire nothing (promotion)"
    assert ingest_calls == [], "default project_id=None must fire nothing (ingestion)"

    asyncio.run(
        ajr.run_ask_job(
            ask_id=2, enterprise_id="c1", question="q", dataset="d",
            conversation_id=5, user_id="replier-1",
            context_source={"kind": "project", "params": {"project_id": 9, "surface": "private"}},
        )
    )
    assert len(promote_calls) == 1
    assert len(ingest_calls) == 1
    assert ingest_calls[0] == (9, 5, "replier-1", "q")

    # The pre-existing promotion call must still be intact, unaltered by
    # this addition.
    import pathlib

    src = pathlib.Path(__file__).parent.parent / "app" / "ask_job_runner.py"
    text = src.read_text()
    assert text.count("maybe_promote_turn(") >= 1
