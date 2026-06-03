"""Tests for the public-surface rate limits (P5-07):

    GET  /v1/design-agent/by-token/{token}            — 60 req/min PER TOKEN
    POST /v1/design-agent/by-token/{token}/comments   — 10 comments/hour PER IP

Both CONSUME the P5-04 `SlidingWindowLimiter` primitive (no new limiter class is
introduced in P5-07) via two module-level singletons in
`app/design_agent/rate_limit.py`:

    PUBLIC_TOKEN_LIMITER   = SlidingWindowLimiter(60, 60)
    PUBLIC_COMMENT_LIMITER = SlidingWindowLimiter(10, 3600)

The security posture under test:

  - the 61st token-view / 11th public-comment in-window request returns HTTP 429
    with the fail-closed `{"error": "rate_limit", "retry_after_seconds": <int>}`
    shape (matching P5-04's iterate limiter);
  - keying is deliberately split — the view is per-token, the comment is per-IP;
  - invisibility is preserved: the feature-off 404 (token view) and the
    private/not-ready 404 (comment) fire BEFORE the limiter, so a 429 never leaks
    that a hidden prototype exists;
  - observability hashes the token (never raw) and records only an ip_present bool
    (never the raw IP).

Harness mirrors test_design_agent_comment_routes.py: in-memory FakeSupabaseClient,
the design-agent module stack reloaded in dependency order, feature flag ON. The
three rate-limit singletons are reset per test by the autouse `_reset_iterate_limiter`
fixture in conftest.py (the limiter module is NOT reloaded, so its class identity —
and the isinstance checks below — stays stable). `request.client.host` is the
constant "testclient" under TestClient, so per-IP comment tests vary the OTHER key
directly on the singleton, the same approach test_design_agent_rate_limit.py uses
for the iterate limiter.
"""
from __future__ import annotations

import importlib
import logging
import pathlib
import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.design_agent.rate_limit import SlidingWindowLimiter

# SQLite-compatible end-state of `prototypes` (P1-06 + P2-06 sharing columns) +
# `prototype_checkpoints` + `prototype_comments` (P3-01) — the comment write path
# needs the comments table. Copied verbatim from test_design_agent_comment_routes.py
# so the fake exercises the same SQL semantics.
_DDL = """
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
CREATE TABLE prototype_comments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id  INTEGER NOT NULL,
    workspace_id  TEXT NOT NULL,
    anchor_id     TEXT NOT NULL,
    body          TEXT NOT NULL,
    author        TEXT NOT NULL DEFAULT 'demo',
    status        TEXT NOT NULL DEFAULT 'open'
                  CHECK (status IN ('open', 'resolved', 'orphaned')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at   TEXT
);
"""

