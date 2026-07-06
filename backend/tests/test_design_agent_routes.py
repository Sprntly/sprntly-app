"""Tests for app.routes.design_agent — POST /v1/design-agent/generate,
GET /v1/design-agent/{id}, the feature-flag gate, and the main.py wiring (P1-07).

Runs fully in isolation against the in-memory FakeSupabaseClient (the P1 dev env
is mid-migration; live integration is deferred to the P1-11 smoke). We reuse
conftest's `isolated_settings` for env + module-reload + fake-client wiring, then
add the `prototypes` tables on top (same approach as test_db_prototypes.py) and
reload app.db.prototypes → app.routes.design_agent → app.main in dependency order
so the route binds to the fake-Supabase-wired helpers.

AUTH NOTE (P6-10): the routes now gate on `require_company` — a Supabase
`Authorization: Bearer` JWT resolved to a `company_members` row — instead of the
legacy `sprntly_app_session` cookie. So these tests use conftest's bearer-authed
`company_client` (via the local `client` fixture), whose calls resolve
`workspace_id == _TEST_COMPANY_ID` ("co-test"). The distinct company id (not
"app") is what proves the workspace value's SOURCE moved from the session aud to
`company.company_id`. Cross-workspace isolation (AC #3/#7) is exercised by seeding
a row under a *foreign* workspace_id directly via the DB helper and asserting the
company GET returns 404 — the workspace filter itself is what we prove.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.auth import CompanyContext
from tests.conftest import (
    _TEST_COMPANY_ID,
    _TEST_USER_ID,
    _enable_supabase_bearer,
    _mint_supabase_token,
)

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
    completed_at           TEXT,
    share_mode             TEXT NOT NULL DEFAULT 'private'
                           CHECK (share_mode IN ('private', 'public', 'passcode')),
    share_token            TEXT UNIQUE,
    share_passcode_hash    TEXT
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
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())

    import importlib as _il
    import app.config as _config_mod
    _il.reload(_config_mod)
    import app.connectors.tokens as _tokens_mod
    _il.reload(_tokens_mod)

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)            # rebind require_client -> reloaded client
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)           # rebind its `from app.db.prototypes import ...`
    import app.main as main_mod
    importlib.reload(main_mod)             # rebuild the app with the reloaded router

    import app.db as db_mod
    return SimpleNamespace(proto=proto_mod, routes=routes_mod, main=main_mod, db=db_mod)


@pytest.fixture
def client(company_client) -> TestClient:
    """Bearer-authed TestClient (require_company) — see conftest.company_client.
    Every authed call resolves workspace_id to _TEST_COMPANY_ID."""
    return company_client


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


def _stub_generate(monkeypatch, routes_mod, *, status="complete", iters=1, raises=None, virtual_fs=None):
    """Patch routes.generate_prototype; return the captured-kwargs list.

    P1-08 changed the runner contract: generate_prototype now returns
    `(RunResult, virtual_fs)`. The stub returns the matching tuple. `virtual_fs`
    defaults to `{}` so a "complete" status hits the route's "emitted no files"
    branch and does NOT trigger a real vite build in these route-level tests
    (the build/stage path has its own coverage in test_design_agent_storage.py).
    """
    calls: list[dict] = []

    async def _fake(**kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        return SimpleNamespace(status=status, iters=iters), (virtual_fs or {})

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
    pid = env.proto.start_prototype(prd_id=1, workspace_id=_TEST_COMPANY_ID, template_version=1)
    assert client.get(f"/v1/design-agent/{pid}").status_code == 200
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)
    assert client.get(f"/v1/design-agent/{pid}").status_code == 404


# ─── Short-circuit on existing row (AC #5) ─────────────────────────────────


def test_generate_short_circuits_on_existing_ready_row(env, client, monkeypatch):
    calls = _stub_generate(monkeypatch, env.routes)
    # A ready row at the current template_version for (prd_id, workspace).
    pid = env.proto.start_prototype(
        prd_id=1, workspace_id=_TEST_COMPANY_ID,
        template_version=env.routes.DESIGN_AGENT_TEMPLATE_VERSION,
    )
    env.proto.complete_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, bundle_url="https://x")

    resp = client.post("/v1/design-agent/generate", json={"prd_id": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["prototype_id"] == pid
    assert body["status"] == "ready"
    # No new background run fired.
    assert calls == []
    # No duplicate row inserted.
    rows = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert rows is not None
    from tests import _fake_supabase
    all_rows = _fake_supabase.get_fake_db().execute(
        "SELECT id FROM prototypes WHERE prd_id = 1 AND workspace_id = ?", (_TEST_COMPANY_ID,)
    ).fetchall()
    assert len(all_rows) == 1


def test_generate_short_circuits_on_existing_generating_row(env, client, monkeypatch):
    calls = _stub_generate(monkeypatch, env.routes)
    pid = env.proto.start_prototype(
        prd_id=2, workspace_id=_TEST_COMPANY_ID,
        template_version=env.routes.DESIGN_AGENT_TEMPLATE_VERSION,
    )
    resp = client.post("/v1/design-agent/generate", json={"prd_id": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["prototype_id"] == pid
    assert body["status"] == "generating"
    assert calls == []


# ─── Workspace isolation (AC #2, #3, #7) ───────────────────────────────────


def test_generate_writes_workspace_id_from_company_id(env, client, monkeypatch):
    # AC3: the persisted workspace_id comes from the caller's resolved company_id
    # (_TEST_COMPANY_ID), NOT the hardcoded string "app". A distinct company id is
    # what proves the SOURCE changed (session aud → company.company_id).
    _stub_generate(monkeypatch, env.routes)
    _seed_prd(env.db)
    resp = client.post("/v1/design-agent/generate", json={"prd_id": 1})
    assert resp.status_code == 200, resp.text
    pid = resp.json()["prototype_id"]
    # Row is visible under the caller's company_id and carries it as workspace_id.
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row is not None
    assert row["workspace_id"] == _TEST_COMPANY_ID
    assert row["workspace_id"] != "app"


def test_get_returns_row_for_same_workspace(env, client):
    pid = env.proto.start_prototype(prd_id=5, workspace_id=_TEST_COMPANY_ID, template_version=1)
    resp = client.get(f"/v1/design-agent/{pid}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == pid


def test_get_returns_404_for_cross_workspace_id(env, client):
    # Row seeded under a FOREIGN workspace ('demo'); the company caller filters by
    # its resolved company_id (_TEST_COMPANY_ID) and must not see it. Kept on a
    # non-_TEST_COMPANY_ID value on purpose — cross-tenant invisibility holds with
    # company_id as the workspace value exactly as it did with the session aud.
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
    resp = await env.routes.generate(
        body=req,
        company=CompanyContext(
            company_id=_TEST_COMPANY_ID, role="owner", user_id=_TEST_USER_ID
        ),
    )
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
    # Sanitized: the failed row carries the safe class + generic message, never
    # the raw exception text ("boom").
    assert row["error"] == "error_class=INTERNAL | error_message=Something went wrong."
    assert "boom" not in row["error"]


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


# ─── Failure-path diagnostics: propagate RunResult.error_message (P2-02) ────


def _stub_generate_result(
    monkeypatch, routes_mod, *, status="error", iters=3,
    error_message=None, error_class=None,
):
    """Patch routes.generate_prototype to return a non-ok RunResult carrying the
    structured error fields (error_message / error_class), mirroring what the
    runner sets at runner.py:256-257 when the agent loop catches an exception.

    Distinct from `_stub_generate` (which builds a bare status/iters namespace)
    because P2-02 specifically needs the error_message / error_class fields
    populated on the result.
    """

    async def _fake(**kwargs):
        return (
            SimpleNamespace(
                status=status,
                iters=iters,
                error_message=error_message,
                error_class=error_class,
            ),
            {},  # no files -> the route's else-branch fails the row
        )

    monkeypatch.setattr(routes_mod, "generate_prototype", _fake)


@pytest.mark.asyncio
async def test_run_generation_bg_propagates_error_message_to_db(env, monkeypatch):
    """P2-02 AC1/AC4: a structured RunResult error reaches the error column.

    The failure path must store error_message AND error_class alongside the
    base status/iters summary so an Anthropic BadRequestError can be
    root-caused instead of being dropped on the floor.
    """
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    _stub_generate_result(
        monkeypatch, env.routes,
        status="error", iters=3,
        error_message="BadRequestError: messages.3: content blocks may not be empty",
        error_class="BadRequestError",
    )
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)

    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "failed"
    stored = row["error"]
    assert "status=error" in stored
    assert "iters=3" in stored
    assert "error_message=BadRequestError: messages.3" in stored
    assert "error_class=BadRequestError" in stored


@pytest.mark.asyncio
async def test_run_generation_bg_falls_through_when_error_message_none(env, monkeypatch):
    """P2-02 AC2: when error_message/error_class are None the format is unchanged.

    No `error_message=` / `error_class=` segments are appended, preserving
    today's behaviour for results that carry no structured error.
    """
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    _stub_generate_result(
        monkeypatch, env.routes,
        status="error", iters=2, error_message=None, error_class=None,
    )
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)

    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "failed"
    assert row["error"] == "agent_loop ended with status=error iters=2"
    assert "error_message=" not in row["error"]
    assert "error_class=" not in row["error"]


@pytest.mark.asyncio
async def test_run_generation_bg_error_truncated_to_500_downstream(env, monkeypatch):
    """P2-02 AC3: the 500-char cap is preserved (applied in fail_prototype).

    The caller passes the full pipe-joined string untruncated; fail_prototype
    caps it at 500. An 800-char error_message proves truncation still happens
    downstream rather than in the caller.
    """
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    long_msg = "X" * 800
    _stub_generate_result(
        monkeypatch, env.routes,
        status="error", iters=1,
        error_message=long_msg, error_class="BadRequestError",
    )
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)

    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "failed"
    assert len(row["error"]) == 500
    assert row["error"].startswith("agent_loop ended with status=error iters=1")


# ─── P6-10: require_company migration (auth swap) ──────────────────────────


def test_cookie_only_path_now_rejected(env):
    """AC9 regression — proves the swap. A TestClient carrying only the legacy
    `sprntly_app_session` cookie (NO Bearer) AND a valid Origin now floors to
    401/403 from require_company. The valid Origin guarantees the rejection is the
    AUTH gate, not require_same_origin's CSRF 403 firing first for a missing Origin.
    Fails on unfixed code (where the cookie alone authed via require_app_session)."""
    c = TestClient(env.main.app)
    login = c.post("/v1/auth/login", json={"password": "test-pw", "audience": "app"})
    assert login.status_code == 200, login.text
    # Cookie is set; no Authorization header. Explicit valid Origin so the rejection
    # is the auth path (require_company → "requires a signed-in user" 403), not CSRF.
    resp = c.post(
        "/v1/design-agent/generate",
        json={"prd_id": 1},
        headers={"Origin": "http://localhost:3000"},
    )
    assert resp.status_code in (401, 403), resp.text


def test_no_bearer_no_company_floors_403(env):
    """AC1 — a request with no auth at all is rejected before the handler body
    (require_session → 401 "Not signed in"); the body never runs."""
    c = TestClient(env.main.app)
    resp = c.post(
        "/v1/design-agent/generate",
        json={"prd_id": 1},
        headers={"Origin": "http://localhost:3000"},
    )
    assert resp.status_code in (401, 403), resp.text


def test_valid_bearer_no_membership_403(env, isolated_settings, monkeypatch):
    """AC2 — a valid Supabase bearer whose `sub` has NO company_members row returns
    403 "No company membership — complete onboarding first" (from require_company);
    the handler body never runs. No membership seeded for this user."""
    _enable_supabase_bearer(monkeypatch)
    c = TestClient(env.main.app)
    c.headers["Authorization"] = f"Bearer {_mint_supabase_token('user-no-membership')}"
    resp = c.post(
        "/v1/design-agent/generate",
        json={"prd_id": 1},
        headers={"Origin": "http://localhost:3000"},
    )
    assert resp.status_code == 403, resp.text
    assert "No company membership" in resp.json().get("detail", "")


def test_authed_route_resolves_under_company(env, client):
    """AC1/AC6 — a row seeded under the caller's company_id is visible to the
    bearer-authed company client (200)."""
    pid = env.proto.start_prototype(
        prd_id=42, workspace_id=_TEST_COMPANY_ID, template_version=1
    )
    resp = client.get(f"/v1/design-agent/{pid}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == pid


def test_no_require_app_session_dep_remains():
    """Migration-completeness guard: no live `Depends(require_app_session)` remains
    in the route module. Every Design Agent route gates on session/company auth via
    `require_company`; none bypasses back to the old `require_app_session`.

    The load-bearing assertion is that the `require_app_session` dependency count is
    0. The count of `require_company` deps is intentionally NOT pinned to an exact
    number — that figure grows by one at every newly-added authenticated route, so an
    exact-equality check is brittle by design and re-breaks on legitimate route
    additions. A floor guards against the company gate being dropped wholesale
    without coupling the test to the exact route count."""
    import app.routes.design_agent as da

    src = Path(da.__file__).read_text()
    # Load-bearing: zero routes still depend on the OLD require_app_session.
    assert "Depends(require_app_session)" not in src
    # Floor only (not an exact count) — the company gate is present on the migrated
    # routes; the exact number is deliberately unpinned to survive new routes.
    assert src.count("Depends(require_company)") >= 16


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


# ─── GET /by-prd/{prd_id} — read-only PRD→ready-prototype lookup ────────────


def _seed_ready_prototype(env, *, prd_id: int, workspace_id: str) -> int:
    """Seed a READY prototype row for a PRD under a workspace; return its id.

    Mirrors the ready-row seeding used by the generate short-circuit test:
    start a generating row, then mark it complete (status='ready', bundle_url
    populated)."""
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id=workspace_id, template_version=1
    )
    env.proto.complete_prototype(
        prototype_id=pid, workspace_id=workspace_id, bundle_url="https://x"
    )
    return pid


def test_by_prd_returns_ready_prototype(env, client):
    # A ready prototype for the PRD in the caller's workspace resolves to 200
    # with the prototype row.
    pid = _seed_ready_prototype(env, prd_id=70, workspace_id=_TEST_COMPANY_ID)
    resp = client.get("/v1/design-agent/by-prd/70")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == pid
    assert body["prd_id"] == 70
    assert body["status"] == "ready"


def test_by_prd_returns_404_when_none(env, client):
    # No ready prototype for the PRD → 404 (the frontend swallows 404→null).
    resp = client.get("/v1/design-agent/by-prd/71")
    assert resp.status_code == 404


def test_by_prd_no_generate_side_effect(env, client):
    # The lookup is a pure read: calling it for a PRD with no prototype must NOT
    # insert a prototypes row (count stays 0), unlike POST /generate.
    from tests import _fake_supabase

    resp = client.get("/v1/design-agent/by-prd/72")
    assert resp.status_code == 404
    rows = _fake_supabase.get_fake_db().execute(
        "SELECT id FROM prototypes WHERE prd_id = ? AND workspace_id = ?",
        (72, _TEST_COMPANY_ID),
    ).fetchall()
    assert rows == []


def test_by_prd_cross_workspace_returns_404(env, client):
    # A ready prototype under a FOREIGN workspace ('demo') is invisible to the
    # company caller (filtered by its resolved company_id) → 404, not 403, not
    # 200: cross-tenant existence is never disclosed.
    _seed_ready_prototype(env, prd_id=73, workspace_id="demo")
    resp = client.get("/v1/design-agent/by-prd/73")
    assert resp.status_code == 404


def test_by_prd_returns_404_when_flag_off(env, client, monkeypatch):
    # Seed a real ready row; with the flag ON the lookup resolves it (200), with
    # the flag cleared the same PRD is invisible (404) — proves the gate, not a
    # missing row.
    _seed_ready_prototype(env, prd_id=74, workspace_id=_TEST_COMPANY_ID)
    assert client.get("/v1/design-agent/by-prd/74").status_code == 200
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)
    assert client.get("/v1/design-agent/by-prd/74").status_code == 404


def test_by_prd_without_app_session_returns_401(env, unauth):
    # No signed-in session → 401 (require_company runs before the handler body,
    # so the auth rejection precedes the feature-flag check).
    resp = unauth.get("/v1/design-agent/by-prd/70")
    assert resp.status_code == 401


def test_by_prd_two_segment_resolves(env, client):
    # The two-segment path /by-prd/{prd_id} resolves to get_by_prd and is NOT
    # consumed by the single-segment GET /{prototype_id} catch-all. A one-segment
    # route pattern can only match one-segment paths, so /by-prd/<id> can never
    # be coerced into the int prototype_id param (a 422 never occurs here),
    # regardless of declaration order. A ready row → 200 with the by-prd result
    # (keyed by prd_id, not prototype_id) confirms reachability; no row → 404.
    pid = _seed_ready_prototype(env, prd_id=75, workspace_id=_TEST_COMPANY_ID)
    resp = client.get("/v1/design-agent/by-prd/75")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == pid
    assert body["prd_id"] == 75
    # And a PRD with no ready prototype resolves to the handler's 404, not a
    # 422 path-validation error from the single-segment catch-all.
    assert client.get("/v1/design-agent/by-prd/76").status_code == 404


def test_by_prd_registered_exactly_once(env):
    # Regression: a duplicate, identical get_by_prd definition once registered the
    # same GET /by-prd/{prd_id} route twice (duplicate operation ids / redundant
    # registration). There must be exactly ONE GET route at that path.
    matches = [
        r for r in env.main.app.router.routes
        if getattr(r, "path", None) == "/v1/design-agent/by-prd/{prd_id}"
        and "GET" in getattr(r, "methods", set())
    ]
    assert len(matches) == 1, f"expected one /by-prd route, found {len(matches)}"
    # Operation ids across the router are unique (a duplicate op id breaks the
    # generated OpenAPI client).
    op_ids = [
        r.operation_id for r in env.main.app.router.routes
        if getattr(r, "operation_id", None)
    ]
    assert len(op_ids) == len(set(op_ids))


# ─── GET /by-prd/{prd_id}/active — ready-OR-generating resume lookup ─────────


def test_active_by_prd_returns_generating_prototype(env, client):
    # The resume lookup matches an IN-FLIGHT generating row (unlike /by-prd which
    # is ready-only) so a (re)load mid-generation can re-attach and poll to ready.
    pid = env.proto.start_prototype(
        prd_id=170, workspace_id=_TEST_COMPANY_ID, template_version=1
    )
    resp = client.get("/v1/design-agent/by-prd/170/active")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == pid
    assert body["status"] == "generating"


def test_active_by_prd_returns_ready_prototype(env, client):
    # A ready row also resolves (the route reveals the canvas directly for these).
    pid = _seed_ready_prototype(env, prd_id=171, workspace_id=_TEST_COMPANY_ID)
    resp = client.get("/v1/design-agent/by-prd/171/active")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == pid
    assert body["status"] == "ready"


def test_active_by_prd_returns_404_when_none(env, client):
    # No active prototype for the PRD → 404 (frontend swallows 404→null → panel).
    assert client.get("/v1/design-agent/by-prd/172/active").status_code == 404


def test_active_by_prd_no_generate_side_effect(env, client):
    # Pure read: a lookup for a PRD with no prototype must NOT insert a row.
    from tests import _fake_supabase

    assert client.get("/v1/design-agent/by-prd/173/active").status_code == 404
    rows = _fake_supabase.get_fake_db().execute(
        "SELECT id FROM prototypes WHERE prd_id = ? AND workspace_id = ?",
        (173, _TEST_COMPANY_ID),
    ).fetchall()
    assert rows == []


def test_active_by_prd_cross_workspace_returns_404(env, client):
    # A generating row under a FOREIGN workspace is invisible to the caller → 404,
    # not 403, not 200: cross-tenant existence is never disclosed.
    env.proto.start_prototype(prd_id=174, workspace_id="demo", template_version=1)
    assert client.get("/v1/design-agent/by-prd/174/active").status_code == 404


def test_active_by_prd_returns_404_when_flag_off(env, client, monkeypatch):
    # Gated by the same feature flag as the rest of the surface.
    env.proto.start_prototype(
        prd_id=175, workspace_id=_TEST_COMPANY_ID, template_version=1
    )
    assert client.get("/v1/design-agent/by-prd/175/active").status_code == 200
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)
    assert client.get("/v1/design-agent/by-prd/175/active").status_code == 404


def test_active_by_prd_three_segment_resolves(env, client):
    # The three-segment /by-prd/{prd_id}/active resolves to get_active_by_prd and
    # is never shadowed by the two-segment /by-prd/{prd_id} (ready-only) route: a
    # generating row is 404 on /by-prd but 200 on /by-prd/.../active.
    env.proto.start_prototype(
        prd_id=176, workspace_id=_TEST_COMPANY_ID, template_version=1
    )
    assert client.get("/v1/design-agent/by-prd/176").status_code == 404
    assert client.get("/v1/design-agent/by-prd/176/active").status_code == 200


# ─── GET /by-prd/{prd_id}/latest — any-status (incl 'failed') lookup ─────────


def _seed_failed_prototype(env, *, prd_id: int, workspace_id: str) -> int:
    """Seed a FAILED prototype row for a PRD; return its id."""
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id=workspace_id, template_version=1
    )
    env.proto.fail_prototype(
        prototype_id=pid, workspace_id=workspace_id, error="ViteBuildError: boom"
    )
    return pid


def test_latest_by_prd_returns_failed_prototype(env, client):
    # The latest lookup matches a FAILED row — invisible to both /by-prd (ready
    # only) and /by-prd/.../active (ready-or-generating) — so the prototype route
    # can show an error+retry surface instead of the bare generate CTA.
    pid = _seed_failed_prototype(env, prd_id=270, workspace_id=_TEST_COMPANY_ID)
    resp = client.get("/v1/design-agent/by-prd/270/latest")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == pid
    assert body["status"] == "failed"
    # The same PRD is 404 on the ready-only and active lookups (the gap closed).
    assert client.get("/v1/design-agent/by-prd/270").status_code == 404
    assert client.get("/v1/design-agent/by-prd/270/active").status_code == 404


def test_latest_by_prd_returns_404_when_none(env, client):
    # No prototype at all for the PRD → 404 (frontend swallows 404→null → empty).
    assert client.get("/v1/design-agent/by-prd/271/latest").status_code == 404


def test_latest_by_prd_no_generate_side_effect(env, client):
    # Pure read: a lookup for a PRD with no prototype must NOT insert a row.
    from tests import _fake_supabase

    assert client.get("/v1/design-agent/by-prd/272/latest").status_code == 404
    rows = _fake_supabase.get_fake_db().execute(
        "SELECT id FROM prototypes WHERE prd_id = ? AND workspace_id = ?",
        (272, _TEST_COMPANY_ID),
    ).fetchall()
    assert rows == []


def test_latest_by_prd_cross_workspace_returns_404(env, client):
    # A failed row under a FOREIGN workspace is invisible to the caller → 404,
    # not 403, not 200: cross-tenant existence is never disclosed.
    _seed_failed_prototype(env, prd_id=273, workspace_id="demo")
    assert client.get("/v1/design-agent/by-prd/273/latest").status_code == 404


def test_latest_by_prd_returns_404_when_flag_off(env, client, monkeypatch):
    # Gated by the same feature flag as the rest of the surface.
    _seed_failed_prototype(env, prd_id=274, workspace_id=_TEST_COMPANY_ID)
    assert client.get("/v1/design-agent/by-prd/274/latest").status_code == 200
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)
    assert client.get("/v1/design-agent/by-prd/274/latest").status_code == 404


# ─── Connected-repo identifier threaded into generation ─────────────────────
#
# The Generate modal lets a user pick one of their connected GitHub repos. That
# repo full_name ("org/repo") threads through the request body → the background
# generation task → the scaffold prompt as a single "existing codebase to match"
# context line. When we can match the repo owner to a known GitHub App
# installation, generation also snapshots that installation id on the prototype
# row for the future codebase design-system extractor. There is still no file
# fetch, no clone, and NO new agent tool (the action-tool registry stays at the
# fixed six).


def _scaffold_user_text(calls: list[dict]) -> str:
    """Pull the rendered scaffold user text out of the captured generate kwargs."""
    return calls[0]["user_message"]["content"][0]["text"]


def test_generate_accepts_github_repo(env, client, monkeypatch):
    # A request carrying a repo identifier succeeds with the unchanged response
    # shape — the field is additive and optional. When the company has an App
    # installation covering the repo owner, that installation id is snapshotted
    # on the prototype row. Resolution is company-scoped: it needs no personal
    # OAuth connection (the connector is company-shared).
    _stub_generate(monkeypatch, env.routes)
    _seed_prd(env.db)
    env.db.upsert_github_installation(
        installation_id=12345,
        account_id=99,
        account_login="org",
        account_type="Organization",
        company_id=_TEST_COMPANY_ID,
    )
    resp = client.post(
        "/v1/design-agent/generate",
        json={"prd_id": 1, "github_repo": "org/repo"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "generating"
    assert isinstance(body["prototype_id"], int)
    persisted = env.proto.get_prototype(
        prototype_id=body["prototype_id"],
        workspace_id=_TEST_COMPANY_ID,
    )
    assert persisted["github_installation_id"] == 12345


def test_generate_resolves_installation_without_oauth_connection(
    env, client, monkeypatch
):
    # The connector is company-shared and resolution goes through the company's
    # App installations, NOT the connecting user's personal OAuth token — so a
    # company member with no personal OAuth connection still grounds against the
    # company's installation (and grounding survives OAuth-token expiry).
    _stub_generate(monkeypatch, env.routes)
    _seed_prd(env.db)
    env.db.upsert_github_installation(
        installation_id=12345,
        account_id=99,
        account_login="org",
        account_type="Organization",
        company_id=_TEST_COMPANY_ID,
    )
    # No _seed_github_connection — there is no personal OAuth token.
    resp = client.post(
        "/v1/design-agent/generate",
        json={"prd_id": 1, "github_repo": "org/repo"},
    )
    assert resp.status_code == 200, resp.text
    persisted = env.proto.get_prototype(
        prototype_id=resp.json()["prototype_id"],
        workspace_id=_TEST_COMPANY_ID,
    )
    assert persisted["github_installation_id"] == 12345


def test_generate_does_not_persist_installation_for_another_company(
    env, client, monkeypatch
):
    # An installation owned by a DIFFERENT company never resolves for this
    # company — the resolver is company-scoped — so the id stays null.
    _stub_generate(monkeypatch, env.routes)
    _seed_prd(env.db)
    env.db.upsert_github_installation(
        installation_id=12345,
        account_id=99,
        account_login="org",
        account_type="Organization",
        company_id="some-other-company-" + "deadbeef",
    )
    resp = client.post(
        "/v1/design-agent/generate",
        json={"prd_id": 1, "github_repo": "org/repo"},
    )
    assert resp.status_code == 200, resp.text
    persisted = env.proto.get_prototype(
        prototype_id=resp.json()["prototype_id"],
        workspace_id=_TEST_COMPANY_ID,
    )
    assert persisted["github_installation_id"] is None


def test_generate_resolves_single_bound_install_by_binding(
    env, client, monkeypatch
):
    # Resolve-by-binding: the company has exactly ONE non-suspended install, so
    # grounding resolves to it for any repo it can pick — even when the install's
    # account_login differs from the repo owner. (The picker mints repos against
    # this same single install, so picker/resolver stay in lockstep; a future
    # extractor rejection is an honest extraction-time failure, not a
    # generate-time decision.) Previously this stayed null because resolution
    # keyed on account_login ILIKE owner.
    _stub_generate(monkeypatch, env.routes)
    _seed_prd(env.db)
    env.db.upsert_github_installation(
        installation_id=12345,
        account_id=99,
        account_login="other-org",
        account_type="Organization",
        company_id=_TEST_COMPANY_ID,
    )
    resp = client.post(
        "/v1/design-agent/generate",
        json={"prd_id": 1, "github_repo": "org/repo"},
    )
    assert resp.status_code == 200, resp.text
    persisted = env.proto.get_prototype(
        prototype_id=resp.json()["prototype_id"],
        workspace_id=_TEST_COMPANY_ID,
    )
    assert persisted["github_installation_id"] == 12345


def test_generate_leaves_github_installation_null_when_ambiguous_multi_install(
    env, client, monkeypatch
):
    # Genuine null: the company has MULTIPLE non-suspended installs and none
    # matches the repo owner by login → ambiguous by the DB alone → grounding
    # resolves to None and generation proceeds ungrounded (id stays null).
    _stub_generate(monkeypatch, env.routes)
    _seed_prd(env.db)
    env.db.upsert_github_installation(
        installation_id=12345, account_id=99, account_login="other-org",
        account_type="Organization", company_id=_TEST_COMPANY_ID,
    )
    env.db.upsert_github_installation(
        installation_id=67890, account_id=88, account_login="second-org",
        account_type="Organization", company_id=_TEST_COMPANY_ID,
    )
    resp = client.post(
        "/v1/design-agent/generate",
        json={"prd_id": 1, "github_repo": "org/repo"},
    )
    assert resp.status_code == 200, resp.text
    persisted = env.proto.get_prototype(
        prototype_id=resp.json()["prototype_id"],
        workspace_id=_TEST_COMPANY_ID,
    )
    assert persisted["github_installation_id"] is None


def test_scaffold_user_renders_codebase_block_when_repo_present():
    from app.design_agent.prompts import render_scaffold_user

    rendered = render_scaffold_user(
        prd_md="# prd",
        target_platform="both",
        instructions="",
        figma_frames="(no Figma source detected)",
        codebase_repo="org/repo",
    )
    assert "Existing codebase to match: org/repo" in rendered
    assert "(no codebase source)" not in rendered


def test_scaffold_user_renders_no_codebase_line_when_absent():
    from app.design_agent.prompts import render_scaffold_user

    # Both the omitted and the explicit-empty cases render the no-source line and
    # never leak a repo name.
    for missing in (None, "", "   "):
        rendered = render_scaffold_user(
            prd_md="# prd",
            target_platform="both",
            instructions="",
            figma_frames="(no Figma source detected)",
            codebase_repo=missing,
        )
        assert "(no codebase source)" in rendered
        assert "Existing codebase to match" not in rendered


@pytest.mark.asyncio
async def test_github_repo_threads_to_generate_prototype(env, monkeypatch):
    # The repo identifier reaches generate_prototype AND lands in the rendered
    # scaffold prompt as the "existing codebase to match" line.
    calls = _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id="app", template_version=1
    )
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
        github_repo="org/repo", github_installation_id=12345,
    )
    assert calls[0]["github_repo"] == "org/repo"
    assert calls[0]["github_installation_id"] == 12345
    assert "Existing codebase to match: org/repo" in _scaffold_user_text(calls)


@pytest.mark.asyncio
async def test_empty_github_repo_treated_as_absent(env, monkeypatch):
    # A whitespace-only repo renders the no-source line, and the request-model
    # normaliser collapses empty / whitespace to None (same as omitted).
    calls = _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id="app", template_version=1
    )
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
        github_repo="   ",
    )
    assert "(no codebase source)" in _scaffold_user_text(calls)

    req = env.routes.GenerateRequest(prd_id=1, github_repo="   ")
    assert req.normalised_github_repo() is None
    assert env.routes.GenerateRequest(prd_id=1).normalised_github_repo() is None
    assert (
        env.routes.GenerateRequest(prd_id=1, github_repo="org/repo")
        .normalised_github_repo()
        == "org/repo"
    )


def test_tool_registry_unchanged_by_repo_threading():
    # Threading a repo identifier into the prompt adds NO agent tool: the action
    # registry stays exactly the fixed six, and the exit-sentinel set is unchanged.
    from app.design_agent.tools import ACTION_TOOLS, SENTINEL_TOOLS

    assert [t.name for t in ACTION_TOOLS] == [
        "view", "write", "line_replace", "search", "fetch_figma", "read_console",
    ]
    assert all(t.category == "action" for t in ACTION_TOOLS)
    assert {t.name for t in SENTINEL_TOOLS} == {
        "clarifying_question", "propose_prd_patch",
    }
    assert len(SENTINEL_TOOLS) <= 4


@pytest.mark.asyncio
async def test_repo_does_not_change_scenario_label(env, monkeypatch):
    # A repo-only generate (no Figma, no website) yields the same scenario label
    # as the baseline no-source case — the existing detector owns scenario
    # inference; a repo string alone does not flip it.
    calls = _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(
        prd_id=prd_id, workspace_id="app", template_version=1
    )
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
        github_repo="org/repo",
    )
    assert calls[0]["scenario"] == "0"


# ─── Figma file listing (Generate modal design selector) ──────────────────────
#
# A read-only authed proxy over the Figma REST API that backs the Generate
# modal's design-source selector. The token resolver (decryption + 404-when-not-
# connected) is reused from the connectors route; the REST helper lives in the
# connectors lane. These tests patch those two boundaries so the route's gating,
# workspace-scoping, route-ordering, and honest-degradation behaviour are
# verified in isolation.

_FIGMA_TOKEN_TARGET = "app.routes.connectors._figma_access_token"
_FIGMA_FETCH_TARGET = "app.connectors.figma_oauth.fetch_files"


def test_list_figma_files_returns_normalised_list(env, client, monkeypatch):
    monkeypatch.setattr(_FIGMA_TOKEN_TARGET, lambda company_id: "figd_tok")
    monkeypatch.setattr(
        _FIGMA_FETCH_TARGET,
        lambda token, *a, **k: [
            {"key": "k1", "name": "Home"},
            {"key": "k2", "name": "Checkout"},
        ],
    )
    resp = client.get("/v1/design-agent/figma-files")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "files": [
            {"key": "k1", "name": "Home"},
            {"key": "k2", "name": "Checkout"},
        ]
    }


def test_list_figma_files_404_when_flag_off(env, client, monkeypatch):
    # Flag is checked FIRST — a probe with the flag off is indistinguishable from
    # a missing route (404, never 401/403). No token resolution happens.
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)
    monkeypatch.setattr(_FIGMA_TOKEN_TARGET, lambda company_id: "figd_tok")
    resp = client.get("/v1/design-agent/figma-files")
    assert resp.status_code == 404


def test_list_figma_files_404_when_not_connected(env, client, monkeypatch):
    # Flag on, no Figma connection: the reused token resolver raises 404, which
    # the route propagates unchanged (it is outside the honest-degradation guard).
    from fastapi import HTTPException

    def _not_connected(company_id):
        raise HTTPException(404, "Figma is not connected")

    monkeypatch.setattr(_FIGMA_TOKEN_TARGET, _not_connected)
    resp = client.get("/v1/design-agent/figma-files")
    assert resp.status_code == 404


def test_list_figma_files_requires_company(env, unauth):
    # No session bearer → require_company rejects before the handler body runs.
    resp = unauth.get("/v1/design-agent/figma-files")
    assert resp.status_code == 401


def test_list_figma_files_scoped_to_company(env, client, monkeypatch):
    # Workspace isolation: the token lookup is scoped to the CALLER's company id,
    # so a request can only ever resolve its own company's Figma connection.
    seen: dict[str, str] = {}

    def _capture(company_id):
        seen["company_id"] = company_id
        return "figd_tok"

    monkeypatch.setattr(_FIGMA_TOKEN_TARGET, _capture)
    monkeypatch.setattr(_FIGMA_FETCH_TARGET, lambda token, *a, **k: [])
    resp = client.get("/v1/design-agent/figma-files")
    assert resp.status_code == 200, resp.text
    assert seen["company_id"] == _TEST_COMPANY_ID


def test_figma_files_route_not_shadowed_by_catch_all(env, client, monkeypatch):
    # A real reachability call: GET /figma-files must hit the listing handler, NOT
    # the single-segment GET /{prototype_id} catch-all (which would 422 trying to
    # parse "figma-files" as an int). A 200 with a files body proves the static
    # route is declared above the catch-all and wins.
    monkeypatch.setattr(_FIGMA_TOKEN_TARGET, lambda company_id: "figd_tok")
    monkeypatch.setattr(
        _FIGMA_FETCH_TARGET, lambda token, *a, **k: [{"key": "k1", "name": "Home"}]
    )
    resp = client.get("/v1/design-agent/figma-files")
    assert resp.status_code == 200, resp.text
    assert resp.status_code != 422
    assert "files" in resp.json()


def test_list_figma_files_upstream_failure_clean_error(env, client, monkeypatch):
    # An upstream listing failure (the helper raising) maps to a clean empty list,
    # NOT a 500 leaking the upstream body — the modal renders the honest empty
    # state.
    monkeypatch.setattr(_FIGMA_TOKEN_TARGET, lambda company_id: "figd_tok")

    def _boom(token, *a, **k):
        raise RuntimeError("upstream 500 body that must not leak")

    monkeypatch.setattr(_FIGMA_FETCH_TARGET, _boom)
    resp = client.get("/v1/design-agent/figma-files")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"files": []}


class _FakeResp:
    def __init__(self, ok, payload, status_code=200, text=""):
        self.ok = ok
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


def test_fetch_files_empty_without_team_id():
    # Honest degradation: the current OAuth grant captures no team_id and lacks a
    # project-listing scope, so with no team_id the helper returns an empty list
    # without making any upstream call (no fake files).
    from app.connectors import figma_oauth

    assert figma_oauth.fetch_files("figd_tok") == []


def test_fetch_files_walks_team_projects(monkeypatch):
    # When a team_id IS supplied, the helper walks teams -> projects -> files and
    # returns a normalised [{key, name}] list.
    from app.connectors import figma_oauth

    def _fake_get(url, **kwargs):
        if "/teams/" in url:
            return _FakeResp(True, {"projects": [{"id": "p1"}]})
        if "/projects/" in url:
            return _FakeResp(
                True,
                {"files": [{"key": "k1", "name": "Home"}, {"key": "k2", "name": "App"}]},
            )
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(figma_oauth.requests, "get", _fake_get)
    assert figma_oauth.fetch_files("figd_tok", team_id="t1") == [
        {"key": "k1", "name": "Home"},
        {"key": "k2", "name": "App"},
    ]


def test_fetch_files_upstream_error_returns_empty(monkeypatch):
    # A non-ok projects response logs + returns an empty list (never raises, never
    # leaks the body to the caller).
    from app.connectors import figma_oauth

    def _fake_get(url, **kwargs):
        return _FakeResp(False, {}, status_code=403, text="forbidden")

    monkeypatch.setattr(figma_oauth.requests, "get", _fake_get)
    assert figma_oauth.fetch_files("figd_tok", team_id="t1") == []
