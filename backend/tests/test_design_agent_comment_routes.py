"""Tests for the anchored-comment routes (P3-02):

    POST  /v1/design-agent/{prototype_id}/comments              (authed)
    GET   /v1/design-agent/{prototype_id}/comments              (authed)
    PATCH /v1/design-agent/{prototype_id}/comments/{cid}/resolve (authed)
    POST  /v1/design-agent/by-token/{token}/comments            (public, no auth)
    GET   /v1/design-agent/by-token/{token}/comments            (public, no auth)

The internal routes reuse the authed-route gates (feature flag +
require_company + workspace filter); the public routes ride on the P2-05
`by-token` resolver, where the share_token IS the access primitive (F6) — no
auth, no session workspace claim, workspace taken from the resolved row. The
security posture under test:

  - workspace isolation (Rule #22): a prototype / comment in a foreign workspace
    returns 404, never 403 (cross-tenant existence is not disclosed).
  - 404-not-401 on the public surface: missing / private / not-ready tokens all
    return 404 (invisibility — F6).
  - internal-only resolve: there is NO public resolve route (spec §4 Stage 2).
  - observability (Rule #24): the public write logs token_hash=, never the raw
    token, and never the comment body (PII).

Runs fully in isolation against the in-memory FakeSupabaseClient — same fixture
shape as test_design_agent_public_routes.py, with the prototype_comments table
added to the DDL. We reload app.db.prototypes → app.db.prototype_comments →
app.routes.design_agent → app.main in dependency order so the route binds to the
fake-wired helpers (the comment helpers are imported at the EOF of
routes/design_agent.py, so prototype_comments must be reloaded before the route
module).
"""
from __future__ import annotations

import hashlib
import importlib
import logging
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.conftest import _TEST_COMPANY_ID