_ROUTES_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "app" / "routes" / "design_agent.py"
)
_RATE_LIMIT_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "app" / "design_agent" / "rate_limit.py"
)


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + prototype tables (incl. comments) + feature flag ON,
    with the design-agent module stack reloaded in dependency order.

    `app.design_agent.rate_limit` is deliberately NOT reloaded — its singletons are
    stable module-level state, cleared per test by conftest's autouse fixture; the
    routes reload here just re-binds to those (freshly-cleared) singletons."""
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
        proto=proto_mod, comments=comments_mod, routes=routes_mod, main=main_mod
    )


@pytest.fixture
def unauth(env) -> TestClient:
    """TestClient with NO session cookie — the public routes need no auth."""
    return TestClient(env.main.app)


# ─── seeding ──────────────────────────────────────────────────────────────


def _seed(
    *,
    share_mode: str = "public",
    status: str = "ready",
    workspace_id: str = "app",
) -> str:
    """Insert one prototype row directly into the fake DB; return its share_token.

    Direct SQL (same approach as the sibling public-route tests) keeps the seed
    independent of set_share_config's workspace guard — the public read/write paths
    are workspace-blind on purpose."""
    from tests import _fake_supabase

    token = str(uuid.uuid4())
    _fake_supabase.get_fake_db().execute(
        "INSERT INTO prototypes "
        "(prd_id, workspace_id, template_version, status, share_mode, share_token, "
        " bundle_url, is_complete) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [1, workspace_id, 1, status, share_mode, token,
         "https://cdn.example/p/abc/index.html", 1],
    )
    return token


def _comment_body() -> dict:
    return {"anchor_id": "deadbeef", "body": "love this"}


# ═══════════════════════════════════════════════════════════════════════════
# Token-view limit (60/min/token) — AC1, AC2
# ═══════════════════════════════════════════════════════════════════════════


def test_token_view_first_60_admitted(unauth):
    # AC1: the first 60 in-window GETs for a token are admitted (public+ready → 200,
    # never 429).
    token = _seed(share_mode="public")
    url = f"/v1/design-agent/by-token/{token}"
    for i in range(60):
        resp = unauth.get(url)
        assert resp.status_code == 200, f"call {i} unexpectedly {resp.status_code}: {resp.text}"


def test_token_view_61st_call_429(unauth):
    # AC1: the 61st GET for the same token within 60s returns 429 with the
    # fail-closed shape; retry_after_seconds is a positive int.
    token = _seed(share_mode="public")
    url = f"/v1/design-agent/by-token/{token}"
    for _ in range(60):
        assert unauth.get(url).status_code == 200
    sixty_first = unauth.get(url)
    assert sixty_first.status_code == 429
    detail = sixty_first.json()["detail"]
    assert detail["error"] == "rate_limit"
    assert isinstance(detail["retry_after_seconds"], int)
    assert detail["retry_after_seconds"] >= 1


def test_token_view_keys_independent(unauth):
    # AC2: two distinct tokens are independent — saturating token A (60 calls + a
    # 429) does not 429 token B.
    token_a = _seed(share_mode="public")
    token_b = _seed(share_mode="public")
    url_a = f"/v1/design-agent/by-token/{token_a}"
    for _ in range(60):
        assert unauth.get(url_a).status_code == 200
    assert unauth.get(url_a).status_code == 429          # A is now saturated
    assert unauth.get(f"/v1/design-agent/by-token/{token_b}").status_code == 200  # B unaffected


# ═══════════════════════════════════════════════════════════════════════════
# Public-comment limit (10/hour/IP) — AC3, AC4
# ═══════════════════════════════════════════════════════════════════════════


def test_public_comment_first_10_admitted(unauth):
    # AC3: the first 10 in-window comment POSTs from one IP are admitted (200).
    token = _seed(share_mode="public")
    url = f"/v1/design-agent/by-token/{token}/comments"
    for i in range(10):
        resp = unauth.post(url, json=_comment_body())
        assert resp.status_code == 200, f"comment {i} unexpectedly {resp.status_code}: {resp.text}"


def test_public_comment_11th_429(unauth):
    # AC3: the 11th comment POST from the same IP within the hour returns 429 with
    # the {error, retry_after_seconds} shape.
    token = _seed(share_mode="public")
    url = f"/v1/design-agent/by-token/{token}/comments"
    for _ in range(10):
        assert unauth.post(url, json=_comment_body()).status_code == 200
    eleventh = unauth.post(url, json=_comment_body())
    assert eleventh.status_code == 429
    detail = eleventh.json()["detail"]
    assert detail["error"] == "rate_limit"
    assert isinstance(detail["retry_after_seconds"], int)
    assert detail["retry_after_seconds"] >= 1


def test_public_comment_ip_keys_independent(unauth, env):
    # AC4: two distinct client IPs are independent. The TestClient host is the
    # constant "testclient", so we saturate a DIFFERENT IP key directly on the
    # route's singleton (the iterate-limiter test's established approach) and prove a
    # real POST (keyed "testclient") is still admitted, i.e. IP A's saturation does
    # not block IP B.
    other_ip = "198.51.100.5"
    for _ in range(10):
        env.routes.PUBLIC_COMMENT_LIMITER.register(other_ip)
    assert env.routes.PUBLIC_COMMENT_LIMITER.check(other_ip) is False  # A saturated

    token = _seed(share_mode="public")
    resp = unauth.post(f"/v1/design-agent/by-token/{token}/comments", json=_comment_body())
    assert resp.status_code == 200, resp.text                          # B (testclient) admitted
    assert env.routes.PUBLIC_COMMENT_LIMITER.check("testclient") is True


# ═══════════════════════════════════════════════════════════════════════════
# Ordering / invisibility — AC5
# ═══════════════════════════════════════════════════════════════════════════


def test_token_view_feature_off_404_before_429(unauth, env, monkeypatch):
    # AC5: even with the token limiter saturated, a feature-off GET returns 404 (the
    # feature gate runs first), NOT 429 — the limiter never leaks existence.
    token = _seed(share_mode="public")
    for _ in range(60):
        env.routes.PUBLIC_TOKEN_LIMITER.register(token)
    assert env.routes.PUBLIC_TOKEN_LIMITER.check(token) is False
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "0")
    resp = unauth.get(f"/v1/design-agent/by-token/{token}")
    assert resp.status_code == 404


def test_public_comment_private_404_before_429(unauth, env):
    # AC5: even with the comment limiter saturated for this IP, a POST to a private
    # prototype returns 404 (the resolution 404 runs first), NOT 429 — the limiter
    # never discloses that a hidden prototype exists.
    token = _seed(share_mode="private")
    for _ in range(10):
        env.routes.PUBLIC_COMMENT_LIMITER.register("testclient")
    assert env.routes.PUBLIC_COMMENT_LIMITER.check("testclient") is False
    resp = unauth.post(f"/v1/design-agent/by-token/{token}/comments", json=_comment_body())
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# Consumes the P5-04 primitive, no new class — AC6
# ═══════════════════════════════════════════════════════════════════════════


def test_uses_p5_04_limiter_no_new_class(env):
    # AC6: both public limiters are instances of the SAME SlidingWindowLimiter class
    # P5-04 extracted, configured per the ticket; and P5-07 introduces no new limiter
    # class (the rate_limit module defines exactly one *Limiter class).
    from app.design_agent import rate_limit as rl

    assert isinstance(rl.PUBLIC_TOKEN_LIMITER, SlidingWindowLimiter)
    assert rl.PUBLIC_TOKEN_LIMITER._max == 60
    assert rl.PUBLIC_TOKEN_LIMITER._window == 60

    assert isinstance(rl.PUBLIC_COMMENT_LIMITER, SlidingWindowLimiter)
    assert rl.PUBLIC_COMMENT_LIMITER._max == 10
    assert rl.PUBLIC_COMMENT_LIMITER._window == 3600

    # Source scan: only ONE limiter class is DEFINED in the module (P5-04's). P5-07
    # added singletons, not a new class.
    classes = re.findall(r"^class\s+(\w*Limiter)\b", _RATE_LIMIT_PATH.read_text(), re.M)
    assert classes == ["SlidingWindowLimiter"]


# ═══════════════════════════════════════════════════════════════════════════
# Observability — AC7
# ═══════════════════════════════════════════════════════════════════════════


def test_429_logs_hashed_token_and_ip_bool(unauth, env, caplog):
    # AC7: the token-view 429 logs `public_token_rate_limited token_hash=<hash>`
    # (hashed token, never raw) + retry_after_seconds; the comment 429 logs
    # `public_comment_rate_limited ip_present=<bool>` (no raw IP) + retry_after_seconds.
    token = _seed(share_mode="public")

    # Token-view 429.
    for _ in range(60):
        env.routes.PUBLIC_TOKEN_LIMITER.register(token)
    with caplog.at_level(logging.INFO, logger="app.routes.design_agent"):
        resp = unauth.get(f"/v1/design-agent/by-token/{token}")
    assert resp.status_code == 429
    tok_recs = [r for r in caplog.records if "public_token_rate_limited" in r.getMessage()]
    assert len(tok_recs) == 1
    tok_msg = tok_recs[0].getMessage()
    assert "token_hash=" in tok_msg
    assert "retry_after_seconds=" in tok_msg
    assert token not in tok_msg                       # raw token never logged

    caplog.clear()

    # Comment 429.
    for _ in range(10):
        env.routes.PUBLIC_COMMENT_LIMITER.register("testclient")
    with caplog.at_level(logging.INFO, logger="app.routes.design_agent"):
        resp = unauth.post(
            f"/v1/design-agent/by-token/{token}/comments", json=_comment_body()
        )
    assert resp.status_code == 429
    com_recs = [r for r in caplog.records if "public_comment_rate_limited" in r.getMessage()]
    assert len(com_recs) == 1
    com_msg = com_recs[0].getMessage()
    assert "ip_present=True" in com_msg               # boolean, not the raw IP
    assert "retry_after_seconds=" in com_msg
    assert "testclient" not in com_msg                # raw IP never logged


# ═══════════════════════════════════════════════════════════════════════════
# Non-breakage of routes/design_agent.py — AC8
# ═══════════════════════════════════════════════════════════════════════════


def test_public_routes_contract_unchanged(env):
    # AC8: the module py_compiles, the new guards consume the shared primitive, and
    # get_by_token's path + response_model are unchanged (the web client contract is
    # intact — adding `request: Request` is a server-internal injection).
    import py_compile

    py_compile.compile(str(_ROUTES_PATH), doraise=True)

    src = _ROUTES_PATH.read_text()
    assert "from app.design_agent.rate_limit import (" in src
    assert "PUBLIC_TOKEN_LIMITER" in src
    assert "PUBLIC_COMMENT_LIMITER" in src
    assert "PUBLIC_TOKEN_LIMITER.check(" in src
    assert "PUBLIC_TOKEN_LIMITER.register(" in src
    assert "PUBLIC_COMMENT_LIMITER.check(" in src
    assert "PUBLIC_COMMENT_LIMITER.register(" in src

    # Path + response_model unchanged on the resolver route.
    get_route = next(
        r for r in env.main.app.router.routes
        if getattr(r, "path", None) == "/v1/design-agent/by-token/{token}"
        and "GET" in getattr(r, "methods", set())
    )
    assert get_route.response_model is env.routes.PublicPrototypeView


# ═══════════════════════════════════════════════════════════════════════════
# request.client None safety + 429 existence-neutrality (reviewer hardening)
# ═══════════════════════════════════════════════════════════════════════════


def test_public_comment_request_client_none_no_crash(unauth, env):
    # `request.client` can be None in some ASGI/test contexts. The per-IP comment
    # guard must degrade to the "0.0.0.0" sentinel (per the ticket's null-guard,
    # mirroring the passcode route) and NOT raise AttributeError on None.host.
    # TestClient always sets a client tuple, so the None branch is unreachable over
    # HTTP — exercise it by calling the handler with a hand-built scope whose
    # `client` is None. (Note: the route trusts `request.client.host` only; it does
    # NOT read X-Forwarded-For, so behind a proxy the key is the proxy IP — an
    # accepted limitation for this in-memory, single-worker build.)
    from starlette.requests import Request as StarletteRequest

    token = _seed(share_mode="public")
    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/v1/design-agent/by-token/{token}/comments",
        "headers": [],
        "query_string": b"",
        "client": None,                       # <-- the case under test
    }
    req = StarletteRequest(scope)
    body = env.routes.CommentCreate(anchor_id="deadbeef", body="from a clientless request")

    # Must NOT raise (no NPE on None.host); the comment is written normally.
    out = env.routes.post_comment_public(token=token, body=body, request=req)
    assert out.author == "external"
    assert out.status == "open"
    # The guard fell back to the "0.0.0.0" sentinel key, so exactly one event landed
    # there (proving the None path keyed the sentinel rather than crashing).
    assert len(env.routes.PUBLIC_COMMENT_LIMITER._events.get("0.0.0.0", [])) == 1


def test_token_view_429_existence_neutral_bogus_vs_real(unauth):
    # The 429 is existence-NEUTRAL: a bogus (never-seeded) token hammered over the
    # limit returns 429 IDENTICALLY to a real token over the limit, so the 429 signal
    # never reveals whether a token exists. Because the limiter runs BEFORE resolution
    # (the locked §Invisibility ordering), the bogus token's first 60 are 404 (unknown)
    # yet the 61st still 429s — same contract as the real token's 61st. This is the
    # security property the reviewer most wants codified.
    real = _seed(share_mode="public")
    bogus = str(uuid.uuid4())                 # never inserted → resolves to 404

    real_url = f"/v1/design-agent/by-token/{real}"
    for _ in range(60):
        assert unauth.get(real_url).status_code == 200      # real + under limit
    real_429 = unauth.get(real_url)

    bogus_url = f"/v1/design-agent/by-token/{bogus}"
    for _ in range(60):
        assert unauth.get(bogus_url).status_code == 404      # unknown + under limit
    bogus_429 = unauth.get(bogus_url)

    # Both 61st calls 429 — existence-neutral.
    assert real_429.status_code == 429
    assert bogus_429.status_code == 429
    real_detail = real_429.json()["detail"]
    bogus_detail = bogus_429.json()["detail"]
    assert real_detail["error"] == "rate_limit"
    assert bogus_detail["error"] == "rate_limit"
    assert isinstance(bogus_detail["retry_after_seconds"], int)
    assert bogus_detail["retry_after_seconds"] >= 1
    # Identical 429 body key set for real and bogus — no extra field leaks existence.
    assert real_detail.keys() == bogus_detail.keys()
