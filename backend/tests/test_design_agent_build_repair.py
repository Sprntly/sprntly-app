"""Tests for the P6-07 build-repair hardening (Fix #11).

Three layers:

1. **Pure repair pass** (`repair_unresolved_relative_imports`): stub a
   `./screens/*Screen` orphan, strip a non-screen orphan, no-op on a clean map,
   candidate-set + dir-join parity with autofixer.js, import-form extraction,
   anchor-less stub.
2. **Bounded rebuild wrapper** (`vite_build_with_repair`): the `vite_build`
   subprocess is replaced with a stateful fake (no Node toolchain) — orphan →
   repaired → green; capital-C real-Rollup detection; bound at max_repairs+1;
   non-orphan re-raise; exhaustion → distinct class.
3. **Route hook** (`_stage_complete_run`, fake Supabase): repairs instead of
   failing, stages the REPAIRED source, logs `build_repair_applied` (count only).

Regression tests (AC1/AC5) FAIL on unfixed code: before P6-07 the orphan-screen
`virtual_fs` raised `ViteBuildError` with no repair path and `_stage_complete_run`
shipped `status=failed`.
"""
from __future__ import annotations

import importlib
import logging
from types import SimpleNamespace

import pytest

import app.design_agent.storage as storage
from app.design_agent.storage import (
    UnresolvedImportRepairExhausted,
    ViteBuildError,
    repair_unresolved_relative_imports,
    vite_build_with_repair,
)

# The verbatim Rollup/esbuild orphan emit (capital-C), as it lands in the
# ViteBuildError message `f"vite build exit={rc}: {stderr_tail}"`.
_REAL_ROLLUP_MSG = (
    'vite build exit=1: Could not resolve "./screens/ScheduleBuilderScreen" '
    'from "src/App.tsx"'
)

_ORPHAN_APP_TSX = (
    'import ScheduleBuilderScreen from "./screens/ScheduleBuilderScreen";\n'
    'export default function App() { return <ScheduleBuilderScreen />; }\n'
)


def _stateful_build(*, required_key, signature=_REAL_ROLLUP_MSG, dist=None):
    """A `vite_build` replacement: raise ViteBuildError naming the orphan UNTIL
    `required_key` (the stub the repair pass writes) appears in the vfs, then
    return a dist. Records call count so the bound can be asserted."""
    state = {"calls": 0}

    async def _build(virtual_fs):
        state["calls"] += 1
        if required_key not in virtual_fs:
            raise ViteBuildError(signature)
        return dict(dist or {"index.html": "<html>built</html>"})

    return _build, state


# ─── Pure repair pass ────────────────────────────────────────────────────────


def test_repair_stubs_screens_orphan():
    """AC1: `./screens/XScreen` orphan → a stub file appears; action records `stub`."""
    vfs = {"src/App.tsx": _ORPHAN_APP_TSX}
    repaired, actions = repair_unresolved_relative_imports(vfs)
    assert "src/screens/ScheduleBuilderScreen.tsx" in repaired
    assert any(a.startswith("stub ") for a in actions)
    assert "ScheduleBuilderScreen" in repaired["src/screens/ScheduleBuilderScreen.tsx"]


def test_repair_strips_non_screen_orphan():
    """AC2: a `./lib/formatDate` orphan with no target is STRIPPED, not stubbed."""
    vfs = {
        "src/App.tsx": (
            'import { formatDate } from "./lib/formatDate";\n'
            "export default function App() { return <div />; }\n"
        ),
    }
    repaired, actions = repair_unresolved_relative_imports(vfs)
    assert "formatDate" not in repaired["src/App.tsx"]
    assert 'import { formatDate }' not in repaired["src/App.tsx"]
    assert any(a.startswith("strip ") for a in actions)
    # No stub file was fabricated for a non-screen orphan.
    assert "src/lib/formatDate.tsx" not in repaired


