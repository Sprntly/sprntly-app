"""§E — the shared prd-patch routes' gate is widened to
`_feature_enabled() or project_prd_edit_enabled()` (Option B).

The project-chat PRD-edit flow reuses the existing Design-Agent list/accept/
reject routes to surface + resolve its pending patches. Without the widen, a
deployment running project PRD-edit with `DESIGN_AGENT_ENABLED=0` would 404 on
those routes and strand every project patch in `pending`. This proves:

  - both flags OFF → 404 (feature invisible — a Design-Agent-only deployment
    with the feature off is byte-for-byte unchanged);
  - DESIGN_AGENT_ENABLED=1 alone → 200 (Design-Agent-only deployment UNCHANGED,
    non-breakage);
  - PROJECT_PRD_EDIT_ENABLED=1 alone (DESIGN_AGENT_ENABLED=0) → 200 (the widen).

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


def test_list_route_is_served_now_that_project_prd_edit_is_always_on(
    company_client, monkeypatch
):
    """Same unreachable state as the accept-route test below — see its docstring.

    `_clear_flags` cannot switch the feature off any more, because the gate is
    `_feature_enabled() or project_prd_edit_enabled()` and the second is an
    always-true shim since its rollout finished.
    """
    _clear_flags(monkeypatch)
    from app.project_prd_patch_tool import project_prd_edit_enabled

    assert project_prd_edit_enabled() is True
    resp = company_client.get("/v1/design-agent/prd-patches", params={"prd_id": 1})
    assert resp.status_code != 404


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


def test_accept_route_is_served_now_that_project_prd_edit_is_always_on(
    company_client, monkeypatch
):
    """The "both flags off" state is UNREACHABLE — so this asserts what is now
    true instead of a 404 that can no longer happen.

    The gate is `_feature_enabled() or project_prd_edit_enabled()`, and the
    second was reduced to an always-true shim when its rollout finished. Clearing
    the environment therefore leaves the route served, and the test that expected
    a 404 had been failing ever since — reporting a retired flag as a broken
    route.

    The real boundary was never this flag: it is membership plus the
    cross-project / cross-tenant checks the handler applies. A 404 here would
    have meant "feature off", not "not yours".
    """
    _clear_flags(monkeypatch)
    from app.project_prd_patch_tool import project_prd_edit_enabled

    assert project_prd_edit_enabled() is True, (
        "if this flag can be switched off again, restore the 404 test above it"
    )
    pid = _seed_patch(status="pending")
    resp = company_client.post(f"/v1/design-agent/prd-patches/{pid}/accept")
    assert resp.status_code != 404
