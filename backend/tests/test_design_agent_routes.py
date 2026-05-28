"""Tests for app.routes.design_agent — POST /v1/design-agent/generate,
GET /v1/design-agent/{id}, the feature-flag gate, and the main.py wiring (P1-07).

Runs fully in isolation against the in-memory FakeSupabaseClient (the P1 dev env
is mid-migration; live integration is deferred to the P1-11 smoke). We reuse
conftest's `isolated_settings` for env + module-reload + fake-client wiring, then
add the `prototypes` tables on top (same approach as test_db_prototypes.py) and
reload app.db.prototypes → app.routes.design_agent → app.main in dependency order
so the route binds to the fake-Supabase-wired helpers.

AUTH NOTE: the routes use `require_app_session` (app audience only, per the
ticket + BUILD.md §6 — production traffic is the app cookie). So these tests log
in with `audience="app"` (conftest's shared `app_client` uses the demo cookie and
would 401 here). Cross-workspace isolation (AC #3/#7) is exercised by seeding a
row under a *foreign* workspace_id directly via the DB helper and asserting the
app-session GET returns 404 — the route is app-only, so a demo *session* never
reaches the workspace filter (it 401s at the auth dep); the workspace filter
itself is what we prove.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

# SQLite-compatible translation of the P1-06 prototypes migration (mirrors
# test_db_prototypes.py — the fake exercises SQL semantics, not Postgres DDL).
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
    completed_at           TEXT
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

_MAIN_PY = Path(__file__).resolve().parents[1] / "app" / "main.py"


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + prototypes tables + feature flag ON, with the design
    agent module stack reloaded in dependency order. Returns the live modules."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)

    # Gate ON by default; individual gate tests flip/clear it. Read at request
    # time, so no reload needed when a test changes it.
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)            # rebind require_client -> reloaded client
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)           # rebind its `from app.db.prototypes import ...`
    import app.main as main_mod
    importlib.reload(main_mod)             # rebuild the app with the reloaded router

    import app.db as db_mod
    return SimpleNamespace(proto=proto_mod, routes=routes_mod, main=main_mod, db=db_mod)


@pytest.fixture
def client(env) -> TestClient:
    """TestClient with an APP-audience session cookie (require_app_session)."""
    c = TestClient(env.main.app)
    resp = c.post("/v1/auth/login", json={"password": "test-pw", "audience": "app"})
    assert resp.status_code == 200, resp.text
    return c


@pytest.fixture
def unauth(env) -> TestClient:
    """TestClient without any session cookie."""
    return TestClient(env.main.app)


# ─── helpers ────────────────────────────────────────────────────────────────


def _seed_prd(db_mod, body: str = "# PRD body") -> int:
    """Insert a ready PRD row so _load_prd_body finds payload_md."""
    prd_id = db_mod.start_prd(
        brief_id=1, insight_index=0, title="t", template_version=1, variant="v2"
    )
    db_mod.complete_prd(prd_id, title="t", md=body)
    return prd_id


def _stub_generate(monkeypatch, routes_mod, *, status="complete", iters=1, raises=None):
    """Patch routes.generate_prototype; return the captured-kwargs list."""
    calls: list[dict] = []

    async def _fake(**kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        return SimpleNamespace(status=status, iters=iters)

    monkeypatch.setattr(routes_mod, "generate_prototype", _fake)
    return calls


# ─── Creation (AC #1) ─────────────────────────────────────────────────────


def test_generate_returns_within_200ms(env, client, monkeypatch):
    _stub_generate(monkeypatch, env.routes)
    _seed_prd(env.db)
    start = time.perf_counter()
    resp = client.post("/v1/design-agent/generate", json={"prd_id": 1})
    elapsed = time.perf_counter() - start
    assert resp.status_code == 200, resp.text
    # No Anthropic call in the request path — the agent loop runs in the
    # background task, so the handler returns near-instantly.
    assert elapsed < 0.2, f"POST took {elapsed:.3f}s (>200ms budget)"


def test_generate_returns_prototype_id_and_generating_status(env, client, monkeypatch):
    _stub_generate(monkeypatch, env.routes)
    _seed_prd(env.db)
    resp = client.post("/v1/design-agent/generate", json={"prd_id": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["prototype_id"], int) and body["prototype_id"] > 0
    assert body["status"] == "generating"


# ─── Feature-flag gate (AC #4) ─────────────────────────────────────────────


def test_generate_returns_404_when_flag_unset(env, client, monkeypatch):
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)
    resp = client.post("/v1/design-agent/generate", json={"prd_id": 1})
    assert resp.status_code == 404


def test_generate_returns_404_when_flag_false_string(env, client, monkeypatch):
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "false")
    resp = client.post("/v1/design-agent/generate", json={"prd_id": 1})
    assert resp.status_code == 404


def test_generate_returns_404_when_flag_zero(env, client, monkeypatch):
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "0")
    resp = client.post("/v1/design-agent/generate", json={"prd_id": 1})
    assert resp.status_code == 404


def test_generate_succeeds_when_flag_one(env, client, monkeypatch):
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    _stub_generate(monkeypatch, env.routes)
    _seed_prd(env.db)
    resp = client.post("/v1/design-agent/generate", json={"prd_id": 1})
    assert resp.status_code == 200, resp.text


def test_get_returns_404_when_flag_unset(env, client, monkeypatch):
    # Seed a real row; with the flag ON the GET resolves it (200), with the flag
    # cleared the same id is invisible (404) — proves the gate, not a missing row.
    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    assert client.get(f"/v1/design-agent/{pid}").status_code == 200
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)
    assert client.get(f"/v1/design-agent/{pid}").status_code == 404


# ─── Short-circuit on existing row (AC #5) ─────────────────────────────────


def test_generate_short_circuits_on_existing_ready_row(env, client, monkeypatch):
    calls = _stub_generate(monkeypatch, env.routes)
    # A ready row at the current template_version for (prd_id, workspace).
    pid = env.proto.start_prototype(
        prd_id=1, workspace_id="app",
        template_version=env.routes.DESIGN_AGENT_TEMPLATE_VERSION,
    )
    env.proto.complete_prototype(prototype_id=pid, workspace_id="app", bundle_url="https://x")

    resp = client.post("/v1/design-agent/generate", json={"prd_id": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["prototype_id"] == pid
    assert body["status"] == "ready"
    # No new background run fired.
    assert calls == []
    # No duplicate row inserted.
    rows = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert rows is not None
    from tests import _fake_supabase
    all_rows = _fake_supabase.get_fake_db().execute(
        "SELECT id FROM prototypes WHERE prd_id = 1 AND workspace_id = 'app'"
    ).fetchall()
    assert len(all_rows) == 1


def test_generate_short_circuits_on_existing_generating_row(env, client, monkeypatch):
    calls = _stub_generate(monkeypatch, env.routes)
    pid = env.proto.start_prototype(
        prd_id=2, workspace_id="app",
        template_version=env.routes.DESIGN_AGENT_TEMPLATE_VERSION,
    )
    resp = client.post("/v1/design-agent/generate", json={"prd_id": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["prototype_id"] == pid
    assert body["status"] == "generating"
    assert calls == []


# ─── Workspace isolation (AC #2, #3, #7) ───────────────────────────────────


def test_generate_writes_workspace_id_from_session_aud(env, client, monkeypatch):
    _stub_generate(monkeypatch, env.routes)
    _seed_prd(env.db)
    resp = client.post("/v1/design-agent/generate", json={"prd_id": 1})
    assert resp.status_code == 200, resp.text
    pid = resp.json()["prototype_id"]
    # Row is visible under 'app' (the session aud) and carries workspace_id='app'.
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row is not None
    assert row["workspace_id"] == "app"


def test_get_returns_row_for_same_workspace(env, client):
    pid = env.proto.start_prototype(prd_id=5, workspace_id="app", template_version=1)
    resp = client.get(f"/v1/design-agent/{pid}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == pid


def test_get_returns_404_for_cross_workspace_id(env, client):
    # Row seeded under a FOREIGN workspace ('demo'); the app-session caller
    # filters by 'app' and must not see it.
    pid = env.proto.start_prototype(prd_id=9, workspace_id="demo", template_version=1)
    resp = client.get(f"/v1/design-agent/{pid}")
    assert resp.status_code == 404


def test_get_returns_404_for_unknown_id(env, client):
    assert client.get("/v1/design-agent/999999").status_code == 404


# ─── Auth gate (error handling) ────────────────────────────────────────────


def test_generate_without_app_session_returns_401(env, unauth):
    resp = unauth.post("/v1/design-agent/generate", json={"prd_id": 1})
    assert resp.status_code == 401


def test_get_without_app_session_returns_401(env, unauth):
    resp = unauth.get("/v1/design-agent/1")
    assert resp.status_code == 401


def test_generate_with_invalid_prd_id_returns_422(env, client):
    # prd_id=0 violates Field(..., gt=0).
    resp = client.post("/v1/design-agent/generate", json={"prd_id": 0})
    assert resp.status_code == 422


# ─── Background-task discipline (AC #6) — direct async handler call ─────────


@pytest.mark.asyncio
async def test_background_task_held_in_inflight_set(env, monkeypatch):
    """After POST, the bg task is strong-ref'd in _inflight_tasks; once it
    completes, add_done_callback(discard) removes it (proves AC #6)."""
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    _stub_generate(monkeypatch, env.routes)
    _seed_prd(env.db)

    req = env.routes.GenerateRequest(prd_id=1)
    resp = await env.routes.generate(body=req, session={"aud": "app"})
    assert resp.status == "generating"
    # The task was created but has not been scheduled to run yet (no await
    # boundary crossed inside generate after create_task).
    assert len(env.routes._inflight_tasks) == 1

    # Drain: yield until the done-callback discards the completed task.
    for _ in range(1000):
        if not env.routes._inflight_tasks:
            break
        await asyncio.sleep(0)
    assert len(env.routes._inflight_tasks) == 0  # discarded via add_done_callback


@pytest.mark.asyncio
async def test_background_task_failure_marks_prototype_failed(env, monkeypatch):
    """generate_prototype raises -> _run_generation_bg marks the row failed with
    the existing Sprntly error format f'{type}: {msg}' (AC #9)."""
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    _stub_generate(monkeypatch, env.routes, raises=ValueError("boom"))
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)

    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "failed"
    assert row["error"].startswith("ValueError: boom")


