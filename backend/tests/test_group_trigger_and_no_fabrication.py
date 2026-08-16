"""Fast fake-DB/fake-LLM tests for the group smart-trigger port:

  - `agent_spoke_last` + `trigger_kind` derivation in
    `post_group_turn_route` (AC3)
  - `resolve_project_chat_intent`'s 3-tuple return, both callers unpacking
    it, and no stray 2-tuple unpack (AC4)
  - `_GroupEditOutcome`'s three cases (AC5)
  - the B2 narration guard — a completed "Done" claim only on a real
    `sections_changed` write (AC6)
  - the `run_tool_loop` fallback `edit_note` + the group system prompt's
    "no PRD-editing tool" rule, and that the "Done" literal is single-
    sourced (AC7)
  - `_ADDRESSING_NOTES` semantics + `trigger_kind`-based selection (AC8)
  - DRY source-scans: one classify-and-edit path, one resolver, shared by
    both surfaces (AC9)
  - no turn content leaking into any log line this ticket adds (AC11)

Every test here mocks the classifier/editor at the module seam
(`resolve_chat_intent`, `apply_chat_edit_scoped`, `resolve_project_chat_
intent`) against the in-memory fake Supabase (`isolated_settings`) — fast
and deterministic, proving the WIRING and the no-fabrication CONTRACT.

The real classifier's actual CONTINUATION/AMBIGUOUS judgment, and the real
write-vs-no-write B2 distinction end to end, are proven live in
`test_group_trigger_live.py` (env-gated) — a stubbed classifier here
cannot prove the model actually honors the prompt's new rules
([[feedback_stubbed-e2e-masks-loop-behaviour]])."""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.routes.projects as projects_route
from app.db.workspaces import ensure_default_workspace

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECTS_ROUTE_SRC = (REPO_ROOT / "backend" / "app" / "routes" / "projects.py").read_text()


def _seed_project(t, isolated_settings, *, name: str = "Trigger project") -> int:
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    return projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name=name, created_by=t.user_id,
    )["id"]


def _ctx(t) -> SimpleNamespace:
    return SimpleNamespace(
        company_id=t.company_id, workspace_id=ensure_default_workspace(t.company_id)["id"],
        user_id=t.user_id, user_email=None,
    )


# `_project_prd_ids` walks `list_artifacts_for_project` -> `list_artifacts_
# for_company`, which queries `prototypes` unconditionally — not in
# conftest's shared fake schema (same convention `test_project_intent_
# route.py`/`test_group_chat_prd_edit.py` already use). Only the two new
# clarify tests below need real PRDs, so this fixture is opt-in, not
# autouse.
_PROTOTYPE_DDL = """
CREATE TABLE IF NOT EXISTS prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    created_at             TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture
def _prototypes_table(isolated_settings):
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    yield


def _seed_prd(db_mod, dataset="acme", html="<html><body><h1>Doc</h1></body></html>"):
    brief_id = db_mod.save_brief(
        dataset=dataset, week_label="Week of stub",
        payload={"summary_headline": "s", "insights": [{"title": "I0"}], "_schema_version": 1},
        schema_version=1,
    )
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="Doc",
        template_version=1, variant="v3", source="chat", theme_id="chat:seed",
    )
    db_mod.complete_prd(prd_id, title="Doc", md=html)
    return prd_id


def _fake_loop_capturing(systems: list[str], *, reply: str = "unused"):
    """Patches the unified answer engine (`qa_agent.answer`) instead of the
    pre-collapse `run_tool_loop` call — captures the SAME assembled system
    text (`scope.system_addendum` + `scope.context_payload`, joined exactly
    the way `_respond_as_group_agent` builds it and the sixth ladder branch
    reassembles it) so every EDIT STATUS / addressing-note assertion below
    keeps working unchanged."""
    def _fake_answer(*, enterprise_id, question, dataset, scope=None, **kw):  # noqa: ARG001
        system = "\n\n".join(
            p for p in ((scope.system_addendum if scope else ""), (scope.context_payload if scope else "")) if p
        )
        systems.append(system)
        return {"answer": reply, "citations": []}
    return _fake_answer


# ── agent_spoke_last / trigger_kind derivation (AC3) ──────────────────────


def test_agent_spoke_last_derivation(tenant_client, isolated_settings, monkeypatch):
    """`recent[-2].role == 'assistant'` drives the flag; fewer than 2
    recent turns (nothing preceding, or only the just-posted turn) ⇒
    False."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    # A SECOND human member — a solo (single-human) project now bypasses the
    # gate entirely (the solo-project auto-respond fix) and `should_respond`
    # is never consulted, which this test needs to observe.
    from app.db import projects as projects_db

    projects_db.add_member(project_id, "second-human")

    calls: list[bool] = []
    monkeypatch.setattr(
        projects_route, "should_respond",
        lambda *a, agent_spoke_last=False, **kw: calls.append(agent_spoke_last) or False,  # noqa: ARG005
    )

    # First non-mention turn: only 1 recent turn (itself) -> False.
    r = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "does anyone know the deploy status?"},
    )
    assert r.status_code == 200
    assert calls == [False]

    # Seed an assistant turn directly (Sprntly having just replied), then
    # post a second non-mention human turn: recent[-2] is now the assistant
    # turn -> True.
    from app.db import conversations as conversations_db

    conv = conversations_db.get_group_chat(project_id)
    conversations_db.post_group_turn(conv["id"], None, "On it.", role="assistant")

    r2 = t.client.post(
        f"/v1/projects/{project_id}/group/turns", json={"content": "ok do that"},
    )
    assert r2.status_code == 200
    assert calls == [False, True]