def test_repair_noop_on_resolving_imports():
    """AC6: all relative imports resolve → empty action list, map unchanged."""
    vfs = {
        "src/App.tsx": 'import Foo from "./Foo";\nexport default function App(){return <Foo/>;}\n',
        "src/Foo.tsx": "export default function Foo(){ return <div/>; }\n",
    }
    repaired, actions = repair_unresolved_relative_imports(vfs)
    assert actions == []
    assert repaired == vfs


def test_repair_resolves_in_vfs_candidate_set():
    """Suffix parity with autofixer.js:53-58 — base, .ts, .tsx, /index.ts, /index.tsx."""
    base = "src/lib/util"
    for key in (base, base + ".ts", base + ".tsx", base + "/index.ts", base + "/index.tsx"):
        assert storage._resolves_in_vfs(base, {key}) is True
    assert storage._resolves_in_vfs(base, {"src/lib/other.tsx"}) is False


def test_repair_base_path_dir_join():
    """Dir-join base production parity with autofixer.js:51+67 — `src/App.tsx`
    importing `./screens/ScheduleBuilderScreen` → base `src/screens/ScheduleBuilderScreen`;
    the stub is written at base + `.tsx`."""
    repaired, actions = repair_unresolved_relative_imports({"src/App.tsx": _ORPHAN_APP_TSX})
    assert actions == ["stub src/screens/ScheduleBuilderScreen.tsx"]


def test_repair_extracts_both_import_forms():
    """Parity with the autofixer's ImportDeclaration walk: captures both
    `import X from "./rel"` and side-effect `import "./rel"`; a resolving import in
    the same file is NOT flagged."""
    vfs = {
        "src/App.tsx": (
            'import Default from "./screens/AScreen";\n'   # orphan, default form
            'import "./screens/BScreen";\n'                # orphan, side-effect form
            'import Real from "./Real";\n'                 # resolves — not flagged
            "export default function App(){return <Default/>;}\n"
        ),
        "src/Real.tsx": "export default function Real(){return <div/>;}\n",
    }
    repaired, actions = repair_unresolved_relative_imports(vfs)
    assert "src/screens/AScreen.tsx" in repaired
    assert "src/screens/BScreen.tsx" in repaired
    # The resolving import produced no action.
    assert all("./Real" not in a for a in actions)
    assert len([a for a in actions if a.startswith("stub")]) == 2


def test_stub_carries_no_anchor_id():
    """AD4: the stub source contains no `data-anchor-id` (the Vite plugin applies
    anchors on rebuild; raw stub is anchor-less, like the agent's own source)."""
    repaired, _ = repair_unresolved_relative_imports({"src/App.tsx": _ORPHAN_APP_TSX})
    assert "data-anchor-id" not in repaired["src/screens/ScheduleBuilderScreen.tsx"]


def test_stub_is_default_export_tsx(tmp_path):
    """AC11: the stub is a valid default-export TSX component, anchor-less, and
    round-trips through a filesystem read (what the export serialiser does) without
    raising."""
    repaired, _ = repair_unresolved_relative_imports({"src/App.tsx": _ORPHAN_APP_TSX})
    stub = repaired["src/screens/ScheduleBuilderScreen.tsx"]
    assert stub.startswith("export default function ScheduleBuilderScreen()")
    assert "data-anchor-id" not in stub
    # Serialiser reads raw TSX as text from _source/ — prove it reads back clean.
    p = tmp_path / "ScheduleBuilderScreen.tsx"
    p.write_text(stub, encoding="utf-8")
    assert p.read_text(encoding="utf-8") == stub


def test_repair_is_idempotent():
    """A second pass over an already-repaired map produces no further actions
    (guarantees the bounded loop converges)."""
    once, _ = repair_unresolved_relative_imports({"src/App.tsx": _ORPHAN_APP_TSX})
    twice, actions = repair_unresolved_relative_imports(once)
    assert actions == []
    assert twice == once


# ─── Detection (case-insensitive, real capital-C) ────────────────────────────


