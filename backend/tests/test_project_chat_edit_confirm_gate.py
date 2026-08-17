"""The confirmation gate on project PRD edits: propose -> confirm -> apply.

An edit from a project chat no longer writes immediately. The agent PROPOSES
the change (the computed patch is stored keyed by a single-use token); nothing
touches `prds` until the user confirms; confirm commits EXACTLY the stored
patch (applied content == proposed content, no second editor call). Both IDOR
gates (`assert_prd_on_project`, `require_owned_prd`) run at PROPOSE and AGAIN
at APPLY on the caller — the stored token target is never trusted. Tokens are
tenant-scoped, expiry-filtered, and single-use.

Fast lane: real `projects`/`project_artifacts`/`prds`/`prd_versions`/
`prd_edit_proposals` rows via `tenant_client` + `isolated_settings` (the fake
in-memory Supabase); the editor LLM call is mocked at
`app.prd_questions.apply_chat_edit`, the same seam every chat-edit test mocks.
The real-DB propose->confirm round-trip is proven in the env-gated
`test_projects_prd_chat_edit_route_live.py`/`test_group_chat_prd_edit_live.py`.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.prd_questions as prd_questions
import app.project_chat_edit as pce
import app.routes.projects as projects_route
from tests import _fake_supabase
from app.db import prd_edit_proposals as proposals_db
from app.db.client import require_client
from app.db.workspaces import ensure_default_workspace

# `_resolve_prd_id`/`assert_prd_on_project` walk `list_artifacts_for_project`
# -> `list_artifacts_for_company`, which queries `prototypes` unconditionally —
# deliberately NOT in conftest's shared fake schema (same convention as
# `test_projects_prd_chat_edit_route.py`).
_PROTOTYPE_DDL = """
CREATE TABLE IF NOT EXISTS prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    created_at             TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_PROPOSED_HTML = "<html><body><h1>Proposed v2</h1><p>tightened</p></body></html>"


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


def _proposal_rows():
    return require_client().table("prd_edit_proposals").select("*").execute().data or []


def _company(company_id, workspace_id):
    return SimpleNamespace(
        company_id=company_id, workspace_id=workspace_id, user_id="u1", user_email=None,
    )


def _workspace_id(company_id):
    return ensure_default_workspace(company_id)["id"]


def _ctx(t):
    return SimpleNamespace(
        company_id=t.company_id, workspace_id=_workspace_id(t.company_id),
        user_id=t.user_id, user_email=None,
    )


def _seed_project(t, isolated_settings, *, with_prd=True, name="Launch"):
    from app.db import projects as projects_db

    ws_id = _workspace_id(t.company_id)
    project = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name=name, created_by=t.user_id,
    )
    prd_id = None
    if with_prd:
        prd_id = _seed_prd(isolated_settings["db"], dataset=t.slug)
        projects_db.add_artifact(project["id"], "prd", prd_id)
    return project["id"], prd_id


def _mock_editor(monkeypatch, *, html=_PROPOSED_HTML, sections=("Requirements",),
                 summary="Tightened requirements."):
    calls = []

    def _fake(*a, **kw):
        calls.append(1)
        return {"html": html, "sections_changed": list(sections), "summary": summary}

    monkeypatch.setattr(prd_questions, "apply_chat_edit", _fake)
    return calls


# ── AC2 — a private edit proposes, writes nothing ────────────────────────────
def test_private_edit_proposes_not_commits(tenant_client, isolated_settings, monkeypatch):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings)
    before = _payload(prd_id)
    _mock_editor(monkeypatch)

    resp = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "tighten requirements"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["edited"] is False
    assert body["pending"] is True
    mut = body["mutation"]
    assert mut["token"] and isinstance(mut["token"], str)
    assert mut["prd_id"] == prd_id
    assert mut["sections_changed"] == ["Requirements"]
    # Nothing written — the PRD is untouched and no version snapshot taken.
    assert _payload(prd_id) == before
    assert _versions(prd_id) == []
    # A single proposal row exists carrying the computed patch.
    rows = _proposal_rows()
    assert len(rows) == 1
    assert rows[0]["proposed_html"] == _PROPOSED_HTML
    assert rows[0]["surface"] == "private"


