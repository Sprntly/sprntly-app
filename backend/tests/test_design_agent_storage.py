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
from app.design_agent.storage import TypeCheckError, ViteBuildError

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
    """Build a subprocess.run replacement that fabricates a dist/ dir in cwd.

    P3-15: `_vite_build_sync` now runs a SECOND subprocess (`tsc --noEmit`) after a
    successful vite build (the scoped runtime-break gate). This fake answers that
    `tsc` invocation with a clean, no-diagnostic result so the vite-build
    orchestration tests below stay focused on the build step — the type-check gate
    has its own dedicated tests further down. (Without this branch the fake would
    fabricate a second `dist/` and overwrite `capture` with the tsc argv.)
    """
    def _run(cmd, cwd=None, **kwargs):
        if "tsc" in cmd:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if capture is not None:
            capture["cmd"] = cmd
            capture["cwd"] = cwd
            capture["timeout"] = kwargs.get("timeout")  # P6-21 — the vite-build budget
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


@pytest.mark.integration
async def test_vite_build_symlinks_node_modules_not_install(monkeypatch):
    capture: dict = {}
    monkeypatch.setattr(storage.subprocess, "run", _fake_vite_run(capture=capture))
    await storage.vite_build({"src/App.tsx": "export default () => null;"})
    # node_modules is symlinked from the scaffold (installed) — never npm install.
    assert capture["node_modules_is_symlink"] is True
    # The build subprocess is `npx vite build` (no `npm install`). P3-15 adds a
    # second `tsc` subprocess after build; the fake answers it cleanly without
    # capturing, so `capture` still reflects the vite invocation here.
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
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 60))
    monkeypatch.setattr(storage.subprocess, "run", _timeout)
    with pytest.raises(ViteBuildError) as ei:
        await storage.vite_build({"src/App.tsx": "x"})
    assert "timed out" in str(ei.value)


# ─── P6-21: env-configurable Vite build budget (read at call-time) ────────────


async def test_build_timeout_reads_configured_value(monkeypatch):
    """AC1 (regression — FAILS on unfixed code): the timeout passed to
    subprocess.run is sourced from settings.design_agent_vite_build_timeout_seconds
    at call-time, not the old hardcoded 60. Unfixed code passes 60 → assert fails."""
    monkeypatch.setattr(
        storage.settings, "design_agent_vite_build_timeout_seconds", 90, raising=False
    )
    capture: dict = {}
    monkeypatch.setattr(storage.subprocess, "run", _fake_vite_run(capture=capture))
    await storage.vite_build({"src/App.tsx": "export default () => null;"})
    assert capture["timeout"] == 90


def test_default_build_timeout_is_120():
    """AC2: with no env override, the configured default is 120s."""
    from app.config import settings as live_settings
    assert live_settings.design_agent_vite_build_timeout_seconds == 120


def test_env_override_build_timeout(monkeypatch):
    """AC1: the field is env-overridable via DESIGN_AGENT_VITE_BUILD_TIMEOUT_SECONDS."""
    from app.config import Settings
    monkeypatch.setenv("DESIGN_AGENT_VITE_BUILD_TIMEOUT_SECONDS", "200")
    fresh = Settings()
    assert fresh.design_agent_vite_build_timeout_seconds == 200


