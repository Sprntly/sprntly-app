"""Tests for app.design_agent.storage + the _run_generation_bg success hook (P1-08).

Three test layers:

1. **Pure storage units** (always run): filesystem staging, Supabase staging
   (client mocked), entry-point detection, content-types, and `vite_build`
   orchestration with the `vite build` subprocess MOCKED. These need no DB and
   no Node toolchain.
2. **Real-build integration** (`@pytest.mark.integration`, skipped when
   `prototype-runtime/node_modules` or `npx` is absent): runs the actual
   `npx vite build` so the P0-02 anchor-id plugin runs over agent TSX — the
   AD4-load-bearing assertion that closes the P1-11 gap. Skips cleanly in a
   Python-only CI / a dev env without the runtime installed (per the P1-08
   dispatch note that the dev env may be mid-migration).
3. **Route hook** (fake Supabase, mirrors test_design_agent_routes.py): drives
   `_run_generation_bg` / `_stage_complete_run` to prove the
   vite_build → checkpoint → stage_bundle → complete_prototype state machine and
   every failure branch.

Settings are patched on the SAME `storage.settings` reference the module holds
(`from app.config import settings`), so the unit layer is robust to module-reload
ordering with the fixture-based route layer.
"""
from __future__ import annotations

import importlib
import logging
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import app.design_agent.storage as storage
from app.design_agent.storage import ViteBuildError

# ─── shared helpers ──────────────────────────────────────────────────────────


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


# ─── Filesystem staging (AC #4, #6) ──────────────────────────────────────────


async def test_stage_bundle_filesystem_writes_files(monkeypatch, tmp_path):
    _fs_settings(monkeypatch, tmp_path)
    await storage.stage_bundle(
        prototype_id=3, checkpoint_id=9,
        files={"index.html": "<html>x</html>", "assets/main.js": "console.log(1)"},
    )
    base = tmp_path / "prototypes" / "3" / "9"
    assert (base / "index.html").read_text() == "<html>x</html>"
    assert (base / "assets" / "main.js").read_text() == "console.log(1)"


async def test_stage_bundle_filesystem_returns_public_url(monkeypatch, tmp_path):
    _fs_settings(monkeypatch, tmp_path, public_url="https://x.example/")
    url = await storage.stage_bundle(
        prototype_id=3, checkpoint_id=9, files={"index.html": "<html></html>"},
    )
    assert url == "https://x.example/prototypes/3/9/index.html"


async def test_stage_bundle_filesystem_returns_file_uri_when_no_public_url(monkeypatch, tmp_path):
    _fs_settings(monkeypatch, tmp_path, public_url="")
    url = await storage.stage_bundle(
        prototype_id=1, checkpoint_id=2, files={"index.html": "<html></html>"},
    )
    assert url.startswith("file://")
    assert url.endswith("/prototypes/1/2/index.html")


async def test_filesystem_creates_nested_directories(monkeypatch, tmp_path):
    _fs_settings(monkeypatch, tmp_path)
    await storage.stage_bundle(
        prototype_id=5, checkpoint_id=6,
        files={"src/components/Card.tsx": "export const Card = () => null;"},
    )
    assert (tmp_path / "prototypes" / "5" / "6" / "src" / "components" / "Card.tsx").exists()


async def test_stage_bundle_handles_unicode_file_content(monkeypatch, tmp_path):
    _fs_settings(monkeypatch, tmp_path)
    content = "// 🎨 café — naïve façade\nexport default 1;"
    await storage.stage_bundle(
        prototype_id=7, checkpoint_id=8, files={"index.html": "<x/>", "src/x.ts": content},
    )
    assert (tmp_path / "prototypes" / "7" / "8" / "src" / "x.ts").read_text(encoding="utf-8") == content


# ─── Supabase staging — mocked (AC #5, #14, #15) ─────────────────────────────


