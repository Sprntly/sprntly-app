"""Tests for the per-prototype iterate rate limiter (P5-04):

    backend/app/design_agent/rate_limit.py   (SlidingWindowLimiter + ITERATE_LIMITER)
    backend/app/routes/design_agent.py        (POST /iterate 429 rate_limit guard)

Two layers, matching the ticket's Unit Tests section:

- LIMITER — the generic SlidingWindowLimiter primitive: admit/block, prune,
  retry_after, key-independence. Pure, no app/network; the monotonic clock is
  patched for the prune/retry_after cases.
- ROUTE — the POST /iterate 429 shape (`{"error": "rate_limit",
  "retry_after_seconds": int}`), the gate ordering (feature/session/workspace
  gates fire BEFORE the limiter), estimate-unaffected, observability, and
  non-breakage of routes/design_agent.py.

Reload note: the `env` fixture reloads `app.design_agent.rate_limit` BEFORE
`app.routes.design_agent`, so each test gets a FRESH `ITERATE_LIMITER` singleton
(the limiter is module-level process state — without the reload, counts would
leak across tests). Route tests reference `env.routes.ITERATE_LIMITER`, never a
stale top-level import.
"""
from __future__ import annotations

import importlib
import logging
import pathlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.design_agent import rate_limit
from app.design_agent.rate_limit import SlidingWindowLimiter
from tests.conftest import _TEST_COMPANY_ID


# SQLite-compatible mirror of the prototypes end-state (enough columns for
# start_prototype + complete_prototype to seed a ready row). The queue table is
# included so an un-stubbed enqueue path never NameErrors on the table.
_DDL = """
DROP TABLE IF EXISTS prototypes;
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
CREATE TABLE prototype_pending_iterations (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id       INTEGER NOT NULL,
    workspace_id       TEXT NOT NULL,
    prompt             TEXT NOT NULL,
    applied_comment_id INTEGER,
    mode               TEXT NOT NULL DEFAULT 'execute',
    status             TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'running', 'done', 'failed')),
    error              TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    started_at         TEXT,
    finished_at        TEXT
);
"""


# ═══════════════════════════════════════════════════════════════════════════
# LIMITER — pure primitive (AC1–AC4)
# ═══════════════════════════════════════════════════════════════════════════


def test_limiter_admits_max_blocks_next():
    # AC1: 6 admitted, 7th blocked. check() is True for the first 6 (before each
    # register); after 6 registers, check() is False.
    lim = SlidingWindowLimiter(max_events=6, window_seconds=3600)
    for _ in range(6):
        assert lim.check("k") is True
        lim.register("k")
    assert lim.check("k") is False


def test_limiter_prunes_expired(monkeypatch):
    # AC2: an event older than window_seconds does not count toward the limit.
    clock = {"t": 1000.0}
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock["t"])
    lim = SlidingWindowLimiter(max_events=6, window_seconds=3600)

    # Fill the window at t=1000 → at the limit.
    for _ in range(6):
        lim.register("k")
    assert lim.check("k") is False

    # Advance past the window → all 6 prune; back under the limit.
    clock["t"] = 1000.0 + 3601
    assert lim.check("k") is True
    # And the pruned events truly do not count: 6 fresh registers re-saturate.
    for _ in range(6):
        lim.register("k")
    assert lim.check("k") is False


def test_retry_after_zero_under_limit_positive_over(monkeypatch):
    # AC3: 0 under the limit; a positive int (<= window) at/over the limit,
    # computed from the OLDEST in-window event.
    clock = {"t": 5000.0}
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock["t"])
    lim = SlidingWindowLimiter(max_events=6, window_seconds=3600)

    assert lim.retry_after("k") == 0          # nothing registered

    for _ in range(6):
        lim.register("k")                     # oldest at t=5000
    ra = lim.retry_after("k")
    assert isinstance(ra, int)
    assert 1 <= ra <= 3600
    assert ra == 3600                         # now == oldest → full window remains

    clock["t"] = 5000.0 + 100                 # 100s elapse since the oldest event
    assert lim.retry_after("k") == 3500


