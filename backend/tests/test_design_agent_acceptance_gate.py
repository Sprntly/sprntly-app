"""Tests for the fail-closed acceptance gate that stops a generated prototype from
shipping the scaffold placeholder instead of agent-authored UI.

A generation that writes leaf components but never wires `src/App.tsx` leaves the
scaffold's default App.tsx in place. Vite tree-shakes the unreferenced leaves out
and the build is green, so the prototype would ship the empty placeholder. The
scaffold default carries a minification-surviving sentinel string; a real
generation replaces that file, so the sentinel is absent from a correctly-wired
build. Its presence in the built dist is the positive signal that the entry was
never wired.

Three layers, mirroring test_design_agent_storage.py:

1. **Pure gate units** — `assert_mounts_generated_content` over synthetic dist
   dicts (sentinel present / absent / hidden in a base64 entry). No build.
2. **Staged-run fast lane** — drive `_stage_complete_run` / `_stage_iterate_run`
   headless with DB/staging callables stubbed and the build seams stubbed to
   return synthetic dist. Proves the gate fails-closed (never completes / advances
   while the sentinel is present) and the repair loop is bounded.
3. **Real-build lane** (`@pytest.mark.real_build` + toolchain skipif) — assemble a
   real `vite build` of a placeholder fixture vs a wired control and prove the
   sentinel survives minification and is the positive signal, independent of
   component symbol names.
"""
from __future__ import annotations

import shutil
import types

import pytest

import app.design_agent.storage as storage
from app.design_agent.storage import (
    PlaceholderShippedError,
    SCAFFOLD_SENTINEL,
    assert_mounts_generated_content,
)
from app.routes import design_agent as da
from app.llm_telemetry import RunUsage


# ─── Fixtures: a real, type-clean leaf tree (NO entry point) vs a wired control ─
#
# LEAF_FILES is what the agent emits when the bug fires: resolvable leaf
# components + the data/lib they import, but NO src/App.tsx and NO src/main.tsx,
# so the scaffold's own placeholder App.tsx survives the build. MARKER is a unique
# string only the leaf carries, used to prove the leaf reaches (or is shaken out
# of) the dist. CONTROL_FS adds the App.tsx that composes the leaf — the wired,
# post-fix "good" shape.

MARKER = "Q3_REVENUE_BEACON_a1b2c3"

LEAF_FILES = {
    "src/index.css": (
        "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n"
    ),
    "src/data/insights.ts": (
        "export interface Insight { id: string; label: string; value: string }\n"
        "export const INSIGHTS: Insight[] = [\n"
        f"  {{ id: 'q3', label: '{MARKER}', value: '$1.2M' }},\n"
        "];\n"
    ),
    "src/lib/format.ts": (
        "export function fmtMoney(v: string): string { return v.trim(); }\n"
    ),
    "src/components/InsightCard.tsx": (
        "import { Card, CardHeader, CardContent } from '@/components/ui/card';\n"
        "import { INSIGHTS } from '@/data/insights';\n"
        "import { fmtMoney } from '@/lib/format';\n"
        "export function InsightCard() {\n"
        "  const i = INSIGHTS[0];\n"
        "  return (\n"
        "    <Card>\n"
        "      <CardHeader>{i.label}</CardHeader>\n"
        "      <CardContent>{fmtMoney(i.value)}</CardContent>\n"
        "    </Card>\n"
        "  );\n"
        "}\n"
    ),
}

# BUG fixture: leaf files only — scaffold placeholder App.tsx survives.
BUG_FS = dict(LEAF_FILES)

# CONTROL fixture: same leaves PLUS an App.tsx that composes InsightCard.
CONTROL_FS = dict(LEAF_FILES)
CONTROL_FS["src/App.tsx"] = (
    "import { InsightCard } from '@/components/InsightCard';\n"
    "export default function App() {\n"
    "  return <div><InsightCard /></div>;\n"
    "}\n"
)


