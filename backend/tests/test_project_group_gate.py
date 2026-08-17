"""Tests for `app/project_group_gate.py` (the smart-interjection
should-respond classifier) and its wiring into
`routes/projects.py::post_group_turn_route`.

Covers:
  - the mention path stays deterministic — no classifier call, no
    `interjection_gate` cost line (AC1)
  - a non-mention turn the classifier decides is agent-directed triggers
    exactly one reply (AC2)
  - a non-mention human-to-human turn produces no reply (AC3)
  - `should_respond` never raises: a forced classifier failure defaults to
    stay-out without ever blocking the post (AC4, mutation-proofed)
  - the cheap pre-filter skips the classifier entirely for a trivial
    acknowledgement (AC5)
  - at most one `interjection_gate` cost line per non-mention turn, none
    on the mention path, no turn content in any log line (AC6)
  - isolation: the gate's only context source (`list_group_turns`) already
    refuses a non-`kind='group'` conversation id (AC7)
  - no user-facing toggle anywhere in this ticket's surface (AC8)
  - prompt property: explicit negative-space / stay-out rules (AC2 note)

Most of this suite mocks the classifier at the module seam
(`app.project_group_gate.call_json`) against the in-memory fake Supabase
(`isolated_settings`) — fast and deterministic, proving the gate's
CONTRACT (pre-filter bound, cost line shape, never-raises) rather than
that a real model will always honor the prompt's prose rules.

Two tests need the REAL classifier (and, when it decides to respond, the
REAL reply) — a stub masks whether the should-respond decision is actually
connected end to end
([[feedback_stubbed-e2e-masks-loop-behaviour]]):

  - `test_live_multi_human_project_relevant_message_interjects` / `test_
    gate_live_stays_out_of_human_backforth` — the real, project-aware
    classifier's actual DECISION on a project-relevant vs. human-to-human
    turn, against the real local Supabase rig.

Both are gated behind `RUN_INTERJECTION_GATE_LIVE=1` PLUS a real
`ANTHROPIC_API_KEY`, mirroring `test_group_chat_turns_live.py`'s /
`test_project_memory_promotion.py`'s rig-gating shape. Run them with:

    RUN_INTERJECTION_GATE_LIVE=1 \\
        pytest tests/test_project_group_gate.py -m integration
"""
from __future__ import annotations

import logging
import os
import time
import uuid

import jwt as pyjwt
import pytest

from app import project_group_gate
from app import project_memory
from app.db import projects as projects_db
from app.routes import projects as projects_route
from tests._company_helpers import company_client


def _create_project(ctx, *, name: str = "Interjection gate project") -> dict:
    return ctx.client.post("/v1/projects", json={"name": name}).json()


def _stub_reply_path(monkeypatch, *, reply: str = "On it — taking a look.") -> list[dict]:
    """Stub BOTH downstream calls a successful `_respond_as_group_agent`
    makes (the reply itself — patched ONE LEVEL BELOW the unified answer
    engine, at `app.llm.run_tool_loop`, RELOCATED from the pre-collapse
    call this stub used to patch — so the REAL `qa_agent.answer` /
    `qa_agent._try_scoped_tool_answer` wrapper still runs, including its
    cost-log line, exactly as it did pre-collapse; and the memory-promotion
    classifier fired afterwards, `app.project_memory.call_json`) so a test
    that lets the gate say `respond=true` doesn't also reach Anthropic for
    those two unrelated surfaces. The fake reply does NOT invoke
    `dispatch(...)` — none of these tests exercise delegation. Returns the
    list of reply calls made, each carrying the SAME `system`/`user` kwargs
    `run_tool_loop` always received, so every downstream
    `reply_calls[0]["system"]` assertion keeps working unchanged."""
    calls: list[dict] = []

    def _fake_run_tool_loop(  # noqa: ARG001
        *, system, user, tools, dispatch, model, meta_out=None, **kwargs
    ):
        calls.append({"system": system, "user": user, "model": model})
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model, "input_tokens": 40, "output_tokens": 10,
                    "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                }
            )
        return reply

    monkeypatch.setattr("app.llm.run_tool_loop", _fake_run_tool_loop)
    monkeypatch.setattr(
        project_memory, "call_json",
        lambda **kw: {"should_promote": False, "insight": ""},  # noqa: ARG005
    )
    return calls


def _fake_gate_call_json(respond: bool):
    def _inner(*, system, user, model, schema=None, meta_out=None, **kwargs):  # noqa: ARG001
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model,
                    "input_tokens": 55,
                    "output_tokens": 4,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                }
            )
        return {"respond": respond}
    return _inner


# ── Deterministic path (AC1) ────────────────────────────────────────────


