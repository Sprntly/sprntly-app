"""★ The @Sprntly GROUP agent's `edit_prd` dispatch — the group-surface half
of the shared scoped-edit + IDOR gate proven privately.

Composition: `post_group_turn_route` decides whether to reply at all (an
explicit `@Sprntly` mention, deterministic, or a `should_respond=True`
smart-interjection verdict) — that gate is UNCHANGED and re-verified here
only at its edges (AC1). Once `_respond_as_group_agent` runs,
`_classify_and_maybe_edit_group_prd` classifies the triggering turn via
`resolve_chat_intent` (reused verbatim) and, on `edit_prd` with
`PROJECT_PRD_EDIT_ENABLED` on and a target resolved via `_resolve_prd_id`
(the group's OWN project, NEVER a client/model id), applies the edit through
the SAME `apply_chat_edit_scoped` the private surface calls — the ★ gate
(`assert_prd_on_project` then `require_owned_prd`) fires identically. Every
other envelope (`answer`, and any generate/open phrasing — group
generate/open is DEFERRED, spec ⭐) falls through to the unified answer
engine (`qa_agent.answer`, scoped to this project) unchanged in shape from
the pre-collapse `run_tool_loop` call it replaces.

The classifier and (for the two IDOR proofs) the target resolver are
monkeypatched for determinism — `resolve_chat_intent`'s own thresholds are
unit-tested in `test_chat_intent_route.py`/`test_chat_intent_evals.py`, not
re-litigated here. The editor LLM call (`app.prd_questions.apply_chat_edit`)
is mocked the same way every chat-edit test in this repo mocks it. Real
`projects`/`project_artifacts`/`prds`/`prd_versions`/`prd_patches` rows via
`tenant_client` + `isolated_settings` (the fake in-memory Supabase every
backend suite composes on) so the mutation proofs are genuine row counts, not
assertions on a mock. The real cross-project/cross-tenant Postgres fan-out
through a real LLM is exercised by the env-gated `test_group_chat_prd_edit_live.py`.

The two IDOR proofs (cross-project / cross-tenant) call
`_classify_and_maybe_edit_group_prd` directly with `pytest.raises` — that
function is the one that PROPAGATES a gate refusal; `_respond_as_group_agent`
wraps it in a best-effort try/except (AD-P7) that would otherwise swallow the
very exception these tests need to observe. Both force the resolved target
via a monkeypatched `_resolve_prd_id` rather than an organic cross-tenant
resolution: `list_artifacts_for_project` already intersects a project's own
refs with the CALLER's tenant-scoped fan-out (`db/artifacts.py:456`), so a
foreign-tenant id can never actually be resolved from a real project's own
manifest — forcing it is what proves the ★ gate is a second, independent
check, not the only thing standing between a caller and a foreign row.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.prd_questions as prd_questions
import app.project_chat_edit as pce
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
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    created_at             TEXT NOT NULL DEFAULT (datetime('now'))
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


def _seed_project(t, isolated_settings, *, with_prd: bool = True):
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    project = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name="Group PRD project",
        created_by=t.user_id,
    )
    prd_id = None
    if with_prd:
        prd_id = _seed_prd(isolated_settings["db"], dataset=t.slug)
        projects_db.add_artifact(project["id"], "prd", prd_id)
    return project["id"], prd_id


def _ctx(t):
    return SimpleNamespace(
        company_id=t.company_id, workspace_id=ensure_default_workspace(t.company_id)["id"],
        user_id=t.user_id, user_email=None,
    )


def _group_turns(project_id):
    from app.db import conversations as conversations_db

    conv = conversations_db.get_group_chat(project_id)
    if not conv:
        return []
    return conversations_db.list_group_turns(conv["id"])


# ── AC1 — classify only after should_respond/mention ─────────────────────────
def test_group_classifies_only_after_should_respond(tenant_client, isolated_settings, monkeypatch):
    from app.db import projects as projects_db

    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)
    # A SECOND human member so this actually exercises `should_respond` — a
    # solo (single-human) project now bypasses the gate entirely (the
    # solo-project auto-respond fix) and always replies.
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


def test_group_casual_scope_question_routes_answer(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(projects_route, "should_respond", lambda *a, **kw: True)
    classify_calls = []

    def _fake_classify(company_id, message, history, *, prd_id=None, **kw):
        classify_calls.append({"message": message, "prd_id": prd_id})
        return {"intent": "answer", "confidence": 0.9, "instruction": None}

    monkeypatch.setattr(projects_route, "resolve_chat_intent", _fake_classify)
    scoped_calls = []
    monkeypatch.setattr(
        projects_route, "apply_chat_edit_scoped", lambda *a, **kw: scoped_calls.append(1)
    )
    loop_calls = []
    monkeypatch.setattr(
        projects_route.qa_agent, "answer",
        lambda **kw: loop_calls.append(1) or {"answer": "let's discuss", "citations": []},
    )

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "should we tighten scope?"},
    )
    assert resp.status_code == 200
    assert len(classify_calls) == 1
    assert classify_calls[0]["message"] == "should we tighten scope?"
    assert scoped_calls == []
    assert len(loop_calls) == 1


# ── AC2 — edit_prd persists + broadcasts ──────────────────────────────────────
def test_group_edit_prd_persists_and_broadcasts(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings, with_prd=True)
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(
        projects_route, "resolve_chat_intent",
        lambda *a, **kw: {"intent": "edit_prd", "instruction": "do it"},
    )
    scoped_calls = []

    def _fake_scoped(pid, instruction, ctx, *, project_id, dataset):
        scoped_calls.append(
            {"prd_id": pid, "instruction": instruction, "project_id": project_id, "dataset": dataset}
        )
        return {"prd": {"id": pid}, "sections_changed": ["X"], "summary": "Updated X."}

    monkeypatch.setattr(projects_route, "apply_chat_edit_scoped", _fake_scoped)
    broadcasts = []
    monkeypatch.setattr(
        projects_route, "publish_broadcast",
        lambda topic, event, payload: broadcasts.append((topic, event, payload)),
    )
    loop_calls = []
    monkeypatch.setattr(
        projects_route.qa_agent, "answer",
        lambda **kw: loop_calls.append(1) or {"answer": "unused", "citations": []},
    )

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns", json={"content": "@Sprntly do it"},
    )
    assert resp.status_code == 200, resp.text
    assert len(scoped_calls) == 1
    assert scoped_calls[0] == {
        "prd_id": prd_id, "instruction": "do it", "project_id": project_id, "dataset": t.slug,
    }
    assert loop_calls == []  # never fell through to the existing loop

    turns = _group_turns(project_id)
    assistant_turns = [row for row in turns if row["role"] == "assistant"]
    assert len(assistant_turns) == 1
    # B2 no-fabrication: a completed edit narrates as a past-tense "Done"
    # ONLY because `sections_changed` came back truthy — the fixture below
    # sets it to ["X"].
    assert assistant_turns[0]["content"] == "Done — I've updated the PRD. Updated X."

    assert broadcasts[-1][0] == f"project:{project_id}"
    assert broadcasts[-1][1] == "turn.created"
    assert broadcasts[-1][2]["role"] == "assistant"


# ── AC3 — every non-edit envelope runs the EXISTING loop unchanged ───────────
def test_group_non_edit_runs_existing_loop(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(
        projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"}
    )
    scoped_calls = []
    monkeypatch.setattr(
        projects_route, "apply_chat_edit_scoped", lambda *a, **kw: scoped_calls.append(1)
    )
    loop_calls = []

    def _fake_answer(*, enterprise_id, question, dataset, scope=None, **kw):
        loop_calls.append([tl["name"] for tl in (scope.extra_tools if scope else [])])
        return {"answer": "an ordinary answer", "citations": []}

    monkeypatch.setattr(projects_route.qa_agent, "answer", _fake_answer)

    # `answer`, and any generate/open PHRASING — the envelope is mocked to
    # `answer` regardless of wording (group generate/open has no executor to
    # route to; the classifier's own generate/open handling is out of scope
    # here — see test_chat_intent_route.py).
    for phrasing in [
        "@Sprntly what's the status here",
        "@Sprntly generate a new PRD for this",
        "@Sprntly open the pricing doc",
    ]:
        resp = t.client.post(
            f"/v1/projects/{project_id}/group/turns", json={"content": phrasing}
        )
        assert resp.status_code == 200, resp.text

    assert scoped_calls == []
    assert len(loop_calls) == 3
    for tool_names in loop_calls:
        assert "propose_prd_patch" not in tool_names
        assert "generate_prd" not in tool_names
        assert "generate_tickets" not in tool_names
        assert "open_artifact" not in tool_names


# ── AC4 — propose tool no longer wired; no prd_patches row ────────────────────
def test_group_no_propose_tool_wired(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(
        projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"}
    )
    tools_seen = []

    def _fake_answer(*, enterprise_id, question, dataset, scope=None, **kw):
        tools_seen.append(list(scope.extra_tools) if scope else [])
        return {"answer": "ok", "citations": []}

    monkeypatch.setattr(projects_route.qa_agent, "answer", _fake_answer)

    resp = t.client.post(f"/v1/projects/{project_id}/group/turns", json={"content": "@Sprntly hi"})
    assert resp.status_code == 200, resp.text
    tool_names = [tl["name"] for tl in tools_seen[0]]
    assert "propose_prd_patch" not in tool_names
    assert not hasattr(projects_route, "PROPOSE_PROJECT_PRD_PATCH_TOOL")
    patches = require_client().table("prd_patches").select("id").execute().data
    assert patches == []


# ── AC5 — cross-project (in-tenant) refused, zero write ──────────────────────
def test_group_edit_cross_project_refused_zero_write(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_a, _ = _seed_project(t, isolated_settings, with_prd=False)
    project_b, prd_b = _seed_project(t, isolated_settings, with_prd=True)
    before = _payload(prd_b)
    before_versions = len(_versions(prd_b))

    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    # A genuinely resolvable id can never leave its own project's manifest
    # (`db/artifacts.py:456`); force the resolver's return to simulate the
    # only way a cross-project id could ever reach the callable, and prove
    # the ★ gate — not resolution — is what refuses it.
    monkeypatch.setattr(
        projects_route, "_resolve_prd_id", lambda *a, **kw: (prd_b, None)
    )
    monkeypatch.setattr(
        projects_route, "resolve_chat_intent",
        lambda *a, **kw: {"intent": "edit_prd", "instruction": "tighten it"},
    )
    editor_called = []
    monkeypatch.setattr(prd_questions, "apply_chat_edit", lambda *a, **kw: editor_called.append(1))

    with pytest.raises(ProjectPrdWriteDenied):
        projects_route._classify_and_maybe_edit_group_prd(
            project_a, 999, _ctx(t), "please tighten the scope", [], t.slug,
        )
    assert editor_called == []
    assert _payload(prd_b) == before
    assert len(_versions(prd_b)) == before_versions
    patches = require_client().table("prd_patches").select("id").eq("prd_id", prd_b).execute().data
    assert patches == []


# ── AC6 — cross-tenant refused, zero write ────────────────────────────────────
def test_group_edit_cross_tenant_refused_zero_write(tenant_client, isolated_settings, monkeypatch):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    project_a, _ = _seed_project(a, isolated_settings, with_prd=False)
    prd_b = _seed_prd(isolated_settings["db"], dataset="globex")
    before = _payload(prd_b)
    before_versions = len(_versions(prd_b))

    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    # The project gate PASSES (deliberately, per test_project_chat_edit.py's
    # own technique) — the refusal must come from the cross-TENANT check,
    # proving the two gates are independent, not one masking the other.
    monkeypatch.setattr(pce, "assert_prd_on_project", lambda **kw: None)
    monkeypatch.setattr(
        projects_route, "_resolve_prd_id", lambda *a, **kw: (prd_b, None)
    )
    monkeypatch.setattr(
        projects_route, "resolve_chat_intent",
        lambda *a, **kw: {"intent": "edit_prd", "instruction": "tighten it"},
    )
    editor_called = []
    monkeypatch.setattr(prd_questions, "apply_chat_edit", lambda *a, **kw: editor_called.append(1))

    with pytest.raises(HTTPException) as exc:
        projects_route._classify_and_maybe_edit_group_prd(
            project_a, 999, _ctx(a), "tighten the scope", [], "globex",
        )
    assert exc.value.status_code == 404
    assert editor_called == []
    assert _payload(prd_b) == before
    assert len(_versions(prd_b)) == before_versions
    assert a.company_id != b.company_id


# ── AC7 — own-project edit applies in place + exactly one version ────────────
def test_group_edit_own_project_in_place_versioned_broadcast(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings, with_prd=True)
    before_versions = len(_versions(prd_id))

    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(
        projects_route, "resolve_chat_intent",
        lambda *a, **kw: {"intent": "edit_prd", "instruction": "tighten requirements"},
    )
    monkeypatch.setattr(
        prd_questions, "apply_chat_edit",
        lambda *a, **kw: {
            "html": "<html><body><h1>Doc v2</h1></body></html>",
            "sections_changed": ["Requirements"],
            "summary": "Tightened requirements.",
        },
    )
    loop_calls = []
    monkeypatch.setattr(
        projects_route.qa_agent, "answer",
        lambda **kw: loop_calls.append(1) or {"answer": "unused", "citations": []},
    )

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "@Sprntly tighten the requirements section"},
    )
    assert resp.status_code == 200, resp.text
    assert loop_calls == []

    assert "Doc v2" in _payload(prd_id)
    versions = _versions(prd_id)
    assert len(versions) == before_versions + 1
    assert "Doc v2" not in versions[-1]["payload_md"]  # snapshot is PRE-edit

    patches = require_client().table("prd_patches").select("id").eq("prd_id", prd_id).execute().data
    assert patches == []

    turns = _group_turns(project_id)
    assistant_turns = [row for row in turns if row["role"] == "assistant"]
    assert len(assistant_turns) == 1
    # B2 no-fabrication: same completed-edit narration guard as above.
    assert assistant_turns[0]["content"] == "Done — I've updated the PRD. Tightened requirements."


# ── AC8 — target resolved server-side; ambiguous/none → no write ─────────────
def test_group_edit_target_resolved_not_client_supplied(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)  # no PRD -> unresolvable
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    monkeypatch.setattr(
        projects_route, "resolve_chat_intent",
        lambda *a, **kw: {"intent": "edit_prd", "instruction": "tighten it"},
    )
    loop_calls = []

    def _fake_answer(*, enterprise_id, question, dataset, scope=None, **kw):
        loop_calls.append([tl["name"] for tl in (scope.extra_tools if scope else [])])
        return {"answer": "falling through", "citations": []}

    monkeypatch.setattr(projects_route.qa_agent, "answer", _fake_answer)

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        # A client/model-supplied id in the text changes NOTHING — the route
        # never reads one; the target is resolved server-side only.
        json={"content": "@Sprntly edit prd 999999 please"},
    )
    assert resp.status_code == 200, resp.text
    assert len(loop_calls) == 1  # fell through, no write attempted
    assert "propose_prd_patch" not in loop_calls[0]
    assert require_client().table("prd_patches").select("id").execute().data == []


# ── AC9 — flag off: no write, falls through ───────────────────────────────────
def test_group_edit_disabled_flag_no_write(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings, with_prd=True)
    monkeypatch.delenv("PROJECT_PRD_EDIT_ENABLED", raising=False)
    monkeypatch.setattr(
        projects_route, "resolve_chat_intent",
        lambda *a, **kw: {"intent": "edit_prd", "instruction": "tighten it"},
    )
    loop_calls = []
    monkeypatch.setattr(
        projects_route.qa_agent, "answer",
        lambda **kw: loop_calls.append(1) or {"answer": "fell through", "citations": []},
    )
    before = _payload(prd_id)
    before_versions = len(_versions(prd_id))

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns", json={"content": "@Sprntly tighten the scope"},
    )
    assert resp.status_code == 200, resp.text
    assert len(loop_calls) == 1
    assert _payload(prd_id) == before
    assert len(_versions(prd_id)) == before_versions
