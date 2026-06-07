"""End-to-end smoke test — Scenario A happy path with anchor-id stability (P1-11).

This is Phase 1's required CI smoke test (BUILD-PHASES.md §Phase 1 AC +
§"CI checks added across phases"). It proves the full Scenario A path through
the *real* stack, with only the Anthropic API mocked at the client boundary:

    POST /v1/design-agent/generate    →  <200ms, {prototype_id, status:'generating'}
      → background task: generate_prototype (real agent loop, mocked LLM)
          → write tool populates virtual_fs
          → P1-10 autofixer (real Node @babel companion)
          → _stage_complete_run: real `vite build` (P0-02 anchor-id plugin runs)
              → stage_bundle → filesystem (tmp_path) → complete_prototype
    GET  /v1/design-agent/{id}         →  status:'ready', non-empty bundle_url
      → the served dist/ bundle contains data-anchor-id="<8-hex>" (AD4 closure)

WHY THIS DIVERGES FROM THE TICKET'S DRAFT SNIPPET (all verified against HEAD):

1. Patch site. The runner does `from app.design_agent.client import
   get_design_agent_client` (runner.py) and calls the *local* name, so the mock
   must be installed at `app.design_agent.runner.get_design_agent_client`
   ("patch where it's used"), NOT at `app.design_agent.client...`. Patching the
   client module would leave the runner's already-bound reference pointing at the
   real Anthropic client. AC #6's intent — mock isolation, no real API call, no
   network — is fully met; only the dotted path is corrected.

2. Async harness, not sync TestClient. The route fires generation via
   `asyncio.create_task` (fire-and-forget). A bare `TestClient(app)` runs each
   request on a fresh per-request anyio portal, so that background task is
   orphaned when the request's loop tears down and never completes — the poll
   would time out. Running the app through `httpx.ASGITransport` inside an
   `async def` test keeps the task on the *test's own* event loop, where it
   progresses deterministically across `await asyncio.sleep` / `await get(...)`.
   (Lifespan is intentionally not run — the design-agent router is mounted at
   app construction, and the smoke needs no startup invalidation.)

3. Bundle scan + regex. The P0-02 plugin annotates JSX at *source* level; after
   @vitejs/plugin-react + esbuild compile/minify, the attribute lands in the
   compiled JS chunk as the object-property form `"data-anchor-id":"<hex>"`,
   NOT in index.html and NOT as an HTML attribute. So we scan the WHOLE staged
   dir and reuse the exact regex proven by
   test_design_agent_storage.py::...applies_anchor_id_plugin_integration.

4. Self-contained fixture. prototype-runtime ships only react/react-dom/@babel —
   no shadcn, no `@` alias, no Tailwind pipeline. The draft fixture's
   `@/components/ui/*` imports would fail `vite build` (unresolved) → prototype
   `failed` → smoke fails. The fixture therefore imports only `useState` from
   `react`; className strings are inert (no Tailwind processing) but harmless.

5. PRD seeding uses the real `start_prd` + `complete_prd` helpers (the draft's
   `insert_prd` / `asyncio.run(...)` do not exist; the db helpers are sync).

The real-build path (vite + Node) is guarded by `_skip_no_toolchain`, the same
pattern test_design_agent_storage.py uses: it RUNS in test-backend.yml (which
installs prototype-runtime/node_modules + @babel/parser, sets
DESIGN_AGENT_NODE_PATH) and SKIPS cleanly on a Python-only dev box. The
feature-flag, cross-workspace, cost-summary and runner-failure smokes are
always-on and need no toolchain.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import re
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from tests._fake_anthropic import _FakeStream
from urllib.parse import unquote, urlparse

import pytest
from httpx import ASGITransport, AsyncClient

from app.design_agent import storage as _storage_mod

from tests.conftest import (
    _TEST_COMPANY_ID,
    _bearer_header,
    _enable_supabase_bearer,
    _seed_company_membership,
)

# ─── Fixtures on disk ───────────────────────────────────────────────────────

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "design_agent"
PRD_FIXTURE = (FIXTURE_DIR / "scenario_a_prd.md").read_text(encoding="utf-8")
_APP_TSX = json.loads((FIXTURE_DIR / "scaffold_response.json").read_text(encoding="utf-8"))[
    "app_tsx_content"
]

# Matches both the source-level HTML-attr form (data-anchor-id="abc12345") and
# the compiled-JS object-property form ("data-anchor-id":"abc12345"). Identical
# to the assertion in test_design_agent_storage.py's real-build integration test.
ANCHOR_ID_RE = re.compile(r'data-anchor-id["\s:=]+["\'][0-9a-f]{8}')

# ─── Real-build toolchain guard (mirrors test_design_agent_storage.py) ──────

_HAS_TOOLCHAIN = (
    _storage_mod._RUNTIME_ROOT / "node_modules"
).exists() and shutil.which("npx") is not None
_skip_no_toolchain = pytest.mark.skipif(
    not _HAS_TOOLCHAIN,
    reason="prototype-runtime/node_modules or npx absent (dev env not provisioned)",
)

# SQLite-compatible DDL for the P1-06 prototypes tables (mirrors
# test_design_agent_routes.py — the fake exercises SQL semantics, not PG DDL).
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


# ─── Mock Anthropic client ──────────────────────────────────────────────────


def _block(data: dict):
    """A stand-in content block — the runner only ever calls `.model_dump()`."""
    return SimpleNamespace(model_dump=lambda: data)


def _usage(cache_creation=0, cache_read=0, inp=0, out=0):
    return SimpleNamespace(
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
        input_tokens=inp,
        output_tokens=out,
    )


def _mock_design_agent_client(*, raise_on_first: bool = False) -> MagicMock:
    """A MagicMock Anthropic client whose `.messages.create` drives a 2-iter run.

    Iter 1 → a `write` tool_use for src/App.tsx (the agent never emits
             data-anchor-id; the Vite plugin applies it at build time — AD4).
    Iter 2 → end_turn with a one-line summary.

    With `raise_on_first=True` the first call raises, exercising the runner's
    error path (RunResult.status == 'error' → prototype marked 'failed').
    """
    client = MagicMock()
    # stream() delegates to create() so the recorded usage/content/exception
    # are the real scripted ones, not auto-generated MagicMocks.
    client.messages.stream.side_effect = lambda **kw: _FakeStream(client.messages.create(**kw))
    if raise_on_first:
        client.messages.create.side_effect = RuntimeError(
            "smoke: simulated Anthropic failure"
        )
        return client
    client.messages.create.side_effect = [
        SimpleNamespace(
            stop_reason="tool_use",
            content=[_block({
                "type": "tool_use",
                "id": "tu_1",
                "name": "write",
                "input": {"path": "src/App.tsx", "content": _APP_TSX},
            })],
            usage=_usage(cache_creation=2000, cache_read=0, inp=500, out=300),
        ),
        SimpleNamespace(
            stop_reason="end_turn",
            content=[_block({"type": "text", "text": "Built a sign-in screen."})],
            usage=_usage(cache_creation=0, cache_read=2000, inp=200, out=100),
        ),
    ]
    return client


# ─── Fixtures + helpers ─────────────────────────────────────────────────────


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + prototypes tables + flag ON, modules reloaded in
    dependency order (proto → routes → main). Mirrors test_design_agent_routes."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    # prompt_history / comment_state are jsonb in Postgres — register them so the
    # fake JSON-encodes the lists create_checkpoint passes (the complete/success
    # path reaches create_checkpoint; mirrors test_design_agent_storage.py).
    monkeypatch.setitem(
        _fake_supabase._JSONB_COLUMNS,
        "prototype_checkpoints",
        {"prompt_history", "comment_state"},
    )
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    # P6-10: wire the bearer-authed require_company path (this e2e suite stays async +
    # ASGITransport for the background create_task; only the auth source changed).
    _enable_supabase_bearer(monkeypatch)
    _seed_company_membership(isolated_settings["supabase"])

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    import app.db as db_mod
    return SimpleNamespace(proto=proto_mod, routes=routes_mod, main=main_mod, db=db_mod)


def _seed_prd(db_mod, body: str = PRD_FIXTURE) -> int:
    """Insert a ready PRD whose payload_md carries the :::design fixture (AC #9).

    Uses the real sync helpers (db/prds.py); there is no `insert_prd`.
    """
    prd_id = db_mod.start_prd(
        brief_id=1, insight_index=0, title="Sign-In Flow", template_version=1, variant="v2"
    )
    db_mod.complete_prd(prd_id, title="Sign-In Flow", md=body)
    return prd_id


def _point_storage_to_tmp(monkeypatch, tmp_path: Path) -> None:
    """Force filesystem-mode staging into tmp_path (no Supabase, file:// URL)."""
    monkeypatch.delenv("SUPABASE_STORAGE_BUCKET", raising=False)
    monkeypatch.setattr(_storage_mod.settings, "storage_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(_storage_mod.settings, "storage_public_url", "", raising=False)


@asynccontextmanager
async def _login(env):
    """An httpx AsyncClient driving the ASGI app, authed via a Supabase Bearer JWT
    (require_company). The seeded membership + JWT secret are wired by the `env`
    fixture; authed calls resolve workspace_id to _TEST_COMPANY_ID.

    ASGITransport runs the app on the *current* event loop, so the route's
    `asyncio.create_task` background generation completes deterministically as
    the test awaits — unlike a per-request-portal sync TestClient. (P6-10 keeps
    this async harness; only the cookie-login → bearer-header swap changed.)
    """
    transport = ASGITransport(app=env.main.app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", headers=_bearer_header()
    ) as client:
        yield client


async def _poll_until(client, prototype_id: int, *, terminal: set[str], timeout_s: float):
    """Poll GET /{id} until status ∈ terminal or timeout; return the final row."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        resp = await client.get(f"/v1/design-agent/{prototype_id}")
        assert resp.status_code == 200, resp.text
        row = resp.json()
        if row["status"] in terminal:
            return row
        await asyncio.sleep(0.5)
    return None


# ─── Always-on smokes (no Node/Vite toolchain required) ─────────────────────


async def test_smoke_feature_flag_off_returns_404(env, monkeypatch):
    """AC #5: with DESIGN_AGENT_ENABLED cleared the route is invisible (404)."""
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)
    async with _login(env) as client:
        resp = await client.post(
            "/v1/design-agent/generate",
            json={"prd_id": 1, "target_platform": "both", "instructions": ""},
        )
    assert resp.status_code == 404


async def test_smoke_get_cross_workspace_returns_404(env):
    """Workspace isolation: a row under a foreign workspace is invisible to the
    app session (404, not 403 — existence is not disclosed; Rule #22)."""
    pid = env.proto.start_prototype(prd_id=9, workspace_id="demo", template_version=1)
    async with _login(env) as client:
        resp = await client.get(f"/v1/design-agent/{pid}")
    assert resp.status_code == 404


async def test_smoke_emits_cost_summary_log(env, monkeypatch, caplog):
    """AC #12: a completed run emits exactly one design_agent.run.complete cost
    line with identifiers only (no PRD body / instructions / API key).

    Driven directly through generate_prototype with the mocked client — the cost
    line is emitted regardless of the (toolchain-dependent) build/stage step, so
    this assertion stays always-on.
    """
    monkeypatch.setattr(
        "app.design_agent.runner.get_design_agent_client",
        lambda: _mock_design_agent_client(),
    )
    monkeypatch.setattr("app.design_agent.runner._resolve_figma_access_token", lambda k, ws: None)
    from app.design_agent.runner import generate_prototype

    secret_user = "TOP_SECRET_PRD_BODY_VALUE"
    with caplog.at_level(logging.INFO):
        result, virtual_fs = await generate_prototype(
            prototype_id=4242,
            workspace_id=_TEST_COMPANY_ID,
            system_blocks=[{
                "type": "text",
                "text": "system prompt",
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }],
            user_message={"role": "user", "content": [{"type": "text", "text": secret_user}]},
            figma_file_key=None,
            scenario="A",
        )

    assert result.status == "complete"
    assert virtual_fs.get("src/App.tsx"), "write tool should have populated virtual_fs"

    cost_lines = [
        r.getMessage() for r in caplog.records
        if r.getMessage().startswith("design_agent.run.complete")
    ]
    assert len(cost_lines) == 1, f"expected exactly one cost-summary line, got {cost_lines}"
    line = cost_lines[0]
    for token in ("prototype_id=4242", "scenario=A", "status=complete", "est_cost_usd=", "iters="):
        assert token in line, f"missing {token!r} in {line!r}"
    # P5-08 AC1/AC5: the scaffold line carries the FULL 11-field set + mode=scaffold.
    _assert_full_field_set(line)
    assert "mode=scaffold" in line.split(), f"expected mode=scaffold token in {line!r}"
    # No PII / no prompt body / no API key in the cost line (Rule #24).
    assert secret_user not in line
    assert "sk-" not in line


async def test_smoke_runner_failure_marks_failed(env, monkeypatch):
    """Error path: the Anthropic client raising → the prototype ends 'failed'
    (no bundle staged). Also proves the POST→background→poll→GET state machine
    works end-to-end without any Node/Vite toolchain."""
    monkeypatch.setattr(
        "app.design_agent.runner.get_design_agent_client",
        lambda: _mock_design_agent_client(raise_on_first=True),
    )
    monkeypatch.setattr("app.design_agent.runner._resolve_figma_access_token", lambda k, ws: None)
    prd_id = _seed_prd(env.db)

    async with _login(env) as client:
        resp = await client.post(
            "/v1/design-agent/generate",
            json={"prd_id": prd_id, "target_platform": "both", "instructions": ""},
        )
        assert resp.status_code == 200, resp.text
        prototype_id = resp.json()["prototype_id"]

        row = await _poll_until(
            client, prototype_id, terminal={"failed"}, timeout_s=30
        )

    assert row is not None, "generation did not reach status=failed within 30s"
    assert row["status"] == "failed"
    assert "status=error" in (row.get("error") or "")
    assert not row.get("bundle_url"), "no bundle should be staged on the failure path"


# ─── P5-08: per-mode cost-summary contract (telemetry verify + lock) ────────
#
# The structured cost-summary line already exists (P1-04 `log_llm_run`) and is
# wired at all three runner sites — scaffold (`design_agent.run.complete`,
# mode=scaffold), iterate (`design_agent.run.iterate`, mode=iterate), and
# manual-edit (`design_agent.run.manual_edit`, mode=manual). These tests LOCK the
# per-mode contract: every mode emits its line with the correct mode/scenario
# labels and the full 11-field set, carrying identifiers + token counts only.
#
# Each test drives the runner entrypoint DIRECTLY with the mocked client (the
# always-on pattern of test_smoke_emits_cost_summary_log) — the cost line is
# emitted regardless of the toolchain-dependent build/stage step, so no Node/Vite
# guard is needed and the LLM is never actually called.

# The 11-field cost-summary contract (BUILD-PHASES §Phase 5 deliverable 8).
# Asserted as space-delimited tokens so `input_tokens=` is never satisfied by the
# `cached_input_tokens=` substring.
_COST_OP = {
    "scaffold": "design_agent.run.complete",
    "iterate": "design_agent.run.iterate",
    "manual": "design_agent.run.manual_edit",
}
_FULL_FIELD_KEYS = (
    "prototype_id=",
    "scenario=",
    "mode=",
    "iters=",
    "cached_input_tokens=",
    "input_tokens=",
    "output_tokens=",
    "duration_ms=",
    "est_cost_usd=",
    "status=",
    "error_class=",
)
_SYS_BLOCK = {
    "type": "text",
    "text": "system prompt",
    "cache_control": {"type": "ephemeral", "ttl": "1h"},
}


def _user_msg(text: str) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _install_mock(monkeypatch) -> None:
    """Patch where it's USED (runner-local name) + stub the Figma token lookup so
    the run is hermetic. A fresh 2-iter mock per get_design_agent_client() call —
    each runner entrypoint calls it once, so each run consumes its own side_effect
    pair from position 0 (no cross-run replay leakage)."""
    monkeypatch.setattr(
        "app.design_agent.runner.get_design_agent_client",
        lambda: _mock_design_agent_client(),
    )
    monkeypatch.setattr(
        "app.design_agent.runner._resolve_figma_access_token", lambda k, ws: None
    )


def _cost_line(caplog, op_prefix: str) -> str:
    lines = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith(op_prefix)
    ]
    assert len(lines) == 1, f"expected exactly one {op_prefix!r} line, got {lines}"
    return lines[0]


def _assert_full_field_set(line: str) -> None:
    """Every one of the 11 contract fields is present as its own space-delimited
    token — a missing field fails. Token-level (not substring) so the
    `cached_input_tokens=` / `input_tokens=` overlap can't mask an absent field."""
    tokens = line.split()
    for key in _FULL_FIELD_KEYS:
        assert any(t.startswith(key) for t in tokens), f"missing {key!r} in {line!r}"


async def _run_scaffold(monkeypatch, caplog, *, scenario="A", user_text="build a screen"):
    _install_mock(monkeypatch)
    from app.design_agent.runner import generate_prototype

    with caplog.at_level(logging.INFO):
        result, _vfs = await generate_prototype(
            prototype_id=4242,
            workspace_id=_TEST_COMPANY_ID,
            system_blocks=[dict(_SYS_BLOCK)],
            user_message=_user_msg(user_text),
            figma_file_key=None,
            scenario=scenario,
        )
    assert result.status == "complete", f"scaffold run not complete: {result.status}"
    return result


async def _run_iterate(monkeypatch, caplog, *, scenario="A", user_text="tweak the header", source=None):
    _install_mock(monkeypatch)
    from app.design_agent.runner import iterate_prototype

    with caplog.at_level(logging.INFO):
        result, _vfs = await iterate_prototype(
            prototype_id=4343,
            workspace_id=_TEST_COMPANY_ID,
            system_blocks=[dict(_SYS_BLOCK)],
            user_message=_user_msg(user_text),
            current_source=source or {"src/App.tsx": "export default function App(){return null}"},
            figma_file_key=None,
            scenario=scenario,
        )
    assert result.status == "complete", f"iterate run not complete: {result.status}"
    return result


async def _run_manual(monkeypatch, caplog, *, scenario="A", user_text="commit the change", source=None):
    _install_mock(monkeypatch)
    from app.design_agent.runner import manual_edit_prototype

    with caplog.at_level(logging.INFO):
        result, _vfs = await manual_edit_prototype(
            prototype_id=4444,
            workspace_id=_TEST_COMPANY_ID,
            system_blocks=[dict(_SYS_BLOCK)],
            user_message=_user_msg(user_text),
            current_source=source or {"src/App.tsx": "export default function App(){return null}"},
            figma_file_key=None,
            scenario=scenario,
        )
    assert result.status == "complete", f"manual-edit run not complete: {result.status}"
    return result


async def test_iterate_emits_cost_summary_mode_iterate(env, monkeypatch, caplog):
    """AC2: an iterate run emits one design_agent.run.iterate line with
    mode=iterate, the run's scenario label, and the full 11-field set."""
    await _run_iterate(monkeypatch, caplog, scenario="A")
    line = _cost_line(caplog, _COST_OP["iterate"])
    assert "mode=iterate" in line.split(), f"expected mode=iterate token in {line!r}"
    assert "scenario=A" in line.split(), f"expected scenario=A token in {line!r}"
    assert "status=complete" in line.split(), f"expected status=complete in {line!r}"
    _assert_full_field_set(line)


async def test_manual_edit_emits_cost_summary_mode_manual(env, monkeypatch, caplog):
    """AC3: a manual-edit run emits one design_agent.run.manual_edit line with
    mode=manual (the runner literal — NOT 'manual-edit') and the full field set."""
    await _run_manual(monkeypatch, caplog, scenario="A")
    line = _cost_line(caplog, _COST_OP["manual"])
    assert "mode=manual" in line.split(), f"expected mode=manual token in {line!r}"
    assert "mode=manual-edit" not in line, "mode label must be the runner literal 'manual'"
    assert "status=complete" in line.split(), f"expected status=complete in {line!r}"
    _assert_full_field_set(line)


async def test_cost_lines_have_correct_mode_labels(env, monkeypatch, caplog):
    """AC4: the three modes carry mode=scaffold / mode=iterate / mode=manual
    respectively — not swapped. Each line's mode token matches its operation."""
    await _run_scaffold(monkeypatch, caplog)
    await _run_iterate(monkeypatch, caplog)
    await _run_manual(monkeypatch, caplog)

    expected_mode = {"scaffold": "scaffold", "iterate": "iterate", "manual": "manual"}
    for key, op in _COST_OP.items():
        line = _cost_line(caplog, op)
        tokens = line.split()
        assert f"mode={expected_mode[key]}" in tokens, (
            f"{op} line missing mode={expected_mode[key]}: {line!r}"
        )
        # Not swapped: no OTHER mode label appears on this line.
        for other_key, other_mode in expected_mode.items():
            if other_key != key:
                assert f"mode={other_mode}" not in tokens, (
                    f"{op} line carries wrong mode={other_mode}: {line!r}"
                )


async def test_cost_lines_carry_full_field_set(env, monkeypatch, caplog):
    """AC5: every mode's line contains all 11 named fields; a missing field
    fails the test."""
    await _run_scaffold(monkeypatch, caplog)
    await _run_iterate(monkeypatch, caplog)
    await _run_manual(monkeypatch, caplog)
    for op in _COST_OP.values():
        _assert_full_field_set(_cost_line(caplog, op))


async def test_cost_lines_no_content_leak(env, monkeypatch, caplog):
    """AC8 (Rule #24): no PRD / source / prompt content leaks into any
    cost-summary line — identifiers + token counts only."""
    secret_user = "ZZZ_SECRET_USER_PROMPT_ZZZ"
    secret_src = "QQQ_SECRET_SOURCE_BODY_QQQ"
    seeded = {"src/App.tsx": f"// {secret_src}\nexport default function App(){{return null}}"}

    await _run_scaffold(monkeypatch, caplog, user_text=secret_user)
    await _run_iterate(monkeypatch, caplog, user_text=secret_user, source=dict(seeded))
    await _run_manual(monkeypatch, caplog, user_text=secret_user, source=dict(seeded))

    for op in _COST_OP.values():
        line = _cost_line(caplog, op)
        assert secret_user not in line, f"user-prompt content leaked into {op} line: {line!r}"
        assert secret_src not in line, f"source content leaked into {op} line: {line!r}"
        assert "sk-" not in line, f"api-key-shaped token in {op} line: {line!r}"


def test_runner_has_three_cost_call_sites():
    """AC6: runner.py calls log_llm_run at exactly three sites (scaffold,
    iterate, manual-edit), each with its operation + mode literal. A new run mode
    added without the cost line drops the count and fails this guard."""
    import app.design_agent.runner as runner_mod

    src = Path(runner_mod.__file__).read_text(encoding="utf-8")
    call_sites = re.findall(r"\blog_llm_run\(", src)
    assert len(call_sites) == 3, (
        f"expected 3 log_llm_run call sites in runner.py, found {len(call_sites)}"
    )
    for op in _COST_OP.values():
        assert f'operation="{op}"' in src, f"missing operation={op!r} call in runner.py"
    for mode in ("scaffold", "iterate", "manual"):
        assert f'"mode": "{mode}"' in src, f"missing mode={mode!r} identifier in runner.py"


def test_llm_telemetry_unchanged():
    """AC7: the shared log_llm_run primitive's signature is the locked P1-04
    contract — keyword-only operation/identifier/usage/duration_ms/status/model,
    optional error_class defaulting None, and **extra. P5-08 verifies adoption; it
    never edits the primitive."""
    import inspect

    from app.llm_telemetry import log_llm_run

    params = inspect.signature(log_llm_run).parameters
    for name in ("operation", "identifier", "usage", "duration_ms", "status", "model"):
        assert name in params, f"log_llm_run lost required param {name!r}"
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{name} must stay keyword-only"
        )
    assert params["error_class"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["error_class"].default is None
    assert any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    ), "log_llm_run must keep **extra"