def test_detection_matches_real_capital_c_rollup_message():
    """AC3: the orphan-detection matches the REAL capital-C Rollup string (not only
    a lowercased fixture); a non-`could not resolve` error does NOT match."""
    assert storage._is_unresolved_relative_import_error(_REAL_ROLLUP_MSG) is True
    # Lowercase variant also matches (case-insensitive).
    assert storage._is_unresolved_relative_import_error(_REAL_ROLLUP_MSG.lower()) is True
    # A non-orphan build error must NOT match (it must re-raise, not repair).
    assert storage._is_unresolved_relative_import_error(
        "vite build exit=1: SyntaxError: Unexpected token (3:5)"
    ) is False
    # "Could not resolve entry module" (no relative specifier) must NOT match.
    assert storage._is_unresolved_relative_import_error(
        'vite build exit=1: Could not resolve entry module "index.html"'
    ) is False


# ─── Bounded rebuild wrapper ─────────────────────────────────────────────────


async def test_orphan_screen_import_is_repaired_to_green_build(monkeypatch):
    """REGRESSION (AC1/AC5): the 2/2-reproduced orphan is stubbed and
    vite_build_with_repair returns a dist map. FAILS on unfixed code (no repair
    path; vite_build raises ViteBuildError)."""
    build, state = _stateful_build(required_key="src/screens/ScheduleBuilderScreen.tsx")
    monkeypatch.setattr(storage, "vite_build", build)
    dist, repaired = await vite_build_with_repair({"src/App.tsx": _ORPHAN_APP_TSX})
    assert dist == {"index.html": "<html>built</html>"}
    assert "src/screens/ScheduleBuilderScreen.tsx" in repaired
    assert state["calls"] == 2  # initial fail + one rebuild after repair


async def test_clean_build_returns_source_unchanged(monkeypatch):
    """AC6: a clean build runs vite_build ONCE and returns the original map as the
    second element (no rebuild, no repair)."""
    state = {"calls": 0}

    async def _build(virtual_fs):
        state["calls"] += 1
        return {"index.html": "<html>built</html>"}

    monkeypatch.setattr(storage, "vite_build", _build)
    vfs = {"src/App.tsx": "export default function App(){ return <div/>; }\n"}
    dist, repaired = await vite_build_with_repair(vfs)
    assert state["calls"] == 1
    assert repaired == vfs


async def test_non_orphan_vite_error_reraises_unchanged(monkeypatch):
    """AC3: a bad-JSX ViteBuildError (no `could not resolve` relative) re-raises
    as-is — NOT UnresolvedImportRepairExhausted — and repair is never attempted."""
    state = {"calls": 0}

    async def _build(virtual_fs):
        state["calls"] += 1
        raise ViteBuildError("vite build exit=1: SyntaxError: Unexpected token (3:5)")

    monkeypatch.setattr(storage, "vite_build", _build)
    with pytest.raises(ViteBuildError) as ei:
        await vite_build_with_repair({"src/App.tsx": "broken"})
    assert not isinstance(ei.value, UnresolvedImportRepairExhausted)
    assert state["calls"] == 1  # no rebuild — failure is not an orphan


async def test_repair_noop_reraises_original_error(monkeypatch):
    """AC3: when the failure names an orphan the repair pass cannot fix (e.g. a
    dynamic import the regex does not capture), the ORIGINAL ViteBuildError
    re-raises unchanged — not the exhaustion class."""
    async def _build(virtual_fs):
        # Names a relative module, but the file has no static `import "<rel>"` the
        # repair pass can act on → repair returns no actions → original re-raised.
        raise ViteBuildError(
            'vite build exit=1: Could not resolve "./screens/GhostScreen" from "src/App.tsx"'
        )

    monkeypatch.setattr(storage, "vite_build", _build)
    # App.tsx imports a DIFFERENT, resolving module — nothing for repair to fix.
    vfs = {
        "src/App.tsx": 'import Foo from "./Foo";\nexport default function App(){return <Foo/>;}\n',
        "src/Foo.tsx": "export default function Foo(){return <div/>;}\n",
    }
    with pytest.raises(ViteBuildError) as ei:
        await vite_build_with_repair(vfs)
    assert not isinstance(ei.value, UnresolvedImportRepairExhausted)


