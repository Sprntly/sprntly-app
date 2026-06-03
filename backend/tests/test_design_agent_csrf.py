"""Tests for the P5-06 server-side CSRF / Origin check.

`app.design_agent.csrf.require_same_origin` is a FastAPI dependency attached to every
AUTHED MUTATING Design Agent route (POST/PATCH/DELETE that depend on
`require_app_session`). It rejects (403 `{"error": "origin_mismatch"}`) a request whose
`Origin` header is missing or not in `settings.origins_list` — the SAME allow-list CORS
uses (config.py / main.py), no second list. It is NEVER attached to the anonymous public
`/by-token/*` routes, which are cross-origin by design (F6).

The suite covers three layers:
  - the dependency in isolation (missing / mismatch / match),
  - the per-route application (a representative 403 + a structural inventory that EVERY
    authed-mutating route carries the dependency and NO public route does),
  - the exemptions (public `/by-token/*` writes + authed GETs stay reachable without a
    valid Origin) and the gate-ordering / observability / single-allow-list ACs.

Runs fully in isolation against the in-memory FakeSupabaseClient. We reload the design
agent module stack in dependency order — config (via isolated_settings) → csrf →
prototypes → routes → main — so the route binds to the reloaded `require_same_origin`
and the reloaded helpers.
"""
from __future__ import annotations

import importlib
import logging
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

# The app's single known origin under isolated_settings (ALLOWED_ORIGINS).
_APP_ORIGIN = "http://localhost:3000"
_FOREIGN_ORIGIN = "https://evil.example"

