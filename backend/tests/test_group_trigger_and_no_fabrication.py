"""Fast fake-DB/fake-LLM tests for the group smart-trigger port:

  - `agent_spoke_last` + `trigger_kind` derivation in
    `post_group_turn_route` (AC3)
  - `resolve_project_chat_intent`'s 3-tuple return, the PRIVATE route
    unpacking it, and no stray 2-tuple unpack (AC4)
  - the in-band `edit_prd` tool handler `_propose_group_prd_edit`'s cases —
    proposed / unresolved-target / flag-off (AC7/AC8)
  - the B2 narration guard — a completed "Done" claim only on a real
    `sections_changed` write, and that the "Done" literal is single-sourced
    to the confirm route (AC6/AC8)
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


def test_private_resolver_caller_unpacks_triple():
    """The PRIVATE route unpacks all three values from
    `resolve_project_chat_intent`; a source-scan proves NO 2-tuple unpack
    remains. The GROUP surface no longer calls this helper (it edits
    in-band via the `edit_prd` tool, resolving its own target), so there is
    now exactly ONE call site (the private route) + the `def`."""
    src = PROJECTS_ROUTE_SRC
    assert src.count("envelope, prd_id, refusal = resolve_project_chat_intent(") == 1
    assert "envelope, prd_id, _refusal = resolve_project_chat_intent(" not in src
    assert "envelope, prd_id = resolve_project_chat_intent(" not in src
    assert src.count("resolve_project_chat_intent(") == 2  # def + 1 call site (private)


# ── The in-band `edit_prd` tool handler `_propose_group_prd_edit` (AC7/AC8) ──


def test_edit_tool_proposes_and_hands_back_pending(tenant_client, isolated_settings, monkeypatch):
    """A found edit → a PROPOSAL narration (not a completed 'Done') plus the
    pending mutation the group turn stamps onto `reply.pending_mutation`."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)

    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(projects_route, "_resolve_prd_id", lambda *a, **kw: (42, None))
    monkeypatch.setattr(
        projects_route, "propose_chat_edit_scoped",
        lambda *a, **kw: {
            "proposed": True, "token": "tok-1", "summary": "Updated X.",
            "sections_changed": ["X"], "prd_id": 42,
        },
    )
    narration, pending = projects_route._propose_group_prd_edit(
        project_id, 1, _ctx(t), t.slug, "please update the PRD",
    )
    assert narration.startswith("I'd like to update the PRD:")
    assert "Confirm to apply." in narration
    assert pending == {"token": "tok-1", "summary": "Updated X.", "prd_id": 42}


def test_edit_tool_unresolved_target_asks_no_write(tenant_client, isolated_settings, monkeypatch):
    """No target resolves (no/ambiguous PRD) → the server-resolved refusal is
    the narration (the model relays it to ask which PRD); nothing proposed."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)

    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(
        projects_route, "_resolve_prd_id",
        lambda *a, **kw: (None, "This project has no PRD to edit."),
    )
    proposed: list[int] = []
    monkeypatch.setattr(
        projects_route, "propose_chat_edit_scoped", lambda *a, **kw: proposed.append(1),  # noqa: ARG005
    )
    narration, pending = projects_route._propose_group_prd_edit(
        project_id, 1, _ctx(t), t.slug, "please update the PRD",
    )
    assert narration == "This project has no PRD to edit."
    assert pending is None
    assert proposed == []


def test_edit_tool_flag_off_no_propose(tenant_client, isolated_settings, monkeypatch):
    """Flag off → a plain 'not turned on' narration, no propose, no write."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)

    monkeypatch.delenv("PROJECT_PRD_EDIT_ENABLED", raising=False)
    proposed: list[int] = []
    monkeypatch.setattr(
        projects_route, "propose_chat_edit_scoped", lambda *a, **kw: proposed.append(1),  # noqa: ARG005
    )
    narration, pending = projects_route._propose_group_prd_edit(
        project_id, 1, _ctx(t), t.slug, "please update the PRD",
    )
    assert "isn't turned on" in narration
    assert pending is None
    assert proposed == []