async def test_timeout_raises_vite_build_error_with_configured_value(monkeypatch):
    """AC4: on TimeoutExpired the ViteBuildError message names the LIVE configured
    budget (not a hardcoded 60), keeping the timeout class distinguishable."""
    monkeypatch.setattr(
        storage.settings, "design_agent_vite_build_timeout_seconds", 90, raising=False
    )

    def _timeout(cmd, cwd=None, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(storage.subprocess, "run", _timeout)
    with pytest.raises(ViteBuildError) as ei:
        await storage.vite_build({"src/App.tsx": "x"})
    assert "timed out after 90s" in str(ei.value)
    # distinct from the exit-code class and the no-dist class
    assert "exit=" not in str(ei.value)


async def test_timeout_propagates_through_repair_loop(monkeypatch):
    """AC5: vite_build_with_repair re-raises a timeout ViteBuildError UNCHANGED
    (a timeout is not a 'could not resolve' class), with no repair re-attempt —
    proving the P6-07 import-repair path is untouched by P6-21."""
    monkeypatch.setattr(
        storage.settings, "design_agent_vite_build_timeout_seconds", 75, raising=False
    )
    calls = {"n": 0}

    def _timeout(cmd, cwd=None, **kwargs):
        calls["n"] += 1
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(storage.subprocess, "run", _timeout)
    with pytest.raises(ViteBuildError) as ei:
        await storage.vite_build_with_repair({"src/App.tsx": "x"})
    assert "timed out after 75s" in str(ei.value)
    # exactly one build attempt — no repair rebuild on a timeout class
    assert calls["n"] == 1


async def test_build_under_limit_succeeds(monkeypatch):
    """AC3: raising the budget does not change the success path — a build that
    completes (returncode=0) returns dist files normally regardless of the budget."""
    monkeypatch.setattr(
        storage.settings, "design_agent_vite_build_timeout_seconds", 300, raising=False
    )
    monkeypatch.setattr(
        storage.subprocess, "run",
        _fake_vite_run(dist_files={"index.html": "<html>ok</html>"}),
    )
    out = await storage.vite_build({"src/App.tsx": "export default () => null;"})
    assert out["index.html"] == "<html>ok</html>"


async def test_no_hardcoded_timeout_constant_used(monkeypatch):
    """AC6: the call-time read tracks the setting — changing the setting changes
    the subprocess.run timeout, so no stale 60 constant shadows it."""
    capture: dict = {}
    monkeypatch.setattr(storage.subprocess, "run", _fake_vite_run(capture=capture))
    monkeypatch.setattr(
        storage.settings, "design_agent_vite_build_timeout_seconds", 111, raising=False
    )
    await storage.vite_build({"src/App.tsx": "x"})
    assert capture["timeout"] == 111
    assert not hasattr(storage, "_VITE_BUILD_TIMEOUT_SECONDS")


def test_settings_field_additive():
    """AC8: the new field exists with the expected type/default and the import is
    clean (the additive Settings change breaks no existing consumer)."""
    from app.config import settings as live_settings
    val = live_settings.design_agent_vite_build_timeout_seconds
    assert isinstance(val, int)
    assert val == 120


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


# ─── Scoped runtime-break type-check gate — subprocess MOCKED (P3-15) ────────
#
# These prove the parse/raise/fail-open LOGIC and run in toolchain-less CI (the
# real-tsc cases below skip there). Per the ticket's Unit Tests note: mock
# subprocess.run to return crafted diagnostic stdout where a real tsc is too slow.


def _fake_tsc(*, stdout="", stderr="", returncode=0, raises=None):
    """subprocess.run replacement for the `tsc --noEmit` invocation."""
    def _run(cmd, cwd=None, **kwargs):
        if raises is not None:
            raise raises
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return _run


def test_typecheck_blocks_missing_hook_import(monkeypatch, tmp_path):
    """AC #1 (parse/raise): TS2304 (useState used, not imported — the #20 bug)."""
    monkeypatch.setattr(
        storage.subprocess, "run",
        _fake_tsc(stdout="src/App.tsx(1,47): error TS2304: Cannot find name 'useState'.\n",
                  returncode=2),
    )
    with pytest.raises(TypeCheckError) as ei:
        storage._typecheck_runtime_break(tmp_path)
    assert "TS2304" in str(ei.value)


def test_typecheck_blocks_bad_module_import(monkeypatch, tmp_path):
    """AC #2 (parse/raise): TS2307 (bad import path)."""
    monkeypatch.setattr(
        storage.subprocess, "run",
        _fake_tsc(stdout="src/App.tsx(1,23): error TS2307: Cannot find module './does-not-exist'.\n",
                  returncode=2),
    )
    with pytest.raises(TypeCheckError) as ei:
        storage._typecheck_runtime_break(tmp_path)
    assert "TS2307" in str(ei.value)


def test_typecheck_allows_cosmetic_type_error(monkeypatch, tmp_path):
    """AC #3: implicit-any (TS7006) + scaffold TS2339/TS2353 noise → NO raise.

    Non-zero exit with no fatal-code line is not fatal — the gate is scoped, not
    blanket; cosmetic type errors still render (tsconfig.json:10-11 intent)."""
    monkeypatch.setattr(
        storage.subprocess, "run",
        _fake_tsc(
            stdout=(
                "src/App.tsx(1,17): error TS7006: Parameter 'x' implicitly has an 'any' type.\n"
                "src/components/ui/resizable.tsx(9,51): error TS2339: Property 'PanelGroup' does not exist.\n"
                "src/components/ui/calendar.tsx(87,9): error TS2353: Object literal may only specify known properties.\n"
            ),
            returncode=2,
        ),
    )
    storage._typecheck_runtime_break(tmp_path)  # no raise


def test_typecheck_clean_bundle_unaffected(monkeypatch, tmp_path):
    """AC #4 (parse): a clean build (rc=0, no diagnostics) → no raise."""
    monkeypatch.setattr(storage.subprocess, "run", _fake_tsc(stdout="", returncode=0))
    storage._typecheck_runtime_break(tmp_path)  # no raise


def test_typecheck_fail_open_when_tsc_binary_missing(monkeypatch, tmp_path, caplog):
    """AC #5 / #8: tsc binary missing (FileNotFoundError) → fail-open, WARNING, no raise."""
    monkeypatch.setattr(
        storage.subprocess, "run", _fake_tsc(raises=FileNotFoundError("npx: command not found")),
    )
    with caplog.at_level(logging.WARNING):
        storage._typecheck_runtime_break(tmp_path)  # no raise
    msgs = [r.getMessage() for r in caplog.records]
    assert any("typecheck_tool_failed" in m and "FileNotFoundError" in m for m in msgs)


def test_typecheck_fail_open_on_timeout(monkeypatch, tmp_path):
    """AC #5: tsc timeout → fail-open, no raise (a tooling hang must not block staging)."""
    monkeypatch.setattr(
        storage.subprocess, "run",
        _fake_tsc(raises=subprocess.TimeoutExpired(cmd=["npx", "tsc"], timeout=60)),
    )
    storage._typecheck_runtime_break(tmp_path)  # no raise


def test_typecheck_fail_open_nonzero_no_fatal_codes(monkeypatch, tmp_path):
    """AC #5: non-zero exit carrying only a config/tooling diagnostic (TS5057, not in
    the fatal set) → no raise. Only a curated fatal code blocks."""
    monkeypatch.setattr(
        storage.subprocess, "run",
        _fake_tsc(stdout="error TS5057: Cannot find a tsconfig.json file at the specified directory.\n",
                  returncode=1),
    )
    storage._typecheck_runtime_break(tmp_path)  # no raise


def test_fatal_codes_keyed_on_code_not_message(monkeypatch, tmp_path):
    """AC #6: a reworded/localized TS2304 message still triggers — keyed on the code."""
    monkeypatch.setattr(
        storage.subprocess, "run",
        _fake_tsc(stdout="src/App.tsx(1,5): error TS2304: <<localized message text>>\n",
                  returncode=2),
    )
    with pytest.raises(TypeCheckError):
        storage._typecheck_runtime_break(tmp_path)


def test_typecheck_scans_stderr_for_fatal_code(monkeypatch, tmp_path):
    """Defensive: a fatal code surfacing on stderr is still caught (TS2552 variant)."""
    monkeypatch.setattr(
        storage.subprocess, "run",
        _fake_tsc(
            stdout="",
            stderr="src/App.tsx(1,1): error TS2552: Cannot find name 'usestate'. Did you mean 'useState'?\n",
            returncode=2,
        ),
    )
    with pytest.raises(TypeCheckError):
        storage._typecheck_runtime_break(tmp_path)


def test_fatal_codes_is_curated_frozenset():
    """AC #6: _FATAL_TS_CODES is a frozenset containing at least TS2304 and TS2307."""
    assert isinstance(storage._FATAL_TS_CODES, frozenset)
    assert {"TS2304", "TS2307"} <= storage._FATAL_TS_CODES


def test_typecheck_blocked_generation_logs_codes_only(monkeypatch, tmp_path):
    """AC #8: the raised diagnostic carries codes + truncated message (≤5 lines),
    not a full source dump (Rule #24)."""
    lines = "".join(f"src/F{i}.tsx(1,1): error TS2304: Cannot find name 'x{i}'.\n" for i in range(8))
    monkeypatch.setattr(storage.subprocess, "run", _fake_tsc(stdout=lines, returncode=2))
    with pytest.raises(TypeCheckError) as ei:
        storage._typecheck_runtime_break(tmp_path)
    msg = str(ei.value)
    assert "TS2304" in msg
    assert msg.count("error TS2304") <= 5  # truncated to first 5 hits


async def test_vite_build_sync_propagates_typecheck_error(monkeypatch):
    """The shared build path runs vite build (success) THEN tsc; a fatal tsc code
    propagates out of vite_build() as TypeCheckError (so the route's widened except
    catches it). #20 end-to-end at the unit layer."""
    def _run(cmd, cwd=None, **kwargs):
        if "tsc" in cmd:
            return SimpleNamespace(
                returncode=2,
                stdout="src/App.tsx(1,47): error TS2304: Cannot find name 'useState'.\n",
                stderr="",
            )
        dist = Path(cwd) / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "index.html").write_text("<html>built</html>")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(storage.subprocess, "run", _run)
    with pytest.raises(TypeCheckError):
        await storage.vite_build({"src/App.tsx": "useState"})


