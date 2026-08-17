"""★ The @Sprntly GROUP agent's in-band `edit_prd` tool — the group-surface
half of the shared scoped-edit + confirm gate proven privately.

Composition: `post_group_turn_route` decides whether to reply at all (an
explicit `@Sprntly` mention, deterministic, or a `should_respond=True`
smart-interjection verdict) — that gate is UNCHANGED and re-verified here
only at its edges. Once `_respond_as_group_agent` runs, the reply is produced
by the unified engine (`qa_agent.answer`, scoped to this project) with the
GROUP-only `edit_prd` tool in its `extra_tools`. When the turn asks for a PRD
change, the model calls `edit_prd` mid-answer; the handler
(`_propose_group_prd_edit`) resolves the target SERVER-SIDE (`_resolve_prd_id`
over THIS project's own PRDs, NEVER a client/model id — the tool schema omits
`prd_id`) and routes to the SAME `propose_chat_edit_scoped` the private
surface uses — the ★ IDOR gate (`assert_prd_on_project` then
`require_owned_prd`) fires identically. Propose writes NOTHING; the existing
`POST /{id}/prd/chat-edit/confirm` route applies exactly the stored patch
(applied == proposed). The proposal rides back out of the tool loop as the
group turn's `reply.pending_mutation` so the FE confirm card fires.

There is NO pre-classify edit fork any more — `_classify_and_maybe_edit_
group_prd` is retired; the group turn's own classify runs ONLY for card
enrichment (`_classify_group_envelope`), never to decide/apply an edit.

The editor LLM call (`app.prd_questions.apply_chat_edit`) is mocked the same
way every chat-edit test in this repo mocks it; the model's own tool call is
driven by stubbing `app.llm.run_tool_loop` to invoke `dispatch("edit_prd",
...)` (the real handler + real gate then run against the fake in-memory
Supabase, so the mutation proofs are genuine row counts). The real
tool-calling-reliability arm (does the model reliably CALL `edit_prd`) is the
env-gated `test_group_chat_prd_edit_live.py` / ship-gate's job.

The two IDOR proofs (cross-project / cross-tenant) call the tool handler
`_propose_group_prd_edit` DIRECTLY with `pytest.raises` — the handler is the
one that PROPAGATES a gate refusal (`run_tool_loop` would otherwise fold a
dispatch exception into a tool_result string). Both force the resolved target
via a monkeypatched `_resolve_prd_id` rather than an organic cross-tenant
resolution: `list_artifacts_for_project` already intersects a project's own
refs with the CALLER's tenant-scoped fan-out, so a foreign-tenant id can never
be resolved from a real project's own manifest — forcing it is what proves the
★ gate is a second, independent check.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.prd_questions as prd_questions
import app.project_chat_edit as pce
import app.project_group_context as pgc
import app.routes.projects as projects_route
from tests import _fake_supabase
from app.db.client import require_client
from app.db.workspaces import ensure_default_workspace
from app.project_prd_gate import ProjectPrdWriteDenied

# `_resolve_prd_id` walks `list_artifacts_for_project` -> `list_artifacts_for_
# company`, which queries `prototypes` unconditionally — deliberately NOT in
# conftest's shared fake schema (mirrors `test_projects_prd_chat_edit_route.py`'s
# own trimmed copy, same convention as `test_project_artifacts_fanout.py`).
_PROTOTYPE_DDL = """
CREATE TABLE IF NOT EXISTS prototypes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id INTEGER,
    workspace_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'generating',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture(autouse=True)
def _prototypes_table(isolated_settings):
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


def _versions(prd_id):
    return (
        require_client().table("prd_versions").select("*")
        .eq("prd_id", prd_id).execute().data or []
    )


def _payload(prd_id):
    return require_client().table("prds").select("payload_md").eq(
        "id", prd_id
    ).execute().data[0]["payload_md"]


def _seed_project(t, isolated_settings, *, with_prd: bool = True, prd_count: int = 1):
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    project = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name="Group PRD project",
        created_by=t.user_id,
    )
    prd_ids = []
    if with_prd:
        for _ in range(prd_count):
            prd_id = _seed_prd(isolated_settings["db"], dataset=t.slug)
            projects_db.add_artifact(project["id"], "prd", prd_id)
            prd_ids.append(prd_id)
    return project["id"], (prd_ids[0] if prd_ids else None)


