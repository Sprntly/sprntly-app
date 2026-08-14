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


def _fake_loop_capturing(systems: list[str], *, reply: str = "unused"):
    def _fake_loop(*, system, user, tools, dispatch, model, meta_out=None, **kw):  # noqa: ARG001
        systems.append(system)
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model, "input_tokens": 1, "output_tokens": 1,
                    "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                }
            )
        return reply
    return _fake_loop


# ── agent_spoke_last / trigger_kind derivation (AC3) ──────────────────────


def test_agent_spoke_last_derivation(tenant_client, isolated_settings, monkeypatch):
    """`recent[-2].role == 'assistant'` drives the flag; fewer than 2
    recent turns (nothing preceding, or only the just-posted turn) ⇒
    False."""
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)

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

    kinds: list[str] = []
    monkeypatch.setattr(
        projects_route, "_respond_as_group_agent",
        lambda *a, trigger_kind="mention", **kw: kinds.append(trigger_kind),  # noqa: ARG005
    )

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
    values; a source-scan proves NO 2-tuple unpack remains anywhere."""
    src = PROJECTS_ROUTE_SRC
    assert "envelope, prd_id, _refusal = resolve_project_chat_intent(" in src
    assert "envelope, prd_id, refusal = resolve_project_chat_intent(" in src
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
    monkeypatch.setattr(
        projects_route, "apply_chat_edit_scoped",
        lambda *a, **kw: {"prd": {"id": 42}, "sections_changed": ["X"], "summary": "Updated X."},
    )

    outcome = projects_route._classify_and_maybe_edit_group_prd(
        project_id, conv["id"], _ctx(t), "please update the PRD", [], t.slug,
    )
    assert outcome.applied_turn is not None
    assert outcome.was_edit_request is True
    assert outcome.refusal is None
    assert outcome.applied_turn["content"] == "Done — I've updated the PRD. Updated X."


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
        projects_route, "apply_chat_edit_scoped",
        lambda *a, **kw: {
            "prd": {"id": 42}, "sections_changed": ["Requirements"], "summary": "Tightened it.",
        },
    )
    outcome = projects_route._classify_and_maybe_edit_group_prd(
        project_id, conv["id"], _ctx(t), "tighten it", [], t.slug,
    )
    content = outcome.applied_turn["content"]
    assert content.startswith("Done — I've updated the PRD.")
    assert "Tightened it." in content


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
    monkeypatch.setattr(
        projects_route, "apply_chat_edit_scoped",
        lambda *a, **kw: {"prd": {"id": 42}, "sections_changed": [], "summary": ""},
    )
    outcome = projects_route._classify_and_maybe_edit_group_prd(
        project_id, conv["id"], _ctx(t), "tighten it", [], t.slug,
    )
    content = outcome.applied_turn["content"]
    for claim in ("Done", "updated", "changed", "added"):
        assert claim not in content
    assert content == "I didn't find anything in the PRD to change for that."


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
    monkeypatch.setattr(projects_route, "run_tool_loop", _fake_loop_capturing(systems))

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
    """The 'Done — I've updated the PRD' literal (the ternary's two
    branches both use it — with vs. without an appended summary) appears
    ONLY inside the `sections_changed`-guarded narration block of
    `_classify_and_maybe_edit_group_prd` — the `run_tool_loop` fallback
    (built inside `_respond_as_group_agent`, further down the file) has no
    such literal anywhere; it only ever gets an `edit_note` steering it
    AWAY from claiming a write."""
    src = PROJECTS_ROUTE_SRC
    classify_start = src.index("def _classify_and_maybe_edit_group_prd(")
    respond_start = src.index("def _respond_as_group_agent(")
    assert respond_start > classify_start

    classify_body = src[classify_start:respond_start]
    fallback_body = src[respond_start:]

    occurrences = classify_body.count("Done — I've updated the PRD")
    assert occurrences >= 1, "expected the narration literal inside the classify/edit function"
    assert 'result.get("sections_changed")' in classify_body

    assert "Done — I've updated the PRD" not in fallback_body, (
        "the run_tool_loop fallback path must never carry the completed-"
        "edit narration literal — a second producer there would let the "
        "reply fabricate a completed edit claim"
    )


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
    monkeypatch.setattr(projects_route, "run_tool_loop", _fake_loop_capturing(systems))

    # mention -> mention note.
    project_mention = _seed_project(t, isolated_settings, name="mention project")
    r = t.client.post(
        f"/v1/projects/{project_mention}/group/turns", json={"content": "@Sprntly hi there"},
    )
    assert r.status_code == 200, r.text
    assert projects_route._ADDRESSING_NOTES["mention"] in systems[-1]

    # gate -> gate note (fresh project, no prior turns).
    monkeypatch.setattr(projects_route, "should_respond", lambda *a, **kw: True)
    project_gate = _seed_project(t, isolated_settings, name="gate project")
    r2 = t.client.post(
        f"/v1/projects/{project_gate}/group/turns",
        json={"content": "is anyone free to help today?"},
    )
    assert r2.status_code == 200, r2.text
    assert projects_route._ADDRESSING_NOTES["gate"] in systems[-1]

    # continuation -> continuation note (fresh project, seeded assistant turn).
    project_cont = _seed_project(t, isolated_settings, name="continuation project")
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
    SAME `run_tool_loop` call."""
    src = PROJECTS_ROUTE_SRC
    assert src.count("_classify_and_maybe_edit_group_prd(") == 2  # def + 1 call site
    assert src.count("reply = run_tool_loop(") == 1  # one shared reply call, no fork


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
