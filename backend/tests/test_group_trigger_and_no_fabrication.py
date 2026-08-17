"""Fast fake-DB/fake-LLM tests for the group smart-trigger port:

  - `agent_spoke_last` + `trigger_kind` derivation in
    `post_group_turn_route` (AC3)
  - `_ADDRESSING_NOTES` semantics + `trigger_kind`-based selection (AC8)
  - DRY source-scans: one classify path, shared by both surfaces (AC9)
  - no turn content leaking into any log line this ticket adds (AC11)

The in-band `edit_prd` tool's direct-apply behavior (proposed edit /
unresolved target / flag-off) and the completed "Done" narration are covered
by `test_project_prd_edit_parity.py` now that the edit applies in-band
without a confirm step.

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


def test_single_reply_path_no_fork():
    """Every trigger kind falls through to the SAME unified-engine call
    (`qa_agent.answer`) — a source-scan proves ONE shared reply call, no
    per-trigger-kind fork. The pre-classify edit fork is retired (the edit is
    an in-band tool now): `_classify_and_maybe_edit_group_prd`/`_GroupEditOutcome`
    are gone, and `_classify_group_envelope` (card enrichment only) has exactly
    one call site (plus its `def`)."""
    src = PROJECTS_ROUTE_SRC
    assert "_classify_and_maybe_edit_group_prd(" not in src
    assert "_GroupEditOutcome" not in src
    assert src.count("_classify_group_envelope(") == 2  # def + 1 call site
    assert src.count("result = qa_agent.answer(") == 1  # one shared reply call, no fork
    assert "reply = run_tool_loop(" not in src
    assert "import run_tool_loop" not in src


def test_group_edit_is_in_band_tool_not_a_classify_fork():
    """The GROUP surface no longer classifies-to-edit through
    `resolve_project_chat_intent` (that helper is now the PRIVATE route's
    alone). The group edits in-band via the `edit_prd` tool handler
    (`_edit_prd_handler`, closed over the turn's own resolved target),
    applying directly through the shared editor."""
    src = PROJECTS_ROUTE_SRC
    assert src.count("def resolve_project_chat_intent(") == 1
    intent_route_body = src[
        src.index("def project_chat_intent("):src.index("def resolve_project_chat_intent(")
    ]
    assert "resolve_project_chat_intent(" in intent_route_body  # private route uses it
    # The group edit tool handler applies directly through the shared editor.
    reply_body = src[src.index("def _respond_as_group_agent("):]
    assert "def _edit_prd_handler(" in reply_body
    assert "apply_chat_edit_scoped(" in reply_body
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


def test_group_plain_message_never_reaches_the_edit_tool():
    """NEGATIVE regression (the over-fire guard, restated for ): a PLAIN
    non-edit message does NOT pass the `is_project_edit_request` gate, so it
    can never reach the in-band `edit_prd` tool — the over-fire the old
    `needs_prd_clarify` signal guarded against is now structurally impossible
    (the tool only runs when the model calls it on an edit-gated turn)."""
    from app.skill_router import is_project_edit_request

    assert is_project_edit_request("what's the status?") is False
    assert is_project_edit_request("who is on this project?") is False
    assert is_project_edit_request("thanks team!") is False
    # An actual edit request still passes.
    assert is_project_edit_request("update the PRD to add a section") is True