def _ctx(t):
    return SimpleNamespace(
        company_id=t.company_id, workspace_id=ensure_default_workspace(t.company_id)["id"],
        user_id=t.user_id, user_email=None,
    )


def _group_conv(project_id):
    from app.db import conversations as conversations_db

    return conversations_db.get_group_chat(project_id)


def _group_turns(project_id):
    from app.db import conversations as conversations_db

    conv = conversations_db.get_group_chat(project_id)
    if not conv:
        return []
    return conversations_db.list_group_turns(conv["id"])


def _mock_editor(monkeypatch, *, html="<html><body><h1>Group v2</h1></body></html>",
                 sections=("X",), summary="Updated X."):
    monkeypatch.setattr(prd_questions, "apply_chat_edit", lambda *a, **kw: {
        "html": html, "sections_changed": list(sections), "summary": summary,
    })


def _drive_edit_tool(instruction="tighten the requirements"):
    """A `run_tool_loop` stub that makes the model call `edit_prd` once."""
    return lambda *, dispatch, **kw: dispatch("edit_prd", {"instruction": instruction})


# ── AC1 — classify/reply only after should_respond ──────────────────────────
def test_group_classifies_only_after_should_respond(tenant_client, isolated_settings, monkeypatch):
    from app.db import projects as projects_db

    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)
    projects_db.add_member(project_id, "second-human")
    monkeypatch.setattr(projects_route, "should_respond", lambda *a, **kw: False)
    classify_calls = []
    monkeypatch.setattr(
        projects_route, "resolve_chat_intent",
        lambda *a, **kw: classify_calls.append(1) or {"intent": "answer"},
    )
    loop_calls = []
    monkeypatch.setattr(
        projects_route.qa_agent, "answer",
        lambda **kw: loop_calls.append(1) or {"answer": "unused", "citations": []},
    )

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "just chatting, no agent needed here"},
    )
    assert resp.status_code == 200
    assert classify_calls == []
    assert loop_calls == []
    turns = _group_turns(project_id)
    assert len(turns) == 1
    assert turns[0]["role"] == "user"


# ── AC7/AC9 — the edit tool is registered for GROUP only ─────────────────────
def test_group_edit_prd_tool_registered_group_only(tenant_client, isolated_settings, monkeypatch):
    """`edit_prd` + an `edit_prd_handler` are on the GROUP scope only; the
    private scope registers NEITHER (private edits via its FE route), so
    private's `extra_tools`/`answer()` shape is unchanged. The retired
    pre-step symbol is gone."""
    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"})
    captured = {}

    def _fake_answer(*, enterprise_id, question, dataset, scope=None, **kw):
        captured["scope"] = scope
        return {"answer": "ok", "citations": []}

    monkeypatch.setattr(projects_route.qa_agent, "answer", _fake_answer)
    resp = t.client.post(f"/v1/projects/{project_id}/group/turns", json={"content": "@Sprntly hi"})
    assert resp.status_code == 200, resp.text

    group_scope = captured["scope"]
    group_tool_names = [tl["name"] for tl in group_scope.extra_tools]
    assert "edit_prd" in group_tool_names
    assert group_scope.edit_prd_handler is not None

    # Private scope: NO edit tool, NO handler.
    from app.ask_job_runner import _build_private_scope

    private_scope = _build_private_scope(
        project_id=project_id, conversation_id=None, user_id=t.user_id,
    )
    private_tool_names = [tl["name"] for tl in private_scope.extra_tools]
    assert "edit_prd" not in private_tool_names
    assert private_scope.edit_prd_handler is None

    # The retired pre-step + its NamedTuple are gone.
    assert not hasattr(projects_route, "_classify_and_maybe_edit_group_prd")
    assert not hasattr(projects_route, "_GroupEditOutcome")


