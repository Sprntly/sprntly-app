"""`POST /v1/projects/{project_id}/prd/content` — the project artifact
drawer's full-document PRD-save endpoint, and the wave's cross-tenant IDOR
moment: `prd_id` in the body is CLIENT-SUPPLIED, unlike the main-chat
`PUT /v1/prd/{id}` (cross-TENANT-gated only, no project concept).

Covers, in the route's own gate order: `assert_prd_on_project` (the ★
cross-PROJECT gate) BEFORE `require_owned_prd` (the cross-TENANT gate) BEFORE
any `save_prd_version`/`update_prd_content` write — per-path mutation proofs
(call-count == 0 on every refuse path), the valid-save snapshot-then-update
sequence, best-effort snapshot-failure swallowing, and the no-body-content /
no-cost-line observability contract.

Real `projects`/`project_members`/`project_artifacts`/`prds`/`prd_versions`
rows via `tenant_client` + `isolated_settings` (the fake in-memory Supabase
every backend suite composes on) — same convention as the sibling
`test_projects_prd_chat_edit_route.py`. The real cross-project/cross-tenant
Postgres fan-out (two REAL tenants through the REAL route) is exercised by
the env-gated `test_project_prd_content_live.py`.
"""
from __future__ import annotations

import logging

import pytest

import app.routes.projects as routes_projects
from tests import _fake_supabase
from app.db.client import require_client
from app.db.workspaces import ensure_default_workspace

# `assert_prd_on_project` walks `list_artifacts_for_project` ->
# `list_artifacts_for_company`, which queries `prototypes` unconditionally —
# deliberately NOT in conftest's shared fake schema; every fan-out test file
# adds its own trimmed copy, same convention as
# `test_projects_prd_chat_edit_route.py` / `test_project_artifacts_fanout.py`.
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


def _seed_prd(db_mod, dataset="acme", title="Doc", html="<html><body><h1>Doc</h1></body></html>"):
    brief_id = db_mod.save_brief(
        dataset=dataset, week_label="Week of stub",
        payload={"summary_headline": "s", "insights": [{"title": "I0"}], "_schema_version": 1},
        schema_version=1,
    )
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title=title,
        template_version=1, variant="v3", source="chat", theme_id="chat:seed",
    )
    db_mod.complete_prd(prd_id, title=title, md=html)
    return prd_id


def _seed_project(t, isolated_settings, *, with_prd: bool = True, dataset=None):
    from app.db import projects as projects_db

    ws_id = ensure_default_workspace(t.company_id)["id"]
    project = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name="Launch", created_by=t.user_id,
    )
    prd_id = None
    if with_prd:
        prd_id = _seed_prd(isolated_settings["db"], dataset=dataset or t.slug)
        projects_db.add_artifact(project["id"], "prd", prd_id)
    return project["id"], prd_id


def _versions(prd_id):
    return (
        require_client().table("prd_versions").select("*")
        .eq("prd_id", prd_id).execute().data or []
    )


def _payload(prd_id):
    return require_client().table("prds").select("payload_md").eq(
        "id", prd_id
    ).execute().data[0]["payload_md"]


def _title(prd_id):
    return require_client().table("prds").select("title").eq(
        "id", prd_id
    ).execute().data[0]["title"]


def _post(t, project_id, prd_id, title="New Title", html="<html><body>new</body></html>"):
    return t.client.post(
        f"/v1/projects/{project_id}/prd/content",
        json={"prd_id": prd_id, "title": title, "html": html},
    )