async def test_vite_build_sync_returns_dist_when_only_cosmetic(monkeypatch):
    """Non-regression: vite build success + tsc reporting only cosmetic codes →
    vite_build returns the dist normally (no raise)."""
    def _run(cmd, cwd=None, **kwargs):
        if "tsc" in cmd:
            return SimpleNamespace(
                returncode=2,
                stdout="src/components/ui/resizable.tsx(9,51): error TS2339: Property 'PanelGroup' does not exist.\n",
                stderr="",
            )
        dist = Path(cwd) / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "index.html").write_text("<html>built</html>")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(storage.subprocess, "run", _run)
    out = await storage.vite_build({"src/App.tsx": "export default () => null;"})
    assert out["index.html"] == "<html>built</html>"


# ─── Type-check gate — REAL tsc integration (AC #1, #2, #3, #4) ──────────────
#
# Run real `tsc --noEmit` in an assembled build dir (skipped when the toolchain is
# absent — same gate as the real-vite-build integration tests above). The
# bad-import (TS2307) case must hit tsc DIRECTLY: a real `vite build` would fail to
# resolve the missing module before tsc ever runs, so these assemble the dir and
# call _typecheck_runtime_break, bypassing vite.


def _assemble_build_dir(build_path: Path, virtual_fs: dict[str, str]) -> None:
    """Mirror _vite_build_sync's tempdir assembly: scaffold copy + node_modules
    symlink + virtual_fs overlay. Lets the real-tsc tests run the gate in isolation."""
    storage._copy_scaffold(storage._RUNTIME_ROOT, build_path)
    storage._symlink_node_modules(storage._RUNTIME_ROOT, build_path)
    for rel, content in virtual_fs.items():
        target = build_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