# ── AC7a — schema exposes instruction only; target server-resolved ───────────
def test_group_edit_tool_schema_has_no_prd_id_server_resolved(
    tenant_client, isolated_settings, monkeypatch
):
    props = pgc.EDIT_PRD_TOOL["input_schema"]["properties"]
    assert list(props.keys()) == ["instruction"]
    assert "prd_id" not in props
    assert pgc.EDIT_PRD_TOOL["input_schema"]["required"] == ["instruction"]

    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings, with_prd=True)
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    # The handler must resolve the target via `_resolve_prd_id({}, ...)` — an
    # EMPTY dict — never a model-supplied id. Capture what reaches propose.
    resolve_inputs = []
    real_resolve = projects_route._resolve_prd_id
    monkeypatch.setattr(
        projects_route, "_resolve_prd_id",
        lambda ti, *a, **kw: resolve_inputs.append(ti) or real_resolve(ti, *a, **kw),
    )
    proposed_with = {}
    monkeypatch.setattr(
        projects_route, "propose_chat_edit_scoped",
        lambda pid, instr, ctx, **kw: proposed_with.update({"prd_id": pid, "instruction": instr})
        or {"proposed": True, "token": "tk", "summary": "s", "prd_id": pid},
    )

    # A model-supplied id in the instruction text must change NOTHING.
    narration, pending = projects_route._propose_group_prd_edit(
        project_id, 1, _ctx(t), t.slug, "edit prd 999999 to add a section",
    )
    assert resolve_inputs == [{}]  # empty dict, never a model id
    assert proposed_with["prd_id"] == prd_id  # the server-resolved target
    assert proposed_with["prd_id"] != 999999
    assert pending["token"] == "tk"


# ── AC7a — multi-PRD project asks which one (needs_prd_clarify) ───────────────
def test_group_edit_multi_prd_asks_which_prd(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=True, prd_count=2)
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    proposed = []
    monkeypatch.setattr(
        projects_route, "propose_chat_edit_scoped",
        lambda *a, **kw: proposed.append(1) or {},
    )

    narration, pending = projects_route._propose_group_prd_edit(
        project_id, 1, _ctx(t), t.slug, "tighten the requirements",
    )
    assert "more than one PRD" in narration  # asks which one
    assert pending is None  # nothing proposed
    assert proposed == []  # never auto-picks / writes


# ── AC8 — the tool PROPOSES (no write) and the group turn carries pending ─────
def test_group_edit_tool_proposes_not_commits(tenant_client, isolated_settings, monkeypatch):
    """Driving the model to call `edit_prd` through the real answer engine:
    NOTHING is written to `prds`, and the group turn's `reply.pending_mutation`
    carries the proposal token — the FE confirm card's contract."""
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings, with_prd=True)
    before = _payload(prd_id)
    before_versions = len(_versions(prd_id))
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"})
    _mock_editor(monkeypatch, summary="Tightened it.", sections=["Requirements"])
    monkeypatch.setattr("app.llm.run_tool_loop", _drive_edit_tool("tighten requirements"))

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "@Sprntly update the PRD to tighten requirements"},
    )
    assert resp.status_code == 200, resp.text

    # PROPOSE — nothing written, no version snapshot.
    assert _payload(prd_id) == before
    assert len(_versions(prd_id)) == before_versions
    assert require_client().table("prd_patches").select("id").execute().data == []

    turns = _group_turns(project_id)
    assistant = [row for row in turns if row["role"] == "assistant"]
    assert len(assistant) == 1
    proposal_turn = assistant[0]
    assert proposal_turn["content"].startswith("I'd like to update the PRD:")
    pending = proposal_turn["reply"]["pending_mutation"]
    assert pending["prd_id"] == prd_id
    assert pending["token"]


