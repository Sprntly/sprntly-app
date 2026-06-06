"""Tests for the checkpoint chain (P3-12, AD6 + F7):

    advance_current_checkpoint                     (db/prototypes.py)
    _stage_iterate_run → advance_current_checkpoint (routes/design_agent.py, seam fill)

Each iterate creates a NEW `prototype_checkpoints` row (P1-08 `create_checkpoint`)
and `prototypes.current_checkpoint_id` advances to it so the stable share URL
(P2-06) resolves to the LATEST build. F7: the advance must NOT rotate
`share_token` or change `share_mode` — the public `/p/<token>` URL is reused
across regenerations and now serves the new checkpoint's `bundle_url`.

Two layers:
- HELPER — `advance_current_checkpoint` against the in-memory FakeSupabaseClient:
  bundle/current update, workspace filtering, retained-history, observability.
- ROUTE / FLOW — the iterate staging path (`_stage_iterate_run`) end-to-end: a
  second checkpoint is created + current advances + the token-resolver serves the
  new bundle while the token stays byte-identical.

DB helpers are SYNCHRONOUS (mirrors db/prds.py) — called without await even
though the staging path that drives them is async.
"""
from __future__ import annotations

import importlib
import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

# SQLite-compatible end-state of prototypes (P1-06 + P2-06 sharing/lock columns) +
# prototype_checkpoints + prototype_comments. Mirrors test_design_agent_iterate.py.
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
    resolved_at   TEXT,
    user_id        TEXT
);
"""


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + prototype tables + feature flag ON, with the design
    agent module stack reloaded in dependency order (proto → comments → routes →
    main). jsonb columns registered so create_checkpoint round-trips its list
    prompt_history / comment_state as real lists.
    """
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_DDL)
    monkeypatch.setitem(
        _fake_supabase._JSONB_COLUMNS, "prototype_checkpoints",
        {"prompt_history", "comment_state"},
    )
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.db.prototype_comments as comments_mod
    importlib.reload(comments_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    return SimpleNamespace(proto=proto_mod, comments=comments_mod, routes=routes_mod, main=main_mod)


# ─── helpers ──────────────────────────────────────────────────────────────


def _seed_ready(env, *, workspace_id: str = "app", current_checkpoint_id=None,
                bundle_url: str = "https://bundle/original") -> int:
    """Insert a ready prototype (generate path: complete_prototype sets current)."""
    pid = env.proto.start_prototype(prd_id=1, workspace_id=workspace_id, template_version=1)
    env.proto.complete_prototype(
        prototype_id=pid, workspace_id=workspace_id,
        bundle_url=bundle_url, current_checkpoint_id=current_checkpoint_id,
    )
    return pid


def _make_checkpoint(env, pid, *, workspace_id: str = "app", bundle_url=None) -> int:
    return env.proto.create_checkpoint(
        prototype_id=pid, workspace_id=workspace_id,
        bundle_url=bundle_url, prd_revision_hash=None, figma_frame_hash=None,
        prompt_history=[{"kind": "iterate", "prompt": "p"}],
    )


def _checkpoint_count(pid) -> int:
    from tests import _fake_supabase
    rows = _fake_supabase.get_fake_db().execute(
        "SELECT id FROM prototype_checkpoints WHERE prototype_id = ?", [pid]
    ).fetchall()
    return len(rows)


def _stub_staging(env, monkeypatch, *, bundle_url="https://bundle/iterated"):
    """Stub vite_build + stage_bundle + reconcile so _stage_iterate_run exercises
    the REAL create_checkpoint + advance_current_checkpoint without real build /
    storage / orphan-reconcile work (P3-04's reconcile is out of P3-12's scope)."""
    async def fake_vite(vfs):
        return {"index.html": "<html></html>"}

    async def fake_stage(*, prototype_id, checkpoint_id, files, sub_prefix=None):
        return bundle_url

    monkeypatch.setattr(env.routes, "vite_build", fake_vite)
    monkeypatch.setattr(env.routes, "stage_bundle", fake_stage)
    monkeypatch.setattr(env.routes, "reconcile_comments_on_checkpoint", lambda **k: None)


# ═══════════════════════════════════════════════════════════════════════════
# Layer 1 — advance_current_checkpoint helper (db/prototypes.py)
# ═══════════════════════════════════════════════════════════════════════════


def test_advance_current_checkpoint_updates_bundle_url(env):
    # AC2: advance sets current_checkpoint_id + bundle_url on the row.
    pid = _seed_ready(env, current_checkpoint_id=None)
    row = env.proto.advance_current_checkpoint(
        prototype_id=pid, workspace_id="app",
        checkpoint_id=42, bundle_url="https://bundle/new",
    )
    assert row is not None
    assert row["current_checkpoint_id"] == 42
    assert row["bundle_url"] == "https://bundle/new"


def test_advance_workspace_filtered(env):
    # AC2: a 'demo' call on an 'app' prototype is a no-op (returns None) and does
    # NOT mutate the 'app' row.
    pid = _seed_ready(env, workspace_id="app", current_checkpoint_id=7,
                      bundle_url="https://bundle/original")
    result = env.proto.advance_current_checkpoint(
        prototype_id=pid, workspace_id="demo",
        checkpoint_id=99, bundle_url="https://bundle/leaked",
    )
    assert result is None  # no row in 'demo' workspace
    # The 'app' row is untouched.
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["current_checkpoint_id"] == 7
    assert row["bundle_url"] == "https://bundle/original"


def test_advance_does_not_rotate_share_token(env):
    # AC3 (helper-level F7): advance leaves share_token / share_mode /
    # share_passcode_hash byte-identical.
    pid = _seed_ready(env, current_checkpoint_id=1)
    shared = env.proto.set_share_config(prototype_id=pid, workspace_id="app", share_mode="public")
    token_before = shared["share_token"]
    assert token_before  # a public share minted a token

    env.proto.advance_current_checkpoint(
        prototype_id=pid, workspace_id="app",
        checkpoint_id=2, bundle_url="https://bundle/iterated",
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["share_token"] == token_before          # byte-identical (F7)
    assert row["share_mode"] == "public"
    assert row["share_passcode_hash"] is None


def test_advance_logs_checkpoint_advanced(env, caplog):
    # AC5: identifiers-only INFO line; the bundle_url (a storage path) is NOT logged.
    pid = _seed_ready(env, current_checkpoint_id=None)
    with caplog.at_level(logging.INFO):
        env.proto.advance_current_checkpoint(
            prototype_id=pid, workspace_id="app",
            checkpoint_id=314, bundle_url="https://bundle/secret-path",
        )
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert f"prototype_checkpoint_advanced prototype_id={pid} checkpoint_id=314" in blob
    assert "https://bundle/secret-path" not in blob  # Rule #24: no storage path in logs


def test_old_checkpoint_rows_retained(env):
    # AC4: the chain is forward-only — advancing to B does NOT delete A's row.
    pid = _seed_ready(env, current_checkpoint_id=None)
    ckpt_a = _make_checkpoint(env, pid, bundle_url="https://a")
    env.proto.advance_current_checkpoint(
        prototype_id=pid, workspace_id="app", checkpoint_id=ckpt_a, bundle_url="https://a")
    ckpt_b = _make_checkpoint(env, pid, bundle_url="https://b")
    env.proto.advance_current_checkpoint(
        prototype_id=pid, workspace_id="app", checkpoint_id=ckpt_b, bundle_url="https://b")

    assert ckpt_a != ckpt_b
    assert _checkpoint_count(pid) == 2  # both rows retained
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["current_checkpoint_id"] == ckpt_b  # points at the newest


def test_generate_path_still_sets_current_checkpoint(env):
    # AC7 (non-breakage): the GENERATE path (complete_prototype) still sets
    # current_checkpoint_id — P3-12 did not touch complete_prototype.
    pid = env.proto.start_prototype(prd_id=2, workspace_id="app", template_version=1)
    ckpt = _make_checkpoint(env, pid, bundle_url="https://gen")
    env.proto.complete_prototype(
        prototype_id=pid, workspace_id="app",
        bundle_url="https://gen", current_checkpoint_id=ckpt,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["current_checkpoint_id"] == ckpt
    assert row["status"] == "ready"


# ═══════════════════════════════════════════════════════════════════════════
# Layer 2 — iterate staging path + public resolver (routes)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_iterate_creates_new_checkpoint_and_advances_current(env, monkeypatch):
    # AC1: generate (ckpt A, current=A) → iterate (ckpt B) → current == B, both rows exist.
    _stub_staging(env, monkeypatch)
    pid = _seed_ready(env, current_checkpoint_id=None)
    ckpt_a = _make_checkpoint(env, pid, bundle_url="https://bundle/original")
    env.proto.complete_prototype(
        prototype_id=pid, workspace_id="app",
        bundle_url="https://bundle/original", current_checkpoint_id=ckpt_a,
    )
    assert _checkpoint_count(pid) == 1

    await env.routes._stage_iterate_run(
        prototype_id=pid, workspace_id="app",
        virtual_fs={"src/App.tsx": "x"}, iterate_prompt="make it blue",
    )

    assert _checkpoint_count(pid) == 2  # a NEW checkpoint B was created
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["current_checkpoint_id"] != ckpt_a       # advanced off A
    assert row["current_checkpoint_id"] > ckpt_a        # to the newest (B)
    assert row["bundle_url"] == "https://bundle/iterated"


@pytest.mark.asyncio
async def test_iterate_does_not_rotate_share_token(env, monkeypatch):
    # AC3 (F7): across a full iterate, share_token + share_mode are byte-identical.
    _stub_staging(env, monkeypatch)
    pid = _seed_ready(env, current_checkpoint_id=None)
    shared = env.proto.set_share_config(prototype_id=pid, workspace_id="app", share_mode="public")
    token_before = shared["share_token"]
    mode_before = shared["share_mode"]

    await env.routes._stage_iterate_run(
        prototype_id=pid, workspace_id="app",
        virtual_fs={"src/App.tsx": "x"}, iterate_prompt="tweak",
    )

    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["share_token"] == token_before  # byte-identical across iterate
    assert row["share_mode"] == mode_before


@pytest.mark.asyncio
async def test_by_token_returns_new_bundle_after_iterate(env, monkeypatch):
    # AC3: the public resolver GET /by-token/{token} serves the ADVANCED bundle_url
    # on the SAME token after an iterate (stable URL, latest content).
    _stub_staging(env, monkeypatch, bundle_url="https://bundle/v2")
    pid = _seed_ready(env, current_checkpoint_id=None, bundle_url="https://bundle/v1")
    shared = env.proto.set_share_config(prototype_id=pid, workspace_id="app", share_mode="public")
    token = shared["share_token"]

    client = TestClient(env.main.app)
    before = client.get(f"/v1/design-agent/by-token/{token}")
    assert before.status_code == 200, before.text
    assert before.json()["bundle_url"] == "https://bundle/v1"

    await env.routes._stage_iterate_run(
        prototype_id=pid, workspace_id="app",
        virtual_fs={"src/App.tsx": "x"}, iterate_prompt="v2 please",
    )

    after = client.get(f"/v1/design-agent/by-token/{token}")
    assert after.status_code == 200, after.text
    assert after.json()["bundle_url"] == "https://bundle/v2"  # SAME token, new bundle
