"""`POST /v1/projects/{project_id}/prd/chat-edit` — the private project
chat's PRD-edit write endpoint.

Covers, in order (mirrors the route's own gate order): membership, the
`PROJECT_PRD_EDIT_ENABLED` rollout flag off → no-op, and a client-supplied
`prd_id` naming a PRD in ANOTHER TENANT entirely (soft-refused, zero write —
the ★ cross-project gate's manifest read is tenant-scoped, so a foreign-
tenant id falls away there before it ever reaches the cross-tenant gate).
The route's target is now ALWAYS the explicit open-drawer `prd_id` — no
server-side auto-resolution — so the direct-apply happy path, the "no PRD
open" clarify, and the mutation-proofed cross-project IDOR guard are covered
by `test_project_prd_edit_parity.py` instead.

Real `projects`/`project_members`/`project_artifacts`/`prds`/`prd_versions`
rows via `tenant_client` + `isolated_settings` (the fake in-memory Supabase
every backend suite composes on); the editor LLM call is mocked at
`app.prd_edit.apply_chat_edit`, the same seam every chat-edit test in
this repo patches. The real cross-project/cross-tenant Postgres fan-out is
exercised by the env-gated `test_projects_prd_chat_edit_route_live.py`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.prd_edit as prd_edit
from tests import _fake_supabase
from app.db.client import require_client
from app.db.workspaces import ensure_default_workspace
from tests._project_helpers import seed_same_tenant_non_member

# The ★ cross-project gate's manifest read walks `list_artifacts_for_project`
# -> `list_artifacts_for_company`, which queries `prototypes` unconditionally
# — deliberately NOT in conftest's shared fake schema (see its own "NOTE"
# comment); every fan-out test file adds its own trimmed copy, same
# convention as `test_project_artifacts_fanout.py`.
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
    monkeypatch.setattr(prd_edit, "apply_chat_edit", lambda *a, **kw: editor_called.append(1))

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
    monkeypatch.setattr(prd_edit, "apply_chat_edit", lambda *a, **kw: editor_called.append(1))

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


def test_route_explicit_prd_id_cross_tenant_denied(
    tenant_client, isolated_settings, monkeypatch
):
    """A client-supplied `prd_id` naming a PRD in ANOTHER TENANT entirely is
    refused with zero write. `assert_prd_on_project`'s manifest read is
    ALREADY tenant-scoped (`list_artifacts_for_project` intersects project
    attachment with the caller's OWN tenant fan-out — see
    `project_prd_gate.py`'s own docstring), so a foreign-tenant id never
    naturally reaches `require_owned_prd` through this project-scoped route
    at all — it falls away at the SAME ★ gate the cross-project case does."""
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    tenant_client.make(slug="globex")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=True)
    foreign_prd_id = _seed_prd(isolated_settings["db"], dataset="globex")
    # NOT attached to any of `t`'s projects — a foreign-tenant PRD altogether.

    editor_called = []
    monkeypatch.setattr(prd_edit, "apply_chat_edit", lambda *a, **kw: editor_called.append(1))

    resp = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "tighten requirements", "prd_id": foreign_prd_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["edited"] is False
    assert "only edit a PRD that's attached to this project" in body["answer"]
    assert editor_called == []
    assert _versions(foreign_prd_id) == []