# ── AC8 — confirm commits applied == proposed, one version snapshot ──────────
def test_group_edit_confirm_commits_proposed_patch(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings, with_prd=True)
    before_versions = len(_versions(prd_id))
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"})
    _mock_editor(monkeypatch, html="<html><body><h1>Group v2</h1></body></html>",
                 sections=["X"], summary="Updated X.")
    monkeypatch.setattr("app.llm.run_tool_loop", _drive_edit_tool("tighten it"))
    broadcasts = []
    monkeypatch.setattr(
        projects_route, "publish_broadcast",
        lambda topic, event, payload: broadcasts.append((topic, event, payload)),
    )

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "@Sprntly update the PRD"},
    )
    assert resp.status_code == 200, resp.text
    token = _group_turns(project_id)[-1]["reply"]["pending_mutation"]["token"]

    # CONFIRM — commits exactly the stored patch (applied == proposed).
    confirm = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit/confirm", json={"token": token},
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["edited"] is True
    assert "Group v2" in _payload(prd_id)
    versions = _versions(prd_id)
    assert len(versions) == before_versions + 1
    assert "Group v2" not in versions[-1]["payload_md"]  # snapshot is PRE-edit

    done = [
        row for row in _group_turns(project_id)
        if row["role"] == "assistant"
        and row["content"].startswith("Done — I've updated the PRD.")
    ]
    assert done
    # A second confirm of a consumed token is a soft no-op (single-use).
    replay = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit/confirm", json={"token": token},
    )
    assert replay.status_code == 200
    assert replay.json()["edited"] is False