# ── B2 narration guard (AC6/AC8) ──────────────────────────────────────────


def test_narration_proposal_names_summary(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)

    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(projects_route, "_resolve_prd_id", lambda *a, **kw: (42, None))
    monkeypatch.setattr(
        projects_route, "propose_chat_edit_scoped",
        lambda *a, **kw: {
            "proposed": True, "token": "tok-2", "summary": "Tightened it.",
            "sections_changed": ["Requirements"], "prd_id": 42,
        },
    )
    narration, pending = projects_route._propose_group_prd_edit(
        project_id, 1, _ctx(t), t.slug, "tighten it",
    )
    # A found edit narrates a PROPOSAL that names the summary and invites
    # confirmation — not a completed past-tense claim (B2 no-fabrication).
    assert narration.startswith("I'd like to update the PRD:")
    assert "Tightened it." in narration
    assert "Confirm to apply." in narration
    assert pending["token"] == "tok-2"


def test_narration_no_change_when_sections_empty(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)

    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(projects_route, "_resolve_prd_id", lambda *a, **kw: (42, None))
    # The editor found nothing to change: `propose_chat_edit_scoped` returns
    # `proposed=False` — no token, nothing to confirm.
    monkeypatch.setattr(
        projects_route, "propose_chat_edit_scoped",
        lambda *a, **kw: {"proposed": False, "summary": "", "sections_changed": []},
    )
    narration, pending = projects_route._propose_group_prd_edit(
        project_id, 1, _ctx(t), t.slug, "tighten it",
    )
    for claim in ("Done", "updated", "changed", "added"):
        assert claim not in narration
    assert narration == "I didn't find anything in the PRD to change for that."
    # A no-op proposes nothing, so the turn carries NO pending mutation.
    assert pending is None


def test_group_system_carries_shared_contract_and_edit_via_confirm():
    """The group system base (`_GROUP_SCOPE_SYSTEM`) now carries the SAME
    project-surface behavioral contract private carries — the read-tool /
    retrieval / synthesis / tenancy framing it previously omitted — and the
    group HAS an in-band edit tool, so the old "you have NO PRD-editing tool"
    clause is gone in favour of the private-style edit-applies-via-confirm
    framing. The retired symbol `_GROUP_AGENT_SYSTEM_PROMPT` no longer exists."""
    system = projects_route._GROUP_SCOPE_SYSTEM
    # Read-tool + retrieval + synthesis + tenancy framing (previously omitted).
    assert "get_project_memory" in system
    assert "list_project_artifacts" in system
    assert "get_artifact_content" in system
    assert "get_task_ledger" in system
    assert "synthesize" in system
    assert "THIS project only" in system
    # No longer a retrieval-suppressing hard cap.
    assert "a few sentences, not a document" not in system
    # The group now HAS an edit tool; the old "no edit tool" contract is gone.
    assert "NO PRD-editing tool" not in system
    assert "edit_prd" in system
    # Edit-applies-via-confirm framing (newline-insensitive).
    assert "the team confirms it before it takes" in system
    assert "proposed the change" in system
    # The retired symbol is gone.
    assert not hasattr(projects_route, "_GROUP_AGENT_SYSTEM_PROMPT")