def test_mention_bypasses_gate(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _stub_reply_path(monkeypatch)

    gate_calls: list[dict] = []
    monkeypatch.setattr(
        project_group_gate, "call_json",
        lambda **kw: gate_calls.append(kw) or {"respond": True},  # noqa: ARG005
    )

    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        r = ctx.client.post(
            f"/v1/projects/{project['id']}/group/turns",
            json={"content": "@Sprntly please delegate the export review to Fortune"},
        )
    assert r.status_code == 200
    assert gate_calls == [], "an @Sprntly mention must never consult the classifier"

    lines = [rec.getMessage() for rec in caplog.records]
    assert not any("interjection_gate" in line for line in lines)
    assert any("mention_reply" in line for line in lines)

    from app.db import conversations as conversations_db

    conv = conversations_db.get_group_chat(project["id"])
    turns = conversations_db.list_group_turns(conv["id"])
    assert [t["role"] for t in turns] == ["user", "assistant"]


# ── Solo-project auto-respond (bypasses the gate like a mention) ────────
#
# A project with exactly ONE human member has nobody else the turn could be
# addressed to, so an unaddressed opener should trigger the agent instead of
# "staying out" — same deterministic bypass as the @mention path, just
# reached via `_is_solo_project` instead of the `@Sprntly` regex. Multi-human
# projects fall through unchanged to the `should_respond` gate.


def test_solo_project_bypasses_gate_with_unaddressed_turn(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)  # single human member (the creator)
    reply_calls = _stub_reply_path(monkeypatch)

    gate_calls: list[dict] = []
    monkeypatch.setattr(
        project_group_gate, "call_json",
        lambda **kw: gate_calls.append(kw) or {"respond": True},  # noqa: ARG005
    )

    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        r = ctx.client.post(
            f"/v1/projects/{project['id']}/group/turns",
            json={"content": "quick one — can we assign the export review to Fortune?"},
        )
    assert r.status_code == 200
    assert gate_calls == [], "a solo project must never consult the classifier"
    assert len(reply_calls) == 1, "a solo project must always get a reply"

    # The solo-specific addressing note reached the reply prompt.
    assert "only non-human member" in reply_calls[0]["system"]

    from app.db import conversations as conversations_db

    conv = conversations_db.get_group_chat(project["id"])
    turns = conversations_db.list_group_turns(conv["id"])
    assert [t["role"] for t in turns] == ["user", "assistant"]


def test_multi_human_project_still_routes_through_gate(isolated_settings, monkeypatch):
    """The SAME unaddressed content in a project with a SECOND human member
    must NOT take the solo shortcut — it still consults `should_respond`,
    byte-for-byte the pre-existing gate path."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    projects_db.add_member(project["id"], "second-human")
    reply_calls = _stub_reply_path(monkeypatch)

    gate_calls: list[dict] = []
    monkeypatch.setattr(
        project_group_gate, "call_json",
        lambda **kw: gate_calls.append(kw) or {"respond": False},  # noqa: ARG005
    )

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "is anyone able to help debug the deploy pipeline today?"},
    )
    assert r.status_code == 200
    assert len(gate_calls) == 1, "a multi-human project must still consult the classifier"
    assert reply_calls == [], "the gate said stay-out, so no reply"


def test_is_solo_project_counts_only_humans(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.routes.projects import _is_solo_project

    assert _is_solo_project(project["id"]) is True

    projects_db.add_member(project["id"], "second-human")
    assert _is_solo_project(project["id"]) is False


def test_count_project_members_no_profile_join_returns_count(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)  # one human member (the creator)

    assert projects_db.count_project_members(project["id"]) == 1

    projects_db.add_member(project["id"], "second-human")
    assert projects_db.count_project_members(project["id"]) == 2


def test_solo_opening_plain_turn_never_consults_classifier(isolated_settings, monkeypatch):
    """The FIRST turn in a fresh solo-project group chat — no prior agent
    turn, no `@Sprntly` — still gets exactly one reply with the classifier
    never consulted (David's staging case, reproduced without any injected
    failure)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    reply_calls = _stub_reply_path(monkeypatch)

    gate_calls: list[dict] = []
    monkeypatch.setattr(
        project_group_gate, "call_json",
        lambda **kw: gate_calls.append(kw) or {"respond": True},  # noqa: ARG005
    )

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "what is this project about. give me the tldr"},
    )
    assert r.status_code == 200
    assert gate_calls == [], "a solo project's opening turn must never consult the classifier"
    assert len(reply_calls) == 1


def test_is_solo_project_read_error_fails_open_to_solo(isolated_settings, monkeypatch, caplog):
    """A membership-COUNT-read failure must fail OPEN toward responding —
    a silent opener in a genuinely-solo project is the worse failure than a
    rare over-reply. `_is_solo_project` is routed through
    `count_project_members` (no `profiles` join), so this proves the
    fail-open path on THAT helper's failure, not `list_members`'."""
    def _boom(_project_id):
        raise RuntimeError("simulated membership-count read failure")

    monkeypatch.setattr(projects_db, "count_project_members", _boom)

    from app.routes.projects import _is_solo_project

    with caplog.at_level(logging.WARNING):
        assert _is_solo_project(1) is True

    lines = [rec.getMessage() for rec in caplog.records]
    assert any("solo_project_check_failed project_id=1" in line for line in lines)


