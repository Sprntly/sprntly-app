"""P3-11 pre-flight cost-estimate tests (AD14 / AD15).

Two surfaces:
  1. The pure `estimate_iterate_cost` calc (runner.py) — shape, pricing-reuse,
     soft-cap behaviour, determinism, the no-Anthropic-call guarantee, and that it
     counts only OPEN comments.
  2. The `POST /v1/design-agent/{id}/iterate/estimate` route — returns the estimate,
     404 cross-workspace, 401 without session, 422 on empty prompt, 404 flag-off.

The unit calc tests monkeypatch the three data deps `estimate_iterate_cost` resolves
(`get_prototype`, `list_comments`, `read_source_files_for_checkpoint`) in the runner
namespace, so the calc runs with no DB or storage. The route tests use the shared
conftest harness (in-memory FakeSupabaseClient via `isolated_settings`) + the same
reload-in-dependency-order pattern as test_design_agent_prd_patch_routes.py, so the
route binds to the fake-wired DB helpers.
"""
from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app import llm_telemetry
from app.design_agent import runner

from tests.conftest import _TEST_COMPANY_ID


# ═══════════════════════════════════════════════════════════════════════════
# Unit — estimate_iterate_cost (pure calc, monkeypatched data deps)
# ═══════════════════════════════════════════════════════════════════════════


def _patch_calc(monkeypatch, *, source=None, comments=None, checkpoint_id=10):
    source = source or {}
    comments = comments or []

    async def _fake_read(prototype_id, cid):  # positional, async (S2 shape)
        return source

    monkeypatch.setattr(
        runner, "get_prototype", lambda **_k: {"current_checkpoint_id": checkpoint_id}
    )
    monkeypatch.setattr(runner, "list_comments", lambda **_k: comments)
    monkeypatch.setattr(runner, "read_source_files_for_checkpoint", _fake_read)


def test_estimate_returns_expected_shape(monkeypatch):
    _patch_calc(monkeypatch)
    out = asyncio.run(
        runner.estimate_iterate_cost(prototype_id=1, workspace_id=_TEST_COMPANY_ID, prompt="make it blue")
    )
    assert set(out.keys()) == {
        "cached_input_tokens",
        "new_input_tokens",
        "expected_output_tokens",
        "est_cost_usd",
        "soft_cap_usd",
        "exceeds_soft_cap",
        "model",
    }
    assert out["model"] == "claude-sonnet-4-6"
    assert out["soft_cap_usd"] == 0.50
    assert out["expected_output_tokens"] == 2000


def test_estimate_uses_model_pricing_not_duplicate(monkeypatch):
    # The runner must reference the shared llm_telemetry.MODEL_PRICING — the SAME
    # object est_cost_usd prices against — not a second local pricing table (AC1).
    assert runner.MODEL_PRICING is llm_telemetry.MODEL_PRICING

    _patch_calc(monkeypatch, source={"App.tsx": "x" * 4000})
    out = asyncio.run(
        runner.estimate_iterate_cost(prototype_id=1, workspace_id=_TEST_COMPANY_ID, prompt="hello")
    )
    # Recompute independently from the shared pricing dict + the documented formula.
    p = llm_telemetry.MODEL_PRICING["claude-sonnet-4-6"]
    expected = round(
        out["cached_input_tokens"] * p["cache_read"]
        + out["new_input_tokens"] * p["input"]
        + out["expected_output_tokens"] * p["output"],
        4,
    )
    assert out["est_cost_usd"] == expected


def test_estimate_under_soft_cap_for_small_input(monkeypatch):
    _patch_calc(monkeypatch, source={"App.tsx": "small bundle"})
    out = asyncio.run(
        runner.estimate_iterate_cost(prototype_id=1, workspace_id=_TEST_COMPANY_ID, prompt="tweak the header")
    )
    assert out["est_cost_usd"] < 0.50
    assert out["exceeds_soft_cap"] is False


def test_estimate_exceeds_soft_cap_for_large_input(monkeypatch):
    # ~8M chars of bundle source → ~2M cached tokens → well over the $0.50 guide.
    _patch_calc(monkeypatch, source={"App.tsx": "x" * 8_000_000})
    out = asyncio.run(
        runner.estimate_iterate_cost(prototype_id=1, workspace_id=_TEST_COMPANY_ID, prompt="big change")
    )
    assert out["est_cost_usd"] > 0.50
    assert out["exceeds_soft_cap"] is True


def test_estimate_is_deterministic(monkeypatch):
    _patch_calc(
        monkeypatch,
        source={"App.tsx": "y" * 1234},
        comments=[{"anchor_id": "a1", "body": "fix", "status": "open"}],
    )
    args = dict(prototype_id=1, workspace_id=_TEST_COMPANY_ID, prompt="same prompt")
    first = asyncio.run(runner.estimate_iterate_cost(**args))
    second = asyncio.run(runner.estimate_iterate_cost(**args))
    assert first == second


def test_estimate_makes_no_anthropic_call(monkeypatch):
    _patch_calc(monkeypatch, source={"App.tsx": "content"})
    fake_client = MagicMock()
    monkeypatch.setattr(runner, "get_design_agent_client", lambda: fake_client)
    asyncio.run(
        runner.estimate_iterate_cost(prototype_id=1, workspace_id=_TEST_COMPANY_ID, prompt="no api please")
    )
    fake_client.messages.create.assert_not_called()