def test_trigger_kind_mention_continuation_gate(tenant_client, isolated_settings, monkeypatch):
    """The three trigger kinds are derived and passed through to
    `_respond_as_group_agent`: mention branch -> "mention"; non-mention
    respond=True with a prior agent turn -> "continuation"; non-mention
    respond=True with no prior agent turn -> "gate"."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    # A SECOND human member — see test_agent_spoke_last_derivation above; the
    # "gate"/"continuation" trigger kinds are only reachable through a
    # multi-human project now that solo projects short-circuit to "solo".
    from app.db import projects as projects_db

    projects_db.add_member(project_id, "second-human")

    kinds: list[str] = []

    async def _capture(*a, **kw):
        # `_schedule_group_reply` passes trigger_kind as the 4th POSITIONAL
        # arg (job_id/run_id ride as keywords); the reply is now async.
        kinds.append(a[3] if len(a) > 3 else kw.get("trigger_kind", "mention"))

    monkeypatch.setattr(projects_route, "_respond_as_group_agent", _capture)

    # mention -> "mention" regardless of should_respond (never consulted on
    # the mention branch).
    monkeypatch.setattr(projects_route, "should_respond", lambda *a, **kw: False)
    r = t.client.post(
        f"/v1/projects/{project_id}/group/turns", json={"content": "@Sprntly hello"},
    )
    assert r.status_code == 200
    assert kinds == ["mention"]

    # non-mention, no prior assistant turn, should_respond True -> "gate".
    monkeypatch.setattr(projects_route, "should_respond", lambda *a, **kw: True)
    r2 = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "is anyone able to help with this today?"},
    )
    assert r2.status_code == 200
    assert kinds == ["mention", "gate"]

    # non-mention, prior turn IS an assistant turn, should_respond True ->
    # "continuation".
    from app.db import conversations as conversations_db

    conv = conversations_db.get_group_chat(project_id)
    conversations_db.post_group_turn(conv["id"], None, "Sure — on it.", role="assistant")
    r3 = t.client.post(
        f"/v1/projects/{project_id}/group/turns", json={"content": "ok go ahead"},
    )
    assert r3.status_code == 200
    assert kinds == ["mention", "gate", "continuation"]


# ── resolve_project_chat_intent 3-tuple (AC4) ──────────────────────────────


def test_resolve_project_chat_intent_returns_triple(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)  # no PRD -> unresolved

    monkeypatch.setattr(
        projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"},
    )
    envelope, prd_id, refusal = projects_route.resolve_project_chat_intent(
        project_id, "hello", [], t.slug, _ctx(t),
    )
    assert prd_id is None
    assert refusal == "This project has no PRD to edit."
    assert envelope["intent"] == "answer"

    # A resolved target carries refusal=None.
    monkeypatch.setattr(projects_route, "_resolve_prd_id", lambda *a, **kw: (7, None))
    envelope2, prd_id2, refusal2 = projects_route.resolve_project_chat_intent(
        project_id, "hello", [], t.slug, _ctx(t),
    )
    assert prd_id2 == 7
    assert refusal2 is None


def test_both_resolver_callers_unpack_triple():
    """Both callers of `resolve_project_chat_intent` unpack all three
    values; a source-scan proves NO 2-tuple unpack remains anywhere.

    UPDATED for the clarify fix: the private route no longer discards
    `refusal` (`_refusal`) — it now surfaces the >1-PRD disambiguation as a
    real `clarify` envelope, so BOTH callers bind the same `refusal` name."""
    src = PROJECTS_ROUTE_SRC
    assert src.count("envelope, prd_id, refusal = resolve_project_chat_intent(") == 2
    assert "envelope, prd_id, _refusal = resolve_project_chat_intent(" not in src
    assert "envelope, prd_id = resolve_project_chat_intent(" not in src
    assert src.count("resolve_project_chat_intent(") == 3  # def + 2 call sites


# ── _GroupEditOutcome three cases (AC5) ────────────────────────────────────


def test_group_edit_outcome_real_edit(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project_id, t.user_id)

    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(
        projects_route, "resolve_project_chat_intent",
        lambda *a, **kw: ({"intent": "edit_prd", "instruction": "do it"}, 42, None),
    )
    # Under the confirmation gate the classify pass PROPOSES via
    # `propose_chat_edit_scoped` (no immediate write); mock its result.
    monkeypatch.setattr(
        projects_route, "propose_chat_edit_scoped",
        lambda *a, **kw: {
            "proposed": True, "token": "tok-1", "summary": "Updated X.",
            "sections_changed": ["X"], "prd_id": 42,
        },
    )

    outcome = projects_route._classify_and_maybe_edit_group_prd(
        project_id, conv["id"], _ctx(t), "please update the PRD", [], t.slug,
    )
    assert outcome.applied_turn is not None
    assert outcome.was_edit_request is True
    assert outcome.refusal is None
    # The turn narrates a PROPOSAL (not a completed 'Done') and carries the
    # pending mutation for the client to confirm/cancel.
    assert outcome.applied_turn["content"].startswith("I'd like to update the PRD:")
    assert "Confirm to apply." in outcome.applied_turn["content"]
    assert outcome.applied_turn["reply"]["pending_mutation"] == {
        "token": "tok-1", "summary": "Updated X.", "prd_id": 42,
    }


def test_group_edit_outcome_requested_not_written(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project_id, t.user_id)

    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(
        projects_route, "resolve_project_chat_intent",
        lambda *a, **kw: (
            {"intent": "edit_prd", "instruction": "do it"}, None,
            "This project has no PRD to edit.",
        ),
    )
    scoped_calls: list[int] = []
    monkeypatch.setattr(
        projects_route, "apply_chat_edit_scoped", lambda *a, **kw: scoped_calls.append(1),  # noqa: ARG005
    )

    outcome = projects_route._classify_and_maybe_edit_group_prd(
        project_id, conv["id"], _ctx(t), "please update the PRD", [], t.slug,
    )
    assert outcome.applied_turn is None
    assert outcome.was_edit_request is True
    assert outcome.refusal == "This project has no PRD to edit."
    assert scoped_calls == []


def test_group_edit_outcome_flag_off_still_reports_was_edit_request(
    tenant_client, isolated_settings, monkeypatch
):
    """The flag-off case is ALSO a 'requested but not written' outcome —
    `was_edit_request` stays True (regardless of WHY nothing got written)
    so the fallback reply never silently treats a real edit request as an
    ordinary answer and risks implying it happened."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project_id, t.user_id)

    monkeypatch.delenv("PROJECT_PRD_EDIT_ENABLED", raising=False)
    monkeypatch.setattr(
        projects_route, "resolve_project_chat_intent",
        lambda *a, **kw: ({"intent": "edit_prd", "instruction": "do it"}, 42, None),
    )
    scoped_calls: list[int] = []
    monkeypatch.setattr(
        projects_route, "apply_chat_edit_scoped", lambda *a, **kw: scoped_calls.append(1),  # noqa: ARG005
    )

    outcome = projects_route._classify_and_maybe_edit_group_prd(
        project_id, conv["id"], _ctx(t), "please update the PRD", [], t.slug,
    )
    assert outcome.applied_turn is None
    assert outcome.was_edit_request is True
    assert scoped_calls == []


