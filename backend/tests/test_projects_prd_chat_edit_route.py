"""`POST /v1/projects/{project_id}/prd/chat-edit` — the private (and, later,
group) project chat's PRD-edit write endpoint.

Covers, in order (mirrors the route's own gate order): membership (AC8),
the `PROJECT_PRD_EDIT_ENABLED` rollout flag off → no-op (AC9), target
resolution via `_resolve_prd_id` — 0/ambiguous PRDs and NO `prd_id` in the
request body make no write (AC10) — a resolvable own-project PRD applying
in place with exactly one version snapshot (AC7/AC10), and the disambiguated-
pick path: an OPTIONAL client-supplied `prd_id` (the id the caller chose off
a prior `clarify` envelope's `prd_options`) is honored, but ONLY after it
survives the ★ cross-project (`assert_prd_on_project`) + cross-tenant
(`require_owned_prd`) IDOR gate inside `apply_chat_edit_scoped` — a
mutation-proofed guard (a `prd_id` on another project / another tenant is
refused, zero write).

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
        # An id embedded in the INSTRUCTION TEXT changes nothing — the route
        # only ever reads a target from the dedicated `prd_id` FIELD (absent
        # here), never by parsing `instruction`.
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


# ── The ask→pick→apply loop: an OPTIONAL client-supplied `prd_id` ───────────


def _seed_other_project_prd(t, isolated_settings, *, name="Other project"):
    """A second project in the SAME tenant, carrying its own PRD — the
    cross-project (same-tenant) IDOR target."""
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    other_project = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name=name, created_by=t.user_id,
    )
    other_prd_id = _seed_prd(isolated_settings["db"], dataset="acme")
    projects_db.add_artifact(other_project["id"], "prd", other_prd_id)
    return other_project["id"], other_prd_id


def test_route_explicit_prd_id_applies_the_chosen_prd(
    tenant_client, isolated_settings, monkeypatch
):
    """AC2 — closes the ask→pick→apply loop: on a 2-PRD project, the caller
    supplies the id they picked off a prior `clarify` envelope's
    `prd_options`; the edit applies to THAT PRD only — the sibling PRD on
    the same project is untouched."""
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)
    from app.db import projects as projects_db

    prd_a = _seed_prd(isolated_settings["db"], dataset="acme")
    prd_b = _seed_prd(isolated_settings["db"], dataset="acme")
    projects_db.add_artifact(project_id, "prd", prd_a)
    projects_db.add_artifact(project_id, "prd", prd_b)

    monkeypatch.setattr(prd_questions, "apply_chat_edit", lambda *a, **kw: {
        "html": "<html><body><h1>Doc v2 (B)</h1></body></html>",
        "sections_changed": ["Requirements"],
        "summary": "Tightened requirements.",
    })

    resp = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "tighten requirements", "prd_id": prd_b},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["edited"] is True
    assert "Doc v2 (B)" in _payload(prd_b)
    assert len(_versions(prd_b)) == 1
    # The sibling PRD on the SAME project is untouched.
    assert _versions(prd_a) == []
    assert "Doc v2" not in _payload(prd_a)


def test_route_explicit_prd_id_cross_project_denied(
    tenant_client, isolated_settings, monkeypatch
):
    """★ IDOR, mutation-proofed. A client-supplied `prd_id` naming a PRD on
    a DIFFERENT project (same tenant) is refused with zero write — the ★
    cross-project gate (`assert_prd_on_project`, inside
    `apply_chat_edit_scoped`) runs on the client-supplied id exactly as it
    would on a server-resolved one; `_resolve_prd_id` performs no project
    check of its own on an explicit id.

    Mutation proof: with the gate bypassed (`assert_prd_on_project` forced
    to a no-op — the exact effect of deleting the ★ check, a throwaway
    monkeypatch scoped to THIS test only), the SAME request WRITES to the
    other project's PRD — RED, proving the check is load-bearing, not
    coincidentally never reached. With the real gate restored it is refused
    and the other project's PRD is untouched — GREEN."""
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=True)
    _, red_target_prd = _seed_other_project_prd(t, isolated_settings, name="Other project (red)")
    _, green_target_prd = _seed_other_project_prd(t, isolated_settings, name="Other project (green)")

    def _hijack_edit(*a, **kw):
        return {
            "html": "<html><body><h1>HIJACKED</h1></body></html>",
            "sections_changed": ["Requirements"], "summary": "Tightened requirements.",
        }

    monkeypatch.setattr(prd_questions, "apply_chat_edit", _hijack_edit)

    import app.project_chat_edit as project_chat_edit_mod
    from app.project_prd_gate import assert_prd_on_project as real_assert_prd_on_project

    # RED — the ★ gate bypassed.
    monkeypatch.setattr(project_chat_edit_mod, "assert_prd_on_project", lambda **kw: None)
    resp_red = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "tighten requirements", "prd_id": red_target_prd},
    )
    assert resp_red.status_code == 200, resp_red.text
    assert resp_red.json()["edited"] is True  # RED: the bypassed gate let the cross-project write through
    assert "HIJACKED" in _payload(red_target_prd)

    # GREEN — restore the REAL gate function directly (a targeted re-patch,
    # not a full `monkeypatch.undo()` — this fixture's stack also carries
    # `tenant_client`/`isolated_settings`' own fake-DB wiring, which a blanket
    # undo would tear down along with the bypass).
    monkeypatch.setattr(project_chat_edit_mod, "assert_prd_on_project", real_assert_prd_on_project)
    resp_green = t.client.post(
        f"/v1/projects/{project_id}/prd/chat-edit",
        json={"instruction": "tighten requirements", "prd_id": green_target_prd},
    )
    assert resp_green.status_code == 200, resp_green.text
    body_green = resp_green.json()
    assert body_green["edited"] is False
    assert "only edit a PRD that's attached to this project" in body_green["answer"]
    assert "HIJACKED" not in _payload(green_target_prd)
    assert _versions(green_target_prd) == []


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
    monkeypatch.setattr(prd_questions, "apply_chat_edit", lambda *a, **kw: editor_called.append(1))

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
