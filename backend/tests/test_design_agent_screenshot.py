"""Tests for the prototype preview screenshot capture + its completion hook.

Three layers:

1. **capture_bundle_screenshot units** — Playwright is fully mocked via the
   ``_resolve_async_playwright`` seam (no live Chromium), mirroring the website
   extractor's test posture: a fake ``async_playwright`` factory whose
   ``chromium.launch`` yields a fake browser -> context -> page with scriptable
   ``goto`` / ``screenshot``. Proves PNG-bytes-on-success, honest-degrade-to-None
   on every failure class, the 8s nav cap, and per-call browser disposal.
2. **Completion hook** (fake Supabase DB) — drives ``_stage_complete_run`` to
   prove the capture step is best-effort: a capture that returns None or raises
   still completes the prototype ready with ``preview_image_url`` null, and no
   placeholder is stored. Also proves the success path sets the URL on the
   in-workspace row only, and that the step is dormant when the enable flag is off.
3. **Migration** — string-level idempotency / nullability check on the new
   migration file, read from the working tree (same convention as the other
   prototypes-column migration tests).
"""
from __future__ import annotations

import importlib
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.design_agent.screenshot as screenshot

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260605000000_design_agent_preview_image.sql"
)

_FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-screenshot-bytes"


# ═══════════════════════════════════════════════════════════════════════════
# Fake Playwright object graph (mirrors test_design_agent_website_extractor)
# ═══════════════════════════════════════════════════════════════════════════


def _build_fake(
    *,
    screenshot_return=_FAKE_PNG,
    goto_side_effect=None,
    launch_side_effect=None,
    wait_for_selector_side_effect=None,
):
    """Fake Playwright graph + a factory matching the ``async with`` contract."""
    page = MagicMock(name="page")
    page.goto = AsyncMock(side_effect=goto_side_effect)
    page.wait_for_selector = AsyncMock(side_effect=wait_for_selector_side_effect)
    page.wait_for_load_state = AsyncMock()
    page.screenshot = AsyncMock(return_value=screenshot_return)

    context = MagicMock(name="context")
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()

    browser = MagicMock(name="browser")
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()

    chromium = MagicMock(name="chromium")
    chromium.launch = AsyncMock(return_value=browser, side_effect=launch_side_effect)

    p = MagicMock(name="p")
    p.chromium = chromium

    class _CM:
        async def __aenter__(self_inner):
            return p

        async def __aexit__(self_inner, *exc):
            return False

    def _factory():
        return _CM()

    return SimpleNamespace(
        factory=_factory, p=p, chromium=chromium, browser=browser,
        context=context, page=page,
    )


def _install(monkeypatch, handles):
    monkeypatch.setattr(screenshot, "_resolve_async_playwright", lambda: handles.factory)


# A tiny multi-file SPA bundle: index.html with a module script that populates
# #root + an assets/ js (mirrors the Vite dist/ shape the capture re-serves).
# `index.html` MUST be present (the capture refuses an entry-less bundle).
_SPA_BUNDLE = {
    "index.html": (
        '<!doctype html><html><head>'
        '<script type="module" src="./assets/index-abc123.js"></script>'
        '</head><body><div id="root"></div></body></html>'
    ),
    "assets/index-abc123.js": (
        "const r=document.getElementById('root');"
        "const d=document.createElement('div');"
        "d.textContent='rendered';r.appendChild(d);"
    ),
}


# ─── capture_bundle_screenshot — happy path ──────────────────────────────────


async def test_capture_returns_png_bytes_on_success(monkeypatch):
    h = _build_fake()
    _install(monkeypatch, h)
    out = await screenshot.capture_bundle_screenshot(_SPA_BUNDLE)
    assert out == _FAKE_PNG
    assert h.page.screenshot.await_count == 1


async def test_capture_points_at_local_loopback_not_signed_url(monkeypatch):
    """Navigation targets a local 127.0.0.1 loopback URL, NOT a signed storage URL.

    This is the core of the fix: rendering from the signed Supabase object URL
    paints only the un-hydrated shell because relative ./assets/* cannot resolve.
    """
    h = _build_fake()
    _install(monkeypatch, h)
    await screenshot.capture_bundle_screenshot(_SPA_BUNDLE)
    nav_url = h.page.goto.await_args.args[0]
    assert nav_url.startswith("http://127.0.0.1:")
    assert nav_url.endswith("/index.html")
    assert "supabase" not in nav_url and "signed" not in nav_url