def test_limiter_keys_independent():
    # AC4: saturating key A does not affect key B.
    lim = SlidingWindowLimiter(max_events=6, window_seconds=3600)
    for _ in range(6):
        lim.register("A")
    assert lim.check("A") is False
    assert lim.check("B") is True


def test_iterate_limiter_singleton_config():
    # The module singleton consumed by the route is 6 events / 3600s.
    assert isinstance(rate_limit.ITERATE_LIMITER, SlidingWindowLimiter)
    assert rate_limit.ITERATE_LIMITER._max == 6
    assert rate_limit.ITERATE_LIMITER._window == 3600


# ═══════════════════════════════════════════════════════════════════════════
# Route fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + prototype tables + feature flag ON, with the
    design-agent module stack reloaded in dependency order.

    `rate_limit` is deliberately NOT reloaded: ITERATE_LIMITER is a stable
    module-level singleton, and reloading it would change the class identity (and
    break the isinstance unit test under full-suite ordering). Per-test isolation
    of the singleton's window is handled by the autouse `_reset_iterate_limiter`
    fixture in conftest.py — routes' reload here just re-binds to that same
    (freshly-cleared) singleton."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_DDL)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.db.prototype_pending_iterations as queue_mod
    importlib.reload(queue_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    return SimpleNamespace(
        proto=proto_mod, queue=queue_mod,
        routes=routes_mod, main=main_mod,
    )


@pytest.fixture
def client(company_client) -> TestClient:
    """Bearer-authed TestClient (require_company) — see conftest.company_client."""
    return company_client


def _seed_ready(env, *, workspace_id: str = _TEST_COMPANY_ID) -> int:
    """Insert a ready, unlocked prototype (status='ready', is_complete=0)."""
    pid = env.proto.start_prototype(prd_id=1, workspace_id=workspace_id, template_version=1)
    env.proto.complete_prototype(
        prototype_id=pid, workspace_id=workspace_id,
        bundle_url="https://bundle/original", current_checkpoint_id=None,
    )
    return pid


def _saturate(env, pid: int) -> None:
    """Register max_events for the prototype key directly on the route's singleton
    so the NEXT route call would 429 — used to prove the gate ordering ACs."""
    for _ in range(6):
        env.routes.ITERATE_LIMITER.register(str(pid))


# ═══════════════════════════════════════════════════════════════════════════
# ROUTE — 429 shape (AC5)
# ═══════════════════════════════════════════════════════════════════════════


def test_iterate_seventh_call_429_shape(env, client):
    # AC5: with 6 in-window events for the prototype, the next POST → 429 with the
    # rate_limit discriminator (NOT merely status==429 — must not alias queue_full).
    pid = _seed_ready(env)
    _saturate(env, pid)
    resp = client.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": "again"})
    assert resp.status_code == 429
    detail = resp.json()["detail"]
    assert detail["error"] == "rate_limit"
    assert isinstance(detail["retry_after_seconds"], int)
    assert detail["retry_after_seconds"] >= 1


def test_iterate_first_six_admitted(env, client, monkeypatch):
    # AC5: the first 6 iterate POSTs are admitted (not 429); the 7th is rate_limit.
    # enqueue + drain are stubbed so the 5-slot queue_full 429 never interferes —
    # this isolates the rate-limit decision from the queue cap.
    async def _noop_drain(**kwargs):
        return None

    def _fake_enqueue(**kwargs):
        return {"id": 1, "status": "pending", "queue_position": 1}

    monkeypatch.setattr(env.routes, "drain_iteration_queue", _noop_drain)
    monkeypatch.setattr(env.routes, "enqueue_iteration", _fake_enqueue)

    pid = _seed_ready(env)
    for i in range(6):
        resp = client.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": f"p{i}"})
        assert resp.status_code == 200, resp.text

    resp = client.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": "seventh"})
    assert resp.status_code == 429
    assert resp.json()["detail"]["error"] == "rate_limit"


# ═══════════════════════════════════════════════════════════════════════════
# ROUTE — gate ordering (AC6): gates fire BEFORE the limiter (no existence leak)
# ═══════════════════════════════════════════════════════════════════════════


def test_iterate_feature_off_404_before_429(env, client, monkeypatch):
    # AC6: even with the limiter saturated, a feature-off iterate returns 404
    # (the feature gate runs first), NOT 429.
    pid = _seed_ready(env)
    _saturate(env, pid)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "0")
    resp = client.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": "x"})
    assert resp.status_code == 404