def test_group_edit_outcome_not_an_edit(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project_id, t.user_id)

    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(
        projects_route, "resolve_project_chat_intent",
        lambda *a, **kw: ({"intent": "answer"}, 42, None),
    )
    scoped_calls: list[int] = []
    monkeypatch.setattr(
        projects_route, "apply_chat_edit_scoped", lambda *a, **kw: scoped_calls.append(1),  # noqa: ARG005
    )

    outcome = projects_route._classify_and_maybe_edit_group_prd(
        project_id, conv["id"], _ctx(t), "what's the status?", [], t.slug,
    )
    assert outcome.applied_turn is None
    assert outcome.was_edit_request is False
    assert outcome.refusal is None
    assert scoped_calls == []


# ── B2 narration guard (AC6) ────────────────────────────────────────────


def test_narration_done_only_on_sections_changed(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project_id, t.user_id)

    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(
        projects_route, "resolve_project_chat_intent",
        lambda *a, **kw: ({"intent": "edit_prd", "instruction": "do it"}, 42, None),
    )
    monkeypatch.setattr(
        projects_route, "propose_chat_edit_scoped",
        lambda *a, **kw: {
            "proposed": True, "token": "tok-2", "summary": "Tightened it.",
            "sections_changed": ["Requirements"], "prd_id": 42,
        },
    )
    outcome = projects_route._classify_and_maybe_edit_group_prd(
        project_id, conv["id"], _ctx(t), "tighten it", [], t.slug,
    )
    content = outcome.applied_turn["content"]
    # A found edit narrates a PROPOSAL that names the summary and invites
    # confirmation — not a completed past-tense claim (B2 no-fabrication).
    assert content.startswith("I'd like to update the PRD:")
    assert "Tightened it." in content
    assert "Confirm to apply." in content
    assert outcome.applied_turn["reply"]["pending_mutation"]["token"] == "tok-2"


