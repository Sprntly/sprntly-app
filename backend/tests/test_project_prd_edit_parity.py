"""Single-sourced project-chat PRD editing — both project surfaces (private
write route + group in-band `edit_prd` tool) apply through the SAME shared
editor main chat uses (`apply_chat_edit_scoped`), explicit-target only, no
confirm step.

Covers:
  - AC1 — a 2-PRD project, explicit open-drawer `prd_id`: the edit applies
    in place, only the target PRD changes.
  - AC2 — parity oracle: the SAME instruction+content through the project
    route and through main's own `POST /v1/prd/{id}/chat-edit` produce equal
    `sections_changed` + equal rendered `prd.payload_md`.
  - AC3 — `resolve_project_chat_intent` keeps `edit_prd` alive when a target
    is bound (no `_NEEDS_PRD` downgrade).
  - AC4 — a group `edit_prd` turn applies in ONE call, no pending mutation.
  - AC5 — cross-project (same-tenant) `prd_id` is denied, zero write.
  - AC6 — cross-tenant `prd_id` degrades soft, zero write.
  - AC7 — no PRD open (0/1/2+ PRDs alike): the simple "open a PRD" clarify,
    never enumeration/auto-select.
  - AC8 — flag off: both surfaces no-op, zero writes.
  - AC9 — the retired propose/confirm/token machinery is fully gone (closed-
    world grep across the product tree).

Deterministic fake-DB/fake-editor tier (mirrors `test_project_chat_edit.py`
+ `test_projects_prd_chat_edit_route.py`'s own conventions): real
`projects`/`project_members`/`project_artifacts`/`prds`/`prd_versions` rows
via `tenant_client` + `isolated_settings`, the editor LLM call mocked at
`app.prd_questions.apply_chat_edit`, and the group turn's tool-loop call
mocked at `app.llm.run_tool_loop` (same seam the retired group-edit tests
used) so the `edit_prd` tool fires deterministically without a real model.
The real cross-project/cross-tenant Postgres fan-out + a real LLM apply are
exercised by the env-gated `test_projects_prd_chat_edit_route_live.py`.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.prd_questions as prd_questions
import app.routes.projects as projects_route
from app.db.client import require_client
from app.db.workspaces import ensure_default_workspace
from tests import _fake_supabase

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_CHAT_EDIT_SRC = (REPO_ROOT / "backend" / "app" / "project_chat_edit.py").read_text()

# The ★ cross-project gate's manifest read walks `list_artifacts_for_project`
# -> `list_artifacts_for_company`, which queries `prototypes` unconditionally
# — deliberately NOT in conftest's shared fake schema (same convention as
# every sibling PRD-edit test file).
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


def _seed_project(t, isolated_settings, *, name: str = "Edit parity project") -> int:
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    project = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name=name, created_by=t.user_id,
    )
    return project["id"]


def _mock_editor(monkeypatch, *, html="<html><body><h1>Doc v2</h1></body></html>",
                  sections_changed=("Requirements",), summary="Tightened requirements."):
    monkeypatch.setattr(prd_questions, "apply_chat_edit", lambda *a, **kw: {
        "html": html, "sections_changed": list(sections_changed), "summary": summary,
    })


def _mock_edit_prd_tool_call(monkeypatch, instruction: str = "tighten requirements") -> None:
    """Forces the group turn's tool loop to call `edit_prd` deterministically
    — the same seam the retired group-edit tests patched — so the handler
    fires without a real model."""
    monkeypatch.setattr(
        "app.llm.run_tool_loop",
        lambda *, dispatch, **kw: dispatch("edit_prd", {"instruction": instruction}),
    )


# ── AC1 — 2-PRD project, explicit target: applies in place, sibling PRD untouched
def test_private_edit_applies_when_two_prds_and_target_bound(
    tenant_client, isolated_settings, monkeypatch
):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import projects as projects_db

    prd_a = _seed_prd(isolated_settings["db"], dataset="acme")
    prd_b = _seed_prd(isolated_settings["db"], dataset="acme")
    projects_db.add_artifact(project_id, "prd", prd_a)
    projects_db.add_artifact(project_id, "prd", prd_b)
    before_b = _payload(prd_b)

    _mock_editor(monkeypatch)

    resp = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "tighten requirements", "prd_id": prd_a},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["edited"] is True
    assert body["sections_changed"] == ["Requirements"]
    assert "Doc v2" in body["prd"]["payload_md"]
    assert "Doc v2" in _payload(prd_a)
    assert len(_versions(prd_a)) == 1

    # The sibling PRD on the SAME project is untouched.
    assert _payload(prd_b) == before_b
    assert _versions(prd_b) == []


# ── AC2 — parity oracle: project route vs main's own chat-edit route ─────────
def test_private_edit_matches_main_sections_changed_and_prd(
    tenant_client, isolated_settings, monkeypatch
):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import projects as projects_db

    project_prd = _seed_prd(isolated_settings["db"], dataset="acme")
    standalone_prd = _seed_prd(isolated_settings["db"], dataset="acme")
    projects_db.add_artifact(project_id, "prd", project_prd)

    _mock_editor(monkeypatch, html="<html><body><h1>Parity v2</h1></body></html>",
                 sections_changed=("Scope",), summary="Tightened scope.")

    project_resp = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "tighten the scope", "prd_id": project_prd},
    )
    assert project_resp.status_code == 200, project_resp.text
    project_body = project_resp.json()
    assert project_body["edited"] is True

    main_resp = t.client.post(
        f"/v1/prd/{standalone_prd}/chat-edit",
        json={"instruction": "tighten the scope"},
    )
    assert main_resp.status_code == 200, main_resp.text
    main_body = main_resp.json()

    assert project_body["sections_changed"] == main_body["sections_changed"]
    assert project_body["prd"]["payload_md"] == main_body["prd"]["payload_md"]


# ── AC3 — classify keeps edit_prd alive when a target is bound ──────────────
def test_classify_keeps_edit_intent_when_target_bound(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import projects as projects_db

    prd_id = _seed_prd(isolated_settings["db"], dataset="acme")
    projects_db.add_artifact(project_id, "prd", prd_id)

    def _fake_classify(company_id, message, history, *, prd_id=None, **kw):
        # Mirrors the REAL `_NEEDS_PRD` downgrade (chat_intent.py): the
        # verdict survives whenever a target resolved, downgrades otherwise.
        if prd_id:
            return {
                "intent": "edit_prd", "confidence": 0.9, "instruction": message,
                "task": None, "artifact_type": None, "artifact_query": None,
                "reason": "r", "source": "llm",
            }
        return {
            "intent": "answer", "confidence": 0.9, "instruction": None,
            "task": None, "artifact_type": None, "artifact_query": None,
            "reason": "r", "source": "no_target_prd",
        }

    monkeypatch.setattr(projects_route, "resolve_chat_intent", _fake_classify)

    ctx = SimpleNamespace(company_id=t.company_id)
    envelope, resolved_prd_id, refusal = projects_route.resolve_project_chat_intent(
        project_id, "make it shorter", [], "acme", ctx, prd_id,
    )
    assert envelope["intent"] == "edit_prd"
    assert resolved_prd_id == prd_id
    assert refusal is None


# ── AC4 — group edit_prd applies in ONE turn, no pending mutation ───────────
def test_group_edit_applies_in_one_turn_without_confirm(
    tenant_client, isolated_settings, monkeypatch
):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import projects as projects_db

    prd_id = _seed_prd(isolated_settings["db"], dataset="acme")
    projects_db.add_artifact(project_id, "prd", prd_id)
    before_versions = len(_versions(prd_id))

    monkeypatch.setattr(projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"})
    _mock_editor(monkeypatch)
    _mock_edit_prd_tool_call(monkeypatch)

    resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={
            "content": "@Sprntly tighten the requirements section of the PRD",
            "prd_id": prd_id,
        },
    )
    assert resp.status_code == 200, resp.text

    from app.db import conversations as conversations_db

    conv = conversations_db.get_group_chat(project_id)
    assistant = [
        tn for tn in conversations_db.list_group_turns(conv["id"]) if tn["role"] == "assistant"
    ]
    assert assistant
    last = assistant[-1]
    assert last["content"].startswith("Done — I've updated the PRD.")
    assert "pending_mutation" not in (last.get("reply") or {})

    # The PRD changed within THIS one turn — no second confirm call (the
    # confirm route no longer exists).
    assert "Doc v2" in _payload(prd_id)
    assert len(_versions(prd_id)) == before_versions + 1


# ── AC2/AC4 — group applied result matches main's for the same input ────────
def test_group_edit_matches_main_result_shape(
    tenant_client, isolated_settings, monkeypatch
):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import projects as projects_db

    project_prd = _seed_prd(isolated_settings["db"], dataset="acme")
    standalone_prd = _seed_prd(isolated_settings["db"], dataset="acme")
    projects_db.add_artifact(project_id, "prd", project_prd)

    monkeypatch.setattr(projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"})
    _mock_editor(monkeypatch, html="<html><body><h1>Group parity v2</h1></body></html>",
                 sections_changed=("Scope",), summary="Tightened scope.")
    _mock_edit_prd_tool_call(monkeypatch, instruction="tighten the scope")

    group_resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "@Sprntly tighten the requirements section of the PRD", "prd_id": project_prd},
    )
    assert group_resp.status_code == 200, group_resp.text

    main_resp = t.client.post(
        f"/v1/prd/{standalone_prd}/chat-edit",
        json={"instruction": "tighten the scope"},
    )
    assert main_resp.status_code == 200, main_resp.text

    assert _payload(project_prd) == main_resp.json()["prd"]["payload_md"]


def _fake_no_target_downgrade(*a, **kw):
    # Mirrors the REAL `_NEEDS_PRD` downgrade (chat_intent.py) for
    # `prd_id=None`: an edit-phrased verdict comes back rewritten to
    # `answer`, `source="no_target_prd"`.
    return {
        "intent": "answer", "confidence": 0.9, "instruction": None, "task": None,
        "artifact_type": None, "artifact_query": None, "reason": "r",
        "source": "no_target_prd",
    }


# ── AC7 — no PRD open on a 2-PRD project: simple clarify, never enumerated ──
def test_classify_no_drawer_returns_simple_open_a_prd_clarify(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import projects as projects_db

    prd_a = _seed_prd(isolated_settings["db"], dataset="acme")
    prd_b = _seed_prd(isolated_settings["db"], dataset="acme")
    projects_db.add_artifact(project_id, "prd", prd_a)
    projects_db.add_artifact(project_id, "prd", prd_b)

    monkeypatch.setattr(projects_route, "resolve_chat_intent", _fake_no_target_downgrade)

    ctx = SimpleNamespace(company_id=t.company_id)
    envelope, resolved_prd_id, refusal = projects_route.resolve_project_chat_intent(
        project_id, "make it shorter", [], "acme", ctx, None,
    )
    assert envelope["intent"] == "clarify"
    assert envelope["clarification"] == "Open a PRD beside this chat and I'll edit it."
    assert "prd_options" not in envelope
    assert resolved_prd_id is None
    assert refusal is None


# ── AC7 — no PRD open on a SINGLE-PRD project: same simple clarify, no ──────
# auto-select — proving the design does not infer a target even when exactly
# one PRD exists.
def test_classify_no_drawer_single_prd_still_clarifies_not_autoselects(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import projects as projects_db

    prd_id = _seed_prd(isolated_settings["db"], dataset="acme")
    projects_db.add_artifact(project_id, "prd", prd_id)

    monkeypatch.setattr(projects_route, "resolve_chat_intent", _fake_no_target_downgrade)

    ctx = SimpleNamespace(company_id=t.company_id)
    envelope, resolved_prd_id, refusal = projects_route.resolve_project_chat_intent(
        project_id, "make it shorter", [], "acme", ctx, None,
    )
    assert envelope["intent"] == "clarify"
    assert envelope["clarification"] == "Open a PRD beside this chat and I'll edit it."
    assert "prd_options" not in envelope
    assert resolved_prd_id is None
    assert refusal is None


# ── AC7 — the write route with no prd_id: same clarify, zero write ─────────
def test_write_route_no_prd_id_returns_open_a_prd_and_no_write(
    tenant_client, isolated_settings, monkeypatch
):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import projects as projects_db

    prd_id = _seed_prd(isolated_settings["db"], dataset="acme")
    projects_db.add_artifact(project_id, "prd", prd_id)
    before = _payload(prd_id)

    editor_called = []
    monkeypatch.setattr(prd_questions, "apply_chat_edit", lambda *a, **kw: editor_called.append(1))

    resp = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "tighten requirements"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["edited"] is False
    assert body["answer"] == "Open a PRD beside this chat and I'll edit it."
    assert editor_called == []
    assert _payload(prd_id) == before
    assert _versions(prd_id) == []


# ── AC5 — cross-project (same-tenant) denied, zero write ────────────────────
def test_cross_project_prd_edit_denied_and_no_write(
    tenant_client, isolated_settings, monkeypatch
):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_a = _seed_project(t, isolated_settings, name="Project A")
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    project_b = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name="Project B", created_by=t.user_id,
    )["id"]
    prd_b = _seed_prd(isolated_settings["db"], dataset="acme")
    projects_db.add_artifact(project_b, "prd", prd_b)
    before = _payload(prd_b)

    editor_called = []
    monkeypatch.setattr(prd_questions, "apply_chat_edit", lambda *a, **kw: editor_called.append(1))

    resp = t.client.post(
        f"/v1/projects/{project_a}/prd/chat-edit",
        json={"instruction": "tighten requirements", "prd_id": prd_b},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["edited"] is False
    assert "only edit a PRD that's attached to this project" in body["answer"]
    assert editor_called == []
    assert _payload(prd_b) == before
    assert _versions(prd_b) == []


# ── AC5 mutation proof — the ★ gate is present at the single choke-point ────
def test_cross_project_gate_removal_makes_test_red():
    """Source-scan documenting the flip: `apply_chat_edit_scoped` calls
    `assert_prd_on_project` BEFORE `require_owned_prd`, gated on `project_id
    is not None`. Deleting that call (the effect of the flip a ship-gate
    verifier proves live by monkeypatching it away) would let
    `test_cross_project_prd_edit_denied_and_no_write` above write through —
    this test pins the call's presence and ordering so that removal is
    caught at review time too, not only at the live mutation-proof."""
    src = PROJECT_CHAT_EDIT_SRC
    fn_start = src.index("def apply_chat_edit_scoped(")
    # `apply_chat_edit_scoped` is the last def in the module (the propose/
    # apply-proposed functions this ticket deletes used to follow it) — slice
    # to end-of-file rather than assuming a trailing blank-line boundary.
    body = src[fn_start:]
    assert "if project_id is not None:" in body
    gate_idx = body.index("assert_prd_on_project(")
    tenant_idx = body.index("require_owned_prd(")
    assert gate_idx < tenant_idx, (
        "the ★ cross-project gate must run BEFORE the cross-tenant gate"
    )


# ── AC6 — cross-tenant degrades soft, zero write ─────────────────────────────
def test_cross_tenant_prd_edit_soft_404_no_write(
    tenant_client, isolated_settings, monkeypatch
):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    tenant_client.make(slug="globex")
    project_id = _seed_project(t, isolated_settings)
    foreign_prd_id = _seed_prd(isolated_settings["db"], dataset="globex")
    # NOT attached to any of `t`'s projects — a foreign-tenant PRD altogether.

    editor_called = []
    monkeypatch.setattr(prd_questions, "apply_chat_edit", lambda *a, **kw: editor_called.append(1))

    resp = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "tighten requirements", "prd_id": foreign_prd_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["edited"] is False
    assert editor_called == []
    assert _versions(foreign_prd_id) == []


# ── AC8 — flag off: both surfaces no-op, zero writes ─────────────────────────
def test_flag_off_both_surfaces_no_write(tenant_client, isolated_settings, monkeypatch):
    monkeypatch.delenv("PROJECT_PRD_EDIT_ENABLED", raising=False)
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import projects as projects_db

    prd_id = _seed_prd(isolated_settings["db"], dataset="acme")
    projects_db.add_artifact(project_id, "prd", prd_id)
    before = _payload(prd_id)

    editor_called = []
    monkeypatch.setattr(prd_questions, "apply_chat_edit", lambda *a, **kw: editor_called.append(1))

    # Private surface.
    resp = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "tighten requirements", "prd_id": prd_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["edited"] is False
    assert "isn't turned on" in body["answer"]
    assert editor_called == []

    # Group surface.
    monkeypatch.setattr(projects_route, "resolve_chat_intent", lambda *a, **kw: {"intent": "answer"})
    _mock_edit_prd_tool_call(monkeypatch)
    group_resp = t.client.post(
        f"/v1/projects/{project_id}/group/turns",
        json={"content": "@Sprntly tighten requirements", "prd_id": prd_id},
    )
    assert group_resp.status_code == 200, group_resp.text
    from app.db import conversations as conversations_db

    conv = conversations_db.get_group_chat(project_id)
    assistant = [
        tn for tn in conversations_db.list_group_turns(conv["id"]) if tn["role"] == "assistant"
    ]
    assert assistant
    assert "isn't turned on" in assistant[-1]["content"]
    assert editor_called == []
    assert _payload(prd_id) == before
    assert _versions(prd_id) == []


# ── No-op instruction: editor reports empty sections_changed ────────────────
def test_editor_no_change_returns_no_edit(tenant_client, isolated_settings, monkeypatch):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)
    from app.db import projects as projects_db

    prd_id = _seed_prd(isolated_settings["db"], dataset="acme")
    projects_db.add_artifact(project_id, "prd", prd_id)
    before = _payload(prd_id)

    monkeypatch.setattr(prd_questions, "apply_chat_edit", lambda *a, **kw: {
        "html": "<html><body><h1>Doc</h1></body></html>",
        "sections_changed": [], "summary": "",
    })

    resp = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "say hi", "prd_id": prd_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["edited"] is False
    assert isinstance(body["answer"], str) and body["answer"]
    assert _payload(prd_id) == before
    assert _versions(prd_id) == []


# ── AC9 — retirement guard: the confirm/cancel routes are gone ──────────────
def test_confirm_and_cancel_routes_removed(tenant_client, isolated_settings, monkeypatch):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_id = _seed_project(t, isolated_settings)

    confirm = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit/confirm", json={"token": "whatever"},
    )
    cancel = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit/cancel", json={"token": "whatever"},
    )
    assert confirm.status_code == 404
    assert cancel.status_code == 404


# ── AC9 — closed-world grep: zero product hits, snake_case AND camelCase ────
_AC9_PATTERN = re.compile(
    r"propose_chat_edit_scoped|apply_proposed_chat_edit|apply_proposed|"
    r"prd_edit_proposals|/chat-edit/confirm|/chat-edit/cancel|"
    r"_propose_group_prd_edit|_resolve_prd_id|_project_prd_ids|"
    r"pending_?[Mm]utation|confirmMutation|cancelMutation|onConfirmMutation|"
    r"onCancelMutation|prdChatEditConfirm|prdChatEditCancel|mutation-confirm|"
    r"bc-mutation-confirm"
)
_AC9_ROOTS = ("backend/app", "web/app/lib", "web/app/components")
_AC9_EXTS = {".py", ".ts", ".tsx"}


def _is_test_file(path: Path) -> bool:
    parts = path.parts
    if "__tests__" in parts:
        return True
    name = path.name
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    if ".test." in name:
        return True
    return False


def test_no_proposal_or_confirm_symbols_in_tree():
    hits: list[str] = []
    for root_rel in _AC9_ROOTS:
        root = REPO_ROOT / root_rel
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in _AC9_EXTS:
                continue
            if _is_test_file(path):
                continue
            text = path.read_text(errors="ignore")
            for m in _AC9_PATTERN.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                hits.append(f"{path.relative_to(REPO_ROOT)}:{line_no}: {m.group(0)}")
    assert not hits, "AC9 closed-world grep found product hits:\n" + "\n".join(hits)
    assert not (REPO_ROOT / "backend" / "app" / "db" / "prd_edit_proposals.py").exists()