def test_iterate_no_session_401_before_429(env):
    # AC6: even with the limiter saturated, a no-auth iterate returns 401
    # (require_company → require_session runs first), NOT 429.
    pid = _seed_ready(env)
    _saturate(env, pid)
    no_auth = TestClient(env.main.app)  # no login cookie
    resp = no_auth.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": "x"})
    assert resp.status_code == 401


def test_iterate_cross_workspace_404_before_429(env, client):
    # AC6: a prototype owned by another workspace returns 404 (workspace filter
    # runs first), NOT 429 — the limiter never leaks cross-tenant existence.
    pid = _seed_ready(env, workspace_id="demo")
    _saturate(env, pid)
    resp = client.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": "x"})
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# ROUTE — estimate unaffected (AC7)
# ═══════════════════════════════════════════════════════════════════════════


def test_estimate_not_rate_limited(env, client, monkeypatch):
    # AC7: /iterate/estimate is NOT rate-limited — 10 calls all succeed and none
    # consume the iterate quota (the limiter key stays empty).
    async def _fake_estimate(**kwargs):
        return {
            "cached_input_tokens": 0, "new_input_tokens": 0,
            "est_cost_usd": 0.0, "exceeds_soft_cap": False,
        }

    monkeypatch.setattr(env.routes, "estimate_iterate_cost", _fake_estimate)
    pid = _seed_ready(env)
    for _ in range(10):
        resp = client.post(f"/v1/design-agent/{pid}/iterate/estimate", json={"prompt": "preview"})
        assert resp.status_code == 200, resp.text

    # The iterate quota for this prototype was never touched by estimate.
    assert env.routes.ITERATE_LIMITER._events.get(str(pid), []) == []
    assert env.routes.ITERATE_LIMITER.check(str(pid)) is True


# ═══════════════════════════════════════════════════════════════════════════
# ROUTE — observability (AC8)
# ═══════════════════════════════════════════════════════════════════════════


def test_iterate_429_logs_identifiers_only(env, client, caplog):
    # AC8: a 429 logs `iterate_rate_limited prototype_id=<id> retry_after_seconds=<N>`
    # — identifiers + the number only; the prompt body never leaks.
    pid = _seed_ready(env)
    _saturate(env, pid)
    sentinel = "SECRET_PROMPT_BODY_do_not_log"
    with caplog.at_level(logging.INFO, logger="app.routes.design_agent"):
        resp = client.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": sentinel})
    assert resp.status_code == 429

    recs = [r for r in caplog.records if "iterate_rate_limited" in r.getMessage()]
    assert len(recs) == 1
    msg = recs[0].getMessage()
    assert f"prototype_id={pid}" in msg
    assert "retry_after_seconds=" in msg
    assert sentinel not in msg


# ═══════════════════════════════════════════════════════════════════════════
# Non-breakage (AC9)
# ═══════════════════════════════════════════════════════════════════════════


def test_routes_design_agent_compiles_callsites_unchanged():
    # AC9: routes/design_agent.py py_compiles with the additions, wires the shared
    # primitive, and main.py still includes the router unchanged.
    import py_compile

    routes_path = (
        pathlib.Path(__file__).resolve().parents[1] / "app" / "routes" / "design_agent.py"
    )
    py_compile.compile(str(routes_path), doraise=True)

    src = routes_path.read_text()
    assert "from app.design_agent.rate_limit import ITERATE_LIMITER" in src
    assert "ITERATE_LIMITER.check(" in src
    assert "ITERATE_LIMITER.register(" in src

    main_src = (
        pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"
    ).read_text()
    assert "app.include_router(design_agent.router)" in main_src