def test_narration_no_change_when_sections_empty(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project_id, t.user_id)

    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(
        projects_route, "resolve_project_chat_intent",
        lambda *a, **kw: ({"intent": "edit_prd", "instruction": "do it"}, 42, None),
    )
    # The editor found nothing to change: `propose_chat_edit_scoped` returns
    # `proposed=False` — no token, nothing to confirm.
    monkeypatch.setattr(
        projects_route, "propose_chat_edit_scoped",
        lambda *a, **kw: {"proposed": False, "summary": "", "sections_changed": []},
    )
    outcome = projects_route._classify_and_maybe_edit_group_prd(
        project_id, conv["id"], _ctx(t), "tighten it", [], t.slug,
    )
    content = outcome.applied_turn["content"]
    for claim in ("Done", "updated", "changed", "added"):
        assert claim not in content
    assert content == "I didn't find anything in the PRD to change for that."
    # A no-op proposes nothing, so the turn carries NO pending mutation.
    assert (outcome.applied_turn.get("reply") or {}).get("pending_mutation") is None


# ── B2 fallback edit_note + system prompt rule (AC7) ───────────────────────


def test_fallback_edit_note_forbids_write_claim(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)  # no PRD -> requested-but-unresolved

    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(
        projects_route, "resolve_chat_intent",
        lambda *a, **kw: {"intent": "edit_prd", "instruction": "do it"},
    )
    systems: list[str] = []
    monkeypatch.setattr(projects_route.qa_agent, "answer", _fake_loop_capturing(systems))

    r = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "@Sprntly please update the PRD"},
    )
    assert r.status_code == 200, r.text
    assert len(systems) == 1
    system = systems[0]
    assert "EDIT STATUS" in system
    assert "NO edit was made on this turn" in system
    assert "Do NOT say you added, updated, or changed anything" in system


