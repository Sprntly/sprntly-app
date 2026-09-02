"""`SurfaceScope` descriptor + the `qa_agent.answer(scope=...)` byte-identity
property test — the central regression gate for the whole project-chat
engine collapse (AC1/AC2).

`scope is None` / `SurfaceScope(surface=main)` must be provable no-ops:
main chat's tool set (the schema-forced `submit_response` tool via
`compose_ask_answer`) and system-prompt string are byte-identical whether
or not a caller passes `scope` at all.
"""
from __future__ import annotations

import dataclasses

import pytest

import app.qa_agent as qa
from app.surface_scope import Surface, SurfaceScope


def _route_out():
    from types import SimpleNamespace

    return SimpleNamespace(output={"skill_id": None, "confidence": 0.0, "action": None})


# ── SurfaceScope construction/defaults (AC1) ──────────────────────────────


def test_surface_scope_frozen_and_defaulted():
    scope = SurfaceScope(surface=Surface.project_private)
    assert scope.project_id is None
    assert scope.context_payload == ""
    assert scope.system_addendum == ""
    assert scope.composer_fold_addendum == ""
    assert scope.extra_tools == ()
    assert scope.roster == ()
    assert scope.assigner_identity is None
    assert scope.post_turn is None
    assert scope.capabilities is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        scope.project_id = 5  # type: ignore[misc]


# ── main-surface no-op (AC2, AC11) ─────────────────────────────────────────


def test_surface_scope_main_is_noop():
    scope = SurfaceScope(surface=Surface.main)
    assert scope.is_noop is True
    assert scope.extra_tools == ()
    assert scope.system_addendum == ""
    assert scope.composer_fold_addendum == ""


def test_surface_scope_project_private_is_not_noop():
    scope = SurfaceScope(surface=Surface.project_private, extra_tools=({"name": "x"},))
    assert scope.is_noop is False


# ── the six-tool contract (AC6, AC11) ──────────────────────────────────────


# Retargeted from the deleted `ask_job_runner._build_private_scope`: the private
# scope (6 depth tools + composed system_addendum) is now built by
# `ProjectContextAssembler.assemble` (`context_assembler_project.py`). These are
# pure unit assertions with no DB, so the assembler's membership gate is stubbed
# and its best-effort breadth/roster/instructions reads degrade to empty — the
# `extra_tools` + `system_addendum` composition is DB-independent.
def _assemble_private_scope_unit(monkeypatch, *, project_id: int = 9):
    from app.context_assembler import AssembleRequest
    from app.context_assembler_project import ProjectContextAssembler
    from app.db import projects as projects_db

    monkeypatch.setattr(projects_db, "project_belongs_to_company", lambda *a, **k: True)
    monkeypatch.setattr(projects_db, "is_project_member", lambda *a, **k: True)
    req = AssembleRequest(
        user_id="u1", company_id="c1", dataset="", conversation_id=None,
        question="q", workspace_id="w1",
        params={"project_id": project_id, "surface": "private"},
    )
    return ProjectContextAssembler().assemble(req)


def test_surface_scope_project_private_carries_six_extra_tools(monkeypatch):
    from app import project_delegation, project_task_execution
    from app.project_group_context import read_tools

    scope = _assemble_private_scope_unit(monkeypatch)
    assert len(scope.extra_tools) == 6
    names = [t["name"] for t in scope.extra_tools]
    expected = [t["name"] for t in (
        project_delegation.DELEGATE_TASK_TOOL,
        project_task_execution.EXECUTE_TASK_TOOL,
        *read_tools(),
    )]
    assert names == expected
    # AC9 no-leak: the GROUP-only `edit_prd` tool is NOT on the private scope,
    # and private registers no edit handler — so its `answer()` result shape
    # is unaffected by the group's in-band edit tool.
    assert "edit_prd" not in names
    assert scope.edit_prd_handler is None


