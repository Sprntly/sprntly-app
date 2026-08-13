"""`POST /v1/projects/{project_id}/prd/chat-edit` — the private (and, later,
group) project chat's PRD-edit write endpoint.

Covers, in order (mirrors the route's own gate order): membership (AC8),
the `PROJECT_PRD_EDIT_ENABLED` rollout flag off → no-op (AC9), target
resolution via `_resolve_prd_id` rather than any client-supplied id — 0/
ambiguous PRDs make no write (AC10) — and a resolvable own-project PRD
applying in place with exactly one version snapshot (AC7/AC10).

Real `projects`/`project_members`/`project_artifacts`/`prds`/`prd_versions`
rows via `tenant_client` + `isolated_settings` (the fake in-memory Supabase
every backend suite composes on); the editor LLM call is mocked at
`app.prd_questions.apply_chat_edit`, the same seam every chat-edit test in
this repo patches. The real cross-project/cross-tenant Postgres fan-out is
exercised by the env-gated `test_projects_prd_chat_edit_route_live.py`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.prd_questions as prd_questions
from tests import _fake_supabase
from app.db.client import require_client
from app.db.workspaces import ensure_default_workspace
from tests._project_helpers import seed_same_tenant_non_member

# `_resolve_prd_id` walks `list_artifacts_for_project` -> `list_artifacts_for_
# company`, which queries `prototypes` unconditionally — deliberately NOT in
# conftest's shared fake schema (see its own "NOTE" comment); every fan-out
# test file adds its own trimmed copy, same convention as
# `test_project_artifacts_fanout.py`.
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
        company_id=t.company_id, workspace_id=ws_id, name="Launch", created_by=t.user_id,
    )
    prd_id = None
    if with_prd:
        prd_id = _seed_prd(isolated_settings["db"])
        projects_db.add_artifact(project["id"], "prd", prd_id)
    return project["id"], prd_id


# ── AC8 — membership required ─────────────────────────────────────────────
def test_route_requires_membership(tenant_client, isolated_settings, monkeypatch):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings)
    # `seed_same_tenant_non_member` mints its bearer with `_company_helpers`'
    # own test secret, which doesn't match `tenant_client`'s
    # (`_enable_supabase_bearer` patches a DIFFERENT constant) — seed the
    # membership rows from the helper but mint the header via
    # `tenant_client.bearer`, the convention this fixture actually verifies.
    non_member_id, _ = seed_same_tenant_non_member(SimpleNamespace(company_id=t.company_id))
    headers = tenant_client.bearer(non_member_id)
    editor_called = []
    monkeypatch.setattr(prd_questions, "apply_chat_edit", lambda *a, **kw: editor_called.append(1))

    resp = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "tighten the scope"}, headers=headers,
    )
    assert resp.status_code == 403
    assert editor_called == []
    assert _versions(prd_id) == []


# ── AC9 — flag off: no write, no-edit payload ─────────────────────────────
def test_route_flag_off_no_write(tenant_client, isolated_settings, monkeypatch):
    monkeypatch.delenv("PROJECT_PRD_EDIT_ENABLED", raising=False)
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings)
    editor_called = []
    monkeypatch.setattr(prd_questions, "apply_chat_edit", lambda *a, **kw: editor_called.append(1))

    resp = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "tighten the scope"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["edited"] is False
    assert isinstance(body["answer"], str) and body["answer"]
    assert editor_called == []
    assert _versions(prd_id) == []


# ── AC10 — target resolved server-side; 0/ambiguous → no write ───────────────
def test_route_resolves_target_not_client_id(tenant_client, isolated_settings, monkeypatch):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    # No PRD attached at all — zero-PRD refusal.
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)
    editor_called = []
    monkeypatch.setattr(prd_questions, "apply_chat_edit", lambda *a, **kw: editor_called.append(1))

    resp = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        # A client-supplied prd_id in the instruction text changes NOTHING —
        # the route never reads a client id at all, only `{instruction}`.
        json={"instruction": "edit prd 999999 please"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["edited"] is False
    assert "PRD" in body["answer"]
    assert editor_called == []

    # Ambiguous: TWO PRDs on the project → also refused, also no write.
    from app.db import projects as projects_db

    prd_a = _seed_prd(isolated_settings["db"], dataset="acme")
    prd_b = _seed_prd(isolated_settings["db"], dataset="acme")
    projects_db.add_artifact(project_id, "prd", prd_a)
    projects_db.add_artifact(project_id, "prd", prd_b)

    resp2 = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "tighten the scope"},
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["edited"] is False
    assert editor_called == []
    assert _versions(prd_a) == []
    assert _versions(prd_b) == []


# ── AC7/AC10 — resolvable own-project PRD applies in place ───────────────────
def test_route_own_project_edits_in_place(tenant_client, isolated_settings, monkeypatch):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings)
    before_versions = len(_versions(prd_id))

    monkeypatch.setattr(prd_questions, "apply_chat_edit", lambda *a, **kw: {
        "html": "<html><body><h1>Doc v2</h1></body></html>",
        "sections_changed": ["Requirements"],
        "summary": "Tightened requirements.",
    })

    resp = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "tighten requirements"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["edited"] is True
    assert body["sections_changed"] == ["Requirements"]
    assert "Doc v2" in body["prd"]["payload_md"]
    assert "Doc v2" in _payload(prd_id)
    assert len(_versions(prd_id)) == before_versions + 1