# ─── Full end-to-end happy path (real Vite build — toolchain-guarded) ───────


@_skip_no_toolchain
async def test_scenario_a_smoke(env, monkeypatch, tmp_path, caplog):
    """The load-bearing P1 gate: POST → poll → ready + bundle_url, and the served
    bundle carries data-anchor-id (AD4 closure through a real `vite build`)."""
    monkeypatch.setattr(
        "app.design_agent.runner.get_design_agent_client",
        lambda: _mock_design_agent_client(),
    )
    # Scenario A = a Figma file is present in the inputs; stub the connector
    # token lookup so the run is hermetic (the mocked agent never calls fetch_figma).
    monkeypatch.setattr("app.design_agent.runner._resolve_figma_access_token", lambda k, ws: None)
    _point_storage_to_tmp(monkeypatch, tmp_path)
    prd_id = _seed_prd(env.db)

    with caplog.at_level(logging.INFO):
        async with _login(env) as client:
            # ── Step 1: POST /generate returns <200ms with {prototype_id, status}.
            start = time.perf_counter()
            resp = await client.post(
                "/v1/design-agent/generate",
                json={
                    "prd_id": prd_id,
                    "target_platform": "both",
                    "instructions": "",
                    "figma_file_key": "SMOKE-FIGMA-KEY",
                },
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            assert resp.status_code == 200, resp.text
            assert elapsed_ms < 200, f"POST /generate took {elapsed_ms:.0f}ms (cap 200ms)"
            body = resp.json()
            assert isinstance(body["prototype_id"], int)
            assert body["status"] == "generating"
            prototype_id = body["prototype_id"]

            # ── Step 2: Poll GET until ready (real build runs in the bg task).
            row = await _poll_until(
                client, prototype_id, terminal={"ready", "failed"}, timeout_s=60
            )

    assert row is not None, "generation did not reach a terminal status within 60s"
    assert row["status"] == "ready", f"generation failed: {row.get('error')!r}"
    assert row["bundle_url"], "bundle_url is empty on a ready prototype"
    assert row["current_checkpoint_id"], "current_checkpoint_id not populated"

    # ── Step 3: the served bundle exists and carries data-anchor-id.
    index_path = Path(unquote(urlparse(row["bundle_url"]).path))
    assert index_path.exists(), f"bundle entry not on disk at {index_path}"
    bundle_dir = index_path.parent
    files = [p for p in sorted(bundle_dir.rglob("*")) if p.is_file()]
    blob = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in files)
    assert blob, "served bundle is empty"
    # data-anchor-id lands in the COMPILED JS chunk (not index.html), baked in by
    # the P0-02 plugin at build time — closing the AD4 loop end-to-end.
    assert ANCHOR_ID_RE.search(blob), (
        f"no data-anchor-id=<8-hex> in the served bundle under {bundle_dir}. "
        f"Likely the P0-02 plugin is not registered in prototype-runtime/"
        f"vite.config.ts — verify P0-04's snapshot test is green. Files: "
        f"{[p.name for p in files]}"
    )

    # ── AC #12: the run emitted its cost-summary line.
    cost_lines = [
        r.getMessage() for r in caplog.records
        if r.getMessage().startswith("design_agent.run.complete")
    ]
    assert any(
        f"prototype_id={prototype_id}" in ln and "status=complete" in ln
        for ln in cost_lines
    ), f"no design_agent.run.complete cost line for prototype {prototype_id}: {cost_lines}"