@pytest.mark.integration
@_skip_no_toolchain
def test_typecheck_real_tsc_blocks_missing_hook_import(tmp_path):
    """AC #1 (real tsc): the #20 repro — useState without import → TS2304."""
    _assemble_build_dir(tmp_path, {
        "src/App.tsx": "export default function App(){const[n,setN]=useState(0);"
                       "return <div onClick={()=>setN(n+1)}>{n}</div>;}",
    })
    with pytest.raises(TypeCheckError) as ei:
        storage._typecheck_runtime_break(tmp_path)
    assert "TS2304" in str(ei.value)


@pytest.mark.integration
@_skip_no_toolchain
def test_typecheck_real_tsc_blocks_bad_module_import(tmp_path):
    """AC #2 (real tsc): a bad import path → TS2307."""
    _assemble_build_dir(tmp_path, {
        "src/App.tsx": 'import { Thing } from "./does-not-exist";'
                       "export default function App(){return <div><Thing/></div>;}",
    })
    with pytest.raises(TypeCheckError) as ei:
        storage._typecheck_runtime_break(tmp_path)
    assert "TS2307" in str(ei.value)


@pytest.mark.integration
@_skip_no_toolchain
def test_typecheck_real_tsc_allows_cosmetic_error(tmp_path):
    """AC #3 (real tsc): implicit-any param + scaffold's own non-fatal noise → no raise."""
    _assemble_build_dir(tmp_path, {
        "src/App.tsx": "export default function App(){const f=(x)=>x+1;return <div>{f(2)}</div>;}",
    })
    storage._typecheck_runtime_break(tmp_path)  # no raise — cosmetic still renders