def test_solo_opening_turn_replies_when_profile_join_would_fail(isolated_settings, monkeypatch):
    """Genuinely-solo project (membership COUNT intact — exactly one human
    member) but the `profiles`-enriching DISPLAY path (`list_members`) is
    made to raise. The fix routes `_is_solo_project` through
    `count_project_members` (no `profiles` join), so a profile-enrichment
    hiccup can never downgrade the DECISION to the conservative stay-out
    gate — David's staging case. RED on unfixed code: the old
    `_is_solo_project` called `list_members` directly, so this same
    failure made it return False and fall through to the gate.

    Scoped to the DECISION, not full reply delivery: `list_members` is
    ALSO used (unguarded, pre-existing, unrelated to this ticket's Fix 1)
    by `_respond_as_group_agent`'s own roster-block construction
    (`routes/projects.py` ~:1610) when actually generating the reply, so a
    forced `list_members` failure legitimately still prevents that
    downstream step from succeeding on BOTH old and new code — this test
    proves the FIX's own scope (the classifier is never consulted, and
    `_is_solo_project` itself is fail-open), not that reply generation is
    resilient to every possible roster-read failure (a separate,
    pre-existing gap, out of this ticket's scope)."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)  # single human member (the creator)
    _stub_reply_path(monkeypatch)

    def _boom(_project_id):
        raise RuntimeError("simulated profile-join failure")

    monkeypatch.setattr(projects_db, "list_members", _boom)

    gate_calls: list[dict] = []
    monkeypatch.setattr(
        project_group_gate, "call_json",
        lambda **kw: gate_calls.append(kw) or {"respond": True},  # noqa: ARG005
    )

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "what is this project about. give me the tldr"},
    )
    assert r.status_code == 200
    assert gate_calls == [], "a solo project must never consult the classifier"

    from app.routes.projects import _is_solo_project

    assert _is_solo_project(project["id"]) is True, (
        "a profile-join failure must not flip the solo decision to False"
    )


# ── Gate decision wiring (AC2/AC3) ──────────────────────────────────────


def test_gate_respond_true_triggers_single_reply(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    # A SECOND human member so this exercises the actual gate decision — a
    # solo (single-human) project now bypasses the gate entirely (the
    # solo-project auto-respond fix), which would otherwise make this test
    # pass for the wrong reason.
    projects_db.add_member(project["id"], "second-human")
    reply_calls = _stub_reply_path(monkeypatch)
    monkeypatch.setattr(project_group_gate, "call_json", _fake_gate_call_json(True))

    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        r = ctx.client.post(
            f"/v1/projects/{project['id']}/group/turns",
            json={"content": "is anyone able to help — can you assign the export review to Fortune?"},
        )
    assert r.status_code == 200
    assert len(reply_calls) == 1

    from app.db import conversations as conversations_db

    conv = conversations_db.get_group_chat(project["id"])
    turns = conversations_db.list_group_turns(conv["id"])
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[-1]["author_user_id"] is None


def test_gate_respond_false_no_reply(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    # Second human member — see test_gate_respond_true_triggers_single_reply.
    projects_db.add_member(project["id"], "second-human")
    reply_calls = _stub_reply_path(monkeypatch)
    monkeypatch.setattr(project_group_gate, "call_json", _fake_gate_call_json(False))

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "sounds good, I'll circle back once QA signs off on it"},
    )
    assert r.status_code == 200
    assert reply_calls == []

    from app.db import conversations as conversations_db

    conv = conversations_db.get_group_chat(project["id"])
    turns = conversations_db.list_group_turns(conv["id"])
    assert [t["role"] for t in turns] == ["user"]
    assert r.json()["content"] == "sounds good, I'll circle back once QA signs off on it"


# ── Error handling (mutation-proofed — AC4) ─────────────────────────────


def test_gate_failure_defaults_stay_out(isolated_settings, monkeypatch):
    """Forcing the classifier to raise must default to STAY OUT — never an
    exception, never a blocked post. If the guard around `call_json` in
    `should_respond` were removed, this test goes RED (the route would
    500, or `should_respond` itself would raise) — that's the mutation
    proof."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    # Second human member — see test_gate_respond_true_triggers_single_reply.
    projects_db.add_member(project["id"], "second-human")
    reply_calls = _stub_reply_path(monkeypatch)

    def _boom(**kwargs):  # noqa: ARG001
        raise RuntimeError("simulated classifier failure")

    monkeypatch.setattr(project_group_gate, "call_json", _boom)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "does anyone know the current status of the migration?"},
    )
    assert r.status_code == 200, "a gate failure must never break the post"
    assert reply_calls == [], "a gate failure must never spuriously interject"

    from app.db import conversations as conversations_db

    conv = conversations_db.get_group_chat(project["id"])
    turns = conversations_db.list_group_turns(conv["id"])
    assert [t["role"] for t in turns] == ["user"]

    # Direct unit-level proof: the function itself returns False, it does
    # not propagate the exception to its caller.
    result = project_group_gate.should_respond(
        project["id"], conv["id"], recent_turns=[],
        latest_content="does anyone know the current status of the migration?",
    )
    assert result is False


# ── Cost / pre-filter (AC5/AC6) ─────────────────────────────────────────


def test_prefilter_short_ack_no_call(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    # Second human member — see test_gate_respond_true_triggers_single_reply.
    projects_db.add_member(project["id"], "second-human")
    reply_calls = _stub_reply_path(monkeypatch)

    gate_calls: list[dict] = []
    monkeypatch.setattr(
        project_group_gate, "call_json",
        lambda **kw: gate_calls.append(kw) or {"respond": True},  # noqa: ARG005
    )

    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        r = ctx.client.post(
            f"/v1/projects/{project['id']}/group/turns", json={"content": "thanks"}
        )
    assert r.status_code == 200
    assert gate_calls == [], "a trivial short acknowledgement must never reach the classifier"
    assert reply_calls == []

    lines = [rec.getMessage() for rec in caplog.records]
    assert not any("interjection_gate" in line for line in lines)

    from app.db import conversations as conversations_db

    conv = conversations_db.get_group_chat(project["id"])
    turns = conversations_db.list_group_turns(conv["id"])
    assert [t["role"] for t in turns] == ["user"]


@pytest.mark.parametrize("ack", ["ok", "np", "got it", "sounds good"])
def test_prefilter_covers_common_acks(isolated_settings, monkeypatch, ack):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    # Second human member — see test_gate_respond_true_triggers_single_reply.
    projects_db.add_member(project["id"], "second-human")
    _stub_reply_path(monkeypatch)

    gate_calls: list[dict] = []
    monkeypatch.setattr(
        project_group_gate, "call_json",
        lambda **kw: gate_calls.append(kw) or {"respond": True},  # noqa: ARG005
    )

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns", json={"content": ack}
    )
    assert r.status_code == 200
    assert gate_calls == [], f"{ack!r} should be pre-filtered without a classifier call"