def test_estimate_only_counts_open_comments(monkeypatch):
    # Resolved/orphaned comments are NOT in the cacheable prefix (render_iterate_user
    # renders open only), so they must not inflate the estimate.
    mixed = [
        {"anchor_id": "a1", "body": "keep", "status": "open"},
        {"anchor_id": "a2", "body": "x" * 100_000, "status": "resolved"},
    ]
    _patch_calc(monkeypatch, comments=mixed)
    out = asyncio.run(
        runner.estimate_iterate_cost(prototype_id=1, workspace_id=_TEST_COMPANY_ID, prompt="hi")
    )
    _patch_calc(monkeypatch, comments=[{"anchor_id": "a1", "body": "keep", "status": "open"}])
    out2 = asyncio.run(
        runner.estimate_iterate_cost(prototype_id=1, workspace_id=_TEST_COMPANY_ID, prompt="hi")
    )
    assert out["cached_input_tokens"] == out2["cached_input_tokens"]


def test_estimate_missing_checkpoint_yields_empty_bundle(monkeypatch):
    # A prototype with no current_checkpoint_id (never staged) estimates against an
    # empty bundle — no exception, defensive read skipped.
    monkeypatch.setattr(runner, "get_prototype", lambda **_k: {"current_checkpoint_id": None})
    monkeypatch.setattr(runner, "list_comments", lambda **_k: [])

    async def _boom(*_a, **_k):  # must NOT be called when checkpoint is None
        raise AssertionError("read_source_files_for_checkpoint called with no checkpoint")

    monkeypatch.setattr(runner, "read_source_files_for_checkpoint", _boom)
    out = asyncio.run(
        runner.estimate_iterate_cost(prototype_id=1, workspace_id=_TEST_COMPANY_ID, prompt="hi")
    )
    # cacheable = system prompt only; volatile = "hi".
    assert out["new_input_tokens"] == len("hi") // 4
    assert out["model"] == "claude-sonnet-4-6"


# ═══════════════════════════════════════════════════════════════════════════
# Route — POST /v1/design-agent/{id}/iterate/estimate (conftest harness)
# ═══════════════════════════════════════════════════════════════════════════

# SQLite-compatible DDL for the two tables the estimate route + calc touch via the
# fake-wired helpers: `prototypes` (get_prototype) and `prototype_comments`
# (list_comments). Mirrors the sibling design-agent route tests' DDL shape.
_DDL = """
CREATE TABLE prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    template_version       INTEGER,
    preview_image_url      TEXT,
    current_checkpoint_id  INTEGER,
    is_complete            INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE prototype_comments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id  INTEGER NOT NULL,
    workspace_id  TEXT NOT NULL,
    anchor_id     TEXT NOT NULL,
    body          TEXT NOT NULL,
    author        TEXT NOT NULL DEFAULT 'demo',
    status        TEXT NOT NULL DEFAULT 'open',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    user_id        TEXT
);
"""


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + the prototypes/comments tables + feature flag ON, with the
    design-agent route stack reloaded in dependency order so the route binds to the
    fake-wired helpers.

    NOTE: we deliberately do NOT `importlib.reload(app.design_agent.runner)` here.
    Reloading runner mints a fresh `RunResult` class, which pollutes
    test_design_agent_runner.py's `isinstance(result, RunResult)` checks under a full
    suite run. The route reaches `estimate_iterate_cost` via the reloaded routes module
    (which re-imports the CURRENT runner's functions); those call the db helpers, which
    hit the fake client through the global supabase_client monkeypatch — so reloading
    runner is unnecessary as well as harmful. Mirrors the sibling route tests
    (test_design_agent_prd_patch_routes.py), which reload routes + main only."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_DDL)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.db.prototype_comments as comments_mod
    importlib.reload(comments_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    return SimpleNamespace(
        proto=proto_mod, comments=comments_mod, routes=routes_mod, main=main_mod,
    )


def _seed_prototype(env, *, pid=1, workspace_id=_TEST_COMPANY_ID, checkpoint_id=None):
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().execute(
        "INSERT INTO prototypes (id, prd_id, workspace_id, status, template_version, "
        "current_checkpoint_id) VALUES (?, 1, ?, 'ready', 3, ?)",
        (pid, workspace_id, checkpoint_id),
    )
    _fake_supabase.get_fake_db().commit()


@pytest.fixture
def client(company_client) -> TestClient:
    """Bearer-authed TestClient (require_company) — see conftest.company_client."""
    return company_client


@pytest.fixture
def unauth(env) -> TestClient:
    return TestClient(env.main.app)


def test_post_estimate_returns_estimate(client, env):
    _seed_prototype(env)
    resp = client.post("/v1/design-agent/1/iterate/estimate", json={"prompt": "make it blue"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model"] == "claude-sonnet-4-6"
    assert "est_cost_usd" in body
    assert body["exceeds_soft_cap"] is False


def test_post_estimate_wrong_workspace_404(client, env):
    _seed_prototype(env, workspace_id="other-workspace")
    resp = client.post("/v1/design-agent/1/iterate/estimate", json={"prompt": "x"})
    assert resp.status_code == 404


def test_post_estimate_requires_session(unauth, env):
    _seed_prototype(env)
    resp = unauth.post("/v1/design-agent/1/iterate/estimate", json={"prompt": "x"})
    assert resp.status_code == 401


def test_post_estimate_empty_prompt_422(client, env):
    _seed_prototype(env)
    resp = client.post("/v1/design-agent/1/iterate/estimate", json={"prompt": ""})
    assert resp.status_code == 422


def test_post_estimate_404_when_flag_off(client, env, monkeypatch):
    _seed_prototype(env)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "0")
    resp = client.post("/v1/design-agent/1/iterate/estimate", json={"prompt": "x"})
    assert resp.status_code == 404