# ─── DB/staging stub harness (mirrors the repro/smoke harness pattern) ────────


class _Calls:
    """Records whether the staging path completed / failed / advanced and the
    error string passed to fail_prototype."""

    def __init__(self) -> None:
        self.completed = False
        self.failed = False
        self.advanced = False
        self.fail_error: str | None = None


@pytest.fixture
def calls(monkeypatch):
    """Patch every DB/staging callable the staging paths touch on the `da` module
    so `_stage_complete_run` / `_stage_iterate_run` run headless (no DB, no
    network, no real staging), and record complete/fail/advance + the error."""
    rec = _Calls()

    monkeypatch.setattr(da, "publish_step", lambda *a, **k: None)
    monkeypatch.setattr(da, "create_checkpoint", lambda *a, **k: 999001)

    async def _stage_bundle(*a, **k):
        return "memory://bundle"

    monkeypatch.setattr(da, "stage_bundle", _stage_bundle)
    monkeypatch.setattr(da, "reconcile_comments_on_checkpoint", lambda *a, **k: None)

    async def _capture(*a, **k):
        return None

    monkeypatch.setattr(da, "capture_bundle_screenshot", _capture)

    async def _stage_preview(*a, **k):
        return None

    monkeypatch.setattr(da, "stage_preview_image", _stage_preview)
    monkeypatch.setattr(da, "authed_bundle_url", lambda *a, **k: "memory://authed")

    def _complete(*a, **k):
        rec.completed = True

    monkeypatch.setattr(da, "complete_prototype", _complete)

    def _fail(*a, **k):
        rec.failed = True
        rec.fail_error = k.get("error")

    monkeypatch.setattr(da, "fail_prototype", _fail)

    def _advance(*a, **k):
        rec.advanced = True

    monkeypatch.setattr(da, "advance_current_checkpoint", _advance)

    return rec


def _sentinel_dist() -> dict[str, str]:
    return {"assets/x.js": f"x{SCAFFOLD_SENTINEL}x"}


def _clean_dist() -> dict[str, str]:
    return {"assets/x.js": "const a=1;", "index.html": "<div>"}


# ─── Fast lane — pure gate units (no build) ──────────────────────────────────


def test_assert_raises_on_sentinel_in_dist():
    """The sentinel anywhere in a dist file raises PlaceholderShippedError."""
    with pytest.raises(PlaceholderShippedError):
        assert_mounts_generated_content({"assets/x.js": f"...{SCAFFOLD_SENTINEL}..."})


def test_assert_passes_without_sentinel():
    """A wired build (no sentinel in dist) does not raise."""
    assert_mounts_generated_content(
        {"assets/x.js": "const a=1;//no token", "index.html": "<div id=root>"}
    )  # no raise


def test_assert_decodes_b64_entries():
    """A sentinel hidden inside a base64-encoded (`.b64`) binary asset is still
    detected — the gate decodes `.b64` entries before scanning."""
    import base64

    payload = base64.b64encode(
        f"prefix{SCAFFOLD_SENTINEL}suffix".encode("utf-8")
    ).decode("ascii")
    with pytest.raises(PlaceholderShippedError):
        assert_mounts_generated_content({"assets/font.woff2.b64": payload})


# ─── Fast lane — staged-run fail-closed behaviour (build seams stubbed) ──────


async def test_stage_complete_run_does_not_ship_placeholder(calls, monkeypatch):
    """A complete run whose build (and every repair rebuild) still renders the
    placeholder fails the row and NEVER completes the prototype."""
    virtual_fs = dict(BUG_FS)

    async def _build_with_repair(vfs):
        return _sentinel_dist(), vfs

    monkeypatch.setattr(da, "vite_build_with_repair", _build_with_repair)

    async def _repair(**kwargs):
        # The repair agent re-emits, but the rebuild below still ships the
        # placeholder, so the repair loop exhausts and honest-fails.
        return types.SimpleNamespace(usage=RunUsage()), kwargs["virtual_fs"]

    monkeypatch.setattr(da, "repair_build_run", _repair)

    ready = await da._stage_complete_run(
        prototype_id=1,
        workspace_id="w",
        virtual_fs=virtual_fs,
        system_blocks=[{"type": "text", "text": "x"}],
        scenario="A",
        theme_expectations=None,
    )

    assert ready is False
    assert calls.completed is False
    assert calls.failed is True
    assert calls.fail_error is not None
    assert calls.fail_error.startswith("placeholder_shipped")