def test_gate_emits_single_cost_line(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    # Second human member — see test_gate_respond_true_triggers_single_reply.
    projects_db.add_member(project["id"], "second-human")
    _stub_reply_path(monkeypatch)
    monkeypatch.setattr(project_group_gate, "call_json", _fake_gate_call_json(False))

    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        r = ctx.client.post(
            f"/v1/projects/{project['id']}/group/turns",
            json={"content": "is anyone available to help debug this today?"},
        )
    assert r.status_code == 200

    cost_lines = [
        rec.getMessage() for rec in caplog.records
        if "projects.group_chat.interjection_gate" in rec.getMessage()
    ]
    assert len(cost_lines) == 1
    assert "est_cost_usd=" in cost_lines[0]
    assert f"project_id={project['id']}" in cost_lines[0]
    assert "status=complete" in cost_lines[0]

    # Answer-generation cost line: none, because the gate said stay-out.
    mention_reply_lines = [
        rec.getMessage() for rec in caplog.records
        if "mention_reply" in rec.getMessage()
    ]
    assert mention_reply_lines == []


# ── Project-aware decision (Fix 2 — AC5/AC6/AC9) ─────────────────────────


def _empty_group_context_deps(monkeypatch):
    """Monkeypatch every `assemble_group_agent_context` dependency to a
    cheap, controllable stand-in — mirrors `test_project_group_context.py`'s
    own pattern (patch at the seam, not real DB rows)."""
    import app.project_group_context as pgc

    monkeypatch.setattr(
        pgc.projects_db, "list_members",
        lambda pid: [{"user_id": "u1", "name": "Ada Lovelace", "job_role": "PM"}],  # noqa: ARG005
    )
    monkeypatch.setattr(pgc.memory_db, "get_summary", lambda pid: {"summary_md": "project summary text"})  # noqa: ARG005
    monkeypatch.setattr(pgc.memory_db, "get_latest_insight", lambda pid: None)  # noqa: ARG005
    monkeypatch.setattr(pgc.delegation_events_db, "list_status_for_project", lambda pid: [])  # noqa: ARG005
    monkeypatch.setattr(pgc, "list_artifacts_for_project", lambda **kw: [])  # noqa: ARG005
    return pgc


def test_gate_injects_compact_project_context_as_cacheable_prefix(isolated_settings, monkeypatch):
    pgc = _empty_group_context_deps(monkeypatch)
    expected_context = pgc.assemble_group_agent_context(7, "acme", "c1")

    gate_calls: list[dict] = []
    monkeypatch.setattr(
        project_group_gate, "call_json",
        lambda **kw: gate_calls.append(kw) or {"respond": True},  # noqa: ARG005
    )

    result = project_group_gate.should_respond(
        7, 2, recent_turns=[], latest_content="what's the status on the onboarding task?",
        dataset="acme", company_id="c1",
    )
    assert result is True
    assert len(gate_calls) == 1
    assert gate_calls[0]["user_cacheable_prefix"] == expected_context
    assert expected_context not in gate_calls[0]["user"], (
        "the project block must ride user_cacheable_prefix, never be "
        "concatenated into the per-message transcript"
    )


def test_gate_project_signal_is_manifest_not_full_body(isolated_settings, monkeypatch):
    import app.project_group_context as pgc

    large_body = "SECRET_ARTIFACT_BODY_TEXT " * 500
    monkeypatch.setattr(
        pgc.projects_db, "list_members",
        lambda pid: [{"user_id": "u1", "name": "Ada Lovelace", "job_role": "PM"}],  # noqa: ARG005
    )
    monkeypatch.setattr(pgc.memory_db, "get_summary", lambda pid: {"summary_md": ""})  # noqa: ARG005
    monkeypatch.setattr(pgc.memory_db, "get_latest_insight", lambda pid: None)  # noqa: ARG005
    monkeypatch.setattr(pgc.delegation_events_db, "list_status_for_project", lambda pid: [])  # noqa: ARG005
    monkeypatch.setattr(
        pgc, "list_artifacts_for_project",
        lambda **kw: [  # noqa: ARG005
            {
                "type": "prd", "id": 42, "title": "Onboarding PRD",
                "body": large_body, "content": large_body,
            }
        ],
    )

    gate_calls: list[dict] = []
    monkeypatch.setattr(
        project_group_gate, "call_json",
        lambda **kw: gate_calls.append(kw) or {"respond": True},  # noqa: ARG005
    )

    result = project_group_gate.should_respond(
        1, 2, recent_turns=[], latest_content="what's the status on the onboarding PRD?",
        dataset="acme", company_id="c1",
    )
    assert result is True
    prefix = gate_calls[0]["user_cacheable_prefix"]
    assert "Onboarding PRD" in prefix
    assert "42" in prefix  # the manifest, not a body: title + id only
    assert large_body not in prefix
    assert "SECRET_ARTIFACT_BODY_TEXT" not in prefix


def test_gate_assembles_context_only_after_prefilter(isolated_settings, monkeypatch):
    import app.project_group_context as pgc

    assemble_calls: list[tuple] = []

    def _track_assemble(project_id, dataset, company_id):
        assemble_calls.append((project_id, dataset, company_id))
        return "PROJECT CONTEXT stub"

    monkeypatch.setattr(pgc, "assemble_group_agent_context", _track_assemble)

    gate_calls: list[dict] = []
    monkeypatch.setattr(
        project_group_gate, "call_json",
        lambda **kw: gate_calls.append(kw) or {"respond": True},  # noqa: ARG005
    )

    result = project_group_gate.should_respond(
        1, 2, recent_turns=[], latest_content="thanks", dataset="acme", company_id="c1",
    )
    assert result is False
    assert assemble_calls == [], (
        "a trivial ack must not pay the assembler's DB reads even with "
        "dataset/company_id set"
    )
    assert gate_calls == []


def test_prefilter_trivial_ack_multi_human_zero_call(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    projects_db.add_member(project["id"], "second-human")
    reply_calls = _stub_reply_path(monkeypatch)

    gate_calls: list[dict] = []
    monkeypatch.setattr(
        project_group_gate, "call_json",
        lambda **kw: gate_calls.append(kw) or {"respond": True},  # noqa: ARG005
    )

    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        r = ctx.client.post(
            f"/v1/projects/{project['id']}/group/turns", json={"content": "thanks"}
        )
    assert r.status_code == 200
    assert gate_calls == [], "a trivial off-topic ack must never reach the classifier"
    assert reply_calls == []

    lines = [rec.getMessage() for rec in caplog.records]
    assert not any("interjection_gate" in line for line in lines)


def test_gate_prompt_uses_injected_project_context_for_relevance():
    system = project_group_gate._GATE_SYSTEM
    assert "PROJECT CONTEXT" in system
    assert "precedes the transcript" in system
    assert "Use it to judge" in system
    assert "how's the onboarding task going?" in system

    weak_prompt = "Decide whether Sprntly should reply to the latest message."
    assert "PROJECT CONTEXT" not in weak_prompt


def test_gate_prompt_retains_stayout_named_human_and_floor():
    system = project_group_gate._GATE_SYSTEM.lower()
    assert "ordinary human-to-human" in system
    assert "@-addressed to another named human" in system
    assert "conservative default is false" in system

    weak_prompt = "respond true sometimes."
    assert "conservative default is false" not in weak_prompt


def test_should_respond_without_project_context_runs_plain_gate(isolated_settings, monkeypatch):
    """Called with no `dataset`/`company_id` (every pre-existing caller):
    `user_cacheable_prefix` is `None` — no project context assembled — the
    plain, pre-project-aware gate contract, unchanged."""
    import app.project_group_context as pgc

    assemble_calls: list[tuple] = []
    monkeypatch.setattr(
        pgc, "assemble_group_agent_context",
        lambda *a: assemble_calls.append(a) or "PROJECT CONTEXT stub",  # noqa: ARG005
    )

    gate_calls: list[dict] = []
    monkeypatch.setattr(
        project_group_gate, "call_json",
        lambda **kw: gate_calls.append(kw) or {"respond": True},  # noqa: ARG005
    )

    result = project_group_gate.should_respond(
        1, 2, recent_turns=[], latest_content="does anyone know the current status?",
    )
    assert result is True
    assert len(gate_calls) == 1
    assert gate_calls[0]["user_cacheable_prefix"] is None
    assert assemble_calls == []


# ── `agent_spoke_last` pre-filter bypass (continuation, Part A) ──────────


def test_agent_spoke_last_bypasses_prefilter(isolated_settings, monkeypatch):
    """A short, question-free, agent-cue-free turn (`_obviously_human_
    chatter` would return True for it) with `agent_spoke_last=True` REACHES
    the classifier instead of being pre-filtered — the CONTINUATION rule
    then decides."""
    gate_calls: list[dict] = []
    monkeypatch.setattr(
        project_group_gate, "call_json",
        lambda **kw: gate_calls.append(kw) or {"respond": True},  # noqa: ARG005
    )
    assert project_group_gate._obviously_human_chatter("ok do that") is True

    result = project_group_gate.should_respond(
        1, 2, recent_turns=[], latest_content="ok do that", agent_spoke_last=True,
    )
    assert result is True
    assert len(gate_calls) == 1, "agent_spoke_last=True must reach the classifier"


def test_agent_spoke_last_false_prefilter_unchanged(isolated_settings, monkeypatch, caplog):
    """The SAME short content with `agent_spoke_last=False` (the default)
    short-circuits to False with NO classifier call — base behaviour
    unchanged."""
    gate_calls: list[dict] = []
    monkeypatch.setattr(
        project_group_gate, "call_json",
        lambda **kw: gate_calls.append(kw) or {"respond": True},  # noqa: ARG005
    )

    with caplog.at_level(logging.INFO, logger="app.project_group_gate"):
        result = project_group_gate.should_respond(
            1, 2, recent_turns=[], latest_content="ok do that",
        )
    assert result is False
    assert gate_calls == []
    lines = [rec.getMessage() for rec in caplog.records]
    assert any("reason=prefilter" in line for line in lines)


def test_should_respond_never_raises_under_both_modes(isolated_settings, monkeypatch):
    """A forced classifier exception must still default to False whether
    or not the pre-filter was bypassed."""
    def _boom(**kwargs):  # noqa: ARG001
        raise RuntimeError("simulated classifier failure")

    monkeypatch.setattr(project_group_gate, "call_json", _boom)

    # agent_spoke_last=True: bypasses pre-filter, reaches (and survives) the
    # exploding classifier.
    assert project_group_gate.should_respond(
        1, 2, recent_turns=[], latest_content="ok do that", agent_spoke_last=True,
    ) is False
    # agent_spoke_last=False: short content still pre-filtered, never even
    # reaches the classifier, so the exploding stub is never exercised —
    # still returns False either way.
    assert project_group_gate.should_respond(
        1, 2, recent_turns=[], latest_content="ok do that",
    ) is False
    # A longer/questioning turn that reaches the classifier even with the
    # default agent_spoke_last=False must also default False on failure.
    assert project_group_gate.should_respond(
        1, 2, recent_turns=[],
        latest_content="does anyone know the current status of the migration?",
    ) is False


def test_gate_cost_log_no_body_text(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _stub_reply_path(monkeypatch)
    monkeypatch.setattr(project_group_gate, "call_json", _fake_gate_call_json(True))

    secret_content = "is anyone free — SECRET_TURN_CONTENT_DO_NOT_LOG?"
    with caplog.at_level(logging.INFO, logger="app.llm_telemetry"):
        r = ctx.client.post(
            f"/v1/projects/{project['id']}/group/turns", json={"content": secret_content}
        )
    assert r.status_code == 200

    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "SECRET_TURN_CONTENT_DO_NOT_LOG" not in joined


# ── Prompt property (content + negative-space) ──────────────────────────


def test_gate_prompt_states_when_not_to_respond():
    system = project_group_gate._GATE_SYSTEM.lower()
    assert "stay out" in system
    assert "human-to-human" in system
    assert "acknowledgements" in system
    assert "conservative default" in system

    # Negative-space: the phrase checks themselves must actually catch a
    # prompt that DOESN'T carry these rules — proves this isn't vacuous.
    weak_prompt = "Decide whether Sprntly should reply to the latest message."
    assert "stay out" not in weak_prompt.lower()
    assert "acknowledgements" not in weak_prompt.lower()
    assert "conservative default" not in weak_prompt.lower()


def test_gate_schema_forces_boolean_respond():
    assert project_group_gate._GATE_SCHEMA["required"] == ["respond"]
    assert project_group_gate._GATE_SCHEMA["properties"]["respond"]["type"] == "boolean"
    assert project_group_gate._GATE_SCHEMA["additionalProperties"] is False


def test_gate_prompt_has_continuation_rule():
    """CONTINUATION rule: respond=true on a direct reply/follow-up to
    Sprntly's own immediately-preceding turn, missing @handle notwithstanding."""
    system = project_group_gate._GATE_SYSTEM
    assert "CONTINUATION" in system
    assert "immediately preceding line is" in system
    assert "Sprntly's own" in system
    assert "ok do that" in system

    weak_prompt = "Decide whether Sprntly should reply to the latest message."
    assert "CONTINUATION" not in weak_prompt


def test_gate_prompt_has_ambiguous_work_request_rule():
    """AMBIGUOUS WORK REQUEST rule: respond=true on an unaddressed task
    request even when ambiguous — EXCEPT when it names a human, which
    stays false."""
    system = project_group_gate._GATE_SYSTEM
    assert "AMBIGUOUS WORK REQUEST" in system
    assert "does NOT apply when the request names a human" in system
    assert "who's picking up the API docs" in system

    weak_prompt = "Decide whether Sprntly should reply to the latest message."
    assert "AMBIGUOUS WORK REQUEST" not in weak_prompt


def test_gate_prompt_labels_sprntly_own_turns():
    """Sprntly's own prior turns are labeled distinctly in the transcript
    so the classifier can recognize the immediately-preceding-agent case
    the CONTINUATION rule depends on."""
    system = project_group_gate._GATE_SYSTEM
    assert '"Sprntly: message"' in system

    weak_prompt = "Each line of the transcript is a turn."
    assert '"Sprntly: message"' not in weak_prompt


def test_gate_prompt_retains_conservative_floor_and_named_human_exclusion():
    """The port must not weaken the base AD-P10 floor: the conservative
    stay-out default and the human-to-human/named-human exclusions stay
    present verbatim-equivalent alongside the new rules."""
    system = project_group_gate._GATE_SYSTEM.lower()
    assert "conservative default is false" in system
    assert "human-to-human" in system
    assert "acknowledgements" in system
    assert "@-addressed to another named human" in system

    weak_prompt = "respond true sometimes."
    assert "conservative default is false" not in weak_prompt


# ── Isolation (AC7) ──────────────────────────────────────────────────────


def test_gate_never_runs_on_individual_conversation(isolated_settings, monkeypatch):
    """The gate's ONLY context source is `conversations_db.list_group_turns`
    — which already refuses a non-`kind='group'` id (R4/AD-P2 backstop,
    unchanged by this ticket). A private conversation's turns can
    therefore never reach the gate: `post_group_turn_route` never accepts
    a client-supplied `conversation_id` at all, it always resolves via
    `create_group_chat` (which only ever returns a `kind='group'` row), and
    even called directly against a `kind='individual'` id the backstop
    returns an empty transcript."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    from app.db import conversations as conversations_db
    from app.db.client import require_client

    individual = (
        require_client()
        .table("conversations")
        .insert(
            {
                "company_id": ctx.company_id,
                "user_id": ctx.user_id,
                "project_id": project["id"],
                "kind": "individual",
            }
        )
        .execute()
        .data[0]
    )
    require_client().table("conversation_turns").insert(
        {
            "conversation_id": individual["id"],
            "role": "user",
            "content": "private: is anyone free to review my PR?",
        }
    ).execute()

    # Isolation backstop: the gate's context source refuses the individual
    # id outright — it can never see this turn's content.
    recent = conversations_db.list_group_turns(individual["id"])
    assert recent == []

    gate_calls: list[dict] = []
    monkeypatch.setattr(
        project_group_gate, "call_json",
        lambda **kw: gate_calls.append(kw) or {"respond": True},  # noqa: ARG005
    )
    # Called with exactly the (refused, empty) context the route would
    # actually pass for this id: an empty transcript pairs with empty
    # latest_content, which the pre-filter catches — no classifier call,
    # no private content anywhere near the model.
    result = project_group_gate.should_respond(project["id"], individual["id"], recent, "")
    assert result is False
    assert gate_calls == []


# ── Decision persistence (observability — AC11-AC15) ─────────────────────


def test_migration_trigger_kind_column_is_additive_nullable():
    import pathlib

    migration_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "supabase" / "migrations" / "20260815180000_conversation_turns_trigger_kind.sql"
    )
    text = migration_path.read_text()
    lowered = text.lower()
    assert "add column if not exists" in lowered
    assert "trigger_kind" in lowered
    assert "conversation_turns" in lowered
    # Check the SQL statement itself, not the prose comments explaining it
    # (which legitimately say "no default").
    sql_lines = [
        line for line in lowered.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    assert "default" not in "\n".join(sql_lines)


def _trigger_kind_of(turn_id: int) -> str | None:
    from app.db.client import require_client

    rows = (
        require_client().table("conversation_turns")
        .select("trigger_kind").eq("id", turn_id).execute().data
    )
    return rows[0]["trigger_kind"] if rows else None


def test_conftest_fake_persists_and_reads_back_trigger_kind(isolated_settings, monkeypatch):
    from app.db import conversations as conversations_db

    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    conversation = conversations_db.create_group_chat(project["id"], ctx.user_id)
    turn = conversations_db.post_group_turn(conversation["id"], ctx.user_id, "hello team")

    conversations_db.set_group_turn_trigger_kind(turn["id"], "gate")

    assert _trigger_kind_of(turn["id"]) == "gate"


def test_trigger_kind_persisted_mention(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _stub_reply_path(monkeypatch)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "@Sprntly what's the status?"},
    )
    assert r.status_code == 200
    assert _trigger_kind_of(r.json()["id"]) == "mention"


def test_trigger_kind_persisted_solo(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _stub_reply_path(monkeypatch)

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "what is this project about?"},
    )
    assert r.status_code == 200
    assert _trigger_kind_of(r.json()["id"]) == "solo"


def test_trigger_kind_persisted_gate_reply(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    projects_db.add_member(project["id"], "second-human")
    reply_calls = _stub_reply_path(monkeypatch)
    monkeypatch.setattr(project_group_gate, "call_json", _fake_gate_call_json(True))

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "is anyone able to help — can you assign the export review to Fortune?"},
    )
    assert r.status_code == 200
    assert len(reply_calls) == 1
    assert _trigger_kind_of(r.json()["id"]) == "gate"


def test_trigger_kind_persisted_continuation(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    projects_db.add_member(project["id"], "second-human")
    reply_calls = _stub_reply_path(monkeypatch)
    monkeypatch.setattr(project_group_gate, "call_json", _fake_gate_call_json(True))

    # First turn: @-mention, gets an assistant reply — so the immediately
    # preceding turn before the SECOND human turn is Sprntly's own.
    r1 = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "@Sprntly please delegate the export review to Fortune"},
    )
    assert r1.status_code == 200
    assert len(reply_calls) == 1

    r2 = ctx.client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "yes — go ahead and assign the follow-up to Fortune too"},
    )
    assert r2.status_code == 200
    assert len(reply_calls) == 2
    assert _trigger_kind_of(r2.json()["id"]) == "continuation"


def test_trigger_kind_persisted_gate_stayout(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    projects_db.add_member(project["id"], "second-human")
    reply_calls = _stub_reply_path(monkeypatch)
    monkeypatch.setattr(project_group_gate, "call_json", _fake_gate_call_json(False))

    with caplog.at_level(logging.INFO):
        r = ctx.client.post(
            f"/v1/projects/{project['id']}/group/turns",
            json={"content": "sounds good, I'll circle back once QA signs off on it"},
        )
    assert r.status_code == 200
    assert reply_calls == [], "the gate said stay-out — no reply is scheduled either way"
    assert _trigger_kind_of(r.json()["id"]) == "gate_stayout"

    # The stay-out decision is greppably logged, keyed to the persisted
    # trigger_kind, exactly once — and the existing `group_gate_decision`
    # log (already recording the reason) is not duplicated by this line.
    stayout_lines = [
        rec.getMessage()
        for rec in caplog.records
        if rec.getMessage().startswith("group_stayout_recorded")
    ]
    assert len(stayout_lines) == 1
    assert f"project_id={project['id']}" in stayout_lines[0]
    assert "trigger_kind=gate_stayout" in stayout_lines[0]


def test_trigger_kind_persist_failure_swallowed(isolated_settings, monkeypatch, caplog):
    """Forcing the underlying `conversation_turns` UPDATE to raise must not
    500 the route, must not prevent the human turn from persisting, and
    must not prevent the scheduled reply — mutation proof of
    `set_group_turn_trigger_kind`'s own never-raises contract: if its
    try/except were removed, this test goes RED (500)."""
    from tests import _fake_supabase

    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    reply_calls = _stub_reply_path(monkeypatch)

    original_update = _fake_supabase._Query.update

    def _boom_update(self, patch):
        if self.table == "conversation_turns" and "trigger_kind" in patch:
            raise RuntimeError("simulated trigger_kind persist failure")
        return original_update(self, patch)

    monkeypatch.setattr(_fake_supabase._Query, "update", _boom_update)

    secret_content = "what is this project about — SECRET_TRIGGER_KIND_CONTENT?"
    with caplog.at_level(logging.WARNING):
        r = ctx.client.post(
            f"/v1/projects/{project['id']}/group/turns", json={"content": secret_content},
        )
    assert r.status_code == 200, "a trigger_kind persist failure must never break the post"
    assert len(reply_calls) == 1, "the reply must still be scheduled"
    assert r.json()["content"] == secret_content

    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "SECRET_TRIGGER_KIND_CONTENT" not in joined


# ── No toggle anywhere (AC8) ─────────────────────────────────────────────


def test_should_respond_signature_has_no_mode_param():
    """`should_respond` takes no client-controllable mode/toggle
    parameter — every input is server-derived (project/conversation id,
    the already-persisted turn list, the already-persisted latest turn's
    content, `agent_spoke_last` — derived server-side from whether the
    prior turn's role was 'assistant' — and `dataset`/`company_id`, the
    caller's own resolved workspace identifiers, never a client field).
    The decision is entirely the classifier's, not a caller setting."""
    import inspect

    params = list(inspect.signature(project_group_gate.should_respond).parameters)
    assert params == [
        "project_id", "conversation_id", "recent_turns", "latest_content",
        "agent_spoke_last", "dataset", "company_id",
    ]


def test_no_interjection_toggle_request_field():
    """`PostGroupTurnRequest` carries no interjection-mode / auto-reply toggle
    field — the respond-or-stay-out decision is entirely server-side. The
    payload MAY carry execution-identity / SendCommand plumbing
    (`client_message_id`, `pinned_skill`, `attachments`), but never a knob that
    lets the client override the gate."""
    from app.routes.projects import PostGroupTurnRequest

    fields = set(PostGroupTurnRequest.model_fields.keys())
    assert "content" in fields
    # No field that would let a client toggle whether/how the agent interjects.
    assert not (fields & {"mode", "interjection_mode", "auto_reply", "respond", "always_respond"})
    # Only the allowlisted plumbing fields are added beyond `content`.
    assert fields <= {"content", "client_message_id", "pinned_skill", "attachments"}


# ── Real-LLM / real-DB live tier ─────────────────────────────────────────
#
# Gated behind RUN_INTERJECTION_GATE_LIVE=1 PLUS a real ANTHROPIC_API_KEY.
# Mutates real rows against a real (company, workspace, user) already
# seeded in the local rig — mirrors test_group_chat_turns_live.py's /
# test_project_memory_promotion.py's fixture shape.

_RUN_LIVE = os.getenv("RUN_INTERJECTION_GATE_LIVE") == "1" and bool(
    os.getenv("ANTHROPIC_API_KEY")
)

_LIVE_SKIP_REASON = (
    "needs a real local Supabase + a real ANTHROPIC_API_KEY — set "
    "RUN_INTERJECTION_GATE_LIVE=1 with SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY/"
    "SUPABASE_JWT_SECRET/ANTHROPIC_API_KEY pointed at the local rig and the "
    "projects/chat/memory migrations applied"
)


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live gate round-trip against a non-loopback "
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
        .limit(1)
        .execute()
        .data
    )
    assert owners, f"need >=1 owner/admin company_members row for company {company_id}"
    user_id = owners[0]["user_id"]

    yield {"company_id": company_id, "workspace_id": workspace_id, "user_id": user_id}


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

    headers = _bearer(fixture_ids["user_id"])
    headers["X-Workspace-Id"] = fixture_ids["workspace_id"]
    headers["Origin"] = settings.origins_list[0]
    return TestClient(main_mod.app, headers=headers)


@pytest.fixture
def project_ids(sb):
    """NOT autouse — see `test_project_memory_promotion.py`'s identical
    fixture docstring for why: this file mixes fast unit tests with the
    live tier, and an autouse fixture depending on the module-scoped `sb`
    (which `pytest.skip()`s when the live tier is disabled) would silently
    skip the fast tests too."""
    created: list[int] = []
    yield created
    for pid in created:
        sb.table("projects").delete().eq("id", pid).execute()


def _second_human_id(sb, exclude_user_id: str) -> str:
    """A REAL existing `auth.users`-backed id for the "second human" seeded
    into a multi-human live-tier project — `project_members.user_id` (and
    `project_delegations.assignee_user_id`) are `uuid NOT NULL FK ->
    auth.users(id)`, so a fabricated non-uuid string errors immediately
    with Postgres 22P02 before the gate is ever reached. `profiles` mirrors
    `auth.users` 1:1 and is queryable via the service-role client (unlike
    the `auth` schema itself), mirroring how `fixture_ids` elsewhere in
    this file / `test_delegation_events.py`'s own `fixture_ids` resolve a
    real user id — this one deliberately does NOT require same-company
    membership (the FK only cares that the id is a real user), so it works
    even when the seeded company itself only has one member."""
    rows = (
        sb.table("profiles").select("id").neq("id", exclude_user_id).limit(1).execute().data
    )
    assert rows, "need a second real profiles/auth.users row in the local rig"
    return rows[0]["id"]


@pytest.mark.integration
@pytest.mark.real_interjection_gate  # opt OUT of conftest's autouse call_json stub
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_live_multi_human_project_relevant_message_interjects(client, fixture_ids, project_ids, sb):
    """The project-awareness proof (AC8): in a MULTI-human project with a
    seeded task, an untagged message clearly relevant to the project's work
    ("how's the onboarding task going?") → the REAL project-aware
    classifier — fed the compact project signal via
    `assemble_group_agent_context` — returns respond=true → exactly one
    real assistant reply."""
    project = client.post(
        "/v1/projects", json={"name": f"Live gate project-aware {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])
    # A SECOND human member — see test_gate_live_stays_out_of_human_backforth;
    # otherwise the solo-project auto-respond shortcut answers this without
    # ever consulting the real classifier this test means to exercise.
    second_human = _second_human_id(sb, fixture_ids["user_id"])
    sb.table("project_members").insert(
        {"project_id": project["id"], "user_id": second_human}
    ).execute()

    # Seed a real open delegation so the injected project signal's task
    # ledger names "onboarding" — the project-awareness proof needs the
    # classifier to be relying on the injected context, not general
    # keyword coincidence in the prompt.
    conv = sb.table("conversations").insert(
        {
            "company_id": fixture_ids["company_id"],
            "user_id": fixture_ids["user_id"],
            "project_id": project["id"],
            "kind": "individual",
        }
    ).execute().data[0]
    turn = sb.table("conversation_turns").insert(
        {
            "conversation_id": conv["id"],
            "role": "assistant",
            "content": "delivered onboarding task brief",
            "author_user_id": fixture_ids["user_id"],
        }
    ).execute().data[0]
    from app.db.project_delegations import record_delegation

    record_delegation(
        project_id=project["id"],
        assigner_user_id=fixture_ids["user_id"],
        assignee_user_id=second_human,
        task_summary="finish the onboarding task",
        source_conversation_id=None,
        source_turn_id=None,
        delivered_conversation_id=conv["id"],
        delivered_turn_id=turn["id"],
    )

    r = client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "how's the onboarding task going?"},
    )
    assert r.status_code == 200, r.text

    turns = client.get(f"/v1/projects/{project['id']}/group/turns").json()["turns"]
    assert [t["role"] for t in turns] == ["user", "assistant"], (
        "a project-relevant untagged question must get exactly one real "
        f"assistant reply — got roles {[t['role'] for t in turns]}"
    )
    assert turns[-1]["author_user_id"] is None
    assert turns[-1]["content"].strip() != ""


@pytest.mark.integration
@pytest.mark.real_interjection_gate  # opt OUT of conftest's autouse call_json stub
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_gate_live_stays_out_of_human_backforth(client, fixture_ids, project_ids, sb):
    """(b) An ordinary human-to-human exchange, no mention → the REAL
    classifier says respond=false → NO agent turn is added; the trailing
    turn stays the human's, which is exactly what drives the frontend's
    existing "Sprntly stayed out" affordance."""
    project = client.post(
        "/v1/projects", json={"name": f"Live gate stay-out {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])
    # A SECOND human member — a solo (single-human) project now bypasses the
    # gate entirely and always replies (the solo-project auto-respond fix),
    # so this human-to-human scenario needs a real second person for the
    # gate to be consulted at all.
    sb.table("project_members").insert(
        {"project_id": project["id"], "user_id": _second_human_id(sb, fixture_ids["user_id"])}
    ).execute()

    client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "Hey Sam, did you get a chance to look at the invoice PDF I sent over yesterday?"},
    )
    r = client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "Yeah just saw it, looks good — I'll forward it to accounting this afternoon."},
    )
    assert r.status_code == 200, r.text

    turns = client.get(f"/v1/projects/{project['id']}/group/turns").json()["turns"]
    assert [t["role"] for t in turns] == ["user", "user"], (
        "an ordinary human-to-human exchange must get NO agent reply — "
        f"got roles {[t['role'] for t in turns]}"
    )


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_mention_live_always_responds(client, fixture_ids, project_ids, sb):
    """(c) An explicit @Sprntly mention still always responds — real
    reply, deterministic path, gate never in the loop."""
    project = client.post(
        "/v1/projects", json={"name": f"Live mention {uuid.uuid4().hex[:8]}"}
    ).json()
    project_ids.append(project["id"])

    r = client.post(
        f"/v1/projects/{project['id']}/group/turns",
        json={"content": "@Sprntly can you confirm this thread is still on track?"},
    )
    assert r.status_code == 200, r.text

    turns = client.get(f"/v1/projects/{project['id']}/group/turns").json()["turns"]
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[-1]["author_user_id"] is None