# SQLite-compatible end-state of `prototypes` (P1-06 + P2-06 sharing columns) +
# `prototype_checkpoints` + `prototype_comments` (P3-01). Postgres-only constructs
# are translated/omitted the same way the sibling test DDLs do — the fake
# exercises SQL semantics, not Postgres DDL. The prototype_comments status CHECK
# is inlined so the fake rejects illegal statuses exactly as Postgres will.
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
    pin_x_pct          REAL,
    pin_y_pct          REAL,
    resolved_anchor_id TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at   TEXT
);
"""

_OTHER_WS = "other-workspace"  # foreign to the caller's company (_TEST_COMPANY_ID)


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + prototype tables (incl. comments) + feature flag ON,
    with the design agent module stack reloaded in dependency order."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_DDL)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.db.prototype_comments as comments_mod
    importlib.reload(comments_mod)            # rebind require_client/utc_now
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)              # rebinds BOTH its top + EOF db imports
    import app.main as main_mod
    importlib.reload(main_mod)                # rebuild the app with the reloaded router

    return SimpleNamespace(proto=proto_mod, comments=comments_mod, routes=routes_mod, main=main_mod)


@pytest.fixture
def client(company_client) -> TestClient:
    """Bearer-authed TestClient (require_company) — see conftest.company_client."""
    return company_client


@pytest.fixture
def unauth(env) -> TestClient:
    """TestClient with NO session cookie — proves the public routes need no auth."""
    return TestClient(env.main.app)


# ─── seeding helpers ──────────────────────────────────────────────────────


def _seed_prototype(
    *,
    workspace_id: str = _TEST_COMPANY_ID,
    share_mode: str = "private",
    status: str = "ready",
    is_complete: int = 0,
) -> SimpleNamespace:
    """Insert one prototype row directly into the fake DB; return id + token.

    Direct SQL (same approach as test_design_agent_public_routes._seed) keeps the
    seed independent of set_share_config's workspace guard. A share_token is
    always minted (harmless for the internal-route cases that key off id)."""
    from tests import _fake_supabase

    token = str(uuid.uuid4())
    cur = _fake_supabase.get_fake_db().execute(
        "INSERT INTO prototypes "
        "(prd_id, workspace_id, template_version, status, share_mode, share_token, "
        " bundle_url, is_complete) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [1, workspace_id, 1, status, share_mode, token,
         "https://cdn.example/p/abc/index.html", is_complete],
    )
    return SimpleNamespace(id=cur.lastrowid, token=token, workspace_id=workspace_id)


def _seed_comment(
    *,
    prototype_id: int,
    workspace_id: str = _TEST_COMPANY_ID,
    anchor_id: str = "a1b2c3d4",
    body: str = "seeded comment",
    author: str = "demo",
    status: str = "open",
    created_at: str = "2026-01-01 00:00:00",
) -> int:
    """Insert one comment row directly; return its id."""
    from tests import _fake_supabase

    cur = _fake_supabase.get_fake_db().execute(
        "INSERT INTO prototype_comments "
        "(prototype_id, workspace_id, anchor_id, body, author, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [prototype_id, workspace_id, anchor_id, body, author, status, created_at],
    )
    return cur.lastrowid


def _count_comments(prototype_id: int) -> int:
    from tests import _fake_supabase

    cur = _fake_supabase.get_fake_db().execute(
        "SELECT COUNT(*) FROM prototype_comments WHERE prototype_id = ?", [prototype_id]
    )
    return cur.fetchone()[0]


# ─── Internal CRUD ─────────────────────────────────────────────────────────


def test_post_comment_authed_returns_open_comment(client):
    # AC1 — authed POST returns 200 with an open CommentOut + persists a row.
    proto = _seed_prototype(workspace_id=_TEST_COMPANY_ID)
    resp = client.post(
        f"/v1/design-agent/{proto.id}/comments",
        json={"anchor_id": "deadbeef", "body": "make this blue"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "open"
    assert body["author"] == "demo"
    assert body["anchor_id"] == "deadbeef"
    assert body["body"] == "make this blue"
    assert body["resolved_at"] is None
    assert _count_comments(proto.id) == 1


def test_post_comment_wrong_workspace_returns_404(client):
    # AC2 — a prototype in a foreign workspace is invisible to the app session.
    proto = _seed_prototype(workspace_id=_OTHER_WS)
    resp = client.post(
        f"/v1/design-agent/{proto.id}/comments",
        json={"anchor_id": "deadbeef", "body": "x"},
    )
    assert resp.status_code == 404
    assert _count_comments(proto.id) == 0  # nothing written across the tenant line


def test_post_comment_requires_session(unauth):
    # AC (CRUD auth) — no bearer → 401 from require_company.
    proto = _seed_prototype(workspace_id=_TEST_COMPANY_ID)
    resp = unauth.post(
        f"/v1/design-agent/{proto.id}/comments",
        json={"anchor_id": "deadbeef", "body": "x"},
    )
    assert resp.status_code == 401


def test_get_comments_returns_all_statuses(client):
    # AC3 — GET returns every comment (all statuses), created_at-ascending.
    proto = _seed_prototype(workspace_id=_TEST_COMPANY_ID)
    _seed_comment(prototype_id=proto.id, status="open", anchor_id="aaa",
                  created_at="2026-01-01 00:00:01")
    _seed_comment(prototype_id=proto.id, status="resolved", anchor_id="bbb",
                  created_at="2026-01-01 00:00:02")
    _seed_comment(prototype_id=proto.id, status="orphaned", anchor_id="ccc",
                  created_at="2026-01-01 00:00:03")
    resp = client.get(f"/v1/design-agent/{proto.id}/comments")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["status"] for r in rows] == ["open", "resolved", "orphaned"]
    assert [r["created_at"] for r in rows] == sorted(r["created_at"] for r in rows)


def test_get_comments_wrong_workspace_returns_404(client):
    # AC3 (isolation half) — listing a foreign-workspace prototype → 404.
    proto = _seed_prototype(workspace_id=_OTHER_WS)
    _seed_comment(prototype_id=proto.id, workspace_id=_OTHER_WS)
    resp = client.get(f"/v1/design-agent/{proto.id}/comments")
    assert resp.status_code == 404


def test_patch_resolve_flips_status(client):
    # AC4 — PATCH flips the comment to resolved + stamps resolved_at.
    proto = _seed_prototype(workspace_id=_TEST_COMPANY_ID)
    cid = _seed_comment(prototype_id=proto.id, status="open")
    resp = client.patch(f"/v1/design-agent/{proto.id}/comments/{cid}/resolve")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["resolved_at"] is not None


def test_patch_resolve_comment_for_other_prototype_returns_404(client):
    # AC4 — a cid that belongs to a DIFFERENT prototype than the path → 404.
    proto_a = _seed_prototype(workspace_id=_TEST_COMPANY_ID)
    proto_b = _seed_prototype(workspace_id=_TEST_COMPANY_ID)
    cid = _seed_comment(prototype_id=proto_a.id, status="open")
    resp = client.patch(f"/v1/design-agent/{proto_b.id}/comments/{cid}/resolve")
    assert resp.status_code == 404


def test_patch_resolve_other_workspace_returns_404(client):
    # AC4 — a comment in a foreign workspace is invisible to resolve → 404.
    proto = _seed_prototype(workspace_id=_OTHER_WS)
    cid = _seed_comment(prototype_id=proto.id, workspace_id=_OTHER_WS, status="open")
    resp = client.patch(f"/v1/design-agent/{proto.id}/comments/{cid}/resolve")
    assert resp.status_code == 404


# ─── Public variant (no auth) ──────────────────────────────────────────────


def test_post_comment_public_no_auth_persists_external_comment(unauth):
    # AC5 — no-auth POST on a public ready prototype → 200, author='external',
    # workspace from the resolved row.
    proto = _seed_prototype(workspace_id="tenant-x", share_mode="public", status="ready")
    resp = unauth.post(
        f"/v1/design-agent/by-token/{proto.token}/comments",
        json={"anchor_id": "deadbeef", "body": "love this"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.history == []                      # no redirect to /sign-in
    assert "set-cookie" not in {k.lower() for k in resp.headers}
    body = resp.json()
    assert body["author"] == "external"
    assert body["status"] == "open"
    # workspace_id is internal — never surfaces in CommentOut — but the row must
    # carry the resolved prototype's workspace, not a session claim.
    from tests import _fake_supabase
    ws = _fake_supabase.get_fake_db().execute(
        "SELECT workspace_id FROM prototype_comments WHERE id = ?", [body["id"]]
    ).fetchone()[0]
    assert ws == "tenant-x"


def test_post_comment_public_private_mode_returns_404(unauth):
    # AC6 — private share is invisible: 404, not 401/403.
    proto = _seed_prototype(share_mode="private", status="ready")
    resp = unauth.post(
        f"/v1/design-agent/by-token/{proto.token}/comments",
        json={"anchor_id": "deadbeef", "body": "x"},
    )
    assert resp.status_code == 404
    assert _count_comments(proto.id) == 0


def test_post_comment_public_not_ready_returns_404(unauth):
    # AC6 — a still-generating prototype is not commentable → 404.
    proto = _seed_prototype(share_mode="public", status="generating")
    resp = unauth.post(
        f"/v1/design-agent/by-token/{proto.token}/comments",
        json={"anchor_id": "deadbeef", "body": "x"},
    )
    assert resp.status_code == 404
    assert _count_comments(proto.id) == 0


def test_post_comment_public_missing_token_returns_404(unauth):
    # AC5 — brute-force scan of a random UUID returns 404, not 401.
    resp = unauth.post(
        f"/v1/design-agent/by-token/{uuid.uuid4()}/comments",
        json={"anchor_id": "deadbeef", "body": "x"},
    )
    assert resp.status_code == 404


def test_get_comments_public_no_auth_returns_list(unauth):
    # AC7 — no-auth GET returns the comment list for a public/ready prototype.
    proto = _seed_prototype(workspace_id="tenant-x", share_mode="public", status="ready")
    _seed_comment(prototype_id=proto.id, workspace_id="tenant-x", body="hi", status="open")
    resp = unauth.get(f"/v1/design-agent/by-token/{proto.token}/comments")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["status"] == "open"


def test_get_comments_public_private_returns_404(unauth):
    # AC7 — private prototype is invisible on the public list surface → 404.
    proto = _seed_prototype(share_mode="private", status="ready")
    resp = unauth.get(f"/v1/design-agent/by-token/{proto.token}/comments")
    assert resp.status_code == 404


def test_no_public_resolve_route(unauth):
    # AC8 — there is NO public resolve route; the router has no match → 404.
    proto = _seed_prototype(share_mode="public", status="ready")
    cid = _seed_comment(prototype_id=proto.id, status="open")
    resp = unauth.patch(f"/v1/design-agent/by-token/{proto.token}/comments/{cid}/resolve")
    assert resp.status_code == 404


# ─── Edge / validation ─────────────────────────────────────────────────────


def test_post_comment_empty_body_returns_422(client):
    # Pydantic min_length=1 on body → 422 (not a silent empty insert).
    proto = _seed_prototype(workspace_id=_TEST_COMPANY_ID)
    resp = client.post(
        f"/v1/design-agent/{proto.id}/comments",
        json={"anchor_id": "deadbeef", "body": ""},
    )
    assert resp.status_code == 422


def test_post_comment_empty_anchor_returns_422(client):
    # Pydantic min_length=1 on anchor_id → 422.
    proto = _seed_prototype(workspace_id=_TEST_COMPANY_ID)
    resp = client.post(
        f"/v1/design-agent/{proto.id}/comments",
        json={"anchor_id": "", "body": "x"},
    )
    assert resp.status_code == 422


# ─── Observability (AC10) ───────────────────────────────────────────────────


def test_public_write_logs_token_hash_not_raw(unauth, caplog):
    # AC10 — the public write logs token_hash=<sha256[:8]>, never the raw token,
    # and never the comment body (PII per Rule #24).
    proto = _seed_prototype(workspace_id="tenant-x", share_mode="public", status="ready")
    expected_hash = hashlib.sha256(proto.token.encode("utf-8")).hexdigest()[:8]
    with caplog.at_level(logging.INFO, logger="app.routes.design_agent"):
        resp = unauth.post(
            f"/v1/design-agent/by-token/{proto.token}/comments",
            json={"anchor_id": "deadbeef", "body": "secret comment text"},
        )
    assert resp.status_code == 200, resp.text
    public_lines = [r for r in caplog.records if "comment_created_public" in r.getMessage()]
    assert public_lines, "expected a comment_created_public log line"
    text = caplog.text
    assert f"token_hash={expected_hash}" in text
    assert proto.token not in text                 # raw token never logged
    assert "secret comment text" not in text        # comment body never logged


# ─── Non-breakage (AC9) ─────────────────────────────────────────────────────


def test_comment_routes_registered_and_existing_intact(env):
    # AC9 — the new routes are appended to the same router; existing routes and
    # the include_router wiring remain resolvable.
    paths = {r.path for r in env.main.app.router.routes}
    # new comment surface
    assert "/v1/design-agent/{prototype_id}/comments" in paths
    assert "/v1/design-agent/{prototype_id}/comments/{cid}/resolve" in paths
    assert "/v1/design-agent/by-token/{token}/comments" in paths
    # untouched predecessors
    assert "/v1/design-agent/generate" in paths
    assert "/v1/design-agent/{prototype_id}" in paths
    assert "/v1/design-agent/by-token/{token}" in paths
    assert "/v1/design-agent/by-token/{token}/passcode" in paths


# ─── Durable comment position (route-level) ─────────────────────────────────


def test_comment_out_projects_position_fields(env):
    # _comment_to_out on a row WITH position → CommentOut carries the three values.
    # On a row WITHOUT the keys → all three None (uses .get, safe for older rows).
    from app.routes.design_agent import _comment_to_out, CommentOut

    with_pos = _comment_to_out({
        "id": 1,
        "anchor_id": "pin-1",
        "body": "hi",
        "author": "demo",
        "status": "open",
        "created_at": "2026-01-01T00:00:00",
        "resolved_at": None,
        "pin_x_pct": 25.0,
        "pin_y_pct": 50.0,
        "resolved_anchor_id": "abc123",
    })
    out = CommentOut(**with_pos)
    assert out.pin_x_pct == pytest.approx(25.0)
    assert out.pin_y_pct == pytest.approx(50.0)
    assert out.resolved_anchor_id == "abc123"

    without_pos = _comment_to_out({
        "id": 2,
        "anchor_id": "anc1",
        "body": "hi",
        "author": "demo",
        "status": "open",
        "created_at": "2026-01-01T00:00:00",
        "resolved_at": None,
        # no position keys at all (older row)
    })
    out2 = CommentOut(**without_pos)
    assert out2.pin_x_pct is None
    assert out2.pin_y_pct is None
    assert out2.resolved_anchor_id is None


def test_comment_create_rejects_out_of_range_pct(client):
    # CommentCreate ge=0 le=100 rejects out-of-range values with 422.
    proto = _seed_prototype(workspace_id=_TEST_COMPANY_ID)
    resp = client.post(
        f"/v1/design-agent/{proto.id}/comments",
        json={"anchor_id": "pin-1", "body": "x", "pin_x_pct": 150},
    )
    assert resp.status_code == 422

    resp2 = client.post(
        f"/v1/design-agent/{proto.id}/comments",
        json={"anchor_id": "pin-1", "body": "x", "pin_y_pct": -1},
    )
    assert resp2.status_code == 422

    # Omitted position is accepted.
    resp3 = client.post(
        f"/v1/design-agent/{proto.id}/comments",
        json={"anchor_id": "pin-1", "body": "x"},
    )
    assert resp3.status_code == 200


def test_post_comment_authed_round_trips_position(client):
    # Authed POST with position → CommentOut echoes all three; subsequent GET
    # returns them as well.
    proto = _seed_prototype(workspace_id=_TEST_COMPANY_ID)
    resp = client.post(
        f"/v1/design-agent/{proto.id}/comments",
        json={
            "anchor_id": "pin-1",
            "body": "make this bigger",
            "pin_x_pct": 33.5,
            "pin_y_pct": 66.0,
            "resolved_anchor_id": "abcd1234",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pin_x_pct"] == pytest.approx(33.5)
    assert body["pin_y_pct"] == pytest.approx(66.0)
    assert body["resolved_anchor_id"] == "abcd1234"

    get_resp = client.get(f"/v1/design-agent/{proto.id}/comments")
    assert get_resp.status_code == 200
    rows = get_resp.json()
    assert len(rows) == 1
    assert rows[0]["pin_x_pct"] == pytest.approx(33.5)
    assert rows[0]["resolved_anchor_id"] == "abcd1234"


def test_post_comment_public_round_trips_position(unauth):
    # Public POST with position → persisted under the resolved prototype's workspace_id.
    proto = _seed_prototype(workspace_id="tenant-pub", share_mode="public", status="ready")
    resp = unauth.post(
        f"/v1/design-agent/by-token/{proto.token}/comments",
        json={
            "anchor_id": "pin-1",
            "body": "nice layout",
            "pin_x_pct": 10.0,
            "pin_y_pct": 20.0,
            "resolved_anchor_id": "ef567890",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pin_x_pct"] == pytest.approx(10.0)
    assert body["pin_y_pct"] == pytest.approx(20.0)
    assert body["resolved_anchor_id"] == "ef567890"

    # Workspace written from the resolved row, not a session claim.
    from tests import _fake_supabase
    ws = _fake_supabase.get_fake_db().execute(
        "SELECT workspace_id FROM prototype_comments WHERE id = ?", [body["id"]]
    ).fetchone()[0]
    assert ws == "tenant-pub"


def test_post_comment_omitted_position_defaults_null(client):
    # POST with only {anchor_id, body} → all three position fields null (back-compat).
    proto = _seed_prototype(workspace_id=_TEST_COMPANY_ID)
    resp = client.post(
        f"/v1/design-agent/{proto.id}/comments",
        json={"anchor_id": "anc-legacy", "body": "right-click anchor comment"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("pin_x_pct") is None
    assert body.get("pin_y_pct") is None
    assert body.get("resolved_anchor_id") is None


def test_position_not_leaked_cross_workspace(client):
    # A comment with position written under workspace A is not returned when
    # GET /{id}/comments is queried under workspace B — the prototype lookup
    # returns 404 before any row is read (existing workspace filter).
    proto_a = _seed_prototype(workspace_id=_OTHER_WS)
    _seed_comment(
        prototype_id=proto_a.id,
        workspace_id=_OTHER_WS,
        anchor_id="pin-1",
        body="cross-ws check",
    )
    # The client's company is _TEST_COMPANY_ID, which is different from _OTHER_WS.
    resp = client.get(f"/v1/design-agent/{proto_a.id}/comments")
    assert resp.status_code == 404