async def test_stage_complete_run_ships_control(calls, monkeypatch):
    """A complete run whose build mounts real content (no sentinel) completes the
    prototype and never fails the row."""
    virtual_fs = dict(CONTROL_FS)

    async def _build_with_repair(vfs):
        return _clean_dist(), vfs

    monkeypatch.setattr(da, "vite_build_with_repair", _build_with_repair)

    ready = await da._stage_complete_run(
        prototype_id=2,
        workspace_id="w",
        virtual_fs=virtual_fs,
        system_blocks=None,
        scenario="A",
        theme_expectations=None,
    )

    assert ready is True
    assert calls.completed is True
    assert calls.failed is False


async def test_stage_iterate_run_fails_closed_on_placeholder(calls, monkeypatch):
    """An iterate whose build renders the placeholder fails the row fail-closed —
    iterate has NO agent re-entry at this seam, so it must never silently advance
    the checkpoint."""
    virtual_fs = dict(BUG_FS)

    async def _vite_build(vfs):
        return _sentinel_dist()

    monkeypatch.setattr(da, "vite_build", _vite_build)

    staged = await da._stage_iterate_run(
        prototype_id=3,
        workspace_id="w",
        virtual_fs=virtual_fs,
        iterate_prompt="x",
    )

    assert staged is False
    assert calls.failed is True
    assert calls.fail_error is not None
    assert calls.fail_error.startswith("placeholder_shipped")
    assert calls.advanced is False


async def test_stage_iterate_run_advances_control(calls, monkeypatch):
    """An iterate whose build mounts real content advances the checkpoint and never
    fails the row."""
    virtual_fs = dict(CONTROL_FS)

    async def _vite_build(vfs):
        return _clean_dist()

    monkeypatch.setattr(da, "vite_build", _vite_build)

    staged = await da._stage_iterate_run(
        prototype_id=4,
        workspace_id="w",
        virtual_fs=virtual_fs,
        iterate_prompt="x",
    )

    assert staged is True
    assert calls.advanced is True
    assert calls.failed is False


async def test_repair_bound_then_fail(calls, monkeypatch):
    """When every repair rebuild still ships the placeholder, the repair loop is
    bounded to _BUILD_REPAIR_MAX_ITERS agent re-entries (no infinite loop) and the
    run terminally fails — proving the gate is fail-closed AND bounded."""
    virtual_fs = dict(BUG_FS)

    async def _build_with_repair(vfs):
        return _sentinel_dist(), vfs

    monkeypatch.setattr(da, "vite_build_with_repair", _build_with_repair)

    repair_calls = {"n": 0}

    async def _repair(**kwargs):
        repair_calls["n"] += 1
        return types.SimpleNamespace(usage=RunUsage()), kwargs["virtual_fs"]

    monkeypatch.setattr(da, "repair_build_run", _repair)

    ready = await da._stage_complete_run(
        prototype_id=5,
        workspace_id="w",
        virtual_fs=virtual_fs,
        system_blocks=[{"type": "text", "text": "x"}],
        scenario="A",
        theme_expectations=None,
    )

    assert ready is False
    assert calls.completed is False
    assert calls.failed is True
    # The repair agent is re-entered exactly the bounded number of times — never
    # an unbounded loop on a stubbornly-placeholder build.
    assert repair_calls["n"] == da._BUILD_REPAIR_MAX_ITERS