# ── AC3 — confirm commits the proposed content byte-for-byte, no 2nd editor ──
def test_confirm_commits_proposed_content_exactly(tenant_client, isolated_settings, monkeypatch):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings)
    editor_calls = _mock_editor(monkeypatch)

    propose = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "tighten requirements"},
    ).json()
    token = propose["mutation"]["token"]
    assert len(editor_calls) == 1  # editor ran once at propose

    confirm = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit/confirm", json={"token": token},
    )
    assert confirm.status_code == 200, confirm.text
    body = confirm.json()
    assert body["edited"] is True
    assert body["sections_changed"] == ["Requirements"]
    # The written content is byte-for-byte the token's stored proposed_html.
    assert _payload(prd_id) == _PROPOSED_HTML
    assert body["prd"]["payload_md"] == _PROPOSED_HTML
    # Exactly one version snapshot (the pre-edit content), and NO second editor
    # LLM call ran at confirm.
    assert len(editor_calls) == 1
    versions = _versions(prd_id)
    assert len(versions) == 1
    assert _PROPOSED_HTML not in versions[-1]["payload_md"]
    # Single-use: the proposal row is gone after a successful confirm.
    assert _proposal_rows() == []


# ── AC3 — confirm persists the instruction + done-summary turn pair ──────────
def test_confirm_persists_instruction_and_summary_turn_pair(
    tenant_client, isolated_settings, monkeypatch
):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings)
    _mock_editor(monkeypatch, summary="Tightened it.")

    token = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "please tighten the requirements"},
    ).json()["mutation"]["token"]
    # No turn pair is persisted on PROPOSE (moved to confirm).
    turns_before = require_client().table("conversation_turns").select("*").execute().data or []
    assert turns_before == []

    t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit/confirm", json={"token": token},
    )
    rows = require_client().table("conversation_turns").select("*").execute().data or []
    user_turns = [r for r in rows if r["role"] == "user"]
    asst_turns = [r for r in rows if r["role"] == "assistant"]
    # The stored ORIGINAL instruction is what gets persisted as the user turn.
    assert any(r["content"] == "please tighten the requirements" for r in user_turns)
    assert any(r["content"].startswith("Done — I've updated the PRD.") for r in asst_turns)


# ── AC4 — cancel writes nothing and deletes the proposal ─────────────────────
def test_cancel_deletes_proposal_no_write(tenant_client, isolated_settings, monkeypatch):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings)
    before = _payload(prd_id)
    _mock_editor(monkeypatch)

    token = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "tighten requirements"},
    ).json()["mutation"]["token"]
    assert len(_proposal_rows()) == 1

    cancel = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit/cancel", json={"token": token},
    )
    assert cancel.status_code == 200, cancel.text
    assert cancel.json() == {"cancelled": True}
    # Row gone, PRD untouched.
    assert _proposal_rows() == []
    assert _payload(prd_id) == before
    assert _versions(prd_id) == []

    # A cancelled token can no longer be confirmed.
    confirm = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit/confirm", json={"token": token},
    ).json()
    assert confirm["edited"] is False
    assert _payload(prd_id) == before


# ── AC5 — IDOR at PROPOSE: cross-project denied, no row (mutation-proofed) ────
def test_propose_idor_cross_project_denied(tenant_client, isolated_settings, monkeypatch):
    from app.project_prd_gate import ProjectPrdWriteDenied, assert_prd_on_project as real_gate

    t = tenant_client.make(slug="acme")
    # Caller is in project A; the victim PRD lives on a sibling project B in
    # the same tenant — the classic cross-project IDOR shape.
    project_a, _ = _seed_project(t, isolated_settings, with_prd=False, name="caller project")
    project_b, prd_b = _seed_project(t, isolated_settings, name="other project")
    before_b = _payload(prd_b)
    _mock_editor(monkeypatch)
    ctx = _company(t.company_id, _workspace_id(t.company_id))

    # RED — the ★ cross-project gate bypassed: a proposal row appears (caller
    # project A, victim PRD on B), proving the gate is what stops it.
    monkeypatch.setattr(pce, "assert_prd_on_project", lambda **kw: None)
    red = pce.propose_chat_edit_scoped(
        prd_b, "tighten it", ctx, project_id=project_a, dataset="acme", surface="private",
    )
    assert red["proposed"] is True
    assert len(_proposal_rows()) == 1

    # Clean the row the bypass minted, then restore the REAL gate.
    proposals_db.delete_proposal(red["token"], t.company_id)
    monkeypatch.setattr(pce, "assert_prd_on_project", real_gate)

    # GREEN — real gate refuses a cross-project propose before compute/store.
    with pytest.raises(ProjectPrdWriteDenied):
        pce.propose_chat_edit_scoped(
            prd_b, "tighten it", ctx, project_id=project_a, dataset="acme", surface="private",
        )
    assert _proposal_rows() == []
    assert _payload(prd_b) == before_b