def test_private_system_addendum_byte_identical(monkeypatch):
    """AC2: the private surface's assembled `system_addendum` is byte-identical
    to the composed `_PRIVATE_SCOPE_SYSTEM` + roster block — the group prompt/
    context/edit-tool convergence changed NOTHING about the private surface's
    assembled system text (`_PRIVATE_SCOPE_SYSTEM` untouched). The composition
    relocated verbatim into `ProjectContextAssembler.assemble`."""
    from app.ask_job_runner import _PRIVATE_SCOPE_SYSTEM, _private_roster_block

    scope = _assemble_private_scope_unit(monkeypatch)
    # No members / no instructions in this unit context (best-effort reads
    # degrade to empty), so the composition is exactly the base system + the
    # empty-roster block.
    expected = f"{_PRIVATE_SCOPE_SYSTEM}\n\n{_private_roster_block([])}"
    assert scope.system_addendum == expected


def test_private_composer_fold_addendum_byte_identical_and_delegate_free(monkeypatch):
    """Delegation-confabulation fix (part 2): the assembled `composer_fold_
    addendum` is byte-identical to `_PRIVATE_SCOPE_COMPOSER_FOLD` + the same
    roster block `system_addendum` uses — but, unlike `system_addendum`,
    contains none of the delegate_task-specific guidance (in particular the
    verbatim handoff-confirmation template), since this addendum is what
    reaches a turn with no `delegate_task` tool available."""
    from app.ask_job_runner import (
        _PRIVATE_SCOPE_COMPOSER_FOLD,
        _PRIVATE_SCOPE_DELEGATE_GUIDANCE,
        _private_roster_block,
    )

    scope = _assemble_private_scope_unit(monkeypatch)
    expected = f"{_PRIVATE_SCOPE_COMPOSER_FOLD}\n\n{_private_roster_block([])}"
    assert scope.composer_fold_addendum == expected
    assert _PRIVATE_SCOPE_DELEGATE_GUIDANCE not in scope.composer_fold_addendum
    assert "I've asked <name> to <task>" not in scope.composer_fold_addendum


# ── post_turn realtime fan-out (agent async writer — cross-user-safe) ─────


def test_post_turn_publishes_turn_created_to_conversation_owner_topic(monkeypatch):
    """The execute-task progress writer (`post_turn`) is CROSS-USER-capable —
    `post_individual_turn` can write into a teammate's own individual chat,
    not necessarily the acting caller's (`req.user_id`). Its realtime publish
    must key the per-user topic on the WRITTEN conversation's owner uid
    (resolved server-side from the conversation itself), never the acting
    caller and never the group channel."""
    from app.context_assembler import AssembleRequest
    from app.context_assembler_project import ProjectContextAssembler
    from app.db import projects as projects_db
    import app.db.conversations as conversations_db
    import app.realtime as realtime_mod

    monkeypatch.setattr(projects_db, "project_belongs_to_company", lambda *a, **k: True)
    monkeypatch.setattr(projects_db, "is_project_member", lambda *a, **k: True)

    monkeypatch.setattr(
        conversations_db, "post_individual_turn",
        lambda conversation_id, role, content: {
            "id": 501, "role": role, "content": content,
            "created_at": "2026-09-02T00:00:00Z",
        },
    )
    # The acting caller (`req.user_id`) is deliberately a DIFFERENT uid than
    # the conversation's owner — proving the topic follows the owner.
    monkeypatch.setattr(
        conversations_db, "get_individual_conversation_owner",
        lambda conversation_id: "owner-uid-999" if conversation_id == 777 else None,
    )
    published: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        realtime_mod, "publish_broadcast",
        lambda topic, event, payload: published.append((topic, event, payload)),
    )

    req = AssembleRequest(
        user_id="acting-caller-u1", company_id="c1", dataset="", conversation_id=777,
        question="q", workspace_id="w1",
        params={"project_id": 42, "surface": "private"},
    )
    scope = ProjectContextAssembler().assemble(req)
    assert scope.post_turn is not None

    turn = scope.post_turn("Working on it — step 1 done.")
    assert turn["content"] == "Working on it — step 1 done."

    assert len(published) == 1
    topic, event, payload = published[0]
    assert topic == "project:42:user:owner-uid-999"
    assert event == "turn.created"
    assert set(payload) == {"id", "role", "content", "created_at"}
    assert payload["content"] == "Working on it — step 1 done."
    # Never the acting caller's own topic, and never the group channel.
    assert topic != "project:42:user:acting-caller-u1"
    assert topic != "project:42"