async def test_build_with_repair_bounds_at_max_repairs(monkeypatch):
    """AC3: vite_build is invoked at most max_repairs + 1 = 3 times. A repair pass
    that keeps producing actions but never satisfies the build exhausts the bound."""
    state = {"calls": 0}

    async def _always_orphan(virtual_fs):
        state["calls"] += 1
        raise ViteBuildError(_REAL_ROLLUP_MSG)

    # Repair always claims an action so the loop never short-circuits on "no change".
    def _always_acts(virtual_fs):
        return dict(virtual_fs), ["stub src/screens/ScheduleBuilderScreen.tsx"]

    monkeypatch.setattr(storage, "vite_build", _always_orphan)
    monkeypatch.setattr(storage, "repair_unresolved_relative_imports", _always_acts)
    with pytest.raises(UnresolvedImportRepairExhausted):
        await vite_build_with_repair({"src/App.tsx": _ORPHAN_APP_TSX})
    assert state["calls"] == 3  # initial + max_repairs(2) rebuilds


async def test_exhaustion_raises_distinct_class(monkeypatch):
    """AC4: residual orphans after max_repairs → UnresolvedImportRepairExhausted
    (a ViteBuildError subclass) carrying the residual target."""
    async def _always_orphan(virtual_fs):
        raise ViteBuildError(_REAL_ROLLUP_MSG)

    def _always_acts(virtual_fs):
        return dict(virtual_fs), ["stub x"]

    monkeypatch.setattr(storage, "vite_build", _always_orphan)
    monkeypatch.setattr(storage, "repair_unresolved_relative_imports", _always_acts)
    with pytest.raises(UnresolvedImportRepairExhausted) as ei:
        await vite_build_with_repair({"src/App.tsx": _ORPHAN_APP_TSX})
    assert issubclass(UnresolvedImportRepairExhausted, ViteBuildError)
    assert "ScheduleBuilderScreen" in str(ei.value)


def test_vite_build_signature_unchanged():
    """AC9: vite_build's public signature is unchanged (one positional param)."""
    import inspect

    sig = inspect.signature(storage.vite_build)
    assert list(sig.parameters) == ["virtual_fs"]


def test_wrap_up_nudge_has_remove_imports_clause():
    """AC8: BOTH branches of _wrap_up_nudge carry the remove-orphan-import clause;
    the existing P5-03 hard-stop call (_wrap_up_nudge(0)) is unchanged in shape."""
    from app.design_agent.runner import _wrap_up_nudge

    hard = _wrap_up_nudge(0)   # iters_remaining <= 2 branch
    soft = _wrap_up_nudge(8)   # else (early-convergence) branch
    assert "remove that import" in hard
    assert "remove that import" in soft
    # Branch behaviour preserved: the hard-stop still says "STOP now".
    assert "STOP now" in hard