async def test_capture_waits_for_root_to_populate(monkeypatch):
    """The render-wait gate: wait_for_selector('#root > *') before screenshot."""
    h = _build_fake()
    _install(monkeypatch, h)
    await screenshot.capture_bundle_screenshot(_SPA_BUNDLE)
    assert h.page.wait_for_selector.await_count == 1
    assert h.page.wait_for_selector.await_args.args[0] == "#root > *"


async def test_capture_navigation_cap_is_8s(monkeypatch):
    h = _build_fake()
    _install(monkeypatch, h)
    await screenshot.capture_bundle_screenshot(_SPA_BUNDLE)
    assert h.page.goto.await_args.kwargs["timeout"] == 8000
    assert h.page.goto.await_args.kwargs["wait_until"] == "load"


async def test_capture_disposes_browser_on_success(monkeypatch):
    h = _build_fake()
    _install(monkeypatch, h)
    await screenshot.capture_bundle_screenshot(_SPA_BUNDLE)
    assert h.context.close.await_count == 1
    assert h.browser.close.await_count == 1


# ─── capture_bundle_screenshot — honest-degrade (None on any failure) ─────────


def _import_error_resolver():
    def _raise():
        raise ImportError("No module named 'playwright'")
    return _raise


async def test_capture_returns_none_on_import_error(monkeypatch):
    """No Playwright installed → resolver raises ImportError → None (no raise)."""
    monkeypatch.setattr(screenshot, "_resolve_async_playwright", _import_error_resolver())
    out = await screenshot.capture_bundle_screenshot(_SPA_BUNDLE)
    assert out is None


async def test_capture_returns_none_on_empty_bundle(monkeypatch):
    """No renderable entry (no index.html) → None, no browser launched."""
    h = _build_fake()
    _install(monkeypatch, h)
    out = await screenshot.capture_bundle_screenshot({"assets/x.js": "1"})
    assert out is None
    assert h.chromium.launch.await_count == 0


async def test_capture_returns_none_on_launch_failure(monkeypatch):
    """Chromium not provisioned → launch raises → None (no raise)."""
    h = _build_fake(launch_side_effect=RuntimeError("Executable doesn't exist"))
    _install(monkeypatch, h)
    out = await screenshot.capture_bundle_screenshot(_SPA_BUNDLE)
    assert out is None


async def test_capture_returns_none_on_timeout(monkeypatch):
    class TimeoutError(Exception):  # noqa: A001 — mimic playwright's class name
        pass

    h = _build_fake(goto_side_effect=TimeoutError("Timeout 8000ms exceeded"))
    _install(monkeypatch, h)
    out = await screenshot.capture_bundle_screenshot(_SPA_BUNDLE)
    assert out is None


async def test_capture_returns_none_when_root_never_populates(monkeypatch):
    """Render-wait honest-degrade: a bundle whose #root never gets a child →
    wait_for_selector times out → None within the cap, no crash, no shell PNG."""
    class TimeoutError(Exception):  # noqa: A001 — mimic playwright's class name
        pass

    h = _build_fake(wait_for_selector_side_effect=TimeoutError("Timeout 8000ms"))
    _install(monkeypatch, h)
    out = await screenshot.capture_bundle_screenshot(_SPA_BUNDLE)
    assert out is None
    assert h.page.screenshot.await_count == 0   # never screenshot the empty shell


async def test_capture_returns_none_on_nav_error(monkeypatch):
    h = _build_fake(goto_side_effect=RuntimeError("net::ERR_NAME_NOT_RESOLVED"))
    _install(monkeypatch, h)
    out = await screenshot.capture_bundle_screenshot(_SPA_BUNDLE)
    assert out is None


async def test_capture_disposes_browser_on_nav_error(monkeypatch):
    h = _build_fake(goto_side_effect=RuntimeError("net::ERR_TIMED_OUT"))
    _install(monkeypatch, h)
    await screenshot.capture_bundle_screenshot(_SPA_BUNDLE)
    # Disposed even when goto raised (no browser pool).
    assert h.context.close.await_count == 1
    assert h.browser.close.await_count == 1


async def test_capture_never_logs_bundle_contents(monkeypatch, caplog):
    """Bundle contents are never logged by the capture module."""
    h = _build_fake(goto_side_effect=RuntimeError("boom"))
    _install(monkeypatch, h)
    secret_bundle = {
        "index.html": '<div id="root"></div><!--SECRET-TOKEN-->',
        "assets/x.js": "1",
    }
    with caplog.at_level(logging.DEBUG):
        await screenshot.capture_bundle_screenshot(secret_bundle)
    assert all("SECRET-TOKEN" not in r.getMessage() for r in caplog.records)