async def test_stage_bundle_supabase_uploads_each_file(monkeypatch):
    sb = _mock_supabase(monkeypatch)
    files = {"index.html": "<html></html>", "assets/main.js": "x", "assets/style.css": "y"}
    await storage.stage_bundle(prototype_id=4, checkpoint_id=2, files=files)
    assert sb.upload.call_count == 3
    uploaded_paths = {c.kwargs["path"] for c in sb.upload.call_args_list}
    assert uploaded_paths == {
        "prototypes/4/2/index.html",
        "prototypes/4/2/assets/main.js",
        "prototypes/4/2/assets/style.css",
    }


async def test_stage_bundle_supabase_returns_signed_url(monkeypatch):
    _mock_supabase(monkeypatch, signed={"signedURL": "https://signed.example/abc"})
    url = await storage.stage_bundle(
        prototype_id=4, checkpoint_id=2, files={"index.html": "<html></html>"},
    )
    assert url == "https://signed.example/abc"


@pytest.mark.parametrize("key", ["signedURL", "signed_url", "signedUrl"])
async def test_stage_bundle_supabase_supports_alternate_key_shapes(monkeypatch, key):
    _mock_supabase(monkeypatch, signed={key: "https://signed.example/shape"})
    url = await storage.stage_bundle(
        prototype_id=1, checkpoint_id=1, files={"index.html": "<html></html>"},
    )
    assert url == "https://signed.example/shape"


async def test_stage_bundle_supabase_sets_correct_content_types(monkeypatch):
    sb = _mock_supabase(monkeypatch)
    await storage.stage_bundle(
        prototype_id=4, checkpoint_id=2,
        files={"index.html": "<x/>", "assets/m.js": "x", "weird.bin": "z"},
    )
    by_path = {
        c.kwargs["path"]: c.kwargs["file_options"]["content-type"]
        for c in sb.upload.call_args_list
    }
    assert by_path["prototypes/4/2/index.html"].startswith("text/html")
    assert by_path["prototypes/4/2/assets/m.js"].startswith("application/javascript")
    assert by_path["prototypes/4/2/weird.bin"] == "application/octet-stream"


async def test_stage_bundle_supabase_uses_24h_signed_url_ttl(monkeypatch):
    sb = _mock_supabase(monkeypatch)
    await storage.stage_bundle(
        prototype_id=4, checkpoint_id=2, files={"index.html": "<html></html>"},
    )
    assert sb.create_signed_url.call_args.kwargs["expires_in"] == 86400


async def test_supabase_upload_called_with_upsert_true(monkeypatch):
    sb = _mock_supabase(monkeypatch)
    await storage.stage_bundle(
        prototype_id=4, checkpoint_id=2, files={"index.html": "<html></html>"},
    )
    assert sb.upload.call_args.kwargs["file_options"]["upsert"] == "true"


def test_content_type_table():
    assert storage._content_type("a.html").startswith("text/html")
    assert storage._content_type("a.css").startswith("text/css")
    assert storage._content_type("a.js").startswith("application/javascript")
    assert storage._content_type("a.json").startswith("application/json")
    assert storage._content_type("a.svg") == "image/svg+xml"
    assert storage._content_type("a.unknownext") == "application/octet-stream"


# ─── Entry-point detection (AC #8) ───────────────────────────────────────────


async def test_entry_prefers_index_html(monkeypatch, tmp_path):
    _fs_settings(monkeypatch, tmp_path)
    url = await storage.stage_bundle(
        prototype_id=1, checkpoint_id=1,
        files={"src/App.tsx": "x", "index.html": "<x/>"},  # index.html not first
    )
    assert url.endswith("/index.html")


async def test_entry_falls_back_to_first_file_when_no_index_html(monkeypatch, tmp_path):
    _fs_settings(monkeypatch, tmp_path)
    url = await storage.stage_bundle(
        prototype_id=1, checkpoint_id=1, files={"main.js": "x"},
    )
    assert url.endswith("/main.js")


# ─── Error handling (AC #7) ──────────────────────────────────────────────────