# ─── Route hook — fake Supabase DB ───────────────────────────────────────────

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

    Mirrors test_design_agent_storage.py's `env`. The route's `vite_build_with_repair`
    is REAL; tests stub the underlying `storage.vite_build` so the repair loop runs."""
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

    import app.db as db_mod
    return SimpleNamespace(proto=proto_mod, routes=routes_mod, db=db_mod)


def _checkpoints_for(prototype_id: int):
    from tests import _fake_supabase

    return _fake_supabase.get_fake_db().execute(
        f"SELECT id, bundle_url FROM prototype_checkpoints WHERE prototype_id = {prototype_id}"
    ).fetchall()


async def test_stage_complete_run_repairs_instead_of_failing(env, monkeypatch):
    """REGRESSION (AC1): _stage_complete_run over the orphan virtual_fs reaches
    'ready' (staged), NOT fail_prototype. FAILS on unfixed code (fail_prototype
    called on the ViteBuildError)."""
    build, _ = _stateful_build(required_key="src/screens/ScheduleBuilderScreen.tsx")
    monkeypatch.setattr(storage, "vite_build", build)
    monkeypatch.setattr(env.routes, "stage_bundle", _async_return("file:///x/index.html"))
    monkeypatch.setattr(env.routes, "reconcile_comments_on_checkpoint", lambda **kw: None)

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    await env.routes._stage_complete_run(
        prototype_id=pid, workspace_id="app", virtual_fs={"src/App.tsx": _ORPHAN_APP_TSX},
    )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "ready"
    assert _checkpoints_for(pid)  # a checkpoint was created (build did not fail)


async def test_repaired_source_is_staged(env, monkeypatch):
    """AC7: the `_source/` staged map is the REPAIRED one — the stub key present,
    asserting the route rebinds virtual_fs BEFORE the staging read."""
    staged: dict = {}

    async def _stage(*, prototype_id, checkpoint_id, files, sub_prefix=None):
        if sub_prefix == "_source":
            staged.update(files)
        return "file:///x/index.html"

    build, _ = _stateful_build(required_key="src/screens/ScheduleBuilderScreen.tsx")
    monkeypatch.setattr(storage, "vite_build", build)
    monkeypatch.setattr(env.routes, "stage_bundle", _stage)
    monkeypatch.setattr(env.routes, "reconcile_comments_on_checkpoint", lambda **kw: None)

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    await env.routes._stage_complete_run(
        prototype_id=pid, workspace_id="app", virtual_fs={"src/App.tsx": _ORPHAN_APP_TSX},
    )
    assert "src/screens/ScheduleBuilderScreen.tsx" in staged  # the stub was staged


async def test_build_repair_applied_logged_on_repair(env, monkeypatch, caplog):
    """AC5/AC10: build_repair_applied INFO line carries prototype_id + an action
    count only — no source content / import paths."""
    build, _ = _stateful_build(required_key="src/screens/ScheduleBuilderScreen.tsx")
    monkeypatch.setattr(storage, "vite_build", build)
    monkeypatch.setattr(env.routes, "stage_bundle", _async_return("file:///x/index.html"))
    monkeypatch.setattr(env.routes, "reconcile_comments_on_checkpoint", lambda **kw: None)

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    with caplog.at_level(logging.INFO):
        await env.routes._stage_complete_run(
            prototype_id=pid, workspace_id="app", virtual_fs={"src/App.tsx": _ORPHAN_APP_TSX},
        )
    applied = [r.getMessage() for r in caplog.records if r.getMessage().startswith("build_repair_applied")]
    assert applied, "build_repair_applied not logged on a repaired build"
    msg = applied[0]
    assert f"prototype_id={pid}" in msg
    assert "actions=1" in msg              # one stub written
    assert "ScheduleBuilderScreen" not in msg  # no source / import paths in the log


async def test_exhaustion_routes_to_fail_with_distinct_class(env, monkeypatch, caplog):
    """AC4: on exhaustion _stage_complete_run fails the row with
    error_class=UnresolvedImportRepairExhausted in the log and the row error."""
    async def _always_orphan(virtual_fs):
        raise ViteBuildError(_REAL_ROLLUP_MSG)

    def _always_acts(virtual_fs):
        return dict(virtual_fs), ["stub x"]

    monkeypatch.setattr(storage, "vite_build", _always_orphan)
    monkeypatch.setattr(storage, "repair_unresolved_relative_imports", _always_acts)

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    with caplog.at_level(logging.WARNING):
        await env.routes._stage_complete_run(
            prototype_id=pid, workspace_id="app", virtual_fs={"src/App.tsx": _ORPHAN_APP_TSX},
        )
    row = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "failed"
    assert "UnresolvedImportRepairExhausted" in (row["error"] or "")
    failed = [r.getMessage() for r in caplog.records if r.getMessage().startswith("vite_build_failed")]
    assert failed and "error_class=UnresolvedImportRepairExhausted" in failed[0]


def _async_return(value):
    async def _f(*args, **kwargs):
        return value
    return _f