# ═══════════════════════════════════════════════════════════════════════════
# Completion hook — fake Supabase DB
# ═══════════════════════════════════════════════════════════════════════════

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
    """Fake-Supabase DB + design-agent route module reloaded in dependency order.

    The prototypes DDL here carries the new preview_image_url column so the
    success-path completion write is exercised end-to-end.
    """
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    monkeypatch.setitem(
        _fake_supabase._JSONB_COLUMNS, "prototype_checkpoints",
        {"prompt_history", "comment_state"},
    )
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    monkeypatch.delenv("SUPABASE_STORAGE_BUCKET", raising=False)

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    return SimpleNamespace(proto=proto_mod, routes=routes_mod)


def _async_return(value):
    async def _f(*args, **kwargs):
        return value
    return _f


def _async_raise(exc):
    async def _f(*args, **kwargs):
        raise exc
    return _f


def _wire_successful_build(env, monkeypatch):
    """Stub the build + bundle stage so _stage_complete_run reaches the capture step."""
    monkeypatch.setattr(
        env.routes, "vite_build_with_repair",
        _async_return(({"index.html": "<html></html>"}, {"src/App.tsx": "x"})),
    )
    monkeypatch.setattr(
        env.routes, "stage_bundle",
        _async_return("https://x.example/prototypes/1/1/index.html"),
    )


async def test_complete_run_sets_preview_url_on_success(env, monkeypatch):
    """Capture returns PNG → stage_preview_image stores it → row carries the URL."""
    _wire_successful_build(env, monkeypatch)
    monkeypatch.setattr(env.routes, "capture_bundle_screenshot", _async_return(_FAKE_PNG))
    monkeypatch.setattr(
        env.routes, "stage_preview_image",
        _async_return("https://x.example/prototypes/1/1/_preview/preview.png"),
    )
    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    await env.routes._stage_complete_run(prototype_id=pid, workspace_id="app", virtual_fs={"a": "b"})
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "ready"
    assert row["preview_image_url"] == "https://x.example/prototypes/1/1/_preview/preview.png"


async def test_complete_run_completes_ready_when_capture_returns_none(env, monkeypatch):
    """Honest-degrade: capture None → ready, preview null, no stage_preview_image call."""
    _wire_successful_build(env, monkeypatch)
    monkeypatch.setattr(env.routes, "capture_bundle_screenshot", _async_return(None))
    stage_preview = MagicMock()
    monkeypatch.setattr(env.routes, "stage_preview_image", stage_preview)
    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    await env.routes._stage_complete_run(prototype_id=pid, workspace_id="app", virtual_fs={"a": "b"})
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "ready"
    assert row["preview_image_url"] is None     # no placeholder stored
    assert stage_preview.call_count == 0        # nothing to stage when capture returns None


async def test_complete_run_does_not_fail_on_capture_exception(env, monkeypatch):
    """Best-effort: a raised capture/stage exception is swallowed; prototype still ready."""
    _wire_successful_build(env, monkeypatch)
    monkeypatch.setattr(
        env.routes, "capture_bundle_screenshot", _async_raise(RuntimeError("kaboom")),
    )
    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    # No pytest.raises — the exception must not propagate out of _stage_complete_run.
    await env.routes._stage_complete_run(prototype_id=pid, workspace_id="app", virtual_fs={"a": "b"})
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "ready"
    assert row["preview_image_url"] is None


async def test_capture_not_attempted_on_build_failure(env, monkeypatch):
    """Success-path only: a build failure returns before the capture step."""
    monkeypatch.setattr(
        env.routes, "vite_build_with_repair",
        _async_raise(env.routes.ViteBuildError("vite build exit=1: SyntaxError")),
    )
    capture = MagicMock()
    monkeypatch.setattr(env.routes, "capture_bundle_screenshot", capture)
    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    await env.routes._stage_complete_run(prototype_id=pid, workspace_id="app", virtual_fs={"a": "b"})
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "failed"
    assert capture.call_count == 0              # no screenshot on a failed bundle