def test_system_prompt_has_no_prd_tool_rule():
    system = projects_route._GROUP_AGENT_SYSTEM_PROMPT
    assert "NO PRD-editing tool in THIS reply" in system
    assert "NEVER claim you edited the document" in system

    weak_prompt = "You are a helpful assistant."
    assert "NO PRD-editing tool" not in weak_prompt


def test_done_narration_is_single_sourced():
    """Under the confirmation gate the completed-edit 'Done — I've updated the
    PRD' literal moves to the CONFIRM route (where the write actually
    commits). The classify/propose function now narrates a PROPOSAL
    ("I'd like to update the PRD") and never the completed claim; and the
    unified-engine fallback reply carries NEITHER literal — it only ever gets
    an `edit_note` steering it AWAY from claiming a write.

    This keeps each narration single-sourced at exactly the point the state it
    describes is true: a proposal at propose time, a 'Done' only at confirm."""
    src = PROJECTS_ROUTE_SRC
    confirm_start = src.index("def project_chat_edit_confirm(")
    cancel_start = src.index("def project_chat_edit_cancel(")
    classify_start = src.index("def _classify_and_maybe_edit_group_prd(")
    respond_start = src.index("def _respond_as_group_agent(")
    assert confirm_start < classify_start < respond_start

    confirm_body = src[confirm_start:cancel_start]
    classify_body = src[classify_start:respond_start]
    fallback_body = src[respond_start:]

    # The completed 'Done' narration lives in (and only in) the confirm route.
    assert "Done — I've updated the PRD" in confirm_body
    assert "Done — I've updated the PRD" not in classify_body, (
        "the classify/propose path must not claim a completed edit — the write "
        "hasn't happened yet at propose time"
    )
    assert "Done — I've updated the PRD" not in fallback_body, (
        "the unified-engine fallback path must never carry the completed-"
        "edit narration literal — a second producer there would let the "
        "reply fabricate a completed edit claim"
    )

    # The PROPOSAL narration is single-sourced in the classify/propose path and
    # guarded by the `proposed` flag; the fallback never carries it either.
    assert classify_body.count("I'd like to update the PRD") >= 1, (
        "expected the proposal narration literal inside the classify/propose function"
    )
    assert 'proposal.get("proposed")' in classify_body
    assert "I'd like to update the PRD" not in fallback_body


# ── _ADDRESSING_NOTES (AC8) ─────────────────────────────────────────────


def test_addressing_notes_mention_continuation_suppress_question():
    for kind in ("mention", "continuation"):
        note = projects_route._ADDRESSING_NOTES[kind]
        assert "do NOT ask whether" in note


def test_addressing_notes_gate_invites_question():
    note = projects_route._ADDRESSING_NOTES["gate"]
    assert "Are you assigning this to me?" in note
    assert "do NOT assume it is" in note


