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
    user_id            TEXT,
    origin        TEXT NOT NULL DEFAULT 'internal'
                  CHECK (origin IN ('internal', 'public')),
    visitor_id    TEXT,
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
    import app.routes.design_agent_comments as comment_routes_mod
    importlib.reload(comment_routes_mod)      # rebind to the reloaded design_agent + db helpers
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
    origin: str = "internal",
    visitor_id: str | None = None,
) -> int:
    """Insert one comment row directly; return its id."""
    from tests import _fake_supabase

    cur = _fake_supabase.get_fake_db().execute(
        "INSERT INTO prototype_comments "
        "(prototype_id, workspace_id, anchor_id, body, author, status, created_at, "
        " origin, visitor_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [prototype_id, workspace_id, anchor_id, body, author, status, created_at,
         origin, visitor_id],
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
    # B9a: author is now user_email when available, else user_id (UUID). The test
    # JWT has no email claim, so the fallback is _TEST_USER_ID ("user-test").
    assert body["author"] == "user-test"
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
    # Phase 3: anonymous public comment CREATE is ENABLED (the token is the access
    # primitive, F8). A no-auth POST to a public+ready prototype returns 200 with a
    # CommentOut and persists a row. With no viewer_name, the author is "Anonymous".
    proto = _seed_prototype(workspace_id="tenant-x", share_mode="public", status="ready")
    resp = unauth.post(
        f"/v1/design-agent/by-token/{proto.token}/comments",
        json={"anchor_id": "deadbeef", "body": "love this"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "open"
    assert body["author"] == "Anonymous"   # blank/omitted name → Anonymous
    assert body["anchor_id"] == "deadbeef"
    assert body["body"] == "love this"
    assert _count_comments(proto.id) == 1   # the row was written


def test_post_comment_public_viewer_name_maps_to_author(unauth):
    # Phase 3: a supplied viewer_name is trimmed and stored on the EXISTING author
    # column (no new column / no migration). A blank name still falls back to
    # "Anonymous".
    proto = _seed_prototype(workspace_id="tenant-x", share_mode="public", status="ready")
    resp = unauth.post(
        f"/v1/design-agent/by-token/{proto.token}/comments",
        json={"anchor_id": "deadbeef", "body": "tighten the header", "viewer_name": "  Ada Lovelace  "},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["author"] == "Ada Lovelace"   # trimmed

    blank = unauth.post(
        f"/v1/design-agent/by-token/{proto.token}/comments",
        json={"anchor_id": "deadbeef", "body": "and the footer", "viewer_name": "   "},
    )
    assert blank.status_code == 200, blank.text
    assert blank.json()["author"] == "Anonymous"   # whitespace-only → Anonymous


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
    # The public list now serves PUBLIC-origin rows only, so the seed is a
    # public-origin comment — same behaviour under the current invariant.
    proto = _seed_prototype(workspace_id="tenant-x", share_mode="public", status="ready")
    _seed_comment(prototype_id=proto.id, workspace_id="tenant-x", body="hi", status="open",
                  origin="public")
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
    # AC10: the public write logs token_hash= (never the raw token), and never the
    # comment body NOR the viewer name (both PII). Now that the write is ENABLED the
    # log line actually fires, so this is a live (not vacuous) hygiene assertion.
    proto = _seed_prototype(workspace_id="tenant-x", share_mode="public", status="ready")
    with caplog.at_level(logging.INFO, logger="app.routes.design_agent"):
        resp = unauth.post(
            f"/v1/design-agent/by-token/{proto.token}/comments",
            json={
                "anchor_id": "deadbeef",
                "body": "secret comment text",
                "viewer_name": "Grace Hopper",
            },
        )
    assert resp.status_code == 200, resp.text
    assert "comment_created_public" in caplog.text   # the correlation line fired
    assert proto.token not in caplog.text            # raw token never in any log line
    assert "secret comment text" not in caplog.text  # comment body never logged
    assert "Grace Hopper" not in caplog.text         # viewer name (PII) never logged


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
    from app.routes.design_agent_comments import _comment_to_out, CommentOut

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
    # Phase 3: public comment CREATE is enabled → position fields round-trip on the
    # public surface exactly like the authed route.
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
    assert _count_comments(proto.id) == 1


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


# ─── General (unpinned) comments — nullable anchor_id ─────────────────────
#
# A general comment is a plain prototype_comments row with anchor_id=None and
# pin_x_pct/pin_y_pct=None. This needs a NULLABLE anchor_id column, so these
# tests run against a SEPARATE DDL (below) rather than the NOT-NULL `_DDL`
# above — the existing tests all insert non-null anchors and stay valid
# against the original DDL untouched.

_DDL_NULLABLE_ANCHOR = _DDL.replace(
    "anchor_id     TEXT NOT NULL,",
    "anchor_id     TEXT,",
)
assert _DDL_NULLABLE_ANCHOR != _DDL  # guard against a future rename silently no-op'ing the replace


@pytest.fixture
def env_general(isolated_settings, monkeypatch):
    """Same reload dance as `env`, but against the NULLABLE-anchor_id DDL."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_DDL_NULLABLE_ANCHOR)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.db.prototype_comments as comments_mod
    importlib.reload(comments_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.routes.design_agent_comments as comment_routes_mod
    importlib.reload(comment_routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    return SimpleNamespace(proto=proto_mod, comments=comments_mod, routes=routes_mod, main=main_mod)


@pytest.fixture
def unauth_general(env_general) -> TestClient:
    return TestClient(env_general.main.app)


def test_public_create_accepts_null_anchor(unauth_general):
    # A general comment POSTs with anchor_id explicitly null + no pin coords.
    # Before the migration + CommentCreate widen this 422'd (min_length=1).
    from tests import _fake_supabase

    token = str(uuid.uuid4())
    _fake_supabase.get_fake_db().execute(
        "INSERT INTO prototypes "
        "(prd_id, workspace_id, template_version, status, share_mode, share_token, "
        " bundle_url, is_complete) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [1, "tenant-general", 1, "ready", "public", token,
         "https://cdn.example/p/abc/index.html", 0],
    )
    resp = unauth_general.post(
        f"/v1/design-agent/by-token/{token}/comments",
        json={
            "body": "Overall this feels smooth, nice palette.",
            "anchor_id": None,
            "pin_x_pct": None,
            "pin_y_pct": None,
            "viewer_name": "Sarah Chen",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["anchor_id"] is None
    assert body["pin_x_pct"] is None
    assert body["pin_y_pct"] is None
    assert body["author"] == "Sarah Chen"
    row = _fake_supabase.get_fake_db().execute(
        "SELECT anchor_id FROM prototype_comments WHERE id = ?", [body["id"]]
    ).fetchone()
    assert row[0] is None  # persisted as a real null, not the string "None"


def test_public_create_null_anchor_excluded_from_grounding(env_general):
    # Insert a real null-anchor (general) row AND a sibling pinned row against
    # the nullable DDL, then run them through the ACTUAL grounding projection
    # (_project_open_comments_for_grounding) that the iterate background run
    # feeds to the prompt renderer. The pinned row must be present; the
    # null-anchor row must be ABSENT — proving this test would fail if the
    # `c.get("anchor_id")` guard were ever removed from that projection.
    from app.db.prototype_comments import insert_comment, list_comments
    from app.routes.design_agent import _project_open_comments_for_grounding

    insert_comment(
        prototype_id=1, workspace_id="tenant-general",
        anchor_id=None, body="General feedback, no element", author="Marcus K.",
    )
    insert_comment(
        prototype_id=1, workspace_id="tenant-general",
        anchor_id="deadbeef", body="This button needs more weight", author="Jane Doe",
    )

    all_comments = list_comments(prototype_id=1, workspace_id="tenant-general")
    assert len(all_comments) == 2  # both rows really persisted

    grounded = _project_open_comments_for_grounding(all_comments)

    assert len(grounded) == 1  # the null-anchor row did not survive the projection
    assert grounded[0]["anchor_id"] == "deadbeef"
    assert grounded[0]["body"] == "This button needs more weight"
    assert not any(g["anchor_id"] is None for g in grounded)


def test_public_create_pinned_still_works(unauth_general):
    # Regression: a normal anchored/pinned create against the now-nullable
    # column behaves exactly as before (anchor_id required + non-empty).
    from tests import _fake_supabase

    token = str(uuid.uuid4())
    _fake_supabase.get_fake_db().execute(
        "INSERT INTO prototypes "
        "(prd_id, workspace_id, template_version, status, share_mode, share_token, "
        " bundle_url, is_complete) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [1, "tenant-general", 1, "ready", "public", token,
         "https://cdn.example/p/abc/index.html", 0],
    )
    resp = unauth_general.post(
        f"/v1/design-agent/by-token/{token}/comments",
        json={"anchor_id": "deadbeef", "body": "love this", "pin_x_pct": 10.0, "pin_y_pct": 20.0},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["anchor_id"] == "deadbeef"
    assert body["pin_x_pct"] == pytest.approx(10.0)

    # An empty-string anchor_id is still rejected (422) -- distinct from the
    # honest "no anchor" None case.
    resp2 = unauth_general.post(
        f"/v1/design-agent/by-token/{token}/comments",
        json={"anchor_id": "", "body": "x"},
    )
    assert resp2.status_code == 422


def test_comment_out_serializes_null_anchor(env_general):
    # CommentOut / _comment_to_out project a null anchor_id cleanly (no
    # KeyError, no validation error) via .get(), matching the pattern already
    # used for pin_x_pct/pin_y_pct/resolved_anchor_id.
    from app.routes.design_agent_comments import _comment_to_out, CommentOut

    projected = _comment_to_out({
        "id": 9,
        "anchor_id": None,
        "body": "general feedback",
        "author": "Anonymous",
        "status": "open",
        "created_at": "2026-01-01T00:00:00",
        "resolved_at": None,
    })
    out = CommentOut(**projected)
    assert out.anchor_id is None
    assert out.body == "general feedback"


# ─── Internal (authed) general comments — unification on null ────────────
#
# The authed freeform composer used to post the truthy sentinel anchor_id
# 'general' instead of a real null. These tests prove the authed create path
# now behaves identically to the public one: a null-anchor general created via
# the AUTHED route persists as a real null and is excluded from element
# auto-grounding the same way a publicly-created general already is. Also
# covers the one-time backfill that rewrites any pre-existing 'general' rows.


@pytest.fixture
def client_general(env_general, isolated_settings, monkeypatch) -> TestClient:
    """Bearer-authed TestClient (require_company), bound to the NULLABLE-anchor
    DDL (env_general) instead of company_client's default `env`. Same auth
    wiring as company_client (conftest.py), just composed on the local
    env_general fixture so authed create can post a real null anchor_id."""
    from tests.conftest import _enable_supabase_bearer, _mint_supabase_token, _seed_company_membership

    _enable_supabase_bearer(monkeypatch)
    _seed_company_membership(isolated_settings["supabase"])
    c = TestClient(env_general.main.app)
    c.headers["Authorization"] = f"Bearer {_mint_supabase_token()}"
    return c


def test_authed_create_accepts_null_anchor(client_general):
    # The authed General composer posts anchor_id: null (was the 'general'
    # sentinel string) -- mirrors test_public_create_accepts_null_anchor for
    # the signed-in route.
    proto = _seed_prototype(workspace_id=_TEST_COMPANY_ID)
    resp = client_general.post(
        f"/v1/design-agent/{proto.id}/comments",
        json={"anchor_id": None, "body": "Overall this feels smooth, nice palette."},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["anchor_id"] is None
    from tests import _fake_supabase

    row = _fake_supabase.get_fake_db().execute(
        "SELECT anchor_id FROM prototype_comments WHERE id = ?", [body["id"]]
    ).fetchone()
    assert row[0] is None  # persisted as a real null, never the string "general"


def test_null_general_excluded_from_grounding_authed(client_general):
    # A null-anchor general created through the AUTHED route also drops out of
    # element auto-grounding: the exclusion in _project_open_comments_for_grounding
    # is keyed on anchor_id, not on which surface created the row -- this is the
    # authed-surface counterpart of test_public_create_null_anchor_excluded_from_grounding.
    from app.db.prototype_comments import list_comments
    from app.routes.design_agent import _project_open_comments_for_grounding

    proto = _seed_prototype(workspace_id=_TEST_COMPANY_ID)
    resp = client_general.post(
        f"/v1/design-agent/{proto.id}/comments",
        json={"anchor_id": None, "body": "General feedback from the signed-in editor"},
    )
    assert resp.status_code == 200, resp.text

    _seed_comment(
        prototype_id=proto.id, workspace_id=_TEST_COMPANY_ID,
        anchor_id="deadbeef", body="This button needs more weight",
    )

    all_comments = list_comments(prototype_id=proto.id, workspace_id=_TEST_COMPANY_ID)
    assert len(all_comments) == 2  # both rows really persisted

    grounded = _project_open_comments_for_grounding(all_comments)

    assert len(grounded) == 1  # the null-anchor row did not survive the projection
    assert grounded[0]["anchor_id"] == "deadbeef"
    assert not any(g["anchor_id"] is None for g in grounded)


def test_backfill_general_sentinel_to_null(env_general):
    # Simulates the one-time production backfill migration
    # (20260706010000_general_comments_null_anchor_backfill.sql): a legacy row
    # created by the old sentinel-posting composer (anchor_id='general') is
    # rewritten to a real null, so it is treated as general everywhere
    # downstream (the split + the grounding exclusion) exactly like a comment
    # that was always null.
    from tests import _fake_supabase
    from app.db.prototype_comments import insert_comment, list_comments

    insert_comment(
        prototype_id=1, workspace_id="tenant-general",
        anchor_id="general", body="legacy sentinel row", author="Old Composer",
    )
    insert_comment(
        prototype_id=1, workspace_id="tenant-general",
        anchor_id="deadbeef", body="unrelated pinned row", author="Jane Doe",
    )

    db = _fake_supabase.get_fake_db()
    db.execute("UPDATE prototype_comments SET anchor_id = NULL WHERE anchor_id = 'general'")

    rows = list_comments(prototype_id=1, workspace_id="tenant-general")
    assert len(rows) == 2
    assert not any(r["anchor_id"] == "general" for r in rows)
    backfilled = next(r for r in rows if r["body"] == "legacy sentinel row")
    assert backfilled["anchor_id"] is None
    untouched = next(r for r in rows if r["body"] == "unrelated pinned row")
    assert untouched["anchor_id"] == "deadbeef"  # the backfill does not touch real anchors


# ─── Public/internal comment isolation + visitor identity ──────────────────
#
# The public by-token list serves PUBLIC-origin rows only: internal team
# comments (and the display names resolved for them) must never reach an
# anonymous share-link holder. Anonymous visitors get a durable HttpOnly
# cookie identity minted on their first public WRITE, and the public list
# marks their own rows `mine` — without ever serializing the identity itself.


def test_public_list_excludes_internal_comments(client, unauth):
    # Regression (the leak): one internal comment (authed route) + one public
    # comment (by-token route) → the public list returns ONLY the public one.
    proto = _seed_prototype(workspace_id=_TEST_COMPANY_ID, share_mode="public", status="ready")
    internal = client.post(
        f"/v1/design-agent/{proto.id}/comments",
        json={"anchor_id": "int-1", "body": "internal-only roadmap discussion"},
    )
    assert internal.status_code == 200, internal.text
    pub = unauth.post(
        f"/v1/design-agent/by-token/{proto.token}/comments",
        json={"anchor_id": "pub-1", "body": "love the hero", "viewer_name": "Visitor V"},
    )
    assert pub.status_code == 200, pub.text

    resp = unauth.get(f"/v1/design-agent/by-token/{proto.token}/comments")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["body"] == "love the hero"
    assert rows[0]["origin"] == "public"
    # The internal comment's body appears NOWHERE in the serialized response.
    assert "internal-only roadmap discussion" not in resp.text


def test_public_list_never_contains_internal_author_names(client, unauth):
    # Regression (the leak, author half): the internal author's name never
    # appears anywhere in the public-list JSON.
    proto = _seed_prototype(workspace_id="tenant-x", share_mode="public", status="ready")
    _seed_comment(
        prototype_id=proto.id, workspace_id="tenant-x",
        author="Ivy Internal", body="team-only note",
    )
    pub = unauth.post(
        f"/v1/design-agent/by-token/{proto.token}/comments",
        json={"anchor_id": "pub-1", "body": "nice palette", "viewer_name": "Pat Public"},
    )
    assert pub.status_code == 200, pub.text

    resp = unauth.get(f"/v1/design-agent/by-token/{proto.token}/comments")
    assert resp.status_code == 200, resp.text
    assert "Ivy Internal" not in resp.text
    assert "team-only note" not in resp.text
    assert [r["author"] for r in resp.json()] == ["Pat Public"]


def test_public_write_sets_origin_public_and_visitor_id(unauth):
    # The public write persists origin='public' + the minted cookie's visitor_id.
    from tests import _fake_supabase

    proto = _seed_prototype(workspace_id="tenant-x", share_mode="public", status="ready")
    resp = unauth.post(
        f"/v1/design-agent/by-token/{proto.token}/comments",
        json={"anchor_id": "pub-1", "body": "ship it"},
    )
    assert resp.status_code == 200, resp.text
    cookie_value = resp.cookies.get("da_visitor")
    assert cookie_value

    row = _fake_supabase.get_fake_db().execute(
        "SELECT origin, visitor_id FROM prototype_comments WHERE id = ?",
        [resp.json()["id"]],
    ).fetchone()
    assert row[0] == "public"
    assert row[1] == cookie_value

    # A SECOND write from the same client reuses the same identity (durable).
    resp2 = unauth.post(
        f"/v1/design-agent/by-token/{proto.token}/comments",
        json={"anchor_id": "pub-2", "body": "still shipping"},
    )
    assert resp2.status_code == 200, resp2.text
    row2 = _fake_supabase.get_fake_db().execute(
        "SELECT visitor_id FROM prototype_comments WHERE id = ?",
        [resp2.json()["id"]],
    ).fetchone()
    assert row2[0] == cookie_value


def test_public_write_mints_httponly_lax_pathscoped_cookie(unauth):
    # Set-Cookie attributes exactly per _visitor_cookie_kwargs: HttpOnly,
    # SameSite=Lax, path-scoped to the public by-token surface, HOST-ONLY
    # (no Domain attribute), 1-year max-age.
    proto = _seed_prototype(workspace_id="tenant-x", share_mode="public", status="ready")
    resp = unauth.post(
        f"/v1/design-agent/by-token/{proto.token}/comments",
        json={"anchor_id": "pub-1", "body": "hello"},
    )
    assert resp.status_code == 200, resp.text
    set_cookie = resp.headers.get("set-cookie", "")
    lowered = set_cookie.lower()
    assert "da_visitor=" in lowered
    assert "httponly" in lowered
    assert "samesite=lax" in lowered
    assert "path=/v1/design-agent/by-token" in lowered
    assert "domain=" not in lowered          # host-only, like the grant cookie
    assert "max-age=31536000" in lowered     # 1 year


def test_public_list_mine_true_for_cookie_owner_false_otherwise(unauth, env):
    # With the cookie: the visitor's own comments are mine=true, other public
    # comments mine=false. With no cookie: mine=false on every row.
    proto = _seed_prototype(workspace_id="tenant-x", share_mode="public", status="ready")
    write = unauth.post(
        f"/v1/design-agent/by-token/{proto.token}/comments",
        json={"anchor_id": "pub-1", "body": "my own comment"},
    )
    assert write.status_code == 200, write.text
    _seed_comment(
        prototype_id=proto.id, workspace_id="tenant-x",
        anchor_id="pub-2", body="someone else's comment",
        origin="public", visitor_id="someone-else-visitor-000001",
    )

    listed = unauth.get(f"/v1/design-agent/by-token/{proto.token}/comments")
    assert listed.status_code == 200, listed.text
    mine_by_body = {r["body"]: r["mine"] for r in listed.json()}
    assert mine_by_body == {
        "my own comment": True,
        "someone else's comment": False,
    }

    cookieless = TestClient(env.main.app)   # fresh jar — no visitor cookie
    bare = cookieless.get(f"/v1/design-agent/by-token/{proto.token}/comments")
    assert bare.status_code == 200, bare.text
    assert all(r["mine"] is False for r in bare.json())


def test_cookieless_public_list_all_mine_false(unauth, env):
    # No cookie → mine=false everywhere, and the GET mints NO cookie
    # (mint-on-write only — passive viewers are never tagged).
    proto = _seed_prototype(workspace_id="tenant-x", share_mode="public", status="ready")
    _seed_comment(
        prototype_id=proto.id, workspace_id="tenant-x",
        origin="public", visitor_id="somebody-visitor-0000001",
        body="a public comment",
    )
    fresh = TestClient(env.main.app)
    resp = fresh.get(f"/v1/design-agent/by-token/{proto.token}/comments")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["mine"] is False
    assert "set-cookie" not in resp.headers  # the read mints nothing


def test_authed_list_returns_both_origins_with_origin_field(client, unauth):
    # Team view unchanged: the authed list returns BOTH comments, each carrying
    # its origin; `mine` stays null on the authed surface.
    proto = _seed_prototype(workspace_id=_TEST_COMPANY_ID, share_mode="public", status="ready")
    internal = client.post(
        f"/v1/design-agent/{proto.id}/comments",
        json={"anchor_id": "int-1", "body": "internal note"},
    )
    assert internal.status_code == 200, internal.text
    pub = unauth.post(
        f"/v1/design-agent/by-token/{proto.token}/comments",
        json={"anchor_id": "pub-1", "body": "public note", "viewer_name": "Pat Public"},
    )
    assert pub.status_code == 200, pub.text

    resp = client.get(f"/v1/design-agent/{proto.id}/comments")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 2
    assert {r["origin"] for r in rows} == {"internal", "public"}
    assert all(r["mine"] is None for r in rows)   # authed surface: role, not visitor


def test_public_routes_404_posture_unchanged(unauth):
    # Missing / private / not-ready tokens still 404 on BOTH public routes.
    missing = str(uuid.uuid4())
    assert unauth.get(f"/v1/design-agent/by-token/{missing}/comments").status_code == 404
    assert unauth.post(
        f"/v1/design-agent/by-token/{missing}/comments",
        json={"anchor_id": "a1", "body": "x"},
    ).status_code == 404

    private_proto = _seed_prototype(share_mode="private", status="ready")
    assert unauth.get(f"/v1/design-agent/by-token/{private_proto.token}/comments").status_code == 404
    assert unauth.post(
        f"/v1/design-agent/by-token/{private_proto.token}/comments",
        json={"anchor_id": "a1", "body": "x"},
    ).status_code == 404

    not_ready = _seed_prototype(share_mode="public", status="generating")
    assert unauth.get(f"/v1/design-agent/by-token/{not_ready.token}/comments").status_code == 404
    assert unauth.post(
        f"/v1/design-agent/by-token/{not_ready.token}/comments",
        json={"anchor_id": "a1", "body": "x"},
    ).status_code == 404


def test_visitor_id_absent_from_all_serialized_responses(client, unauth):
    # No response body from ANY comment route contains a visitor_id key or the
    # stored visitor value — public write, public list, authed list.
    proto = _seed_prototype(workspace_id=_TEST_COMPANY_ID, share_mode="public", status="ready")
    write = unauth.post(
        f"/v1/design-agent/by-token/{proto.token}/comments",
        json={"anchor_id": "pub-1", "body": "public note"},
    )
    assert write.status_code == 200, write.text
    visitor_value = write.cookies.get("da_visitor")
    assert visitor_value
    authed_write = client.post(
        f"/v1/design-agent/{proto.id}/comments",
        json={"anchor_id": "int-1", "body": "internal note"},
    )
    assert authed_write.status_code == 200, authed_write.text

    public_list = unauth.get(f"/v1/design-agent/by-token/{proto.token}/comments")
    authed_list = client.get(f"/v1/design-agent/{proto.id}/comments")
    assert public_list.status_code == 200 and authed_list.status_code == 200
    for resp in (write, authed_write, public_list, authed_list):
        assert "visitor_id" not in resp.text
        assert visitor_value not in resp.text


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