# ── AC5 — IDOR at PROPOSE: cross-tenant denied, no row ────────────────────────
def test_propose_idor_cross_tenant_denied(tenant_client, isolated_settings, monkeypatch):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    prd_id = _seed_prd(isolated_settings["db"], dataset="acme")
    _mock_editor(monkeypatch)
    # Project gate PASSES so the refusal must come from the cross-TENANT check.
    monkeypatch.setattr(pce, "assert_prd_on_project", lambda **kw: None)

    with pytest.raises(HTTPException) as exc:
        pce.propose_chat_edit_scoped(
            prd_id, "tighten it",
            _company(b.company_id, _workspace_id(b.company_id)),
            project_id=1, dataset="globex", surface="private",
        )
    assert exc.value.status_code == 404
    assert _proposal_rows() == []
    assert a.company_id != b.company_id


# ── AC6 — IDOR at APPLY: cross-project denied (mutation-proofed) ──────────────
def test_apply_idor_cross_project_denied(tenant_client, isolated_settings, monkeypatch):
    from app.project_prd_gate import ProjectPrdWriteDenied, assert_prd_on_project as real_gate

    t = tenant_client.make(slug="acme")
    caller_project, _ = _seed_project(t, isolated_settings, name="caller project")
    # The VICTIM PRD lives on a DIFFERENT project in the same tenant.
    _, victim_prd = _seed_project(t, isolated_settings, name="victim project")
    victim_before = _payload(victim_prd)
    victim_versions_before = len(_versions(victim_prd))
    ctx = _company(t.company_id, _workspace_id(t.company_id))

    def _mint(token):
        # Hand-craft the attack state: a token whose stored project_id is the
        # caller's own project but whose prd_id is the victim on ANOTHER
        # project — a state the real propose gate would never let exist.
        proposals_db.create_proposal(
            token=token, prd_id=victim_prd, project_id=caller_project,
            conversation_id=None, surface="private",
            company_id=t.company_id, workspace_id=_workspace_id(t.company_id),
            instruction="hijack", base_html=victim_before,
            proposed_title="Doc", proposed_html="<html><body><h1>HIJACKED</h1></body></html>",
            summary="hijacked", sections_changed=["X"], client_message_id=None,
        )

    # RED — apply-time gate bypassed: the victim PRD on the other project IS
    # clobbered, proving the apply-time ★ gate is load-bearing.
    _mint("tok-red")
    monkeypatch.setattr(pce, "assert_prd_on_project", lambda **kw: None)
    red = pce.apply_proposed_chat_edit("tok-red", ctx, project_id=caller_project, dataset="acme")
    assert red["applied"] is True
    assert "HIJACKED" in _payload(victim_prd)

    # Restore the victim + the REAL gate, mint a fresh token, retry. (The RED
    # apply legitimately wrote a version snapshot; the GREEN denial must add
    # NOTHING beyond that — zero further write.)
    from app.db.prds import update_prd_content
    update_prd_content(victim_prd, "Doc", victim_before)
    monkeypatch.setattr(pce, "assert_prd_on_project", real_gate)
    _mint("tok-green")
    versions_before_green = len(_versions(victim_prd))

    with pytest.raises(ProjectPrdWriteDenied):
        pce.apply_proposed_chat_edit("tok-green", ctx, project_id=caller_project, dataset="acme")
    assert "HIJACKED" not in _payload(victim_prd)
    assert _payload(victim_prd) == victim_before
    assert len(_versions(victim_prd)) == versions_before_green  # GREEN wrote nothing