def test_done_narration_is_single_sourced():
    """Under the confirmation gate the completed-edit 'Done — I've updated the
    PRD' literal lives ONLY in the CONFIRM route (where the write actually
    commits). The in-band edit tool handler (`_propose_group_prd_edit`)
    narrates a PROPOSAL ("I'd like to update the PRD") and never the completed
    claim; and the unified-engine reply body (`_respond_as_group_agent`)
    carries NEITHER literal — it only stamps whatever the tool proposed.

    This keeps each narration single-sourced at exactly the point the state it
    describes is true: a proposal at propose time, a 'Done' only at confirm."""
    src = PROJECTS_ROUTE_SRC
    confirm_start = src.index("def project_chat_edit_confirm(")
    cancel_start = src.index("def project_chat_edit_cancel(")
    propose_start = src.index("def _propose_group_prd_edit(")
    respond_start = src.index("def _respond_as_group_agent(")
    assert confirm_start < propose_start < respond_start

    confirm_body = src[confirm_start:cancel_start]
    propose_body = src[propose_start:respond_start]
    reply_body = src[respond_start:]

    # The completed 'Done' narration lives in (and only in) the confirm route.
    assert "Done — I've updated the PRD" in confirm_body
    assert "Done — I've updated the PRD" not in propose_body, (
        "the propose path must not claim a completed edit — the write "
        "hasn't happened yet at propose time"
    )
    assert "Done — I've updated the PRD" not in reply_body, (
        "the unified-engine reply path must never carry the completed-"
        "edit narration literal — a second producer there would let the "
        "reply fabricate a completed edit claim"
    )

    # The PROPOSAL narration is single-sourced in the tool handler and guarded
    # by the `proposed` flag; the reply body never carries it either.
    assert propose_body.count("I'd like to update the PRD") >= 1, (
        "expected the proposal narration literal inside `_propose_group_prd_edit`"
    )
    assert 'proposal.get("proposed")' in propose_body
    assert "I'd like to update the PRD" not in reply_body


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


def test_group_edit_is_in_band_tool_not_a_resolver_fork():
    """The GROUP surface no longer classifies-to-edit through `resolve_project_
    chat_intent` (that helper is now the PRIVATE route's alone). The group
    edits in-band via the `edit_prd` tool handler `_propose_group_prd_edit`,
    which resolves its OWN target via `_resolve_prd_id` and routes to the
    shared `propose_chat_edit_scoped` gate."""
    src = PROJECTS_ROUTE_SRC
    assert src.count("def resolve_project_chat_intent(") == 1
    intent_route_body = src[
        src.index("def project_chat_intent("):src.index("def resolve_project_chat_intent(")
    ]
    assert "resolve_project_chat_intent(" in intent_route_body  # private route uses it
    # The group edit tool handler resolves server-side + routes to the gate.
    propose_body = src[
        src.index("def _propose_group_prd_edit("):src.index("def _respond_as_group_agent(")
    ]
    assert "_resolve_prd_id({}" in propose_body
    assert "propose_chat_edit_scoped(" in propose_body
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


# ── Group PRD-edit clarify — the in-band tool asks which PRD via the REAL ────
# server-side `_resolve_prd_id` over actually-seeded project PRDs (no mocked
# resolution). The tool schema omits `prd_id`, so a 2-PRD project can only ask
# — never auto-pick — mirroring the private route's own clarify posture.


def test_group_edit_asks_which_prd_on_two_prds_via_real_resolver(
    tenant_client, isolated_settings, monkeypatch, _prototypes_table,
):
    """An edit request on a genuinely 2-PRD project → the tool handler's real
    `_resolve_prd_id({})` returns the "more than one PRD" refusal, which the
    handler narrates (asking which one) and proposes NOTHING. No write, no
    proposal row — server-resolved, never auto-picked (AC7a)."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import projects as projects_db

    prd_a = _seed_prd(isolated_settings["db"], dataset=t.slug)
    prd_b = _seed_prd(isolated_settings["db"], dataset=t.slug)
    projects_db.add_artifact(project_id, "prd", prd_a)
    projects_db.add_artifact(project_id, "prd", prd_b)

    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    proposed: list[int] = []
    monkeypatch.setattr(
        projects_route, "propose_chat_edit_scoped", lambda *a, **kw: proposed.append(1),  # noqa: ARG005
    )

    narration, pending = projects_route._propose_group_prd_edit(
        project_id, 1, _ctx(t), t.slug, "please update the PRD",
    )
    assert "more than one PRD" in narration  # asks which one
    assert str(prd_a) in narration and str(prd_b) in narration
    assert pending is None
    assert proposed == []  # never auto-picks / writes


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