async def test_stage_bundle_empty_files_raises_value_error(monkeypatch, tmp_path):
    _fs_settings(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        await storage.stage_bundle(prototype_id=1, checkpoint_id=1, files={})


# ─── Observability (AC #16) ──────────────────────────────────────────────────


async def test_bundle_staged_log_emitted_with_identifiers_only(monkeypatch, tmp_path, caplog):
    _fs_settings(monkeypatch, tmp_path)
    secret = "SECRET_BUNDLE_CONTENT_xyz"
    with caplog.at_level(logging.INFO):
        await storage.stage_bundle(
            prototype_id=11, checkpoint_id=22, files={"index.html": secret},
        )
    recs = [r for r in caplog.records if r.getMessage().startswith("bundle_staged")]
    assert len(recs) == 1
    msg = recs[0].getMessage()
    for token in ("prototype_id=11", "checkpoint_id=22", "backend=filesystem",
                  "entry=index.html", "file_count=1"):
        assert token in msg, f"missing {token!r}"
    assert secret not in msg  # no file content in logs


# ─── vite_build orchestration — subprocess MOCKED (AC #2, #3) ────────────────


def _fake_vite_run(*, dist_files=None, returncode=0, stderr="", capture=None):
    """Build a subprocess.run replacement that fabricates a dist/ dir in cwd."""
    def _run(cmd, cwd=None, **kwargs):
        if capture is not None:
            capture["cmd"] = cmd
            capture["cwd"] = cwd
            capture["node_modules_is_symlink"] = (Path(cwd) / "node_modules").is_symlink()
        if returncode == 0:
            dist = Path(cwd) / "dist"
            dist.mkdir(parents=True, exist_ok=True)
            for rel, content in (dist_files or {"index.html": "<html>built</html>"}).items():
                p = dist / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)
        return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)
    return _run


async def test_vite_build_reads_dist_after_successful_build(monkeypatch):
    monkeypatch.setattr(
        storage.subprocess, "run",
        _fake_vite_run(dist_files={"index.html": "<html>built</html>",
                                   "assets/index.js": "console.log(1)"}),
    )
    out = await storage.vite_build({"src/App.tsx": "export default () => null;"})
    assert out["index.html"] == "<html>built</html>"
    assert out["assets/index.js"] == "console.log(1)"


async def test_vite_build_symlinks_node_modules_not_install(monkeypatch):
    capture: dict = {}
    monkeypatch.setattr(storage.subprocess, "run", _fake_vite_run(capture=capture))
    await storage.vite_build({"src/App.tsx": "export default () => null;"})
    # node_modules is symlinked from the scaffold (installed) — never npm install.
    assert capture["node_modules_is_symlink"] is True
    # The only subprocess invoked is the build itself (no `npm install`).
    assert capture["cmd"][:3] == ["npx", "vite", "build"]


async def test_vite_build_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        storage.subprocess, "run",
        _fake_vite_run(returncode=1, stderr="SyntaxError: Unexpected token (3:5)"),
    )
    with pytest.raises(ViteBuildError) as ei:
        await storage.vite_build({"src/App.tsx": "broken"})
    assert "exit=1" in str(ei.value)
    assert "SyntaxError" in str(ei.value)


async def test_vite_build_raises_on_timeout(monkeypatch):
    def _timeout(cmd, cwd=None, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=60)
    monkeypatch.setattr(storage.subprocess, "run", _timeout)
    with pytest.raises(ViteBuildError) as ei:
        await storage.vite_build({"src/App.tsx": "x"})
    assert "timed out" in str(ei.value)


async def test_vite_build_raises_when_dist_not_produced(monkeypatch):
    def _no_dist(cmd, cwd=None, **kwargs):
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(storage.subprocess, "run", _no_dist)
    with pytest.raises(ViteBuildError) as ei:
        await storage.vite_build({"src/App.tsx": "x"})
    assert "dist/ was not produced" in str(ei.value)


async def test_vite_build_raises_when_prototype_runtime_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "_RUNTIME_ROOT", tmp_path / "nonexistent-runtime")
    with pytest.raises(FileNotFoundError):
        await storage.vite_build({"src/App.tsx": "x"})


# ─── vite_build REAL build — integration (AC #1, #2, #3) ─────────────────────

_HAS_TOOLCHAIN = (storage._RUNTIME_ROOT / "node_modules").exists() and shutil.which("npx") is not None
_skip_no_toolchain = pytest.mark.skipif(
    not _HAS_TOOLCHAIN,
    reason="prototype-runtime/node_modules or npx absent (dev env not provisioned)",
)