async def test_preview_url_written_under_caller_workspace_only(env, monkeypatch):
    """Workspace isolation: the URL lands on the in-workspace row; a same-prd row
    under a different workspace is unaffected."""
    _wire_successful_build(env, monkeypatch)
    monkeypatch.setattr(env.routes, "capture_bundle_screenshot", _async_return(_FAKE_PNG))
    monkeypatch.setattr(env.routes, "stage_preview_image", _async_return("https://x/preview.png"))
    pid_app = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    pid_other = env.proto.start_prototype(prd_id=1, workspace_id="other", template_version=1)
    await env.routes._stage_complete_run(prototype_id=pid_app, workspace_id="app", virtual_fs={"a": "b"})
    row_app = env.proto.get_prototype(prototype_id=pid_app, workspace_id="app")
    row_other = env.proto.get_prototype(prototype_id=pid_other, workspace_id="other")
    assert row_app["preview_image_url"] == "https://x/preview.png"
    assert row_other["preview_image_url"] is None   # untouched cross-workspace row


async def test_complete_run_logs_preview_captured_identifiers_only(env, monkeypatch, caplog):
    """Observability: preview_captured (INFO) on success carries identifiers, not URL."""
    _wire_successful_build(env, monkeypatch)
    monkeypatch.setattr(env.routes, "capture_bundle_screenshot", _async_return(_FAKE_PNG))
    secret_url = "https://signed.example/SECRET-PREVIEW/preview.png"
    monkeypatch.setattr(env.routes, "stage_preview_image", _async_return(secret_url))
    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    with caplog.at_level(logging.INFO):
        await env.routes._stage_complete_run(prototype_id=pid, workspace_id="app", virtual_fs={"a": "b"})
    captured = [r.getMessage() for r in caplog.records if r.getMessage().startswith("preview_captured")]
    assert captured
    assert f"prototype_id={pid}" in captured[0]
    assert "checkpoint_id=" in captured[0]
    assert "SECRET-PREVIEW" not in captured[0]   # signed URL value never logged


async def test_complete_run_logs_preview_capture_failed_on_none(env, monkeypatch, caplog):
    """Observability: the degrade path logs preview_capture_failed (WARNING)."""
    _wire_successful_build(env, monkeypatch)
    monkeypatch.setattr(env.routes, "capture_bundle_screenshot", _async_return(None))
    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    with caplog.at_level(logging.WARNING):
        await env.routes._stage_complete_run(prototype_id=pid, workspace_id="app", virtual_fs={"a": "b"})
    failed = [r.getMessage() for r in caplog.records if r.getMessage().startswith("preview_capture_failed")]
    assert failed
    assert f"prototype_id={pid}" in failed[0]


async def test_complete_run_logs_error_class_on_capture_exception(env, monkeypatch, caplog):
    """Observability: an exception path logs preview_capture_failed with error_class."""
    _wire_successful_build(env, monkeypatch)
    monkeypatch.setattr(env.routes, "capture_bundle_screenshot", _async_raise(RuntimeError("boom")))
    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    with caplog.at_level(logging.WARNING):
        await env.routes._stage_complete_run(prototype_id=pid, workspace_id="app", virtual_fs={"a": "b"})
    failed = [r.getMessage() for r in caplog.records if r.getMessage().startswith("preview_capture_failed")]
    assert failed and "error_class=RuntimeError" in failed[0]


# ═══════════════════════════════════════════════════════════════════════════
# Migration — string-level idempotency / nullability (working-tree file)
# ═══════════════════════════════════════════════════════════════════════════


def _migration_sql_only() -> str:
    """Migration content with `--` line comments stripped, lowercased."""
    lines = [line.split("--", 1)[0] for line in _MIGRATION_PATH.read_text().splitlines()]
    return "\n".join(lines).lower()


def test_preview_image_migration_file_exists_and_named():
    assert _MIGRATION_PATH.exists()
    assert _MIGRATION_PATH.name == "20260605000000_design_agent_preview_image.sql"


def test_preview_image_migration_idempotent():
    # Apply-twice is a no-op: every ADD COLUMN must be guarded IF NOT EXISTS.
    sql = _migration_sql_only()
    for m in re.finditer(r"add\s+column\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent ADD COLUMN near offset {m.start()}")
    assert "preview_image_url text" in sql


def test_preview_image_migration_nullable_no_default_no_workspace_change():
    sql = _migration_sql_only()
    # Nullable: no NOT NULL, no default on the column.
    assert "not null" not in sql
    assert "default" not in sql
    # No workspace_id change — the column inherits the table's workspace_id.
    assert "workspace_id" not in sql
