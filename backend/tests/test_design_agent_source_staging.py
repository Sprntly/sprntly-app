"""Tests for the P2-04 source-staging path: stage raw `virtual_fs` alongside the
built `dist/` under a `_source/` sub-prefix, plus the readback helper the P2-08
markdown serialiser consumes.

Three layers, mirroring `test_design_agent_storage.py`:

1. **Pure storage units** (filesystem path + Supabase path with the client
   mocked): `stage_bundle(..., sub_prefix=...)` write layout, the
   `read_source_files_for_checkpoint` round-trip, and graceful-empty behaviour.
2. **Backward-compat**: `stage_bundle` without `sub_prefix` keeps the pre-P2-04
   layout (`prototypes/<pid>/<cid>/<file>` — no `_source/`, no trailing slash).
3. **Route hook** (fake Supabase DB, mirrors the storage-test `env` fixture):
   proves the Step-3.5 source-stage block is BEST-EFFORT — a source-stage failure
   logs a warning and the prototype still completes `ready` (dist/ is the
   load-bearing artefact).

Settings are patched on the SAME `storage.settings` reference the module holds,
matching `test_design_agent_storage.py`.
"""
from __future__ import annotations

import importlib
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import app.design_agent.storage as storage

# ─── shared helpers (mirror test_design_agent_storage.py) ─────────────────────


def _no_bucket(monkeypatch) -> None:
    monkeypatch.delenv("SUPABASE_STORAGE_BUCKET", raising=False)