@pytest.mark.integration
@_skip_no_toolchain
async def test_vite_build_emits_dist_with_index_html_integration():
    out = await storage.vite_build({
        "src/App.tsx": "export default function App(){return <div><button>Submit</button></div>;}",
    })
    assert "index.html" in out
    # The bundled JS chunk is present too.
    assert any(k.endswith(".js") for k in out), out.keys()


@pytest.mark.integration
@_skip_no_toolchain
async def test_vite_build_applies_anchor_id_plugin_integration():
    """AD4 load-bearing: the P0-02 plugin annotates JSX with an 8-hex anchor id.

    After build the attribute lands in the compiled JS chunk (not index.html),
    so we scan the whole dist blob.
    """
    out = await storage.vite_build({
        "src/App.tsx": "export default function App(){return <div><button>Submit</button></div>;}",
    })
    blob = "\n".join(out.values())
    assert re.search(r'data-anchor-id["\s:=]+["\'][0-9a-f]{8}', blob), \
        "no data-anchor-id=<8-hex> in built output — anchor-id plugin did not run"


@pytest.mark.integration
@_skip_no_toolchain
async def test_vite_build_raises_on_syntax_error_integration():
    with pytest.raises(ViteBuildError) as ei:
        await storage.vite_build({"src/App.tsx": "export default function App(){return <button>unclosed;}"})
    assert "exit=" in str(ei.value)