@pytest.mark.integration
@_skip_no_toolchain
def test_typecheck_real_tsc_clean_bundle_passes(tmp_path):
    """AC #4 (real tsc): a clean bundle → no raise (non-regression)."""
    _assemble_build_dir(tmp_path, {
        "src/App.tsx": "export default function App(){return <div><button>Submit</button></div>;}",
    })
    storage._typecheck_runtime_break(tmp_path)  # no raise


@pytest.mark.integration
@_skip_no_toolchain
async def test_vite_build_full_path_blocks_runtime_break_integration():
    """AC #1 end-to-end: the #20 bundle transpiles under real vite (esbuild does no
    name resolution) but the scoped gate inside _vite_build_sync raises before staging."""
    with pytest.raises(TypeCheckError) as ei:
        await storage.vite_build({
            "src/App.tsx": "export default function App(){const[n,setN]=useState(0);"
                           "return <div onClick={()=>setN(n+1)}>{n}</div>;}",
        })
    assert "TS2304" in str(ei.value)


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
    # P6-07: _stage_complete_run now calls vite_build_with_repair (returns
    # (dist, repaired_vfs)); patch that seam, not the now-internal vite_build.
    _stub_generate(monkeypatch, env.routes, status="complete", virtual_fs={"src/App.tsx": "x"})
    monkeypatch.setattr(
        env.routes, "vite_build_with_repair",
        _async_return(({"index.html": "<html></html>"}, {"src/App.tsx": "x"})),
    )
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
        env.routes, "vite_build_with_repair",
        _async_return(
            ({"index.html": '<html><body><div data-anchor-id="abcd1234"></div></body></html>'},
             {"src/App.tsx": "x"}),
        ),
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

    # P6-07: the route now calls vite_build_with_repair (returns (dist, repaired_vfs));
    # a clean build returns the source unchanged.
    async def _vite(virtual_fs):
        calls.append(("vite_build", virtual_fs))
        return dist, virtual_fs

    async def _stage(*, prototype_id, checkpoint_id, files):
        calls.append(("stage_bundle", files))
        return "https://x/index.html"

    _stub_generate(monkeypatch, env.routes, status="complete", virtual_fs=vfs)
    monkeypatch.setattr(env.routes, "vite_build_with_repair", _vite)
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
    monkeypatch.setattr(
        env.routes, "vite_build_with_repair",
        _async_raise(ViteBuildError("vite build exit=1: SyntaxError")),
    )
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

    # Success → vite_build_succeeded (with dist_file_count), no stderr. P6-07: the
    # repair wrapper returns (dist, repaired_vfs); a clean build returns the source
    # unchanged, so no build_repair_applied line is emitted (asserted below).
    monkeypatch.setattr(
        env.routes, "vite_build_with_repair",
        _async_return(({"index.html": "<x/>"}, {"a": "b"})),
    )
    monkeypatch.setattr(env.routes, "stage_bundle", _async_return("file:///x/index.html"))
    with caplog.at_level(logging.INFO):
        await env.routes._stage_complete_run(prototype_id=pid, workspace_id="app", virtual_fs={"a": "b"})
    succeeded = [r.getMessage() for r in caplog.records if r.getMessage().startswith("vite_build_succeeded")]
    assert succeeded and "dist_file_count=1" in succeeded[0]
    # No-op build (no repair) must NOT log build_repair_applied (AC6).
    assert not [r for r in caplog.records if r.getMessage().startswith("build_repair_applied")]

    # Failure → vite_build_failed with error_class only (no stderr in the log line).
    caplog.clear()
    pid2 = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    monkeypatch.setattr(
        env.routes, "vite_build_with_repair",
        _async_raise(ViteBuildError("vite build exit=1: secret-stderr-blob")),
    )
    with caplog.at_level(logging.WARNING):
        await env.routes._stage_complete_run(prototype_id=pid2, workspace_id="app", virtual_fs={"a": "b"})
    failed = [r.getMessage() for r in caplog.records if r.getMessage().startswith("vite_build_failed")]
    assert failed and "error_class=ViteBuildError" in failed[0]
    assert "secret-stderr-blob" not in failed[0]  # stderr never in the log line


# ─── Type-check gate routing — B3 + iterate seam (P3-15 AC #1a) ──────────────


