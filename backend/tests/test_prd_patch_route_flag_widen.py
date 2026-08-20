"""§E — the shared prd-patch routes' gate is widened to
`_feature_enabled() or project_prd_edit_enabled()` (Option B).

The project-chat PRD-edit flow reuses the existing Design-Agent list/accept/
reject routes to surface + resolve its pending patches. Without the widen, a
deployment running project PRD-edit with `DESIGN_AGENT_ENABLED=0` would 404 on
those routes and strand every project patch in `pending`. This proves:

  - DESIGN_AGENT_ENABLED=1 alone → 200 (Design-Agent-only deployment UNCHANGED,
    non-breakage);
  - PROJECT_PRD_EDIT_ENABLED=1 alone (DESIGN_AGENT_ENABLED=0) → 200 (the widen).

UPDATE (GA): `project_prd_edit_enabled()` is now an always-true survivor — the
`PROJECT_PRD_EDIT_ENABLED` rollout flag was retired and project PRD-edit is GA.
The gate `_feature_enabled() or project_prd_edit_enabled()` is therefore always
open, so the old "both flags OFF → 404, feature invisible" case can no longer
occur: with env cleared the routes are STILL reachable (200). Those two tests
now assert that GA reality instead of a 404 that the retired flag can no longer
produce.

The gate reads env at REQUEST time, so toggling env per request suffices — no
app rebuild. Runs against the base-harness `prd_patches` table.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from tests.conftest import _TEST_COMPANY_ID

_DDL = """
CREATE TABLE IF NOT EXISTS prd_patches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id        INTEGER NOT NULL,
    prototype_id  INTEGER,
    workspace_id  TEXT NOT NULL,
    rationale     TEXT NOT NULL,
    patch_md      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'applied', 'rejected')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at   TEXT
);
"""


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """prd_patches table present + the design-agent module stack reloaded in
    dependency order so the EOF prd_patches imports bind to the fake-wired
    helpers (mirrors test_design_agent_prd_patch_routes.py). Flags are toggled
    per-request in each test (the gate reads env at REQUEST time)."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_DDL)
    import app.db.prd_patches as patches_mod
    importlib.reload(patches_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    return SimpleNamespace(patches=patches_mod, routes=routes_mod, main=main_mod)


def _seed_patch(prd_id=1, workspace_id=_TEST_COMPANY_ID, status="pending"):
    from tests import _fake_supabase

    cur = _fake_supabase.get_fake_db().execute(
        "INSERT INTO prd_patches "
        "(prd_id, prototype_id, workspace_id, rationale, patch_md, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [prd_id, 1, workspace_id, "r", "m", status, "2026-01-01 00:00:00"],
    )
    return cur.lastrowid


def _clear_flags(monkeypatch):
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)
    monkeypatch.delenv("PROJECT_PRD_EDIT_ENABLED", raising=False)


def test_list_route_ga_reachable_with_env_cleared(company_client, monkeypatch):
    # GA: project PRD-edit is always-on, so `project_prd_edit_enabled()` holds
    # the gate open even with both env flags cleared — the old "feature
    # invisible → 404" state can no longer be produced.
    _clear_flags(monkeypatch)
    resp = company_client.get("/v1/design-agent/prd-patches", params={"prd_id": 1})
    assert resp.status_code == 200


def test_list_route_200_design_agent_only_unchanged(company_client, monkeypatch):
    # Non-breakage: DESIGN_AGENT_ENABLED alone still reaches the route.
    _clear_flags(monkeypatch)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    resp = company_client.get("/v1/design-agent/prd-patches", params={"prd_id": 1})
    assert resp.status_code == 200


def test_list_route_200_project_flag_widens_with_design_agent_off(company_client, monkeypatch):
    # The widen: project PRD-edit on, Design Agent OFF → route reachable.
    _clear_flags(monkeypatch)
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    resp = company_client.get("/v1/design-agent/prd-patches", params={"prd_id": 1})
    assert resp.status_code == 200


def test_accept_route_widened_by_project_flag(company_client, monkeypatch):
    _clear_flags(monkeypatch)
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    pid = _seed_patch(status="pending")
    resp = company_client.post(f"/v1/design-agent/prd-patches/{pid}/accept")
    assert resp.status_code == 200
    assert resp.json()["status"] == "applied"


def test_reject_route_widened_by_project_flag(company_client, monkeypatch):
    _clear_flags(monkeypatch)
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    pid = _seed_patch(status="pending")
    resp = company_client.post(f"/v1/design-agent/prd-patches/{pid}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_accept_route_ga_reachable_with_env_cleared(company_client, monkeypatch):
    # GA counterpart of the list route above: the accept route is likewise
    # always reachable now (project PRD-edit GA), even with env flags cleared.
    _clear_flags(monkeypatch)
    pid = _seed_patch(status="pending")
    resp = company_client.post(f"/v1/design-agent/prd-patches/{pid}/accept")
    assert resp.status_code == 200
    assert resp.json()["status"] == "applied"