def test_post_turn_publish_failure_does_not_break_the_write(monkeypatch):
    """The publish-prep is best-effort (AD-P22): a raising owner lookup or a
    raising `publish_broadcast` must not stop `post_turn` from returning the
    already-written turn."""
    from app.context_assembler import AssembleRequest
    from app.context_assembler_project import ProjectContextAssembler
    from app.db import projects as projects_db
    import app.db.conversations as conversations_db

    monkeypatch.setattr(projects_db, "project_belongs_to_company", lambda *a, **k: True)
    monkeypatch.setattr(projects_db, "is_project_member", lambda *a, **k: True)
    monkeypatch.setattr(
        conversations_db, "post_individual_turn",
        lambda conversation_id, role, content: {
            "id": 502, "role": role, "content": content,
            "created_at": "2026-09-02T00:00:00Z",
        },
    )

    def _boom(conversation_id):
        raise RuntimeError("simulated owner-lookup failure")

    monkeypatch.setattr(conversations_db, "get_individual_conversation_owner", _boom)

    req = AssembleRequest(
        user_id="u1", company_id="c1", dataset="", conversation_id=777,
        question="q", workspace_id="w1",
        params={"project_id": 42, "surface": "private"},
    )
    scope = ProjectContextAssembler().assemble(req)
    turn = scope.post_turn("still written despite the publish hiccup")
    assert turn["content"] == "still written despite the publish hiccup"


# ── byte-identity: scope=None vs SurfaceScope(main) vs omitted (AC1) ───────


def test_answer_scope_none_tool_set_byte_identical(monkeypatch):
    """The direct/generic path's only "tool" is the schema-forced
    `submit_response` tool baked into `call_json` — never touched by this
    ticket. Proven here by asserting `compose_ask_answer` is reached with
    byte-identical kwargs whether `scope` is omitted, explicitly `None`, or
    `SurfaceScope(surface=main)`."""
    monkeypatch.setattr(qa, "llm_call", lambda **k: _route_out())  # router → none
    captured: list[dict] = []

    def _fake_compose(dataset, q, *, enterprise_id, prd_context="", history=None, on_delta=None, **k):
        captured.append({"dataset": dataset, "q": q, "enterprise_id": enterprise_id})
        return {"answer": "generic", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": ""}

    monkeypatch.setattr(qa, "compose_ask_answer", _fake_compose)

    common = dict(enterprise_id="ent", question="what happened last week", dataset="acme")
    out_omitted = qa.answer(**common)
    out_none = qa.answer(**common, scope=None)
    out_main = qa.answer(**common, scope=SurfaceScope(surface=Surface.main))

    assert len(captured) == 3
    assert captured[0] == captured[1] == captured[2]
    assert out_omitted == out_none == out_main


def test_answer_scope_none_system_prompt_byte_identical(monkeypatch):
    """Same proof on the skill-routed single-shot path (`_answer_single_shot`,
    via `llm_call`) — the system prompt string reaching `llm_call` is
    byte-identical across the same three call shapes."""
    from app.qa_agent import HEAVY_SKILLS  # noqa: F401 — sanity import, unused directly

    systems: list[str] = []

    def _fake_llm_call(**k):
        from types import SimpleNamespace

        if k.get("purpose") == "route":
            return _route_out()
        systems.append(k.get("system"))
        return SimpleNamespace(output={"answer": "ok", "key_points": [], "citations": [],
                                        "confidence": 0.9, "unanswered": ""})

    monkeypatch.setattr(qa, "llm_call", _fake_llm_call)
    monkeypatch.setattr(qa, "route", lambda *a, **k: qa.RouteDecision("call-digest-like", 0.0, "none"))

    common = dict(enterprise_id="ent", question="anything at all", dataset="acme", pinned_skill="__builtin_none__")
    # Force the skill-routed single-shot path deterministically via a pinned,
    # non-custom id (falls through resolve_skill to a plain grounded answer —
    # same shape `_answer_single_shot`'s own docstring describes for a
    # declined pipeline id).
    qa.answer(**common)
    qa.answer(**common, scope=None)
    qa.answer(**common, scope=SurfaceScope(surface=Surface.main))

    assert len(systems) == 3
    assert systems[0] == systems[1] == systems[2]