def _fs_settings(monkeypatch, tmp_path: Path, *, public_url: str = "") -> None:
    """Point storage at tmp_path for the filesystem fallback path."""
    _no_bucket(monkeypatch)
    monkeypatch.setattr(storage.settings, "storage_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(storage.settings, "storage_public_url", public_url, raising=False)


def _mock_supabase(monkeypatch, *, signed=None) -> MagicMock:
    """Wire SUPABASE_STORAGE_BUCKET + a mock storage client; return the .from_() mock."""
    monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", "proto-bundles")
    storage_obj = MagicMock()
    storage_obj.create_signed_url.return_value = (
        signed if signed is not None else {"signedURL": "https://signed.example/index.html"}
    )
    client = MagicMock()
    client.storage.from_.return_value = storage_obj
    import app.db.client as db_client_mod

    monkeypatch.setattr(db_client_mod, "require_client", lambda: client)
    return storage_obj


# ─── Creation: sub_prefix write layout (AC #5, #6) ───────────────────────────


async def test_stage_bundle_with_sub_prefix_writes_under_subdir(monkeypatch, tmp_path):
    """AC #6: sub_prefix='_source' → file lands at prototypes/<pid>/<cid>/_source/<file>."""
    _fs_settings(monkeypatch, tmp_path)
    await storage.stage_bundle(
        prototype_id=3, checkpoint_id=9,
        files={"src/App.tsx": "export default () => null;"},
        sub_prefix="_source",
    )
    target = tmp_path / "prototypes" / "3" / "9" / "_source" / "src" / "App.tsx"
    assert target.read_text() == "export default () => null;"


async def test_stage_bundle_without_sub_prefix_unchanged(monkeypatch, tmp_path):
    """AC #5: no sub_prefix → file lands at prototypes/<pid>/<cid>/<file> (no _source/)."""
    _fs_settings(monkeypatch, tmp_path)
    await storage.stage_bundle(
        prototype_id=3, checkpoint_id=9, files={"index.html": "<html></html>"},
    )
    base = tmp_path / "prototypes" / "3" / "9"
    assert (base / "index.html").read_text() == "<html></html>"
    assert not (base / "_source").exists()


async def test_stage_bundle_sub_prefix_url_has_no_trailing_slash(monkeypatch, tmp_path):
    """AC #5: sub_prefix=None preserves the exact path layout (no trailing slash)."""
    _fs_settings(monkeypatch, tmp_path, public_url="https://x.example/")
    url = await storage.stage_bundle(
        prototype_id=1, checkpoint_id=2, files={"index.html": "<html></html>"},
    )
    assert url == "https://x.example/prototypes/1/2/index.html"


async def test_stage_bundle_supabase_sub_prefix_in_upload_path(monkeypatch):
    """AC #6 (Supabase backend): every uploaded object path includes the _source/ segment."""
    sb = _mock_supabase(monkeypatch)
    await storage.stage_bundle(
        prototype_id=4, checkpoint_id=2,
        files={"src/App.tsx": "x", "src/index.css": "y"},
        sub_prefix="_source",
    )
    uploaded = {c.kwargs["path"] for c in sb.upload.call_args_list}
    assert uploaded == {
        "prototypes/4/2/_source/src/App.tsx",
        "prototypes/4/2/_source/src/index.css",
    }


# ─── Round-trip: stage → read (AC #2) ────────────────────────────────────────


async def test_round_trip_virtual_fs_through_source_stage_and_read(monkeypatch, tmp_path):
    """AC #2: read_source_files_for_checkpoint returns the staged dict byte-for-byte."""
    _fs_settings(monkeypatch, tmp_path)
    virtual_fs = {
        "src/App.tsx": "export default function App(){ return <div/>; }",
        "src/index.css": "body { margin: 0; }\n",
        "package.json": '{"name":"proto"}',
    }
    await storage.stage_bundle(
        prototype_id=12, checkpoint_id=34, files=virtual_fs, sub_prefix="_source",
    )
    out = await storage.read_source_files_for_checkpoint(12, 34)
    assert out == virtual_fs


async def test_round_trip_preserves_unicode(monkeypatch, tmp_path):
    """AC #2: non-ASCII source content survives the round-trip unchanged."""
    _fs_settings(monkeypatch, tmp_path)
    virtual_fs = {"src/x.ts": "// 🎨 café — naïve façade\nexport default 1;"}
    await storage.stage_bundle(
        prototype_id=7, checkpoint_id=8, files=virtual_fs, sub_prefix="_source",
    )
    out = await storage.read_source_files_for_checkpoint(7, 8)
    assert out == virtual_fs


async def test_round_trip_supabase_flat_files(monkeypatch):
    """AC #2 (Supabase backend): flat-file round-trip through the mocked storage client.

    Supabase Storage `list(prefix)` is non-recursive, so the readback covers the
    flat (top-level) case here; nested-path recursion is exercised on the
    filesystem path above (the round-trip AC's tested backend per the ticket).
    """
    uploaded: dict[str, bytes] = {}
    sb = _mock_supabase(monkeypatch)

    def _upload(*, path, file, file_options):
        uploaded[path] = file

    sb.upload.side_effect = _upload
    sb.list.side_effect = lambda prefix: [
        {"name": p.rsplit("/", 1)[-1]} for p in uploaded if p.startswith(prefix)
    ]
    sb.download.side_effect = lambda path: uploaded[path]

    virtual_fs = {"App.tsx": "export default () => null;", "index.css": "body{}"}
    await storage.stage_bundle(
        prototype_id=2, checkpoint_id=5, files=virtual_fs, sub_prefix="_source",
    )
    out = await storage.read_source_files_for_checkpoint(2, 5)
    assert out == virtual_fs


# ─── Empty / missing (AC #3) ─────────────────────────────────────────────────


async def test_read_source_returns_empty_when_no_source_subprefix(monkeypatch, tmp_path):
    """AC #3: never-staged (historical / pre-P2-04) checkpoint → {}."""
    _fs_settings(monkeypatch, tmp_path)
    out = await storage.read_source_files_for_checkpoint(99, 99)
    assert out == {}


async def test_read_source_returns_empty_when_supabase_list_fails(monkeypatch):
    """AC #3: a Supabase list() error degrades to {} (best-effort readback)."""
    sb = _mock_supabase(monkeypatch)
    sb.list.side_effect = RuntimeError("bucket unreachable")
    out = await storage.read_source_files_for_checkpoint(1, 1)
    assert out == {}


# ─── Backward compatibility (AC #7) ──────────────────────────────────────────


async def test_existing_callers_of_stage_bundle_unchanged(monkeypatch, tmp_path):
    """AC #7: an existing caller (no sub_prefix) produces the pre-P2-04 layout + URL."""
    _fs_settings(monkeypatch, tmp_path, public_url="https://x.example/")
    url = await storage.stage_bundle(
        prototype_id=5, checkpoint_id=6,
        files={"index.html": "<x/>", "assets/main.js": "console.log(1)"},
    )
    base = tmp_path / "prototypes" / "5" / "6"
    assert (base / "index.html").exists()
    assert (base / "assets" / "main.js").exists()
    assert not (base / "_source").exists()
    assert url == "https://x.example/prototypes/5/6/index.html"


# ─── Observability (AC #9) ───────────────────────────────────────────────────


async def test_bundle_staged_log_includes_sub_prefix(monkeypatch, tmp_path, caplog):
    """AC #9: source-stage success emits bundle_staged ... sub_prefix=_source, no content."""
    _fs_settings(monkeypatch, tmp_path)
    secret = "SECRET_SOURCE_CONTENT_xyz"
    with caplog.at_level(logging.INFO):
        await storage.stage_bundle(
            prototype_id=11, checkpoint_id=22,
            files={"src/App.tsx": secret}, sub_prefix="_source",
        )
    recs = [r for r in caplog.records if r.getMessage().startswith("bundle_staged")]
    assert len(recs) == 1
    msg = recs[0].getMessage()
    assert "sub_prefix=_source" in msg
    assert "prototype_id=11" in msg and "checkpoint_id=22" in msg
    assert secret not in msg  # no file content in logs


# ─── Route hook — best-effort source staging (AC #4, #8, #9) ─────────────────

_PROTOTYPE_DDL = """
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
    """Fake-Supabase DB + design-agent route module reloaded in dependency order."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    monkeypatch.setitem(
        _fake_supabase._JSONB_COLUMNS, "prototype_checkpoints",
        {"prompt_history", "comment_state"},
    )
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    _no_bucket(monkeypatch)  # route hook stages to filesystem unless a test mocks stage_bundle

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)

    import app.db as db_mod
    return SimpleNamespace(proto=proto_mod, routes=routes_mod, db=db_mod)