# ─── Route hook — fake Supabase DB (AC #9-#13, #16) ──────────────────────────

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


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """Fake-Supabase DB + design-agent route module reloaded in dependency order."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    # prompt_history / comment_state are jsonb in Postgres — register them so the
    # fake JSON-encodes the lists create_checkpoint passes (mirrors test_db_prototypes.py).
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


def _seed_prd(db_mod, body: str = "# PRD body") -> int:
    prd_id = db_mod.start_prd(brief_id=1, insight_index=0, title="t", template_version=1, variant="v2")
    db_mod.complete_prd(prd_id, title="t", md=body)
    return prd_id


def _stub_generate(monkeypatch, routes_mod, *, status="complete", iters=1, virtual_fs=None):
    async def _fake(**kwargs):
        return SimpleNamespace(status=status, iters=iters), (virtual_fs or {})
    monkeypatch.setattr(routes_mod, "generate_prototype", _fake)


def _async_return(value):
    async def _f(*args, **kwargs):
        return value
    return _f


def _async_raise(exc):
    async def _f(*args, **kwargs):
        raise exc
    return _f


def _checkpoints_for(prototype_id: int):
    from tests import _fake_supabase
    return _fake_supabase.get_fake_db().execute(
        f"SELECT id, bundle_url FROM prototype_checkpoints WHERE prototype_id = {prototype_id}"
    ).fetchall()


async def test_run_generation_bg_marks_failed_when_agent_emits_no_files(env, monkeypatch):
    _stub_generate(monkeypatch, env.routes, status="complete", virtual_fs={})
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "failed"
    assert row["error"] == "agent_loop completed but emitted no files"


async def test_run_generation_bg_marks_failed_on_max_iters(env, monkeypatch):
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
    assert "iters=8" in row["error"]


async def test_run_generation_bg_marks_failed_on_stage_bundle_exception(env, monkeypatch):
    _stub_generate(monkeypatch, env.routes, status="complete", virtual_fs={"src/App.tsx": "x"})
    monkeypatch.setattr(env.routes, "vite_build", _async_return({"index.html": "<html></html>"}))
    monkeypatch.setattr(env.routes, "stage_bundle", _async_raise(RuntimeError("boom")))
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "failed"
    assert row["error"].startswith("RuntimeError: boom")
    # AC #13: checkpoint row exists (created before staging) but bundle_url stays NULL.
    cps = _checkpoints_for(pid)
    assert len(cps) == 1
    assert cps[0]["bundle_url"] is None


async def test_route_complete_path_creates_checkpoint_and_marks_ready(env, monkeypatch):
    _stub_generate(monkeypatch, env.routes, status="complete", virtual_fs={"src/App.tsx": "x"})
    monkeypatch.setattr(
        env.routes, "vite_build",
        _async_return({"index.html": '<html><body><div data-anchor-id="abcd1234"></div></body></html>'}),
    )
    monkeypatch.setattr(env.routes, "stage_bundle", _async_return("https://x.example/prototypes/1/1/index.html"))
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "ready"
    assert row["bundle_url"] == "https://x.example/prototypes/1/1/index.html"
    cps = _checkpoints_for(pid)
    assert len(cps) == 1
    assert row["current_checkpoint_id"] == cps[0]["id"]


async def test_route_hook_runs_vite_build_before_stage_bundle(env, monkeypatch):
    """AC ordering: vite_build(virtual_fs) → create_checkpoint → stage_bundle(dist).

    stage_bundle must receive the BUILT dist files, never the raw virtual_fs.
    """
    calls: list = []
    dist = {"index.html": "<html>built</html>"}
    vfs = {"src/App.tsx": "raw-source"}

    async def _vite(virtual_fs):
        calls.append(("vite_build", virtual_fs))
        return dist

    async def _stage(*, prototype_id, checkpoint_id, files):
        calls.append(("stage_bundle", files))
        return "https://x/index.html"

    _stub_generate(monkeypatch, env.routes, status="complete", virtual_fs=vfs)
    monkeypatch.setattr(env.routes, "vite_build", _vite)
    monkeypatch.setattr(env.routes, "stage_bundle", _stage)
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
    )
    assert [c[0] for c in calls] == ["vite_build", "stage_bundle"]
    assert calls[0][1] == vfs               # vite_build got the raw source
    assert calls[1][1] == dist              # stage_bundle got the BUILT dist, not vfs


async def test_route_hook_marks_failed_on_vite_build_error(env, monkeypatch):
    """AC #12: vite_build error → failed; NO checkpoint; stage_bundle NOT called."""
    stage_mock = MagicMock()
    _stub_generate(monkeypatch, env.routes, status="complete", virtual_fs={"src/App.tsx": "x"})
    monkeypatch.setattr(env.routes, "vite_build", _async_raise(ViteBuildError("vite build exit=1: SyntaxError")))
    monkeypatch.setattr(env.routes, "stage_bundle", stage_mock)
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "failed"
    assert row["error"] == "ViteBuildError: vite build exit=1: SyntaxError"
    assert _checkpoints_for(pid) == []      # no checkpoint created
    assert stage_mock.call_count == 0       # staging never attempted


async def test_stage_complete_run_emits_observability_logs(env, monkeypatch, caplog):
    """AC #16: vite_build_succeeded on success, vite_build_failed on build error."""
    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)

    # Success → vite_build_succeeded (with dist_file_count), no stderr.
    monkeypatch.setattr(env.routes, "vite_build", _async_return({"index.html": "<x/>"}))
    monkeypatch.setattr(env.routes, "stage_bundle", _async_return("file:///x/index.html"))
    with caplog.at_level(logging.INFO):
        await env.routes._stage_complete_run(prototype_id=pid, workspace_id="app", virtual_fs={"a": "b"})
    succeeded = [r.getMessage() for r in caplog.records if r.getMessage().startswith("vite_build_succeeded")]
    assert succeeded and "dist_file_count=1" in succeeded[0]

    # Failure → vite_build_failed with error_class only (no stderr in the log line).
    caplog.clear()
    pid2 = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    monkeypatch.setattr(env.routes, "vite_build", _async_raise(ViteBuildError("vite build exit=1: secret-stderr-blob")))
    with caplog.at_level(logging.WARNING):
        await env.routes._stage_complete_run(prototype_id=pid2, workspace_id="app", virtual_fs={"a": "b"})
    failed = [r.getMessage() for r in caplog.records if r.getMessage().startswith("vite_build_failed")]
    assert failed and "error_class=ViteBuildError" in failed[0]
    assert "secret-stderr-blob" not in failed[0]  # stderr never in the log line