# ── AC1 — gate order (load-bearing) ───────────────────────────────────────
def test_gate_order_project_before_tenant_before_write(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_id, _ = _seed_project(t, isolated_settings, with_prd=False)

    order: list[str] = []

    def _fake_assert(*, prd_id, project_id, dataset, company_id):
        order.append("assert_prd_on_project")

    def _fake_require_owned(prd_id, company_id, workspace_id=None):
        order.append("require_owned_prd")
        return {"title": "Old", "payload_md": "old body"}

    def _fake_save_version(prd_id, title, payload_md, saved_by="user"):
        order.append("save_prd_version")
        return {"id": 1}

    def _fake_update(prd_id, title, html):
        order.append("update_prd_content")
        return {"id": prd_id, "title": title, "payload_md": html}

    monkeypatch.setattr(routes_projects, "assert_prd_on_project", _fake_assert)
    monkeypatch.setattr(routes_projects, "require_owned_prd", _fake_require_owned)
    monkeypatch.setattr(routes_projects, "save_prd_version", _fake_save_version)
    monkeypatch.setattr(routes_projects, "update_prd_content", _fake_update)

    resp = _post(t, project_id, 999)
    assert resp.status_code == 200, resp.text
    assert order == [
        "assert_prd_on_project", "require_owned_prd", "save_prd_version", "update_prd_content",
    ]


# ── AC2/AC4 — cross-project (same tenant) → 403, zero write ──────────────
def test_cross_project_prd_id_403_zero_write(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_a, prd_a = _seed_project(t, isolated_settings)
    project_b, _ = _seed_project(t, isolated_settings, with_prd=False)

    save_calls = []
    update_calls = []
    monkeypatch.setattr(
        routes_projects, "save_prd_version",
        lambda *a, **kw: save_calls.append((a, kw)) or {"id": 1},
    )
    monkeypatch.setattr(
        routes_projects, "update_prd_content",
        lambda *a, **kw: update_calls.append((a, kw)) or {},
    )

    # prd_a is NOT attached to project_b — the REAL assert_prd_on_project gate
    # (unstubbed) must deny it.
    resp = _post(t, project_b, prd_a)
    assert resp.status_code == 403, resp.text
    assert "attached to this project" in resp.json()["detail"]
    assert save_calls == []
    assert update_calls == []
    assert _versions(prd_a) == []


# ── AC2 — fail-closed proof: the gate is the ONLY thing stopping the write ─
def test_cross_project_failclosed_proof(tenant_client, isolated_settings, monkeypatch):
    t = tenant_client.make(slug="acme")
    project_a, prd_a = _seed_project(t, isolated_settings)
    project_b, _ = _seed_project(t, isolated_settings, with_prd=False)

    update_calls = []
    monkeypatch.setattr(
        routes_projects, "update_prd_content",
        lambda *a, **kw: update_calls.append((a, kw)) or {"id": prd_a},
    )

    original_gate = routes_projects.assert_prd_on_project
    # Bypass the gate: it now silently passes ANY prd_id.
    monkeypatch.setattr(routes_projects, "assert_prd_on_project", lambda **kw: None)
    resp_bypassed = _post(t, project_b, prd_a)
    assert resp_bypassed.status_code == 200, resp_bypassed.text
    assert len(update_calls) == 1, "with the gate bypassed the cross-project write DOES occur"

    # Restore the real gate — the SAME cross-project id is blocked again.
    monkeypatch.setattr(routes_projects, "assert_prd_on_project", original_gate)
    update_calls.clear()
    resp_restored = _post(t, project_b, prd_a)
    assert resp_restored.status_code == 403, resp_restored.text
    assert update_calls == []


# ── AC3/AC4 — cross-tenant → 404, zero write ──────────────────────────────
def test_cross_tenant_prd_id_404_zero_write(tenant_client, isolated_settings, monkeypatch):
    t_a = tenant_client.make(slug="acme")
    t_b = tenant_client.make(slug="globex")
    project_a, _ = _seed_project(t_a, isolated_settings, with_prd=False)
    prd_b = _seed_prd(isolated_settings["db"], dataset="globex", title="Globex Doc")

    # Stub ONLY the project gate to pass (a foreign-tenant prd_id would never
    # genuinely survive the real project-fan-out intersection — see
    # project_prd_gate.py's tolerated-stale docstring — so this isolates
    # the SECOND, cross-tenant gate as defense in depth: even if the project
    # gate were somehow bypassed/buggy, require_owned_prd still refuses).
    monkeypatch.setattr(routes_projects, "assert_prd_on_project", lambda **kw: None)
    save_calls = []
    update_calls = []
    monkeypatch.setattr(
        routes_projects, "save_prd_version",
        lambda *a, **kw: save_calls.append((a, kw)) or {"id": 1},
    )
    monkeypatch.setattr(
        routes_projects, "update_prd_content",
        lambda *a, **kw: update_calls.append((a, kw)) or {},
    )

    resp = _post(t_a, project_a, prd_b)
    assert resp.status_code == 404, resp.text
    assert save_calls == []
    assert update_calls == []
    assert _versions(prd_b) == []
    assert _payload(prd_b) != "<html><body>new</body></html>"


# ── AC5 — valid save: one snapshot of the OLD content, then one update ────
def test_valid_save_snapshots_preedit_then_updates(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings)
    before_versions = len(_versions(prd_id))
    old_title = _title(prd_id)
    old_payload = _payload(prd_id)

    resp = _post(t, project_id, prd_id, title="Revised Title", html="<html><body>revised</body></html>")
    assert resp.status_code == 200, resp.text

    versions = _versions(prd_id)
    assert len(versions) == before_versions + 1
    snap = versions[-1]
    assert snap["title"] == old_title
    assert snap["payload_md"] == old_payload

    assert _payload(prd_id) == "<html><body>revised</body></html>"
    assert _title(prd_id) == "Revised Title"
    body = resp.json()
    assert body["title"] == "Revised Title"
    assert body["payload_md"] == "<html><body>revised</body></html>"


# ── AC6 — snapshot failure is swallowed; the write still lands ───────────
def test_snapshot_failure_swallowed_update_still_runs(tenant_client, isolated_settings, monkeypatch, caplog):
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings)

    def _boom(*a, **kw):
        raise RuntimeError("simulated snapshot failure")

    monkeypatch.setattr(routes_projects, "save_prd_version", _boom)

    with caplog.at_level(logging.WARNING, logger="app.routes.projects"):
        resp = _post(t, project_id, prd_id, title="Still Saved", html="<html><body>still saved</body></html>")
    assert resp.status_code == 200, resp.text
    assert _payload(prd_id) == "<html><body>still saved</body></html>"
    assert _versions(prd_id) == []  # the snapshot itself never landed

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("auto-version snapshot failed" in r.getMessage() for r in warnings)
    assert any(f"prd_id={prd_id}" in r.getMessage() for r in warnings)


# ── AC7 — observability: identifiers only, no body content, no cost line ──
def test_no_body_content_in_logs_and_no_cost_line(tenant_client, isolated_settings, caplog):
    t = tenant_client.make(slug="acme")
    project_id, prd_id = _seed_project(t, isolated_settings)
    secret_html = "<html><body>SECRET_PRD_BODY_DO_NOT_LOG</body></html>"
    secret_title = "SECRET_TITLE_DO_NOT_LOG"

    with caplog.at_level(logging.INFO):
        resp = _post(t, project_id, prd_id, title=secret_title, html=secret_html)
    assert resp.status_code == 200, resp.text

    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRET_PRD_BODY_DO_NOT_LOG" not in joined
    assert "SECRET_TITLE_DO_NOT_LOG" not in joined
    assert "est_cost_usd=" not in joined