def _async_return(value):
    async def _f(*args, **kwargs):
        return value
    return _f


def _checkpoints_for(prototype_id: int):
    from tests import _fake_supabase
    return _fake_supabase.get_fake_db().execute(
        f"SELECT id, bundle_url FROM prototype_checkpoints WHERE prototype_id = {prototype_id}"
    ).fetchall()


def _stage_bundle_source_raises(exc: Exception):
    """stage_bundle stub: succeed for the dist/ call (no sub_prefix), raise for _source."""
    async def _stage(*, prototype_id, checkpoint_id, files, sub_prefix=None):
        if sub_prefix == "_source":
            raise exc
        return "https://x.example/prototypes/1/1/index.html"
    return _stage


async def test_stage_complete_run_marks_ready_when_source_stage_fails(env, monkeypatch):
    """AC #4: a source-stage failure logs and proceeds — the prototype still completes ready."""
    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    monkeypatch.setattr(env.routes, "vite_build", _async_return({"index.html": "<x/>"}))
    # P6-07: _stage_complete_run builds via vite_build_with_repair → (dist, repaired_vfs).
    monkeypatch.setattr(
        env.routes, "vite_build_with_repair",
        _async_return(({"index.html": "<x/>"}, {"src/App.tsx": "x"})),
    )
    monkeypatch.setattr(
        env.routes, "stage_bundle", _stage_bundle_source_raises(RuntimeError("source boom")),
    )
    await env.routes._stage_complete_run(
        prototype_id=pid, workspace_id="app", virtual_fs={"src/App.tsx": "x"},
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "ready"
    assert f"/_da-bundle/v1/design-agent/{pid}/bundle/index.html" in row["bundle_url"]
    # The dist/ checkpoint still exists and is threaded back.
    cps = _checkpoints_for(pid)
    assert len(cps) == 1
    assert row["current_checkpoint_id"] == cps[0]["id"]


async def test_stage_complete_run_logs_warning_on_source_stage_failure(env, monkeypatch, caplog):
    """AC #9: failure emits source_stage_failed WARNING with identifiers + error_class only."""
    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    monkeypatch.setattr(env.routes, "vite_build", _async_return({"index.html": "<x/>"}))
    # P6-07: _stage_complete_run builds via vite_build_with_repair → (dist, repaired_vfs).
    monkeypatch.setattr(
        env.routes, "vite_build_with_repair",
        _async_return(({"index.html": "<x/>"}, {"src/App.tsx": "x"})),
    )
    monkeypatch.setattr(
        env.routes, "stage_bundle",
        _stage_bundle_source_raises(RuntimeError("SECRET_SOURCE_blob")),
    )
    with caplog.at_level(logging.WARNING):
        await env.routes._stage_complete_run(
            prototype_id=pid, workspace_id="app", virtual_fs={"src/App.tsx": "x"},
        )
    warns = [r.getMessage() for r in caplog.records if r.getMessage().startswith("source_stage_failed")]
    assert len(warns) == 1
    msg = warns[0]
    assert f"prototype_id={pid}" in msg
    assert "error_class=RuntimeError" in msg
    assert "SECRET_SOURCE_blob" not in msg  # error text / content never in the log line


async def test_stage_complete_run_stages_source_under_source_prefix_on_success(env, monkeypatch):
    """AC #1: on success, stage_bundle is called for dist/ AND for the _source/ sub-prefix."""
    calls: list = []

    async def _stage(*, prototype_id, checkpoint_id, files, sub_prefix=None):
        calls.append((sub_prefix, dict(files)))
        return "https://x.example/index.html"

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    monkeypatch.setattr(env.routes, "vite_build", _async_return({"index.html": "<built/>"}))

    # P6-07: _stage_complete_run builds via vite_build_with_repair → (dist, repaired_vfs);
    # a clean build returns the source unchanged (so the _source/ staging gets the raw vfs).
    async def _build_with_repair(virtual_fs):
        return {"index.html": "<built/>"}, virtual_fs

    monkeypatch.setattr(env.routes, "vite_build_with_repair", _build_with_repair)
    monkeypatch.setattr(env.routes, "stage_bundle", _stage)
    vfs = {"src/App.tsx": "raw-source"}
    await env.routes._stage_complete_run(prototype_id=pid, workspace_id="app", virtual_fs=vfs)

    sub_prefixes = [c[0] for c in calls]
    assert sub_prefixes == [None, "_source"]      # dist/ first, then source
    assert calls[0][1] == {"index.html": "<built/>"}   # dist call got the BUILT dist
    assert calls[1][1] == vfs                          # source call got the raw virtual_fs
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "ready"