# ── AC6 — a cross-tenant token lookup returns 404, no write ──────────────────
def test_apply_cross_tenant_token_404(tenant_client, isolated_settings, monkeypatch):
    a = tenant_client.make(slug="acme")
    b = tenant_client.make(slug="globex")
    project_a, prd_a = _seed_project(a, isolated_settings, name="A project")
    before = _payload(prd_a)

    # Mint a real proposal in tenant A.
    proposals_db.create_proposal(
        token="tok-a", prd_id=prd_a, project_id=project_a, conversation_id=None,
        surface="private", company_id=a.company_id, workspace_id=_workspace_id(a.company_id),
        instruction="edit", base_html=before, proposed_title="Doc",
        proposed_html=_PROPOSED_HTML, summary="s", sections_changed=["X"], client_message_id=None,
    )

    # Tenant B tries to apply tenant A's token — the tenant-scoped lookup finds
    # nothing -> 404, zero write.
    with pytest.raises(HTTPException) as exc:
        pce.apply_proposed_chat_edit(
            "tok-a", _company(b.company_id, _workspace_id(b.company_id)),
            project_id=project_a, dataset="globex",
        )
    assert exc.value.status_code == 404
    assert _payload(prd_a) == before
    assert _versions(prd_a) == []
    assert a.company_id != b.company_id


# ── AC13 — expired token: get_proposal returns nothing, apply 404, no write ──
def test_apply_expired_token_no_write(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings)
    before = _payload(prd_id)
    ctx = _company(t.company_id, _workspace_id(t.company_id))

    proposals_db.create_proposal(
        token="tok-exp", prd_id=prd_id, project_id=project_id, conversation_id=None,
        surface="private", company_id=t.company_id, workspace_id=_workspace_id(t.company_id),
        instruction="edit", base_html=before, proposed_title="Doc",
        proposed_html=_PROPOSED_HTML, summary="s", sections_changed=["X"], client_message_id=None,
    )
    # Force the row's expiry into the past — the filter lives in get_proposal's
    # WHERE, so the row becomes dead to both lookup and apply.
    require_client().table("prd_edit_proposals").update(
        {"expires_at": "2000-01-01T00:00:00+00:00"}
    ).eq("token", "tok-exp").execute()

    assert proposals_db.get_proposal("tok-exp", t.company_id, _workspace_id(t.company_id)) is None
    with pytest.raises(HTTPException) as exc:
        pce.apply_proposed_chat_edit("tok-exp", ctx, project_id=project_id, dataset="acme")
    assert exc.value.status_code == 404
    assert _payload(prd_id) == before
    assert _versions(prd_id) == []


# ── AC14 — replay of a consumed token: 404, no double write ──────────────────
def test_apply_replay_after_success_no_double_write(tenant_client, isolated_settings, monkeypatch):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings)
    _mock_editor(monkeypatch)

    token = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "tighten requirements"},
    ).json()["mutation"]["token"]

    first = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit/confirm", json={"token": token},
    ).json()
    assert first["edited"] is True
    assert _payload(prd_id) == _PROPOSED_HTML
    versions_after_first = len(_versions(prd_id))
    assert versions_after_first == 1

    # Replay: the token was single-use consumed -> soft refuse, NO second write.
    second = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit/confirm", json={"token": token},
    ).json()
    assert second["edited"] is False
    assert len(_versions(prd_id)) == versions_after_first  # no double snapshot

    # And the direct callable raises 404 on the consumed token.
    with pytest.raises(HTTPException) as exc:
        pce.apply_proposed_chat_edit(
            token, _company(t.company_id, _workspace_id(t.company_id)),
            project_id=project_id, dataset="acme",
        )
    assert exc.value.status_code == 404


# ── AC7 — concurrent change since propose: conflict, no clobber ───────────────
def test_apply_concurrent_change_no_clobber(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings)
    ctx = _company(t.company_id, _workspace_id(t.company_id))
    base = _payload(prd_id).strip()

    proposals_db.create_proposal(
        token="tok-conf", prd_id=prd_id, project_id=project_id, conversation_id=None,
        surface="private", company_id=t.company_id, workspace_id=_workspace_id(t.company_id),
        instruction="edit", base_html=base, proposed_title="Doc",
        proposed_html=_PROPOSED_HTML, summary="s", sections_changed=["X"], client_message_id=None,
    )
    # Someone else changes the PRD after the proposal was made.
    from app.db.prds import update_prd_content
    changed = "<html><body><h1>Changed elsewhere</h1></body></html>"
    update_prd_content(prd_id, "Doc", changed)

    result = pce.apply_proposed_chat_edit("tok-conf", ctx, project_id=project_id, dataset="acme")
    assert result["applied"] is False
    assert result["conflict"] is True
    # No clobber — the concurrent content stands; the proposed content is NOT
    # written, and the stale proposal is deleted.
    assert _payload(prd_id) == changed
    assert _proposal_rows() == []


