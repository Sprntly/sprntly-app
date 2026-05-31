"""Tests for the PRD-patch accept/reject routes (P3-10, F11):

    GET  /v1/design-agent/prd-patches?prd_id=<id>          (authed — list pending)
    POST /v1/design-agent/prd-patches/{patch_id}/accept    (authed — flip applied)
    POST /v1/design-agent/prd-patches/{patch_id}/reject    (authed — flip rejected)

These surface P3-09's `prd_patches` proposals to the PrdPatchBanner and resolve
them. The routes reuse the authed-route gates (feature flag 404 when off +
require_app_session 401 + workspace filter). Security/observability posture under
test:

  - workspace isolation (Rule #22): an accept/reject of a patch in a foreign
    workspace returns 404, never 403 (cross-tenant existence is not disclosed);
    the list route simply yields no rows under the wrong workspace.
  - 401 without a session on every route (require_app_session).
  - accept flips status pending→applied + returns the row; reject flips
    pending→rejected; list returns ONLY pending rows, created_at-ascending.
  - observability (Rule #24 / AC12): accept/reject log `prd_patch_applied` /
    `prd_patch_rejected` with patch_id only — never patch_md / rationale (PRD body).

Runs fully in isolation against the in-memory FakeSupabaseClient — same fixture
shape as test_design_agent_comment_routes.py. We reload app.db.prd_patches →
app.routes.design_agent → app.main in dependency order so the route binds to the
fake-wired helpers (the prd_patches helpers are imported at the EOF of
routes/design_agent.py, so prd_patches must be reloaded before the route module).
"""
from __future__ import annotations

import importlib
import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

# SQLite-compatible end-state of `prd_patches` after the P3-09 migration — mirrors
# test_design_agent_prd_patches._PRD_PATCHES_DDL exactly. Postgres-only constructs
# (bigint identity, timestamptz, RLS, FK references) are translated/omitted the way
# the sibling test DDLs do; the status CHECK is inlined so the fake rejects illegal
# values like Postgres. The routes never touch `prototypes`, so it is not seeded.
_DDL = """
CREATE TABLE prd_patches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id        INTEGER NOT NULL,
    prototype_id  INTEGER NOT NULL,
    workspace_id  TEXT NOT NULL,
    rationale     TEXT NOT NULL,
    patch_md      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'applied', 'rejected')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at   TEXT
);
"""

_OTHER_WS = "other-workspace"  # foreign to the app-session's aud ("app")


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + prd_patches table + feature flag ON, with the design
    agent module stack reloaded in dependency order so the EOF prd_patches imports
    bind to the fake-wired helpers."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_DDL)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.db.prd_patches as patches_mod
    importlib.reload(patches_mod)             # rebind require_client/utc_now
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)              # rebinds its EOF prd_patches imports
    import app.main as main_mod
    importlib.reload(main_mod)                # rebuild the app with the reloaded router

    return SimpleNamespace(patches=patches_mod, routes=routes_mod, main=main_mod)


@pytest.fixture
def client(env) -> TestClient:
    """TestClient with an APP-audience session cookie (require_app_session)."""
    c = TestClient(env.main.app)
    resp = c.post("/v1/auth/login", json={"password": "test-pw", "audience": "app"})
    assert resp.status_code == 200, resp.text
    return c


@pytest.fixture
def unauth(env) -> TestClient:
    """TestClient with NO session cookie — proves the routes require auth."""
    return TestClient(env.main.app)


# ─── seeding helper ────────────────────────────────────────────────────────


def _seed_patch(
    *,
    prd_id: int = 1,
    prototype_id: int = 1,
    workspace_id: str = "app",
    rationale: str = "tighten the success metric",
    patch_md: str = "## Success metric\n\nActivation within 7 days, not 30.",
    status: str = "pending",
    created_at: str = "2026-01-01 00:00:00",
) -> int:
    """Insert one prd_patches row directly into the fake DB; return its id."""
    from tests import _fake_supabase

    cur = _fake_supabase.get_fake_db().execute(
        "INSERT INTO prd_patches "
        "(prd_id, prototype_id, workspace_id, rationale, patch_md, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [prd_id, prototype_id, workspace_id, rationale, patch_md, status, created_at],
    )
    return cur.lastrowid


def _status_of(patch_id: int) -> str:
    from tests import _fake_supabase

    cur = _fake_supabase.get_fake_db().execute(
        "SELECT status FROM prd_patches WHERE id = ?", [patch_id]
    )
    return cur.fetchone()[0]


# ─── GET /prd-patches (list pending) ───────────────────────────────────────