# SQLite-compatible `prototypes` end-state (mirrors test_design_agent_public_routes.py)
# so the public/authed handlers that resolve a row do not 500 before the gate is proven.
_PROTOTYPE_DDL = """
CREATE TABLE prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    variant                TEXT NOT NULL DEFAULT 'v1',
    template_version       INTEGER NOT NULL,
    instructions           TEXT,
    target_platform        TEXT NOT NULL DEFAULT 'both',
    figma_file_key         TEXT,
    website_url            TEXT,
    github_installation_id INTEGER,
    bundle_url             TEXT,
    current_checkpoint_id  INTEGER,
    error                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT,
    share_mode             TEXT NOT NULL DEFAULT 'private'
                           CHECK (share_mode IN ('private', 'public', 'passcode')),
    share_token            TEXT UNIQUE,
    share_passcode_hash    TEXT,
    is_complete            INTEGER NOT NULL DEFAULT 0,
    complete_checkpoint_id INTEGER
);
CREATE TABLE prototype_checkpoints (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id      INTEGER NOT NULL,
    workspace_id      TEXT NOT NULL,
    bundle_url        TEXT,
    prd_revision_hash TEXT,
    figma_frame_hash  TEXT,
    prompt_history    TEXT NOT NULL DEFAULT '[]',
    comment_state     TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_MUTATING_METHODS = {"POST", "PATCH", "DELETE"}


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + prototypes tables + feature flag ON, with the design agent
    module stack reloaded in dependency order (csrf before routes so the route binds the
    reloaded dependency)."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.design_agent.csrf as csrf_mod
    importlib.reload(csrf_mod)             # rebind its `from app.config import settings`
    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)           # rebind require_same_origin + db helpers
    import app.main as main_mod
    importlib.reload(main_mod)             # rebuild the app with the reloaded router

    return SimpleNamespace(csrf=csrf_mod, proto=proto_mod, routes=routes_mod, main=main_mod)


@pytest.fixture
def client(env) -> TestClient:
    """TestClient with an APP-audience session cookie (require_app_session). Does NOT set
    a default Origin — each test supplies the Origin it wants to exercise."""
    c = TestClient(env.main.app)
    resp = c.post("/v1/auth/login", json={"password": "test-pw", "audience": "app"})
    assert resp.status_code == 200, resp.text
    return c


@pytest.fixture
def unauth(env) -> TestClient:
    """TestClient with NO session cookie."""
    return TestClient(env.main.app)


def _req(origin: str | None, path: str = "/v1/design-agent/1/iterate") -> Request:
    """A minimal Starlette Request carrying (or omitting) an Origin header."""
    headers = [] if origin is None else [(b"origin", origin.encode())]
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers,
        "query_string": b"",
    }
    return Request(scope)


# ─── Dependency unit (AC1 / AC2 / AC3) ──────────────────────────────────────


def test_require_same_origin_403_missing(env):
    # AC1 — absent Origin → 403 origin_mismatch (fail-closed).
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        env.csrf.require_same_origin(_req(None))
    assert exc.value.status_code == 403
    assert exc.value.detail == {"error": "origin_mismatch"}


def test_require_same_origin_403_mismatch(env):
    # AC2 — present but not in settings.origins_list → 403.
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        env.csrf.require_same_origin(_req(_FOREIGN_ORIGIN))
    assert exc.value.status_code == 403
    assert exc.value.detail == {"error": "origin_mismatch"}


def test_require_same_origin_passes_match(env):
    # AC3 — exact match against an allow-list entry → no raise, returns None.
    assert env.csrf.require_same_origin(_req(_APP_ORIGIN)) is None


# ─── Route application (AC4) ─────────────────────────────────────────────────


def test_iterate_403_on_bad_origin(client):
    # AC4 — a representative authed-mutating route rejects a foreign Origin at the server
    # (the session is valid; the Origin gate is what fails → 403 origin_mismatch).
    resp = client.post(
        "/v1/design-agent/1/iterate",
        json={"prompt": "tweak the header"},
        headers={"origin": _FOREIGN_ORIGIN},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == {"error": "origin_mismatch"}


def _dependency_calls(dependant) -> set:
    """The set of dependency callables a route resolves, walked recursively. We use
    route.dependant.dependencies directly (not get_flat_dependant, which flattens param
    metadata but discards the sub-dependant `.call` objects we need to identify by
    identity)."""
    calls = set()
    for sub in dependant.dependencies:
        if sub.call is not None:
            calls.add(sub.call)
        calls |= _dependency_calls(sub)
    return calls


def _authed_mutating_routes(app, *, require_app_session):
    """Yield (path, methods, calls) for every authed-mutating Design Agent route
    (POST/PATCH/DELETE on /v1/design-agent, NOT a /by-token/* public route, carrying
    require_app_session)."""
    for route in app.router.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if not path.startswith("/v1/design-agent"):
            continue
        if "/by-token/" in path:
            continue
        if not (methods & _MUTATING_METHODS):
            continue
        calls = _dependency_calls(route.dependant)
        if require_app_session in calls:
            yield path, methods, calls


def test_all_authed_mutating_routes_have_origin_dep(env):
    # AC4 (inventory) — EVERY authed POST/PATCH/DELETE Design Agent route (not /by-token)
    # carries require_same_origin. This is the structural regression guard: a future
    # authed mutating route added without the gate fails here.
    require_app_session = env.routes.require_app_session
    require_same_origin = env.routes.require_same_origin

    found = list(
        _authed_mutating_routes(env.main.app, require_app_session=require_app_session)
    )
    assert found, "expected to discover authed-mutating Design Agent routes"
    missing = [
        f"{sorted(m)} {p}"
        for (p, m, calls) in found
        if require_same_origin not in calls
    ]
    assert not missing, f"authed-mutating routes missing the Origin gate: {missing}"

    # Sanity: the known authed-mutating surface is covered (post-drift count ≥ 12).
    assert len(found) >= 12, f"expected ≥12 authed-mutating routes, found {len(found)}"


# ─── Public-route exemption (AC5) ────────────────────────────────────────────


def test_public_comment_write_exempt_from_origin(unauth):
    # AC5 — the public comment write carries NO Origin gate: a foreign Origin (and no
    # session) still reaches the handler → normal 404 for an unknown token, never a 403
    # origin_mismatch.
    token = str(uuid.uuid4())
    resp = unauth.post(
        f"/v1/design-agent/by-token/{token}/comments",
        json={"anchor_id": "el-1", "body": "looks good"},
        headers={"origin": _FOREIGN_ORIGIN},
    )
    assert resp.status_code != 403, resp.text
    assert resp.status_code == 404


def test_public_passcode_exempt_from_origin(unauth):
    # AC5 — the public passcode verify carries NO Origin gate either.
    token = str(uuid.uuid4())
    resp = unauth.post(
        f"/v1/design-agent/by-token/{token}/passcode",
        json={"passcode": "hunter2"},
        headers={"origin": _FOREIGN_ORIGIN},
    )
    assert resp.status_code != 403, resp.text
    assert resp.status_code == 404


def test_public_comment_write_exempt_with_absent_origin(unauth):
    # AC5 (strengthened) — even with NO Origin header at all, the public write is reachable
    # (the planner-emphasised constraint: a fail-closed gate here would break public
    # commenting from arbitrary share contexts).
    token = str(uuid.uuid4())
    resp = unauth.post(
        f"/v1/design-agent/by-token/{token}/comments",
        json={"anchor_id": "el-1", "body": "looks good"},
    )
    assert resp.status_code != 403, resp.text
    assert resp.status_code == 404


# ─── GET exemption (AC6) ─────────────────────────────────────────────────────


def test_authed_get_export_no_origin_check(client):
    # AC6 — authed GET routes are not CSRF targets: a missing Origin does not 403.
    resp = client.get("/v1/design-agent/999999/export")
    assert resp.status_code != 403, resp.text


# ─── Single allow-list (AC7) ─────────────────────────────────────────────────


def test_no_second_allow_list(env):
    # AC7 — the check reuses settings.origins_list; it introduces no second allow-list
    # (no hardcoded origin literal in the helper).
    src = Path(env.csrf.__file__).read_text()
    assert "settings.origins_list" in src
    assert "http://" not in src and "https://" not in src


# ─── Gate ordering (AC8) ─────────────────────────────────────────────────────


def test_feature_off_404_before_origin_403(client, monkeypatch):
    # AC8 — with a VALID Origin the gate passes, so a feature-off request still surfaces
    # the feature 404 (the Origin gate does not mask it) ...
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "0")
    resp = client.post(
        "/v1/design-agent/999999/complete",
        json={},
        headers={"origin": _APP_ORIGIN},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("detail") != {"error": "origin_mismatch"}


def test_no_session_401_with_valid_origin(unauth):
    # AC8 — a valid Origin but no session still 401s (require_app_session): the Origin gate
    # does not leak feature existence to an attacker who supplies a good Origin.
    resp = unauth.post(
        "/v1/design-agent/999999/complete",
        json={},
        headers={"origin": _APP_ORIGIN},
    )
    assert resp.status_code == 401, resp.text


# ─── Observability (AC9) ─────────────────────────────────────────────────────


def test_origin_403_logs_route_and_bool_not_raw_origin(env, caplog):
    # AC9 — a rejection logs the route + an origin_present boolean, NEVER the raw Origin.
    from fastapi import HTTPException

    path = "/v1/design-agent/1/iterate"
    with caplog.at_level(logging.WARNING, logger="app.design_agent.csrf"):
        with pytest.raises(HTTPException):
            env.csrf.require_same_origin(_req(_FOREIGN_ORIGIN, path=path))

    msgs = [r.getMessage() for r in caplog.records]
    rejected = [m for m in msgs if "csrf_origin_rejected" in m]
    assert rejected, f"expected a csrf_origin_rejected log line, got {msgs}"
    line = rejected[0]
    assert f"route={path}" in line
    assert "origin_present=True" in line
    assert _FOREIGN_ORIGIN not in line          # the raw Origin value is never logged


def test_origin_403_logs_origin_present_false_when_absent(env, caplog):
    # AC9 — absent Origin logs origin_present=False (boolean, not the value).
    from fastapi import HTTPException

    with caplog.at_level(logging.WARNING, logger="app.design_agent.csrf"):
        with pytest.raises(HTTPException):
            env.csrf.require_same_origin(_req(None))
    line = next(r.getMessage() for r in caplog.records if "csrf_origin_rejected" in r.getMessage())
    assert "origin_present=False" in line


# ─── Non-breakage (AC10) ─────────────────────────────────────────────────────


def test_routes_compile_and_legit_origin_passes(client):
    # AC10 — a legitimate same-origin authed request is NOT blocked by the gate: it passes
    # the Origin check and proceeds (here to a 404 for a missing prototype), never 403.
    resp = client.post(
        "/v1/design-agent/999999/complete",
        json={},
        headers={"origin": _APP_ORIGIN},
    )
    assert resp.status_code != 403, resp.text


def test_include_router_callsites_unchanged(env):
    # AC10 — the router public surface is intact: main still mounts it and the canonical
    # routes still resolve (adding a dependency does not change the router's wiring).
    paths = {r.path for r in env.main.app.router.routes}
    assert "/v1/design-agent/generate" in paths
    assert "/v1/design-agent/{prototype_id}/iterate" in paths
    assert "/v1/design-agent/by-token/{token}/comments" in paths
