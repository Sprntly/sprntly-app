"""Tests for the preview-image backfill one-off (`python -m app.backfill_previews`).

Proves the backfill re-renders a staged bundle locally and repairs the row:
- a target row's preview_image_url is updated and the storage object is upserted
  in place (mock the staging + assert the row update + the upsert path),
- BOTH a wrong-capture row (preview already set) AND a null-preview row are
  repaired by the `all` walk,
- an honest-degrade capture (None) leaves the row untouched (never blanked),
- the backfill never raises on a bad row (one failure does not abort `all`).

Uses the same fake-Supabase DB posture as the screenshot completion-hook tests.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-screenshot-bytes"

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
    preview_image_url      TEXT,
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


@pytest.fixture
def env(isolated_settings, monkeypatch):
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    monkeypatch.setitem(
        _fake_supabase._JSONB_COLUMNS, "prototype_checkpoints",
        {"prompt_history", "comment_state"},
    )
    monkeypatch.delenv("SUPABASE_STORAGE_BUCKET", raising=False)

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.backfill_previews as backfill_mod
    importlib.reload(backfill_mod)
    return SimpleNamespace(proto=proto_mod, backfill=backfill_mod)


def _ready_row(env, *, workspace_id="app", preview=None) -> tuple[int, int]:
    """Insert a ready prototype with a checkpoint; return (prototype_id, checkpoint_id)."""
    pid = env.proto.start_prototype(prd_id=1, workspace_id=workspace_id, template_version=1)
    cid = env.proto.create_checkpoint(
        prototype_id=pid, workspace_id=workspace_id, bundle_url=None,
        prd_revision_hash=None, figma_frame_hash=None, prompt_history=[], comment_state=[],
    )
    env.proto.complete_prototype(
        prototype_id=pid, workspace_id=workspace_id,
        bundle_url="proxy://x", current_checkpoint_id=cid, preview_image_url=preview,
    )
    return pid, cid


_BUNDLE = {"index.html": "<div id='root'></div>", "assets/a.js": "1"}


def _wire_capture(env, monkeypatch, *, png=_FAKE_PNG):
    """Stub the bundle readback + capture so backfill reaches stage + row update.

    backfill_one imports these lazily from their SOURCE modules, so patch there.
    """
    import app.design_agent.screenshot as shot
    import app.design_agent.storage as storage
    monkeypatch.setattr(shot, "capture_bundle_screenshot", AsyncMock(return_value=png))
    monkeypatch.setattr(storage, "read_bundle_files_for_checkpoint", AsyncMock(return_value=dict(_BUNDLE)))


async def test_backfill_target_updates_url_and_upserts_in_place(env, monkeypatch):
    """A target re-capture updates preview_image_url AND upserts the storage object
    in place at prototypes/<pid>/<cid>/_preview/preview.png (overwrite)."""
    pid, cid = _ready_row(env, preview="https://stale.example/shell.png")
    _wire_capture(env, monkeypatch)

    # Capture the staging call so we can assert path + overwrite intent.
    import app.design_agent.storage as storage
    stage = AsyncMock(return_value="https://x/prototypes/{}/{}/_preview/preview.png".format(pid, cid))
    monkeypatch.setattr(storage, "stage_preview_image", stage)

    updated, total = await env.backfill.backfill_target(pid)
    assert (updated, total) == (1, 1)

    # The PNG was staged for this exact pid/cid (upsert overwrites in place).
    assert stage.await_count == 1
    assert stage.await_args.kwargs["prototype_id"] == pid
    assert stage.await_args.kwargs["checkpoint_id"] == cid
    assert stage.await_args.kwargs["png_bytes"] == _FAKE_PNG

    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["preview_image_url"].endswith("_preview/preview.png")
    assert "stale" not in row["preview_image_url"]   # the wrong capture was replaced


async def test_backfill_all_repairs_wrong_and_null_previews(env, monkeypatch):
    """The `all` walk repairs BOTH a wrong-capture row and a null-preview row."""
    pid_wrong, _ = _ready_row(env, preview="https://stale.example/shell.png")
    pid_null, _ = _ready_row(env, preview=None)
    _wire_capture(env, monkeypatch)
    import app.design_agent.storage as storage
    monkeypatch.setattr(
        storage, "stage_preview_image", AsyncMock(return_value="https://x/fixed.png"),
    )

    updated, total = await env.backfill.backfill_all()
    assert total == 2 and updated == 2
    for pid in (pid_wrong, pid_null):
        row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
        assert row["preview_image_url"] == "https://x/fixed.png"


async def test_backfill_honest_degrade_leaves_row_untouched(env, monkeypatch):
    """Capture returns None → the existing preview is NOT overwritten/blanked."""
    pid, _ = _ready_row(env, preview="https://existing.example/ok.png")
    _wire_capture(env, monkeypatch, png=None)
    import app.design_agent.storage as storage
    stage = AsyncMock()
    monkeypatch.setattr(storage, "stage_preview_image", stage)

    updated, total = await env.backfill.backfill_target(pid)
    assert (updated, total) == (0, 1)
    assert stage.await_count == 0                    # nothing staged when capture degrades
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["preview_image_url"] == "https://existing.example/ok.png"   # untouched


async def test_backfill_one_never_raises_on_bad_row(env, monkeypatch):
    """A capture/stage exception is swallowed (returns False), so `all` continues."""
    import app.design_agent.storage as storage
    monkeypatch.setattr(
        storage, "read_bundle_files_for_checkpoint",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    ok = await env.backfill.backfill_one(prototype_id=1, workspace_id="app", checkpoint_id=1)
    assert ok is False


async def test_backfill_skips_bundle_without_index_html(env, monkeypatch):
    """No renderable entry staged → skip (no capture, no row write)."""
    pid, _ = _ready_row(env, preview=None)
    import app.design_agent.storage as storage
    monkeypatch.setattr(
        storage, "read_bundle_files_for_checkpoint", AsyncMock(return_value={"assets/a.js": "1"}),
    )
    import app.design_agent.screenshot as shot
    capture = AsyncMock()
    monkeypatch.setattr(shot, "capture_bundle_screenshot", capture)
    updated, total = await env.backfill.backfill_target(pid)
    assert (updated, total) == (0, 1)
    assert capture.await_count == 0


async def test_list_ready_for_backfill_skips_rows_without_checkpoint(env, monkeypatch):
    """A ready row with no current_checkpoint_id is not a backfill candidate."""
    _ready_row(env, preview=None)                                  # has a checkpoint
    env.proto.start_prototype(prd_id=2, workspace_id="app", template_version=1)  # generating, no cp
    rows = env.proto.list_ready_prototypes_for_backfill()
    assert all(r["current_checkpoint_id"] is not None for r in rows)
    assert len(rows) == 1