def test_respond_selects_addressing_note_by_trigger_kind(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(
        projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"},
    )
    systems: list[str] = []
    monkeypatch.setattr(projects_route.qa_agent, "answer", _fake_loop_capturing(systems))

    # mention -> mention note.
    project_mention = _seed_project(t, isolated_settings, name="mention project")
    r = t.client.post(
        f"/v1/projects/{project_mention}/group/turns", json={"content": "@Sprntly hi there"},
    )
    assert r.status_code == 200, r.text
    assert projects_route._ADDRESSING_NOTES["mention"] in systems[-1]

    # gate -> gate note (fresh project, no prior turns). A SECOND human
    # member is required — a solo project short-circuits to the "solo" note
    # instead of ever reaching `should_respond`.
    from app.db import projects as projects_db

    monkeypatch.setattr(projects_route, "should_respond", lambda *a, **kw: True)
    project_gate = _seed_project(t, isolated_settings, name="gate project")
    projects_db.add_member(project_gate, "second-human")
    r2 = t.client.post(
        f"/v1/projects/{project_gate}/group/turns",
        json={"content": "is anyone free to help today?"},
    )
    assert r2.status_code == 200, r2.text
    assert projects_route._ADDRESSING_NOTES["gate"] in systems[-1]

    # continuation -> continuation note (fresh project, seeded assistant
    # turn). Same second-member requirement as the gate case above.
    project_cont = _seed_project(t, isolated_settings, name="continuation project")
    projects_db.add_member(project_cont, "second-human")
    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project_cont, t.user_id)
    conversations_db.post_group_turn(conv["id"], None, "Sure.", role="assistant")
    r3 = t.client.post(
        f"/v1/projects/{project_cont}/group/turns", json={"content": "ok go ahead"},
    )
    assert r3.status_code == 200, r3.text
    assert projects_route._ADDRESSING_NOTES["continuation"] in systems[-1]


# ── DRY (AC9, Gate-1 check) ─────────────────────────────────────────────


def test_single_classify_edit_path_no_fork():
    """Every trigger kind runs the SAME `_classify_and_maybe_edit_group_
    prd` call inside `_respond_as_group_agent` — a source-scan proves
    exactly one call site (plus its one `def`), not a per-trigger-kind
    duplicate classify/edit path, and that every kind falls through to the
    SAME unified-engine call (`qa_agent.answer`, RELOCATED from the former
    `run_tool_loop` call this ticket replaces — post-collapse the literal
    call is gone from this file; historical references survive only in
    prose docstrings, not in any executable line)."""
    src = PROJECTS_ROUTE_SRC
    assert src.count("_classify_and_maybe_edit_group_prd(") == 2  # def + 1 call site
    assert src.count("result = qa_agent.answer(") == 1  # one shared reply call, no fork
    assert "reply = run_tool_loop(" not in src
    assert "import run_tool_loop" not in src


def test_one_resolver_shared_by_both_surfaces():
    """Both project chat surfaces call the SAME `resolve_project_chat_
    intent` — no second resolver or duplicate inline resolve+classify
    pair."""
    src = PROJECTS_ROUTE_SRC
    assert src.count("def resolve_project_chat_intent(") == 1
    intent_route_body = src[
        src.index("def project_chat_intent("):src.index("def resolve_project_chat_intent(")
    ]
    classify_body = src[src.index("def _classify_and_maybe_edit_group_prd("):]
    assert "resolve_project_chat_intent(" in intent_route_body
    assert "resolve_project_chat_intent(" in classify_body
    # The mention gate reuses the existing deterministic regex, not a
    # second matcher.
    assert src.count("_MENTION_RE = re.compile(") == 1


# ── Observability: no content leaks (AC11) ─────────────────────────────────