def test_routes_imports_typecheck_error(env):
    """AC #1a: TypeCheckError is imported into routes/design_agent.py (so its
    except tuple can name it). Asserted against the reloaded route module."""
    assert hasattr(env.routes, "TypeCheckError")
    assert issubclass(env.routes.TypeCheckError, RuntimeError)


async def test_complete_path_routes_typecheck_error_to_precise_fail(env, monkeypatch):
    """AC #1a (B3): a TypeCheckError from the build routes to fail_prototype via the
    PRECISE widened except — status='failed' with the fatal codes in `error`, NOT
    the generic outer except. No checkpoint; stage_bundle never called."""
    stage_mock = MagicMock()
    _stub_generate(monkeypatch, env.routes, status="complete", virtual_fs={"src/App.tsx": "x"})
    # P6-07: the complete path builds via vite_build_with_repair; a TypeCheckError
    # (not a ViteBuildError) propagates out of the wrapper unchanged → the route's
    # widened except routes it to fail_prototype exactly as before.
    monkeypatch.setattr(
        env.routes, "vite_build_with_repair",
        _async_raise(env.routes.TypeCheckError(
            "runtime-breaking type errors: src/App.tsx(1,47): error TS2304: Cannot find name 'useState'."
        )),
    )
    monkeypatch.setattr(env.routes, "stage_bundle", stage_mock)
    prd_id = _seed_prd(env.db)
    pid = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "failed"
    assert "TypeCheckError" in row["error"]
    assert "TS2304" in row["error"]            # the fatal diagnostic lands in error
    assert _checkpoints_for(pid) == []         # no checkpoint created
    assert stage_mock.call_count == 0          # staging never attempted


async def test_complete_path_typecheck_uses_precise_log(env, monkeypatch, caplog):
    """AC #1a: the precise `vite_build_failed` log fires (not the generic outer
    `design_agent.generation_failed`) when a TypeCheckError is raised."""
    _stub_generate(monkeypatch, env.routes, status="complete", virtual_fs={"src/App.tsx": "x"})
    # P6-07: complete path builds via vite_build_with_repair; a TypeCheckError
    # propagates unchanged into the route's precise except.
    monkeypatch.setattr(
        env.routes, "vite_build_with_repair",
        _async_raise(env.routes.TypeCheckError("runtime-breaking type errors: error TS2307: ...")),
    )
    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    with caplog.at_level(logging.WARNING):
        await env.routes._stage_complete_run(prototype_id=pid, workspace_id="app", virtual_fs={"a": "b"})
    failed = [r.getMessage() for r in caplog.records if r.getMessage().startswith("vite_build_failed")]
    assert failed and "error_class=TypeCheckError" in failed[0]


async def test_iterate_path_routes_typecheck_error_to_precise_fail(env, monkeypatch, caplog):
    """Cross-ticket seam: a runtime-break on the ITERATE build is caught by
    _stage_iterate_run's widened except (precise `iterate_vite_build_failed` path),
    routes to fail_prototype, and does NOT propagate (un-widened code would let the
    TypeCheckError escape this helper)."""
    stage_mock = MagicMock()
    monkeypatch.setattr(
        env.routes, "vite_build",
        _async_raise(env.routes.TypeCheckError(
            "runtime-breaking type errors: src/App.tsx(1,23): error TS2307: Cannot find module './x'."
        )),
    )
    monkeypatch.setattr(env.routes, "stage_bundle", stage_mock)
    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    with caplog.at_level(logging.WARNING):
        # No pytest.raises: the widened except swallows it and fails the row.
        await env.routes._stage_iterate_run(
            prototype_id=pid, workspace_id="app",
            virtual_fs={"src/App.tsx": "x"}, iterate_prompt="make the header blue",
        )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "failed"
    assert "TypeCheckError" in row["error"]
    assert "TS2307" in row["error"]
    failed = [r.getMessage() for r in caplog.records
              if r.getMessage().startswith("iterate_vite_build_failed")]
    assert failed and "error_class=TypeCheckError" in failed[0]
    assert stage_mock.call_count == 0          # iterate never staged a runtime-broken bundle


# ─── Preview-image staging (BINARY sibling of stage_bundle) ──────────────────
#
# A PNG cannot ride stage_bundle (text-only, content.encode). stage_preview_image
# is the binary sibling; these prove the dual-path (filesystem / Supabase), the
# byte-identical round-trip, idempotent re-stage, and identifier-only logging.