@pytest.mark.asyncio
async def test_background_task_marks_failed_when_runner_incomplete(env, monkeypatch):
    """A runner result that did not reach 'complete' fails the row (P1-07 has no
    bundle-staging step; P1-08 reverses this on the success path)."""
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    _stub_generate(monkeypatch, env.routes, status="max_iters", iters=8)
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)

    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "failed"
    assert "status=max_iters" in row["error"]


# ─── LLM-calling surface: system block + cache_control (AC #8) ─────────────


@pytest.mark.asyncio
async def test_run_generation_bg_passes_single_cache_controlled_system_block(env, monkeypatch):
    """Exactly one system block, cache_control ephemeral ttl 1h at the end of the
    stable prefix (AD2 / TICKET_STANDARD §2 LLM-calling AC)."""
    calls = _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db, body="# my prd")
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)

    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key="FIGKEY",
    )
    assert len(calls) == 1
    system_blocks = calls[0]["system_blocks"]
    assert isinstance(system_blocks, list) and len(system_blocks) == 1
    last = system_blocks[-1]
    assert last["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    # Figma present -> Scenario A label passed through to the runner.
    assert calls[0]["scenario"] == "A"


@pytest.mark.asyncio
async def test_run_generation_bg_scenario_zero_when_no_inputs(env, monkeypatch):
    calls = _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
    )
    assert calls[0]["scenario"] == "0"


# ─── Observability: no PII in logs (AC #11) ────────────────────────────────


def test_generate_logs_no_pii(env, client, monkeypatch, caplog):
    _stub_generate(monkeypatch, env.routes)
    _seed_prd(env.db)
    secret_instructions = "TOP_SECRET_INSTRUCTIONS_VALUE"
    secret_figma = "SECRET_FIGMA_FILE_KEY_VALUE"
    with caplog.at_level(logging.INFO):
        resp = client.post(
            "/v1/design-agent/generate",
            json={
                "prd_id": 1,
                "instructions": secret_instructions,
                "figma_file_key": secret_figma,
            },
        )
    assert resp.status_code == 200, resp.text
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert secret_instructions not in blob
    assert secret_figma not in blob


# ─── main.py wiring (smoke — string-level, isolation-friendly) (AC #12) ─────


def test_main_py_imports_design_agent_router():
    src = _MAIN_PY.read_text()
    assert "include_router(design_agent.router)" in src


def test_main_py_calls_lifespan_invalidation_helpers():
    src = _MAIN_PY.read_text()
    assert "invalidate_orphan_generating_prototypes" in src
    assert "invalidate_stale_prototypes" in src