def test_gate_logs_decision_and_reason_no_content(tenant_client, isolated_settings, monkeypatch, caplog):
    """No log line this ticket adds (group_turn_posted, the agent_spoke_
    last/trigger_kind derivation) carries turn content — identifiers/
    reason only (R8), matching the existing gate's posture."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    monkeypatch.setattr(projects_route, "should_respond", lambda *a, **kw: False)

    secret = "SECRET_TRIGGER_CONTENT_DO_NOT_LOG"
    with caplog.at_level(logging.INFO):
        r = t.client.post(
            f"/v1/projects/{project_id}/group/turns", json={"content": f"ok do that {secret}"},
        )
    assert r.status_code == 200
    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert secret not in joined


# ── Group PRD-edit clarify — the SAME content-derived signal the private ────
# route uses, driven through the REAL classify path (not a fabricated
# tuple): `resolve_project_chat_intent`/`_resolve_prd_id` run for real over
# actually-seeded project PRDs; only `resolve_chat_intent` (the LLM
# classify call) is mocked, mirroring the REAL `_NEEDS_PRD` downgrade
# (chat_intent.py) for `prd_id=None`.


def test_group_asks_which_prd_on_two_prds_via_real_classify(
    tenant_client, isolated_settings, monkeypatch, _prototypes_table,
):
    """An edit-phrased group turn on a genuinely 2-PRD project sets
    `_GroupEditOutcome.needs_prd_clarify=True` and steers the fallback
    reply's EDIT STATUS note to ask which PRD, via the single-sourced
    `_project_prd_ids`/refusal listing — end to end through the real HTTP
    route + the real `_resolve_prd_id`. On the UNFIXED route `was_edit_
    request` is False (the downgrade rewrote `edit_prd` -> `answer`) so no
    `edit_note` is produced at all (the red)."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import projects as projects_db

    prd_a = _seed_prd(isolated_settings["db"], dataset=t.slug)
    prd_b = _seed_prd(isolated_settings["db"], dataset=t.slug)
    projects_db.add_artifact(project_id, "prd", prd_a)
    projects_db.add_artifact(project_id, "prd", prd_b)

    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")

    def _fake_classify(company_id, message, history, *, prd_id=None, **kw):
        # The REAL `_NEEDS_PRD` downgrade's shape: an edit-phrased turn
        # whose target failed to resolve (prd_id=None, from the REAL
        # `_resolve_prd_id` over the 2 seeded PRDs) comes back rewritten to
        # `answer`, `source="no_target_prd"`; a resolved target keeps
        # `edit_prd`.
        if prd_id is None:
            return {"intent": "answer", "source": "no_target_prd", "instruction": None}
        return {"intent": "edit_prd", "instruction": "do it", "source": "llm"}

    monkeypatch.setattr(projects_route, "resolve_chat_intent", _fake_classify)
    systems: list[str] = []
    monkeypatch.setattr(projects_route.qa_agent, "answer", _fake_loop_capturing(systems))

    r = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "@Sprntly please update the PRD"},
    )
    assert r.status_code == 200, r.text
    assert len(systems) == 1
    system = systems[0]
    assert "EDIT STATUS" in system
    assert "more than one PRD" in system
    assert "Do NOT say you added, updated, or changed anything" in system


def test_group_plain_message_two_prds_no_clarify(
    tenant_client, isolated_settings, monkeypatch, _prototypes_table,
):
    """NEGATIVE regression (group equivalent of AC6, the over-fire guard):
    a PLAIN non-edit group message on a genuinely 2-PRD project has
    `source != "no_target_prd"` (the real classifier never downgrades a
    non-edit turn) -> `needs_prd_clarify=False` -> no "which PRD" edit_note.
    Keying the signal off `edit.refusal` truthiness instead (which is
    non-None on THIS project for EVERY message, since `_resolve_prd_id`
    depends only on PRD count) would make this test RED — that is exactly
    the over-fire regression this ticket must not reintroduce."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import conversations as conversations_db
    from app.db import projects as projects_db

    prd_a = _seed_prd(isolated_settings["db"], dataset=t.slug)
    prd_b = _seed_prd(isolated_settings["db"], dataset=t.slug)
    projects_db.add_artifact(project_id, "prd", prd_a)
    projects_db.add_artifact(project_id, "prd", prd_b)
    conv = conversations_db.create_group_chat(project_id, t.user_id)

    # A plain question — the real classifier's own `intent`/`source` never
    # downgrade a non-edit turn, regardless of the project's PRD count.
    monkeypatch.setattr(
        projects_route, "resolve_chat_intent",
        lambda company_id, message, history, *, prd_id=None, **kw: {
            "intent": "answer", "source": "llm", "instruction": None,
        },
    )

    outcome = projects_route._classify_and_maybe_edit_group_prd(
        project_id, conv["id"], _ctx(t), "what's the status?", [], t.slug,
    )
    # Proves the over-fire trap is real: `refusal` IS set on this 2-PRD
    # project (PRD-count-derived, content-independent) — but the content-
    # derived signal correctly stays False.
    assert outcome.refusal is not None
    assert outcome.needs_prd_clarify is False
    assert outcome.was_edit_request is False