_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4  # PNG signature + binary payload


async def test_stage_preview_image_filesystem_writes_png(monkeypatch, tmp_path):
    _fs_settings(monkeypatch, tmp_path)
    await storage.stage_preview_image(prototype_id=3, checkpoint_id=9, png_bytes=_FAKE_PNG)
    png = tmp_path / "prototypes" / "3" / "9" / "_preview" / "preview.png"
    assert png.exists()
    assert png.read_bytes() == _FAKE_PNG  # byte-identical round-trip


async def test_stage_preview_image_filesystem_returns_file_uri(monkeypatch, tmp_path):
    _fs_settings(monkeypatch, tmp_path, public_url="")
    url = await storage.stage_preview_image(prototype_id=1, checkpoint_id=2, png_bytes=_FAKE_PNG)
    assert url.startswith("file://")
    assert url.endswith("/prototypes/1/2/_preview/preview.png")


async def test_stage_preview_image_filesystem_returns_public_url(monkeypatch, tmp_path):
    _fs_settings(monkeypatch, tmp_path, public_url="https://cdn.example/")
    url = await storage.stage_preview_image(prototype_id=4, checkpoint_id=5, png_bytes=_FAKE_PNG)
    assert url == "https://cdn.example/prototypes/4/5/_preview/preview.png"


async def test_stage_preview_image_idempotent_restage(monkeypatch, tmp_path):
    _fs_settings(monkeypatch, tmp_path)
    await storage.stage_preview_image(prototype_id=7, checkpoint_id=8, png_bytes=_FAKE_PNG)
    second = b"\x89PNG\r\n\x1a\nDIFFERENT"
    await storage.stage_preview_image(prototype_id=7, checkpoint_id=8, png_bytes=second)
    preview_dir = tmp_path / "prototypes" / "7" / "8" / "_preview"
    pngs = list(preview_dir.glob("*.png"))
    assert len(pngs) == 1                      # single artefact (overwrite, not duplicate)
    assert pngs[0].read_bytes() == second      # last write wins


async def test_stage_preview_image_supabase_uploads_raw_png_bytes(monkeypatch):
    sb = _mock_supabase(monkeypatch)
    await storage.stage_preview_image(prototype_id=4, checkpoint_id=2, png_bytes=_FAKE_PNG)
    assert sb.upload.call_count == 1
    kwargs = sb.upload.call_args.kwargs
    assert kwargs["path"] == "prototypes/4/2/_preview/preview.png"
    assert kwargs["file"] == _FAKE_PNG         # raw bytes, not .encode()'d text
    assert kwargs["file_options"]["content-type"] == "image/png"
    assert kwargs["file_options"]["upsert"] == "true"  # idempotent re-stage


async def test_stage_preview_image_supabase_returns_signed_url(monkeypatch):
    _mock_supabase(monkeypatch, signed={"signedURL": "https://signed.example/preview"})
    url = await storage.stage_preview_image(prototype_id=4, checkpoint_id=2, png_bytes=_FAKE_PNG)
    assert url == "https://signed.example/preview"


async def test_stage_preview_image_supabase_uses_24h_ttl(monkeypatch):
    sb = _mock_supabase(monkeypatch)
    await storage.stage_preview_image(prototype_id=4, checkpoint_id=2, png_bytes=_FAKE_PNG)
    assert sb.create_signed_url.call_args.kwargs["expires_in"] == 86400


async def test_preview_image_staged_log_identifiers_only(monkeypatch, tmp_path, caplog):
    _fs_settings(monkeypatch, tmp_path)
    with caplog.at_level(logging.INFO):
        await storage.stage_preview_image(prototype_id=11, checkpoint_id=22, png_bytes=_FAKE_PNG)
    recs = [r for r in caplog.records if r.getMessage().startswith("preview_image_staged")]
    assert len(recs) == 1
    msg = recs[0].getMessage()
    for token in ("prototype_id=11", "checkpoint_id=22", "backend=filesystem"):
        assert token in msg, f"missing {token!r}"
    # The raw PNG bytes never appear in the log line.
    assert "PNG" not in msg
    assert _FAKE_PNG.decode("latin-1") not in msg