# ── AC8 — cross-project (in-tenant) refused, zero write (mutation-proof) ──────
def test_group_edit_cross_project_refused_zero_write(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_a, _ = _seed_project(t, isolated_settings, with_prd=False)
    project_b, prd_b = _seed_project(t, isolated_settings, with_prd=True)
    before = _payload(prd_b)
    before_versions = len(_versions(prd_b))

    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    # Force the resolver to hand back project B's id to project A — the only
    # way a cross-project id could ever reach the callable — and prove the ★
    # gate (`assert_prd_on_project`), not resolution, is what refuses it.
    monkeypatch.setattr(projects_route, "_resolve_prd_id", lambda *a, **kw: (prd_b, None))
    editor_called = []
    monkeypatch.setattr(prd_questions, "apply_chat_edit", lambda *a, **kw: editor_called.append(1))

    with pytest.raises(ProjectPrdWriteDenied):
        projects_route._propose_group_prd_edit(
            project_a, 999, _ctx(t), t.slug, "please tighten the scope",
        )
    assert editor_called == []
    assert _payload(prd_b) == before
    assert len(_versions(prd_b)) == before_versions
    assert require_client().table("prd_patches").select("id").eq("prd_id", prd_b).execute().data == []


# ── AC8 MUTATION-PROOF — flip the cross-project gate to a no-op → the refusal
# vanishes and the write lands, proving the gate (not resolution) refuses. ────
def test_group_edit_cross_project_gate_flip_is_red(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_a, _ = _seed_project(t, isolated_settings, with_prd=False)
    project_b, prd_b = _seed_project(t, isolated_settings, with_prd=True)
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(projects_route, "_resolve_prd_id", lambda *a, **kw: (prd_b, None))
    _mock_editor(monkeypatch, html="<html><body><h1>Leaked</h1></body></html>",
                 sections=["X"], summary="leaked")
    # RED: neuter the cross-project gate — now the propose SUCCEEDS (the very
    # leak the real gate prevents), so no ProjectPrdWriteDenied is raised.
    monkeypatch.setattr(pce, "assert_prd_on_project", lambda **kw: None)
    narration, pending = projects_route._propose_group_prd_edit(
        project_a, None, _ctx(t), t.slug, "tighten the scope",
    )
    assert pending is not None  # the gate-off path leaks a proposal → proof the gate matters


# ── AC8 — cross-tenant refused, zero write (independent second gate) ─────────
def test_group_edit_cross_tenant_refused_zero_write(tenant_client, isolated_settings, monkeypatch):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    project_a, _ = _seed_project(a, isolated_settings, with_prd=False)
    prd_b = _seed_prd(isolated_settings["db"], dataset="globex")
    before = _payload(prd_b)
    before_versions = len(_versions(prd_b))

    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    # The project gate PASSES deliberately — the refusal must come from the
    # cross-TENANT check, proving the two gates are independent.
    monkeypatch.setattr(pce, "assert_prd_on_project", lambda **kw: None)
    monkeypatch.setattr(projects_route, "_resolve_prd_id", lambda *a, **kw: (prd_b, None))
    editor_called = []
    monkeypatch.setattr(prd_questions, "apply_chat_edit", lambda *a, **kw: editor_called.append(1))

    with pytest.raises(HTTPException) as exc:
        projects_route._propose_group_prd_edit(
            project_a, 999, _ctx(a), "globex", "tighten the scope",
        )
    assert exc.value.status_code == 404
    assert editor_called == []
    assert _payload(prd_b) == before
    assert len(_versions(prd_b)) == before_versions
    assert a.company_id != b.company_id


# ── AC7a — the OLD propose tool stays retired; group STILL server-resolves ───
def test_group_no_propose_tool_wired(tenant_client, isolated_settings, monkeypatch):
    """INTENT REVERSED: the group now HAS an `edit_prd` tool, but the
    RETIRED `propose_prd_patch` write tool is still gone, the target is still
    server-resolved (the schema omits `prd_id`), and the tool routes to the
    confirm gate — no `prd_patches` row is ever written."""
    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"})
    tools_seen = []

    def _fake_answer(*, enterprise_id, question, dataset, scope=None, **kw):
        tools_seen.append(list(scope.extra_tools) if scope else [])
        return {"answer": "ok", "citations": []}

    monkeypatch.setattr(projects_route.qa_agent, "answer", _fake_answer)
    resp = t.client.post(f"/v1/projects/{project_id}/group/turns", json={"content": "@Sprntly hi"})
    assert resp.status_code == 200, resp.text
    tool_names = [tl["name"] for tl in tools_seen[0]]
    # The NEW positive invariant: `edit_prd` exists…
    assert "edit_prd" in tool_names
    # …and the retired write tool is still absent; its target is server-only.
    assert "propose_prd_patch" not in tool_names
    assert not hasattr(projects_route, "PROPOSE_PROJECT_PRD_PATCH_TOOL")
    edit_tool = next(tl for tl in tools_seen[0] if tl["name"] == "edit_prd")
    assert "prd_id" not in edit_tool["input_schema"]["properties"]
    patches = require_client().table("prd_patches").select("id").execute().data
    assert patches == []


# ── AC7a — a model-supplied id in the text never redirects the target ────────
def test_group_edit_target_resolved_not_client_supplied(tenant_client, isolated_settings, monkeypatch):
    """The tool omits `prd_id`; the handler resolves via `_resolve_prd_id({})`.
    A project with NO PRD is unresolvable, so a change request (even one naming
    an id in prose) writes nothing and asks for a target."""
    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)  # unresolvable
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    proposed = []
    monkeypatch.setattr(
        projects_route, "propose_chat_edit_scoped",
        lambda *a, **kw: proposed.append(1) or {},
    )

    # A client/model-supplied id in the text changes NOTHING — the handler
    # never reads one; the target is resolved server-side only.
    narration, pending = projects_route._propose_group_prd_edit(
        project_id, 1, _ctx(t), t.slug, "edit prd 999999 please",
    )
    assert pending is None
    assert proposed == []  # no write attempted
    assert "no PRD to edit" in narration
    assert require_client().table("prd_patches").select("id").execute().data == []


# ── AC7 — flag off: no propose, no write ─────────────────────────────────────
def test_group_edit_disabled_flag_no_write(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings, with_prd=True)
    monkeypatch.delenv("PROJECT_PRD_EDIT_ENABLED", raising=False)
    proposed = []
    monkeypatch.setattr(
        projects_route, "propose_chat_edit_scoped",
        lambda *a, **kw: proposed.append(1) or {},
    )
    before = _payload(prd_id)
    before_versions = len(_versions(prd_id))

    narration, pending = projects_route._propose_group_prd_edit(
        project_id, 1, _ctx(t), t.slug, "tighten the scope",
    )
    assert pending is None
    assert proposed == []  # gate off → never proposes
    assert _payload(prd_id) == before
    assert len(_versions(prd_id)) == before_versions


# ── The editor found nothing to change → no pending, no fabrication ──────────
def test_group_edit_no_sections_changed_no_pending(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings, with_prd=True)
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
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
    assert pending is None
    assert _payload(prd_id) == "<html><body><h1>Doc</h1></body></html>"