def test_get_pending_patches_workspace_filtered(client):
    # AC6 — GET returns only PENDING rows for the prd, workspace-filtered,
    # created_at-ascending. Resolved rows + foreign-workspace rows are excluded.
    _seed_patch(prd_id=1, workspace_id="app", status="pending",
                rationale="first", created_at="2026-01-01 00:00:01")
    _seed_patch(prd_id=1, workspace_id="app", status="pending",
                rationale="second", created_at="2026-01-01 00:00:02")
    _seed_patch(prd_id=1, workspace_id="app", status="applied",
                rationale="already-applied")          # resolved → excluded
    _seed_patch(prd_id=1, workspace_id=_OTHER_WS, status="pending",
                rationale="foreign")                   # foreign ws → excluded
    _seed_patch(prd_id=2, workspace_id="app", status="pending",
                rationale="other-prd")                 # different prd → excluded

    resp = client.get("/v1/design-agent/prd-patches", params={"prd_id": 1})
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["rationale"] for r in rows] == ["first", "second"]
    assert all(r["status"] == "pending" for r in rows)
    # PrdPatchOut shape — no workspace_id / resolved_at leaked.
    assert set(rows[0].keys()) == {
        "id", "prd_id", "prototype_id", "rationale", "patch_md", "status", "created_at",
    }


def test_get_pending_patches_empty_when_none(client):
    # A PRD with no pending patches returns [] (banner renders nothing, AC2-adjacent).
    resp = client.get("/v1/design-agent/prd-patches", params={"prd_id": 99})
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_get_pending_patches_requires_session(unauth):
    resp = unauth.get("/v1/design-agent/prd-patches", params={"prd_id": 1})
    assert resp.status_code == 401


# ─── POST /prd-patches/{id}/accept ─────────────────────────────────────────


def test_accept_flips_to_applied(client):
    # AC6 — accept flips status pending→applied and returns the updated row.
    pid = _seed_patch(workspace_id="app", status="pending")
    resp = client.post(f"/v1/design-agent/prd-patches/{pid}/accept")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == pid
    assert body["status"] == "applied"
    assert _status_of(pid) == "applied"


def test_accept_wrong_workspace_returns_404(client):
    # AC6 — a patch in a foreign workspace is invisible to accept → 404, not 403,
    # and the row is NOT flipped across the tenant line.
    pid = _seed_patch(workspace_id=_OTHER_WS, status="pending")
    resp = client.post(f"/v1/design-agent/prd-patches/{pid}/accept")
    assert resp.status_code == 404
    assert _status_of(pid) == "pending"


def test_accept_requires_session(unauth):
    pid = _seed_patch(workspace_id="app", status="pending")
    resp = unauth.post(f"/v1/design-agent/prd-patches/{pid}/accept")
    assert resp.status_code == 401
    assert _status_of(pid) == "pending"


def test_accept_missing_patch_returns_404(client):
    resp = client.post("/v1/design-agent/prd-patches/123456/accept")
    assert resp.status_code == 404


# ─── POST /prd-patches/{id}/reject ─────────────────────────────────────────


def test_reject_flips_to_rejected(client):
    # AC6 — reject flips status pending→rejected and returns the updated row.
    pid = _seed_patch(workspace_id="app", status="pending")
    resp = client.post(f"/v1/design-agent/prd-patches/{pid}/reject")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == pid
    assert body["status"] == "rejected"
    assert _status_of(pid) == "rejected"


def test_reject_wrong_workspace_returns_404(client):
    pid = _seed_patch(workspace_id=_OTHER_WS, status="pending")
    resp = client.post(f"/v1/design-agent/prd-patches/{pid}/reject")
    assert resp.status_code == 404
    assert _status_of(pid) == "pending"


# ─── Observability (AC12) ──────────────────────────────────────────────────


def test_routes_log_no_patch_content(client, caplog):
    # AC12 — accept/reject log `prd_patch_applied` / `prd_patch_rejected` with
    # patch_id only; the patch_md / rationale (PRD body) NEVER reach the logs.
    secret_md = "## SECRET PRD BODY do-not-log"
    secret_rationale = "CONFIDENTIAL rationale do-not-log"
    pid_a = _seed_patch(workspace_id="app", status="pending",
                        patch_md=secret_md, rationale=secret_rationale)
    pid_r = _seed_patch(workspace_id="app", status="pending",
                        patch_md=secret_md, rationale=secret_rationale)
    with caplog.at_level(logging.INFO, logger="app.routes.design_agent"):
        assert client.post(f"/v1/design-agent/prd-patches/{pid_a}/accept").status_code == 200
        assert client.post(f"/v1/design-agent/prd-patches/{pid_r}/reject").status_code == 200
    text = caplog.text
    assert f"prd_patch_applied patch_id={pid_a}" in text
    assert f"prd_patch_rejected patch_id={pid_r}" in text
    assert secret_md not in text            # PRD body never logged
    assert secret_rationale not in text     # rationale never logged


# ─── Non-breakage (AC11) ───────────────────────────────────────────────────


def test_patch_routes_registered_and_existing_intact(env):
    # AC11 — the new routes are appended to the same router; existing routes and
    # the include_router wiring remain resolvable.
    paths = {r.path for r in env.main.app.router.routes}
    # new prd-patch surface
    assert "/v1/design-agent/prd-patches" in paths
    assert "/v1/design-agent/prd-patches/{patch_id}/accept" in paths
    assert "/v1/design-agent/prd-patches/{patch_id}/reject" in paths
    # untouched predecessors
    assert "/v1/design-agent/generate" in paths
    assert "/v1/design-agent/{prototype_id}" in paths
    assert "/v1/design-agent/{prototype_id}/comments" in paths