# ─── F4 scenario matrix: B + 0 happy-path smokes (P5-10) ────────────────────
#
# Clone of test_scenario_a_smoke's shape — same FakeSupabaseClient + mocked-LLM +
# tmp-storage + real-`vite build` harness, toolchain-guarded identically. These
# drive the REAL generate route, so `infer_scenario_from_inputs` runs for real
# (website_url + no Figma → 'B'; nothing → '0'); the scenario label is not stubbed.
# Scenario C is intentionally absent (dropped 2026-06-02, markdown-export-only).


@_skip_no_toolchain
async def test_scenario_b_smoke(env, monkeypatch, tmp_path, caplog):
    """Scenario B (P5-10 AC2): a website URL + NO Figma. The P5-01 extractor is
    mocked (no live browser in CI), but the run still flows through the real
    generate route → `infer_scenario_from_inputs` → real `vite build`, proving
    scenario=B derivation, `prototypes.website_url` persistence (P5-02), and the
    anchor-id bundle end-to-end (AD4)."""
    monkeypatch.setattr(
        "app.design_agent.runner.get_design_agent_client",
        lambda: _mock_design_agent_client(),
    )
    # No Figma in Scenario B; the figma-token resolver is stubbed only for parity
    # with test_scenario_a_smoke (the mocked agent never calls fetch_figma anyway).
    monkeypatch.setattr("app.design_agent.runner._resolve_figma_access_token", lambda k, ws: None)
    # Mock the P5-01 extractor at its SOURCE module — `_website_context_block`
    # does a lazy `from app.design_agent.scenarios.website import
    # extract_website_design_system`, so the patch must land on that module for the
    # `from ... import` to bind the fake. Returns a fixed WebsiteDesignSystem dict
    # so no browser runs; the run reaches the extracted-design-system branch.
    async def _fake_extract(url: str):
        return {
            "primary_color": "#2563eb",
            "background_color": "#ffffff",
            "heading_font_family": "Inter",
            "heading_size_scale": "48px",
            "body_font_family": "Inter",
            "border_radius_convention": "8px",
            "spacing_scale_samples": ["16px", "24px"],
            "logo_url": None,
        }

    monkeypatch.setattr(
        "app.design_agent.scenarios.website.extract_website_design_system",
        _fake_extract,
    )
    _point_storage_to_tmp(monkeypatch, tmp_path)
    prd_id = _seed_prd(env.db)

    with caplog.at_level(logging.INFO):
        async with _login(env) as client:
            resp = await client.post(
                "/v1/design-agent/generate",
                json={
                    "prd_id": prd_id,
                    "target_platform": "both",
                    "instructions": "",
                    "website_url": "https://example.com",
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["status"] == "generating"
            prototype_id = body["prototype_id"]

            row = await _poll_until(
                client, prototype_id, terminal={"ready", "failed"}, timeout_s=60
            )

    assert row is not None, "generation did not reach a terminal status within 60s"
    assert row["status"] == "ready", f"generation failed: {row.get('error')!r}"
    assert row["bundle_url"], "bundle_url is empty on a ready prototype"

    # AC2: prototypes.website_url is persisted from the request; no Figma key.
    persisted = env.proto.get_prototype(prototype_id=prototype_id, workspace_id=_TEST_COMPANY_ID)
    assert persisted is not None
    assert persisted["website_url"] == "https://example.com", (
        f"website_url not persisted: {persisted.get('website_url')!r}"
    )
    assert not persisted["figma_file_key"], "Scenario B must carry no Figma key"

    # AC2: served bundle exists and carries data-anchor-id (AD4 closure).
    index_path = Path(unquote(urlparse(row["bundle_url"]).path))
    assert index_path.exists(), f"bundle entry not on disk at {index_path}"
    bundle_dir = index_path.parent
    files = [p for p in sorted(bundle_dir.rglob("*")) if p.is_file()]
    blob = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in files)
    assert blob, "served bundle is empty"
    assert ANCHOR_ID_RE.search(blob), (
        f"no data-anchor-id=<8-hex> in the served Scenario B bundle under {bundle_dir}. "
        f"Files: {[p.name for p in files]}"
    )

    # AC2: the cost-summary line carries scenario=B (derived at the route by
    # infer_scenario_from_inputs: website_url present + no Figma → 'B').
    cost_lines = [
        r.getMessage() for r in caplog.records
        if r.getMessage().startswith("design_agent.run.complete")
    ]
    assert any(
        f"prototype_id={prototype_id}" in ln and "scenario=B" in ln.split()
        for ln in cost_lines
    ), f"no scenario=B cost line for prototype {prototype_id}: {cost_lines}"


@_skip_no_toolchain
async def test_scenario_0_smoke(env, monkeypatch, tmp_path, caplog):
    """Scenario 0 (P5-10 AC3): no Figma, no website URL, no manual design — the
    generic path. `infer_scenario_from_inputs` returns {'0'}; the run still
    reaches ready with an anchor-id bundle and all three source columns NULL."""
    monkeypatch.setattr(
        "app.design_agent.runner.get_design_agent_client",
        lambda: _mock_design_agent_client(),
    )
    monkeypatch.setattr("app.design_agent.runner._resolve_figma_access_token", lambda k, ws: None)
    _point_storage_to_tmp(monkeypatch, tmp_path)
    prd_id = _seed_prd(env.db)

    with caplog.at_level(logging.INFO):
        async with _login(env) as client:
            resp = await client.post(
                "/v1/design-agent/generate",
                json={
                    "prd_id": prd_id,
                    "target_platform": "both",
                    "instructions": "",
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["status"] == "generating"
            prototype_id = body["prototype_id"]

            row = await _poll_until(
                client, prototype_id, terminal={"ready", "failed"}, timeout_s=60
            )

    assert row is not None, "generation did not reach a terminal status within 60s"
    assert row["status"] == "ready", f"generation failed: {row.get('error')!r}"
    assert row["bundle_url"], "bundle_url is empty on a ready prototype"

    # AC3: all three source columns are NULL (no Figma / website / GitHub).
    persisted = env.proto.get_prototype(prototype_id=prototype_id, workspace_id=_TEST_COMPANY_ID)
    assert persisted is not None
    assert not persisted["figma_file_key"], (
        f"figma_file_key not NULL: {persisted.get('figma_file_key')!r}"
    )
    assert not persisted["website_url"], (
        f"website_url not NULL: {persisted.get('website_url')!r}"
    )
    assert not persisted["github_installation_id"], (
        f"github_installation_id not NULL: {persisted.get('github_installation_id')!r}"
    )

    # AC3: served bundle exists and carries data-anchor-id.
    index_path = Path(unquote(urlparse(row["bundle_url"]).path))
    assert index_path.exists(), f"bundle entry not on disk at {index_path}"
    bundle_dir = index_path.parent
    files = [p for p in sorted(bundle_dir.rglob("*")) if p.is_file()]
    blob = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in files)
    assert blob, "served bundle is empty"
    assert ANCHOR_ID_RE.search(blob), (
        f"no data-anchor-id=<8-hex> in the served Scenario 0 bundle under {bundle_dir}. "
        f"Files: {[p.name for p in files]}"
    )

    # AC3: the cost-summary line carries scenario=0 (no source inputs → generic).
    cost_lines = [
        r.getMessage() for r in caplog.records
        if r.getMessage().startswith("design_agent.run.complete")
    ]
    assert any(
        f"prototype_id={prototype_id}" in ln and "scenario=0" in ln.split()
        for ln in cost_lines
    ), f"no scenario=0 cost line for prototype {prototype_id}: {cost_lines}"