# ── AC8 — a GROUP edit proposes (pending turn), no prds write ─────────────────
def test_group_edit_proposes_and_posts_pending_turn(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings)
    before = _payload(prd_id)
    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project_id, t.user_id)
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    _mock_editor(monkeypatch, summary="Tightened it.")

    # The in-band `edit_prd` tool handler proposes: NOTHING written; the
    # returned pending mutation carries the token the group turn stamps onto
    # `reply.pending_mutation`, and a proposal row (surface="group") is stored.
    narration, pending = projects_route._propose_group_prd_edit(
        project_id, conv["id"], _ctx(t), t.slug, "tighten it",
    )
    assert narration.startswith("I'd like to update the PRD:")
    assert "Confirm to apply." in narration
    assert pending["token"] and pending["prd_id"] == prd_id
    assert _payload(prd_id) == before
    assert _versions(prd_id) == []
    rows = _proposal_rows()
    assert len(rows) == 1 and rows[0]["surface"] == "group"


# ── AC8 — group confirm commits (applied==proposed) + posts the Done turn ─────
def test_group_confirm_commits_and_posts_done(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings)
    from app.db import conversations as conversations_db

    conv = conversations_db.create_group_chat(project_id, t.user_id)
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    _mock_editor(monkeypatch, summary="Tightened it.")

    _, pending = projects_route._propose_group_prd_edit(
        project_id, conv["id"], _ctx(t), t.slug, "tighten it",
    )
    token = pending["token"]

    confirm = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit/confirm", json={"token": token},
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["edited"] is True
    # applied == proposed, byte-for-byte.
    assert _payload(prd_id) == _PROPOSED_HTML
    # A completed "Done" GROUP turn was posted to the group conversation.
    group_turns = conversations_db.list_group_turns(conv["id"])
    done = [r for r in group_turns if r["role"] == "assistant"
            and r["content"].startswith("Done — I've updated the PRD.")]
    assert done, group_turns
    assert _proposal_rows() == []


# ── AC9 — the migration is idempotent + NOT NULL + FK-cascade (source shape) ─
def test_proposal_migration_idempotent_notnull_fks():
    """Static shape guard on the migration; the REAL apply-idempotence and the
    propose->confirm round-trip against local Supabase are proven live
    (`test_projects_prd_chat_edit_route_live.py`) and at dispatch."""
    src = (
        Path(__file__).resolve().parents[2]
        / "supabase" / "migrations" / "20260816170000_prd_edit_proposals.sql"
    ).read_text()
    # Normalize whitespace so column-alignment padding doesn't matter.
    low = " ".join(src.lower().split())
    # Idempotent DDL (double-apply is a no-op).
    assert "create table if not exists prd_edit_proposals" in low
    assert "create index if not exists idx_prd_edit_proposals_tenant" in low
    # NOT NULL on the load-bearing columns.
    for col in (
        "token text primary key", "prd_id bigint not null",
        "project_id bigint not null", "surface text not null",
        "company_id uuid not null", "workspace_id uuid not null",
        "instruction text not null", "base_html text not null",
        "proposed_html text not null", "expires_at timestamptz not null",
    ):
        assert col in low, col
    # Every FK cascades on delete.
    assert "references prds(id) on delete cascade" in low
    assert "references projects(id) on delete cascade" in low
    assert "references conversations(id) on delete cascade" in low
    assert "references companies(id) on delete cascade" in low
    assert "references workspaces(id) on delete cascade" in low


# ── AC1 — main chat's PRD-tab edit path is unchanged (still immediate) ───────
def test_main_prd_tab_edit_unchanged(tenant_client, isolated_settings, monkeypatch):
    """`apply_chat_edit_scoped` with `project_id=None` still writes IMMEDIATELY
    — no propose step, no proposal row — exactly as before the gate."""
    t = tenant_client.make(slug="acme")
    prd_id = _seed_prd(isolated_settings["db"], dataset="acme")
    _mock_editor(monkeypatch, html="<html><body><h1>Main v2</h1></body></html>")

    result = pce.apply_chat_edit_scoped(
        prd_id, "tighten it", _company(t.company_id, _workspace_id(t.company_id)),
        project_id=None, dataset=None,
    )
    assert result["sections_changed"] == ["Requirements"]
    assert "Main v2" in _payload(prd_id)          # written immediately
    assert len(_versions(prd_id)) == 1
    assert _proposal_rows() == []                  # no proposal store involved