# ─── Real-build lane — REAL `vite build` (toolchain skipif + real_build mark) ──
#
# Mirror test_design_agent_storage.py's toolchain guard: apply BOTH the
# `real_build` mark (so CI runs these CPU-heavy builds in their own sequential
# step, isolated from the bulk suite) AND the skipif (skip cleanly without a
# provisioned Node toolchain).

_HAS_TOOLCHAIN = (
    storage._RUNTIME_ROOT / "node_modules"
).exists() and shutil.which("npx") is not None


def _skip_no_toolchain(func):
    return pytest.mark.real_build(
        pytest.mark.skipif(
            not _HAS_TOOLCHAIN, reason="prototype-runtime/node_modules or npx absent"
        )(func)
    )


@pytest.fixture
def generous_vite_timeout(monkeypatch):
    """Give the real `vite build` headroom to finish on a contended runner (the
    prod default can SIGKILL a slow-but-valid build). Prod default untouched."""
    monkeypatch.setattr(
        storage.settings,
        "design_agent_vite_build_timeout_seconds",
        600,
        raising=False,
    )


def _concat(dist: dict[str, str]) -> str:
    return storage._concat_dist(dist)


@pytest.mark.integration
@_skip_no_toolchain
def test_placeholder_build_detected_integration(generous_vite_timeout):
    """A real build of the placeholder fixture (leaves, no entry point) ships the
    scaffold default, so the gate raises PlaceholderShippedError."""
    dist = storage._vite_build_sync(storage._RUNTIME_ROOT, dict(BUG_FS))
    with pytest.raises(PlaceholderShippedError):
        assert_mounts_generated_content(dist)


@pytest.mark.integration
@_skip_no_toolchain
def test_control_build_passes_gate_integration(generous_vite_timeout):
    """A real build of the wired control (App.tsx composes the leaf) does not ship
    the scaffold default, so the gate passes."""
    dist = storage._vite_build_sync(storage._RUNTIME_ROOT, dict(CONTROL_FS))
    assert_mounts_generated_content(dist)  # no raise


@pytest.mark.integration
@_skip_no_toolchain
def test_sentinel_survives_minification_integration(generous_vite_timeout):
    """The sentinel string survives Vite minification and is the positive signal:
    present in the placeholder dist, absent from the wired control dist."""
    bug_dist = storage._vite_build_sync(storage._RUNTIME_ROOT, dict(BUG_FS))
    control_dist = storage._vite_build_sync(storage._RUNTIME_ROOT, dict(CONTROL_FS))
    assert SCAFFOLD_SENTINEL in _concat(bug_dist)
    assert SCAFFOLD_SENTINEL not in _concat(control_dist)


@pytest.mark.integration
@_skip_no_toolchain
def test_detection_ignores_symbol_names_integration(generous_vite_timeout):
    """Detection does not depend on the agent's component symbol names: rename the
    leaf component (InsightCard → MetricPanel) in both fixtures and the control
    still passes the gate while the placeholder still raises."""
    renamed_leaf = LEAF_FILES["src/components/InsightCard.tsx"].replace(
        "InsightCard", "MetricPanel"
    )
    leaves = dict(LEAF_FILES)
    del leaves["src/components/InsightCard.tsx"]
    leaves["src/components/MetricPanel.tsx"] = renamed_leaf

    bug_fs = dict(leaves)
    control_fs = dict(leaves)
    control_fs["src/App.tsx"] = (
        "import { MetricPanel } from '@/components/MetricPanel';\n"
        "export default function App() {\n"
        "  return <div><MetricPanel /></div>;\n"
        "}\n"
    )

    control_dist = storage._vite_build_sync(storage._RUNTIME_ROOT, control_fs)
    assert_mounts_generated_content(control_dist)  # no raise

    bug_dist = storage._vite_build_sync(storage._RUNTIME_ROOT, bug_fs)
    with pytest.raises(PlaceholderShippedError):
        assert_mounts_generated_content(bug_dist)
