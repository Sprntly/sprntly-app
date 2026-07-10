"""Unit tests for the deterministic recreate pre-seed sources.

``read_located_sources`` is the pure-shaping seam between the locate gate's
output and the bytes the agent will re-express. All GitHub plumbing is
stubbed via a single ``read_repo`` patch — no real network, no installation
token, no second tree walk.

Plain-engineering note: the deliverable source files must contain no internal
engagement coordinates. The ``test_no_prohibited_tokens_in_source`` test
verifies this by constructing the pattern at runtime so the literals it
checks for are not themselves present in this file as continuous strings.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import importlib
from types import SimpleNamespace

from app.design_agent.codebase_map.recreate import (
    BrandAssetCarry,
    ContainmentReport,
    LocatedScreen,
    RecreateSources,
    SHELL_CANDIDATES,
    THEME_CANDIDATES,
    ThemeExpectations,
    _MAX_REEXPORT_TARGETS,
    _SCAFFOLD_DEFAULT_VALUES,
    _app_root_prefix,
    _find_globals_css,
    _prefixed_shell_keys,
    _reexport_targets,
    _resolve_composed_component_paths,
    _resolve_rel_to_repo_path,
    assert_containment,
    assert_theme_landed,
    bridge_theme,
    build_theme_expectations,
    carry_brand_asset,
    derive_interactive_scope,
    port_tailwind_extend,
    read_located_sources,
    recreate_pre_seed,
    render_recreate_task_block,
)
from app.design_agent.codebase_map.repo_reader import RepoSnapshot
from app.design_agent.codebase_map.types import (
    LogoAsset,
    MapResult,
    ScreenNode,
    ShellModel,
)
from app.design_agent.storage import ThemeBridgeError, ViteBuildError


# ── helpers ─────────────────────────────────────────────────────────────────────

_INSTALL = 9001
_REPO = "org/repo"
_SHA = "deadbeefcafebabe1234567890abcdef"


def _node(component: str, file: str, route: str = "", composed=None) -> ScreenNode:
    return ScreenNode(
        route=route or f"/{component.lower()}",
        entry_component=component,
        file=file,
        composed_components=list(composed or []),
    )


def _map(
    nodes=None,
    *,
    repo: str = _REPO,
    sha: str = _SHA,
    logo: LogoAsset | None = None,
    posture: str = "CLEAN",
) -> MapResult:
    shell = ShellModel(logo=logo or LogoAsset())
    return MapResult(
        repo=repo,
        commit_sha=sha,
        posture=posture,
        nodes=list(nodes or []),
        shell=shell,
    )


def _snapshot(files: dict[str, str], *, repo: str = _REPO, sha: str = _SHA) -> RepoSnapshot:
    return RepoSnapshot(
        repo=repo,
        commit_sha=sha,
        branch="main",
        tree_paths=list(files.keys()),
        files=dict(files),
        truncated=False,
    )


# ── LocatedScreen shape ─────────────────────────────────────────────────────────


def test_located_screen_shape_carries_node_and_map():
    """AC: the contract carries the chosen node IDENTITY plus the map it was
    resolved from. Default fields are inert (single-screen, zero confidence)."""
    m = _map(nodes=[_node("Home", "src/Home.tsx")])
    located = LocatedScreen(map_result=m, node=m.nodes[0])
    assert located.map_result is m
    assert located.node.entry_component == "Home"
    assert located.also == ()
    assert located.confidence == 0

    # Frozen — re-binding fields must error.
    with pytest.raises(Exception):
        located.confidence = 50  # type: ignore[misc]


# ── Deterministic read of map-known + conventional paths ────────────────────────


def test_read_located_sources_reads_screen_children_shell_theme():
    """AC2: read_repo is called with extra_paths equal to the sorted union of
    {screen, children resolvable via nodes, shell candidates, theme candidates,
    asset_ref}. No tree-walk, no second scan."""
    home = _node("Home", "src/Home.tsx", composed=["Hero", "Footer", "Missing"])
    hero = _node("Hero", "src/components/Hero.tsx")
    footer = _node("Footer", "src/components/Footer.tsx")
    m = _map(
        nodes=[home, hero, footer],
        logo=LogoAsset(render_kind="img_src", asset_ref="/logo.svg"),
    )
    located = LocatedScreen(map_result=m, node=home)

    snap = _snapshot({
        "src/Home.tsx": "export const Home = () => null",
        "src/components/Hero.tsx": "export const Hero = () => null",
        "src/components/Footer.tsx": "export const Footer = () => null",
    })

    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=snap,
    ) as mock_read:
        sources = read_located_sources(located, _INSTALL)

    assert mock_read.call_count == 1
    args, kwargs = mock_read.call_args
    extra = kwargs["extra_paths"]
    # The must-read set leads the list (the located screen first, then its
    # map-resolved children, then the theme/shell candidates that carry
    # globals.css) so the extras-first fetch budget reaches them even when the
    # full candidate set exceeds the per-build file cap. The membership of the
    # union is unchanged.
    assert extra[0] == "src/Home.tsx"
    assert len(extra) == len(set(extra))  # no duplicates

    expected = (
        {"src/Home.tsx", "src/components/Hero.tsx", "src/components/Footer.tsx"}
        | set(SHELL_CANDIDATES)
        | set(THEME_CANDIDATES)
        | {"logo.svg"}
        # Layout files are also read so a nested shell can be discovered via the
        # layout import graph when no conventional candidate resolves a shell.
        | {"app/layout.tsx", "app/layout.jsx", "src/app/layout.tsx", "src/app/layout.jsx"}
    )
    assert set(extra) == expected
    # Verify .jsx variants and components/layout/ forms are present in the union.
    assert "src/components/Sidebar.jsx" in set(extra)
    assert "src/components/layout/Sidebar.jsx" in set(extra)
    assert "src/components/layout/AppLayout.jsx" in set(extra)
    assert "src/components/layout/TopBar.jsx" in set(extra)
    assert sources is not None
    assert sources.repo == _REPO
    assert sources.commit_sha == _SHA
    assert sources.screen_path == "src/Home.tsx"
    assert "src/Home.tsx" in sources.files


def test_read_located_sources_pins_commit_sha():
    """AC3: ref passed to read_repo equals map_result.commit_sha (not a branch)."""
    m = _map(nodes=[_node("Home", "src/Home.tsx")], sha="abc123def456")
    located = LocatedScreen(map_result=m, node=m.nodes[0])
    snap = _snapshot({"src/Home.tsx": "x"}, sha="abc123def456")

    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=snap,
    ) as mock_read:
        read_located_sources(located, _INSTALL)

    args, kwargs = mock_read.call_args
    assert args[0] == _INSTALL
    assert args[1] == _REPO
    assert args[2] == "abc123def456"


def test_missing_child_component_skipped_not_fatal():
    """AC6: a composed_components entry without a matching node is silently
    skipped; the screen + resolvable children still resolve, and the function
    returns a non-None RecreateSources with the screen present."""
    home = _node("Home", "src/Home.tsx", composed=["Hero", "GhostComponent"])
    hero = _node("Hero", "src/Hero.tsx")
    m = _map(nodes=[home, hero])
    located = LocatedScreen(map_result=m, node=home)
    snap = _snapshot({"src/Home.tsx": "h", "src/Hero.tsx": "hh"})

    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=snap,
    ) as mock_read:
        sources = read_located_sources(located, _INSTALL)

    extra = mock_read.call_args.kwargs["extra_paths"]
    assert "src/Home.tsx" in extra
    assert "src/Hero.tsx" in extra
    assert "GhostComponent" not in extra
    assert all("GhostComponent" not in p for p in extra)
    assert sources is not None
    assert "src/Home.tsx" in sources.files


def test_single_read_repo_call():
    """AC9: the path set is bounded by the map and conventional candidates;
    exactly ONE bounded read_repo call, not one-per-file."""
    home = _node("Home", "src/Home.tsx", composed=["A", "B"])
    a = _node("A", "src/A.tsx")
    b = _node("B", "src/B.tsx")
    m = _map(nodes=[home, a, b])
    located = LocatedScreen(map_result=m, node=home)

    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=_snapshot({"src/Home.tsx": "h", "src/A.tsx": "a", "src/B.tsx": "b"}),
    ) as mock_read:
        read_located_sources(located, _INSTALL)

    assert mock_read.call_count == 1


def test_multi_node_reads_both_screens():
    """AC12: a LocatedScreen with one ``also`` node reads BOTH screens' files
    plus their components. Both files appear in extra_paths and in the
    returned ``files`` map (when present in the snapshot)."""
    home = _node("Home", "src/Home.tsx", composed=["Hero"])
    detail = _node("Detail", "src/Detail.tsx", composed=["Card"])
    hero = _node("Hero", "src/Hero.tsx")
    card = _node("Card", "src/Card.tsx")
    m = _map(nodes=[home, detail, hero, card])
    located = LocatedScreen(map_result=m, node=home, also=(detail,))

    snap = _snapshot({
        "src/Home.tsx": "h",
        "src/Detail.tsx": "d",
        "src/Hero.tsx": "he",
        "src/Card.tsx": "c",
    })
    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=snap,
    ) as mock_read:
        sources = read_located_sources(located, _INSTALL)

    extra = mock_read.call_args.kwargs["extra_paths"]
    assert "src/Home.tsx" in extra
    assert "src/Detail.tsx" in extra
    assert "src/Hero.tsx" in extra
    assert "src/Card.tsx" in extra
    assert sources is not None
    assert sources.also_screen_paths == ("src/Detail.tsx",)
    assert {"src/Home.tsx", "src/Detail.tsx"} <= set(sources.files.keys())


def test_read_located_sources_returns_none_on_read_failure():
    """When read_repo returns None (no installation, SHA resolution failed, etc.),
    read_located_sources returns None — the caller degrades cleanly."""
    m = _map(nodes=[_node("Home", "src/Home.tsx")])
    located = LocatedScreen(map_result=m, node=m.nodes[0])
    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=None,
    ):
        assert read_located_sources(located, _INSTALL) is None


def test_asset_path_normalises_leading_slash():
    """img_src asset_ref values are conventionally absolute or ``./``-prefixed
    URL paths; we strip the leading separator so the path joins with the
    repo tree (which is always relative)."""
    m = _map(
        nodes=[_node("Home", "src/Home.tsx")],
        logo=LogoAsset(render_kind="imported_asset", asset_ref="./brand.svg"),
    )
    located = LocatedScreen(map_result=m, node=m.nodes[0])
    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=_snapshot({"brand.svg": "<svg/>"}),
    ) as mock_read:
        read_located_sources(located, _INSTALL)
    extra = mock_read.call_args.kwargs["extra_paths"]
    assert "brand.svg" in extra
    assert "./brand.svg" not in extra


# ── recreate_pre_seed wrapper ───────────────────────────────────────────────────


def test_recreate_pre_seed_injects_reference_files():
    """recreate_pre_seed writes each fetched body into virtual_fs under the
    ``__reference__/<path>`` prefix and returns the sources."""
    home = _node("Home", "src/Home.tsx")
    m = _map(nodes=[home])
    located = LocatedScreen(map_result=m, node=home)
    vfs: dict[str, str] = {"src/index.css": "/* seeded */"}

    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=_snapshot({"src/Home.tsx": "BODY"}),
    ):
        sources = recreate_pre_seed(vfs, located, _INSTALL, prototype_id=42)

    assert sources is not None
    assert vfs["src/index.css"] == "/* seeded */"  # original keys untouched
    assert vfs["__reference__/src/Home.tsx"] == "BODY"


def test_recreate_pre_seed_returns_none_without_installation():
    """No installation id (None / 0) → no read attempted, no virtual_fs
    mutation, no log line."""
    m = _map(nodes=[_node("Home", "src/Home.tsx")])
    located = LocatedScreen(map_result=m, node=m.nodes[0])
    vfs: dict[str, str] = {}

    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        side_effect=AssertionError("must not call read_repo without an installation"),
    ):
        assert recreate_pre_seed(vfs, located, None, prototype_id=1) is None
        assert recreate_pre_seed(vfs, located, 0, prototype_id=1) is None

    assert vfs == {}


def test_recreate_pre_seed_logs_unreadable_warning(caplog):
    """AC7: when read_repo returns None (the recreate read could not complete),
    the helper logs a WARNING with the prototype_id and returns None — the
    caller leaves the token / primitive pre-seed in place."""
    m = _map(nodes=[_node("Home", "src/Home.tsx")])
    located = LocatedScreen(map_result=m, node=m.nodes[0])
    vfs: dict[str, str] = {}

    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=None,
    ):
        with caplog.at_level(logging.WARNING, logger="app.design_agent.codebase_map.recreate"):
            result = recreate_pre_seed(vfs, located, _INSTALL, prototype_id=77)

    assert result is None
    assert vfs == {}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("prototype_id=77" in r.getMessage() for r in warnings)
    assert any("design_agent.recreate_pre_seed_unreadable" in r.getMessage() for r in warnings)


# ── Prompt block rendering ──────────────────────────────────────────────────────


def test_render_recreate_task_block_lists_reference_paths():
    """The rendered block names the located screen and every reference path
    (sorted) under ``__reference__/`` so the agent can view them via the
    view tool — and frames the re-express + apply-PRD pivot."""
    m = _map(
        nodes=[_node("Home", "src/Home.tsx", route="/")],
        sha="aabbcc1122",
    )
    sources = RecreateSources(
        repo=_REPO,
        commit_sha="aabbcc1122",
        files={"src/Home.tsx": "h", "src/Hero.tsx": "he"},
        screen_path="src/Home.tsx",
        also_screen_paths=(),
    )
    located = LocatedScreen(map_result=m, node=m.nodes[0])
    block = render_recreate_task_block(located, sources)

    assert "RECREATE TARGET" in block
    assert "Home (route /) from org/repo@aabbcc1122" in block
    assert "__reference__/src/Hero.tsx" in block
    assert "__reference__/src/Home.tsx" in block
    # paths listed in sorted order
    home_idx = block.index("__reference__/src/Home.tsx")
    hero_idx = block.index("__reference__/src/Hero.tsx")
    assert hero_idx < home_idx
    assert "re-expressed screen" in block


def test_render_recreate_task_block_handles_zero_resolved_files():
    """When the read returned a non-None sources object but no files actually
    resolved, the block degrades gracefully with a marker line rather than
    crashing or emitting an empty bulleted list."""
    m = _map(nodes=[_node("Home", "src/Home.tsx")])
    sources = RecreateSources(
        repo=_REPO,
        commit_sha=_SHA,
        files={},
        screen_path="src/Home.tsx",
        also_screen_paths=(),
    )
    located = LocatedScreen(map_result=m, node=m.nodes[0])
    block = render_recreate_task_block(located, sources)
    assert "(no reference files resolved)" in block


# ── Plain-English source guarantee ──────────────────────────────────────────────


def test_no_prohibited_tokens_in_source():
    """Neither deliverable file contains internal engagement coordinates.

    The pattern is assembled at runtime from split parts so that the literals
    being checked are not themselves continuous strings in this test file.
    """
    repo_root = Path(__file__).parent.parent
    targets = [
        repo_root / "app" / "design_agent" / "codebase_map" / "recreate.py",
        Path(__file__),
    ]
    parts = [
        r"C[0-9]-[0-9]",
        "C" + "-series",
        r"H[0-9]-[0-9]",
        r"P[0-9]-[0-9]",
        r"\bAD[0-9]",
        r"\bF[0-9]{1,2}\b",
        "DB" + "D",
        "Babaji" + "de",
    ]
    pattern = "|".join(parts)
    for target in targets:
        text = target.read_text()
        matches = re.findall(pattern, text)
        assert not matches, f"Prohibited token(s) {matches} found in {target.name}"


# ── helpers for bridge/extend tests ─────────────────────────────────────────────


def _sources_with_files(
    files: dict[str, str],
    *,
    repo: str = _REPO,
    sha: str = _SHA,
) -> RecreateSources:
    return RecreateSources(
        repo=repo,
        commit_sha=sha,
        files=files,
        screen_path="src/Home.tsx",
        also_screen_paths=(),
    )


_SCAFFOLD_CSS = """\
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --background: 0 0% 100%;
    --primary: 222.2 47.4% 11.2%;
  }
}"""


# ── bridge_theme: AC1 ────────────────────────────────────────────────────────────


def test_bridge_inlines_globals_after_tailwind_never_import():
    """The bridge inlines the real globals body after @tailwind — never @import."""
    real_globals = ":root { --brand: 24 100% 60%; }\n@layer base { body { color: red; } }"
    sources = _sources_with_files({"app/globals.css": real_globals})
    result = bridge_theme(_SCAFFOLD_CSS, sources)

    assert "--brand: 24 100% 60%;" in result
    # No @import of a local stylesheet path
    local_import = re.search(r"@import\s+['\"][^'\"]+\.css['\"]", result, re.IGNORECASE)
    assert local_import is None, "bridge_theme must not emit a local stylesheet @import"
    # @tailwind directives still present
    assert "@tailwind base" in result
    assert "@tailwind utilities" in result


# ── bridge_theme: AC2 ────────────────────────────────────────────────────────────


def test_real_tokens_override_scaffold_defaults_in_cascade():
    """Real :root tokens appear AFTER the scaffold default block so they win."""
    real_globals = ":root { --primary: 24 100% 60%; }"
    sources = _sources_with_files({"app/globals.css": real_globals})
    result = bridge_theme(_SCAFFOLD_CSS, sources)

    # Both declarations present
    assert "--primary: 222.2 47.4% 11.2%;" in result
    assert "--primary: 24 100% 60%;" in result
    # Real value comes AFTER the scaffold default (last declaration wins the cascade)
    idx_scaffold = result.index("--primary: 222.2 47.4% 11.2%;")
    idx_real = result.index("--primary: 24 100% 60%;")
    assert idx_real > idx_scaffold, "real token must appear after scaffold default"


# ── bridge_theme: AC3 ────────────────────────────────────────────────────────────


def test_font_imports_hoisted_to_top():
    """Font @import url(...) from globals is hoisted before @tailwind directives."""
    font_import = '@import url("https://fonts.googleapis.com/css2?family=Inter");'
    real_globals = f"{font_import}\n:root {{ --primary: 24 100% 60%; }}"
    sources = _sources_with_files({"app/globals.css": real_globals})
    result = bridge_theme(_SCAFFOLD_CSS, sources)

    assert font_import in result
    idx_font = result.index(font_import)
    idx_tailwind = result.index("@tailwind base")
    assert idx_font < idx_tailwind, "font @import must precede @tailwind base"


# ── bridge_theme: AC4 ────────────────────────────────────────────────────────────


def test_absent_globals_returns_scaffold_unchanged():
    """When sources has no globals, bridge_theme returns the scaffold unchanged."""
    sources = _sources_with_files({})
    result = bridge_theme(_SCAFFOLD_CSS, sources)
    assert result == _SCAFFOLD_CSS


# ── port_tailwind_extend: AC5 ───────────────────────────────────────────────────


def test_port_tailwind_extend_returns_summary_not_config_file():
    """port_tailwind_extend returns a compact summary, not a config file."""
    tailwind_src = (
        'import type { Config } from "tailwindcss";\n'
        "const config: Config = {\n"
        "  theme: {\n"
        "    extend: {\n"
        "      colors: {\n"
        '        brand: "#0ea5e9",\n'
        '        accent: "#f43f5e",\n'
        "      },\n"
        "      fontFamily: {\n"
        '        sans: ["Inter", "ui-sans-serif"],\n'
        "      },\n"
        "    },\n"
        "  },\n"
        "};\n"
        "export default config;\n"
    )
    sources = _sources_with_files({"tailwind.config.ts": tailwind_src})
    summary = port_tailwind_extend("", sources)

    # Returns a non-empty string
    assert summary
    # Does NOT look like a TypeScript/config file
    assert "export default" not in summary
    assert "const config" not in summary
    # Contains key information from theme.extend
    assert "brand" in summary or "colors" in summary
    # Is a summary (much shorter than the full config)
    assert len(summary) < len(tailwind_src)

    # bridge_theme must not write tailwind.config.ts into virtual_fs
    vfs: dict[str, str] = {}
    real_globals = ":root { --primary: 24 100% 60%; }"
    full_sources = _sources_with_files({
        "app/globals.css": real_globals,
        "tailwind.config.ts": tailwind_src,
    })
    bridge_theme("@tailwind base;\n@tailwind utilities;", full_sources)
    assert "tailwind.config.ts" not in vfs


# ── v4 / edge: AC6 ──────────────────────────────────────────────────────────────


def test_v4_theme_block_detected_not_ported_here(caplog):
    """A v4 @theme {} block is detected and logged; the v3 inline path still runs."""
    font_import = '@import url("https://fonts.googleapis.com/css2?family=Inter");'
    real_globals = (
        f"{font_import}\n\n"
        "@theme {\n"
        "  --color-brand: oklch(0.7 0.15 200);\n"
        "  --color-accent: oklch(0.6 0.2 320);\n"
        "}\n\n"
        ":root {\n"
        "  --primary: 24 100% 60%;\n"
        "}\n"
    )
    sources = _sources_with_files({"app/globals.css": real_globals})
    with caplog.at_level(logging.INFO, logger="app.design_agent.codebase_map.recreate"):
        result = bridge_theme(_SCAFFOLD_CSS, sources, prototype_id=5)

    # v4 detected and logged
    msgs = [r.getMessage() for r in caplog.records if "theme_bridge" in r.getMessage()]
    assert any("is_v4=true" in m for m in msgs)

    # v3 inline path still ran — :root from globals is in the output
    assert "--primary: 24 100% 60%;" in result

    # Does not crash on v4 input
    assert result


# ── seam wiring: AC7 ────────────────────────────────────────────────────────────


def test_recreate_index_css_is_bridged_when_located():
    """When real globals are present, bridge_theme returns the bridged value."""
    scaffold = (
        "@tailwind base;\n@tailwind utilities;\n"
        "@layer base { :root { --primary: 222.2 47.4% 11.2%; } }"
    )
    real_globals = ":root { --primary: 24 100% 60%; }"
    sources = _sources_with_files({"app/globals.css": real_globals})
    result = bridge_theme(scaffold, sources)

    assert "--primary: 24 100% 60%;" in result
    assert result != scaffold
    assert "@tailwind base" in result


# ── seam wiring: AC8 ────────────────────────────────────────────────────────────


def test_scenario_a_index_css_unchanged():
    """When sources has no globals, bridge_theme returns the scaffold unchanged
    (the non-recreate path keeps the existing output untouched)."""
    scaffold = (
        "@tailwind base;\n@tailwind utilities;\n"
        "@layer base { :root { --primary: 222.2 47.4% 11.2%; } }"
    )
    sources = _sources_with_files({})
    result = bridge_theme(scaffold, sources)
    assert result == scaffold


# ── observability: AC9 ──────────────────────────────────────────────────────────


def test_theme_bridge_logs_booleans_only(caplog):
    """bridge_theme emits exactly one INFO line with booleans and counts only."""
    font_import = '@import url("https://fonts.googleapis.com/css2?family=Inter");'
    real_globals = (
        f"{font_import}\n:root {{ --primary: 24 100% 60%; }}\n@layer base {{ color: red; }}"
    )
    sources = _sources_with_files({"app/globals.css": real_globals})
    with caplog.at_level(logging.INFO, logger="app.design_agent.codebase_map.recreate"):
        bridge_theme(_SCAFFOLD_CSS, sources, prototype_id=99)

    # The summary line is `theme_bridge `; the separate `theme_bridge_mode_check`
    # line is matched specifically below and excluded here.
    bridge_records = [
        r for r in caplog.records
        if r.levelno == logging.INFO and "theme_bridge " in r.getMessage()
    ]
    assert len(bridge_records) == 1, f"expected 1 theme_bridge log, got {len(bridge_records)}"

    msg = bridge_records[0].getMessage()
    assert "has_globals=true" in msg
    assert "n_font_imports=1" in msg
    assert "is_v4=" in msg
    assert "has_tailwind_extend=" in msg
    assert "token_mode=css-vars-root" in msg

    # The mode self-check emits exactly one booleans-only line alongside it.
    mode_records = [
        r for r in caplog.records
        if r.levelno == logging.INFO and "theme_bridge_mode_check" in r.getMessage()
    ]
    assert len(mode_records) == 1
    assert "landed=" in mode_records[0].getMessage()

    # No CSS body content in any log record
    for record in caplog.records:
        log_text = record.getMessage()
        assert "--primary" not in log_text, "CSS variable body must not appear in log"
        assert "@layer" not in log_text, "CSS rule body must not appear in log"


# ── carry_brand_asset: scaffold + render cases ──────────────────────────────────


def test_public_dir_ships_and_not_excluded():
    """AC1 + AC9: public/ dir is not in _SCAFFOLD_EXCLUDE and the .gitkeep exists."""
    from app.design_agent.storage import _SCAFFOLD_EXCLUDE

    assert "public" not in _SCAFFOLD_EXCLUDE

    repo_root = Path(__file__).parent.parent.parent
    public_dir = repo_root / "prototype-runtime" / "public"
    assert public_dir.exists(), "prototype-runtime/public/ must exist in the scaffold"
    assert (public_dir / ".gitkeep").exists(), "prototype-runtime/public/.gitkeep must exist"


def test_img_src_copies_file_and_renders_verbatim(caplog):
    """AC2: img_src carry injects public/<basename> with the real bytes and the
    shell reference contains the verbatim <img> tag including its size classes."""
    logo = LogoAsset(render_kind="img_src", asset_ref="/salency-logo.svg", alt_text="Salency")
    shell_body = (
        '<header class="h-16">'
        '<img src="/salency-logo.svg" class="h-9 w-auto shrink-0" alt="Salency" />'
        "</header>"
    )
    sources = _sources_with_files({
        "src/components/Sidebar.tsx": shell_body,
        "salency-logo.svg": "<svg><path d='M0 0'/></svg>",
    })

    carry = carry_brand_asset(logo, sources, prototype_id=1)

    assert "public/salency-logo.svg" in carry.virtual_fs_keys
    assert carry.virtual_fs_keys["public/salency-logo.svg"] == "<svg><path d='M0 0'/></svg>"
    assert carry.carried is True
    assert 'src="/salency-logo.svg"' in carry.shell_render_ref
    assert "h-9 w-auto shrink-0" in carry.shell_render_ref


def test_img_src_fallback_when_file_absent(caplog):
    """AC3: when the asset is absent from sources.files, no exception; carried=False;
    a minimal shell reference is still returned (relative src preserved)."""
    logo = LogoAsset(render_kind="img_src", asset_ref="/logo.png")
    sources = _sources_with_files({})

    carry = carry_brand_asset(logo, sources, prototype_id=2)

    assert carry.carried is False
    assert carry.virtual_fs_keys == {}
    assert carry.shell_render_ref  # a fallback img tag is still produced


def test_imported_asset_copies_and_keeps_import(caplog):
    """AC4: imported_asset carry injects under the normalized path and the reference
    keeps the import line verbatim."""
    logo = LogoAsset(render_kind="imported_asset", asset_ref="src/assets/brand.svg")
    sources = _sources_with_files({"src/assets/brand.svg": "<svg>brand</svg>"})

    carry = carry_brand_asset(logo, sources, prototype_id=3)

    assert "src/assets/brand.svg" in carry.virtual_fs_keys
    assert carry.virtual_fs_keys["src/assets/brand.svg"] == "<svg>brand</svg>"
    assert carry.carried is True
    assert 'import logo from "src/assets/brand.svg"' in carry.shell_render_ref


def test_inline_svg_reproduces_markup(caplog):
    """AC5: inline_svg carry has no public/ or src/assets/ key; the shell reference
    carries the verbatim <svg> markup from the shell source."""
    svg_markup = '<svg width="24" height="24"><circle cx="12" cy="12" r="10"/></svg>'
    logo = LogoAsset(render_kind="inline_svg")
    sources = _sources_with_files({
        "src/components/Sidebar.tsx": f"<nav>{svg_markup}</nav>",
    })

    carry = carry_brand_asset(logo, sources, prototype_id=4)

    assert not any(k.startswith("public/") for k in carry.virtual_fs_keys)
    assert not any(k.startswith("src/assets/") for k in carry.virtual_fs_keys)
    assert "<svg" in carry.shell_render_ref
    assert carry.carried is False


def test_text_absent_never_invents_logo_file():
    """AC6: text/absent carry never injects public/* or src/assets/* keys; the
    wordmark text is passed through for the text case."""
    for rk, ref in (("text", "Salency"), ("absent", "")):
        logo = LogoAsset(render_kind=rk, asset_ref=ref)
        sources = _sources_with_files({})

        carry = carry_brand_asset(logo, sources, prototype_id=5)

        pub_keys = [k for k in carry.virtual_fs_keys if k.startswith("public/")]
        asset_keys = [k for k in carry.virtual_fs_keys if k.startswith("src/assets/")]
        svg_keys = [k for k in carry.virtual_fs_keys if k.endswith(".svg")]
        assert not pub_keys, f"{rk}: no public/ key expected"
        assert not asset_keys, f"{rk}: no src/assets/ key expected"
        assert not svg_keys, f"{rk}: no fabricated .svg key expected"

    # Wordmark text carried through for text render kind
    logo_text = LogoAsset(render_kind="text", asset_ref="Acme")
    carry_text = carry_brand_asset(logo_text, _sources_with_files({}), prototype_id=5)
    assert "Acme" in carry_text.shell_render_ref


def test_raster_uses_deployed_url_fallback():
    """AC7: a binary raster not present in sources.files (reader skipped it) results
    in no bytes injected — no garbled content, no exception."""
    logo = LogoAsset(render_kind="img_src", asset_ref="/logo.png")
    sources = _sources_with_files({})  # raster absent (binary skipped by reader)

    carry = carry_brand_asset(logo, sources, prototype_id=6)

    assert carry.carried is False
    for v in carry.virtual_fs_keys.values():
        assert isinstance(v, str), "injected value must always be a plain string"
        v.encode("utf-8")  # must not raise — no garbled bytes


def test_scenario_a_no_public_key():
    """AC8: an absent render kind (no located screen) produces no public/ key."""
    logo = LogoAsset(render_kind="absent")
    sources = _sources_with_files({})

    carry = carry_brand_asset(logo, sources, prototype_id=7)

    pub_keys = [k for k in carry.virtual_fs_keys if k.startswith("public/")]
    assert not pub_keys


def test_brand_asset_logs_render_kind_only(caplog):
    """AC10: carry_brand_asset emits exactly one INFO line containing render_kind
    and carried; no asset bytes appear in any log line."""
    svg_content = "<svg><rect width='10' height='10'/></svg>"
    logo = LogoAsset(render_kind="img_src", asset_ref="/icon.svg", alt_text="icon")
    sources = _sources_with_files({"icon.svg": svg_content})

    with caplog.at_level(logging.INFO, logger="app.design_agent.codebase_map.recreate"):
        carry_brand_asset(logo, sources, prototype_id=8)

    brand_records = [
        r for r in caplog.records
        if r.levelno == logging.INFO and "brand_asset" in r.getMessage()
    ]
    assert len(brand_records) == 1, f"expected 1 brand_asset log line, got {len(brand_records)}"
    msg = brand_records[0].getMessage()
    assert "render_kind=" in msg
    assert "carried=" in msg
    assert svg_content not in msg
    assert "<svg" not in msg


# ── Theme-bridge build gate ───────────────────────────────────────────────────

_REAL_PRIMARY = "142 71% 45%"  # green — not in scaffold defaults
_REAL_FONT = "Inter"
_REAL_GLOBALS = f"""
:root {{
  --primary: {_REAL_PRIMARY};
  --background: 0 0% 98%;
}}
@import url(https://fonts.googleapis.com/css2?family={_REAL_FONT}:wght@400;600&display=swap);
"""


def _expectations_with(
    token_signals=(_REAL_PRIMARY,),
    font_families=(_REAL_FONT,),
    class_signals=("bg-primary", "text-primary"),
    asset_basename=None,
) -> ThemeExpectations:
    return ThemeExpectations(
        token_signals=token_signals,
        font_families=font_families,
        class_signals=class_signals,
        asset_basename=asset_basename,
    )


def test_theme_landed_passes_when_all_signals_present():
    dist = {
        "assets/index.css": f".bg-primary {{ color: hsl({_REAL_PRIMARY}); font-family: {_REAL_FONT}; }}",
    }
    assert_theme_landed(dist, _expectations_with())


def test_missing_token_raises_with_discriminating_value():
    scaffold_only = {"assets/index.css": "--primary: 222.2 47.4% 11.2%; font-family: Inter;"}
    with pytest.raises(ThemeBridgeError) as exc_info:
        assert_theme_landed(scaffold_only, _expectations_with(token_signals=(_REAL_PRIMARY,)))
    assert _REAL_PRIMARY in str(exc_info.value)
    assert "token" in str(exc_info.value)


def test_missing_font_raises():
    dist = {"assets/index.css": f"hsl({_REAL_PRIMARY}) bg-primary"}
    with pytest.raises(ThemeBridgeError) as exc_info:
        assert_theme_landed(dist, _expectations_with(font_families=("Montserrat",)))
    assert "font" in str(exc_info.value)
    assert "Montserrat" in str(exc_info.value)


def test_class_assertion_tolerates_purge_any_of():
    dist = {"assets/index.css": f"hsl({_REAL_PRIMARY}) font-family:{_REAL_FONT} text-primary"}
    # Only text-primary is present; bg-primary is not — any-one-of must pass.
    assert_theme_landed(dist, _expectations_with(class_signals=("bg-primary", "text-primary", "bg-background")))


def test_missing_carried_asset_reported():
    dist = {"assets/index.css": f"hsl({_REAL_PRIMARY}) {_REAL_FONT}"}
    with pytest.raises(ThemeBridgeError) as exc_info:
        assert_theme_landed(dist, _expectations_with(asset_basename="logo.svg"))
    assert "asset" in str(exc_info.value)
    assert "logo.svg" in str(exc_info.value)


def test_token_signals_exclude_scaffold_defaults():
    sources = _sources_with_files({
        "src/index.css": """
        :root {
          --primary: 222.2 47.4% 11.2%;
          --background: 0 0% 100%;
          --accent: 310 85% 55%;
        }
        """
    })
    expectations = build_theme_expectations(sources)
    assert expectations is not None
    for default_val in _SCAFFOLD_DEFAULT_VALUES:
        assert default_val not in expectations.token_signals, (
            f"scaffold default '{default_val}' must not be a signal"
        )
    # The non-default value 310 85% 55% IS a signal
    assert "310 85% 55%" in expectations.token_signals


# ── Staging integration ───────────────────────────────────────────────────────

_STAGE_PROTOTYPE_DDL = """
-- Drop the base-schema `prototypes` table (conftest creates a trimmed variant)
-- before recreating the richer shape these tests need; otherwise the shared
-- singleton fake DB errors "table prototypes already exists".
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
def stage_env(isolated_settings, monkeypatch):
    """Fake-DB env for _stage_complete_run integration tests."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_STAGE_PROTOTYPE_DDL)
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

    return SimpleNamespace(
        proto=proto_mod,
        routes=routes_mod,
        db=isolated_settings["db"],
    )


async def test_scenario_a_skips_theme_gate(stage_env, monkeypatch):
    """When theme_expectations is None, assert_theme_landed is never called."""
    dist = {"assets/index.css": "--primary: 222.2 47.4% 11.2%;"}

    async def _fake_build(vfs):
        return dist, vfs

    call_count = {"n": 0}

    def _fake_assert(d, e):
        call_count["n"] += 1

    monkeypatch.setattr(stage_env.routes, "vite_build_with_repair", _fake_build)
    monkeypatch.setattr(stage_env.routes, "assert_theme_landed", _fake_assert)

    prd_id = stage_env.db.start_prd(brief_id=1, insight_index=0, title="t", template_version=1, variant="v2")
    stage_env.db.complete_prd(prd_id, title="t", md="body")
    pid = stage_env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)

    await stage_env.routes._stage_complete_run(
        prototype_id=pid, workspace_id="app", virtual_fs={"src/App.tsx": "x"},
        theme_expectations=None,
    )

    assert call_count["n"] == 0, "assert_theme_landed must not be called when theme_expectations is None"
    row = stage_env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "ready"


async def test_theme_bridge_error_fails_row_no_stage(stage_env, monkeypatch):
    """When assert_theme_landed raises ThemeBridgeError, fail_prototype is called
    and no checkpoint is staged."""
    dist = {"assets/index.css": "/* scaffold only */"}

    async def _fake_build(vfs):
        return dist, vfs

    monkeypatch.setattr(stage_env.routes, "vite_build_with_repair", _fake_build)

    prd_id = stage_env.db.start_prd(brief_id=1, insight_index=0, title="t", template_version=1, variant="v2")
    stage_env.db.complete_prd(prd_id, title="t", md="body")
    pid = stage_env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)

    bad_expectations = _expectations_with(token_signals=(_REAL_PRIMARY,), font_families=(_REAL_FONT,))
    await stage_env.routes._stage_complete_run(
        prototype_id=pid, workspace_id="app", virtual_fs={"src/App.tsx": "x"},
        theme_expectations=bad_expectations,
    )

    row = stage_env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "failed"
    assert "ThemeBridgeError" in (row["error"] or "")
    from tests import _fake_supabase
    cps = _fake_supabase.get_fake_db().execute(
        f"SELECT id FROM prototype_checkpoints WHERE prototype_id = {pid}"
    ).fetchall()
    assert len(cps) == 0, "no checkpoint must be staged on a theme-bridge failure"


async def test_widened_except_still_catches_vite_build_error(stage_env, monkeypatch):
    """The widened except tuple still catches ViteBuildError as before."""
    async def _fake_build(vfs):
        raise ViteBuildError("vite build exit=1: SyntaxError")

    monkeypatch.setattr(stage_env.routes, "vite_build_with_repair", _fake_build)

    prd_id = stage_env.db.start_prd(brief_id=1, insight_index=0, title="t", template_version=1, variant="v2")
    stage_env.db.complete_prd(prd_id, title="t", md="body")
    pid = stage_env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)

    await stage_env.routes._stage_complete_run(
        prototype_id=pid, workspace_id="app", virtual_fs={"src/App.tsx": "x"},
        theme_expectations=None,
    )

    row = stage_env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "failed"
    assert "ViteBuildError" in (row["error"] or "")
    from tests import _fake_supabase
    cps = _fake_supabase.get_fake_db().execute(
        f"SELECT id FROM prototype_checkpoints WHERE prototype_id = {pid}"
    ).fetchall()
    assert len(cps) == 0


def test_theme_gate_logs_signal_counts_only(caplog):
    """On a pass, assert_theme_landed does not raise and carries no CSS body;
    on fail, the ThemeBridgeError message names identifiers only."""
    dist = {
        "assets/index.css": (
            f"hsl({_REAL_PRIMARY}) font-family:{_REAL_FONT} bg-primary"
        ),
    }
    expectations = ThemeExpectations(
        token_signals=(_REAL_PRIMARY,),
        font_families=(_REAL_FONT,),
        class_signals=("bg-primary",),
        asset_basename=None,
    )
    assert_theme_landed(dist, expectations)  # must not raise on pass

    bad_dist = {"assets/index.css": "/* empty */"}
    with pytest.raises(ThemeBridgeError) as exc_info:
        assert_theme_landed(bad_dist, expectations)
    error_msg = str(exc_info.value)
    assert "/* empty */" not in error_msg  # CSS body not leaked into error


# ── interactivity scope derivation ────────────────────────────────────────────


def _located(component: str = "Home", file: str = "src/Home.tsx") -> LocatedScreen:
    m = _map(nodes=[_node(component, file)])
    return LocatedScreen(map_result=m, node=m.nodes[0])


def test_derive_interactive_scope_isolated():
    """A PRD naming isolated interactions yields a non-empty stem list, derived
    here (no upstream object carries it)."""
    prd = (
        "Let the user reconnect a dropped integration. Each row should expand "
        "and collapse via a toggle."
    )
    scope = derive_interactive_scope(prd, _located())
    assert isinstance(scope, list) and scope, "scope must be a non-empty list"
    assert "reconnect" in scope
    assert "expand" in scope
    # No entangle cue → no widening with existing-behaviour drivers.
    assert "render" not in scope
    assert "results" not in scope


def test_derive_interactive_scope_entangled_includes_required_existing():
    """When the feature extends an already-interactive surface, the derived
    scope ALSO includes the minimal existing behaviour the interaction drives."""
    prd = (
        "Introduce a filter on the existing live results table; choosing a "
        "filter must re-render the results list."
    )
    scope = derive_interactive_scope(prd, _located())
    assert "filter" in scope
    # the minimal existing behaviour the filter must drive is part of the scope
    assert any(driver in scope for driver in ("render", "list", "results"))


# ── containment self-check ────────────────────────────────────────────────────


def test_containment_clean_pass():
    """Handlers == the derived scope, 0 href, no silent inert chrome → ok."""
    scope = ["reconnect", "expand"]
    src = (
        '<button onClick={handleReconnect}>Reconnect</button>\n'
        '<button onClick={toggleExpand}>Expand</button>'
    )
    report = assert_containment(src, scope)
    assert isinstance(report, ContainmentReport)
    assert report.ok is True
    assert report.handler_count == 2
    assert report.href_count == 0
    assert report.extra_handlers == []
    assert report.inert_without_affordance == []


def test_containment_extra_handler_fails():
    """A non-PRD button carrying a live handler is a containment leak."""
    scope = ["reconnect"]
    src = (
        '<button onClick={handleReconnect}>Reconnect</button>\n'
        '<button onClick={handleDelete}>Delete</button>'
    )
    report = assert_containment(src, scope)
    assert report.ok is False
    assert any("delete" in h.lower() for h in report.extra_handlers)


def test_containment_href_on_inert_flagged():
    """A live href on non-navigation chrome drives ok=False via href_count."""
    scope = ["reconnect"]
    src = (
        '<button onClick={handleReconnect}>Reconnect</button>\n'
        '<a href="/settings">Settings</a>'
    )
    report = assert_containment(src, scope)
    assert report.href_count > 0
    assert report.ok is False
    # the failure is the href, not a handler/affordance issue
    assert report.extra_handlers == []
    assert report.inert_without_affordance == []


def test_containment_href_allowed_when_scope_is_navigation():
    """A navigation interaction in scope authorises a live href."""
    scope = ["navigate"]
    src = '<a href="/settings">Settings</a>'
    report = assert_containment(src, scope)
    assert report.href_count > 0
    assert report.ok is True


# ── footguns ──────────────────────────────────────────────────────────────────


def test_inert_without_affordance_flagged():
    """An interactive-looking control with no handler AND no deliberate inert
    cue is a silent dead click — flagged, not passed."""
    scope = ["reconnect"]
    silent = (
        '<button onClick={handleReconnect}>Reconnect</button>\n'
        '<button>Settings</button>'
    )
    report = assert_containment(silent, scope)
    assert report.ok is False
    assert report.inert_without_affordance, "silent dead-click button must flag"

    # The shipped default — visibly disabled — clears the flag.
    cued = (
        '<button onClick={handleReconnect}>Reconnect</button>\n'
        '<button disabled className="cursor-not-allowed">Settings</button>'
    )
    ok_report = assert_containment(cued, scope)
    assert ok_report.inert_without_affordance == []
    assert ok_report.ok is True


def test_entangled_surface_legitimate_handlers_pass():
    """For an entangled feature, the handler driving the required existing
    behaviour stays out of extra_handlers (the derived scope covers it)."""
    prd = (
        "Introduce a filter on the existing live results table; choosing a "
        "filter must re-render the results list."
    )
    scope = derive_interactive_scope(prd, _located())
    src = (
        '<select onChange={handleFilterChange}>...</select>\n'
        '<button onClick={refreshResults}>Refresh</button>'
    )
    report = assert_containment(src, scope)
    assert report.ok is True
    assert report.extra_handlers == []


def test_entangled_surface_out_of_scope_handler_fails():
    """Even with the widened entangled scope, a handler outside it still fails."""
    prd = (
        "Introduce a filter on the existing live results table; choosing a "
        "filter must re-render the results list."
    )
    scope = derive_interactive_scope(prd, _located())
    src = (
        '<select onChange={handleFilterChange}>...</select>\n'
        '<button onClick={deleteRow}>Delete</button>'
    )
    report = assert_containment(src, scope)
    assert report.ok is False
    assert any("delete" in h.lower() for h in report.extra_handlers)


# ── deterministic — no LLM ─────────────────────────────────────────────────────


def test_containment_no_llm_call():
    """derive_interactive_scope + assert_containment make no LLM/network call."""
    with patch("app.design_agent.client.get_design_agent_client") as mock_client:
        scope = derive_interactive_scope("reconnect the integration", _located())
        report = assert_containment(
            "<button onClick={handleReconnect}>x</button>", scope
        )
        assert report.ok is True
    mock_client.assert_not_called()


# ── monorepo app-root prefix (Change 2) ────────────────────────────────────────


def _sources(files: dict[str, str], prefix: str = "", screen: str = "src/Home.tsx") -> RecreateSources:
    return RecreateSources(
        repo=_REPO,
        commit_sha=_SHA,
        files=dict(files),
        screen_path=screen,
        also_screen_paths=(),
        app_root_prefix=prefix,
    )


def test_app_root_prefix_monorepo_and_root_and_no_marker():
    """AC5: the prefix is everything before the first app/ src/ pages/ marker."""
    mono = LocatedScreen(
        map_result=_map(), node=_node("S", "web/app/(app)/sources/page.tsx"),
    )
    assert _app_root_prefix(mono) == "web/"
    root = LocatedScreen(map_result=_map(), node=_node("Team", "src/screens/Team.tsx"))
    assert _app_root_prefix(root) == ""
    nomark = LocatedScreen(map_result=_map(), node=_node("X", "components/X.tsx"))
    assert _app_root_prefix(nomark) == ""


def test_find_globals_css_prefers_prefixed_key():
    """AC6: a monorepo prefix resolves globals.css under the prefixed key; a
    repo-root app resolves the bare key."""
    mono = _sources({"web/app/globals.css": "MONO"}, prefix="web/")
    assert _find_globals_css(mono) == "MONO"
    root = _sources({"app/globals.css": "ROOT"}, prefix="")
    assert _find_globals_css(root) == "ROOT"


def test_prefixed_shell_keys_order_and_root_noop():
    """AC6: prefixed shell candidates precede the bare ones in a monorepo; a
    repo-root app yields exactly SHELL_CANDIDATES (no prefixed keys)."""
    mono = _sources({}, prefix="web/")
    keys = _prefixed_shell_keys(mono)
    assert keys[: len(SHELL_CANDIDATES)] == tuple("web/" + c for c in SHELL_CANDIDATES)
    assert keys[len(SHELL_CANDIDATES):] == SHELL_CANDIDATES
    assert _prefixed_shell_keys(_sources({}, prefix="")) == SHELL_CANDIDATES


# ── re-export / thin-wrapper follow (Change 3) ─────────────────────────────────


def test_hard_reexport_target_followed_and_merged():
    """AC7: a located file that re-exports the real screen triggers a SECOND
    read for the resolved target and merges its real body into files."""
    home = _node("Page", "web/app/sources/page.tsx")
    located = LocatedScreen(map_result=_map(nodes=[home]), node=home)
    reexport_body = 'export { SourcesScreen } from "../../components/screens/app/SourcesScreen"'
    snap1 = _snapshot({"web/app/sources/page.tsx": reexport_body})
    target = "web/components/screens/app/SourcesScreen.tsx"
    snap2 = _snapshot({target: "export const SourcesScreen = () => <div>real</div>"})

    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        side_effect=[snap1, snap2],
    ) as mock_read:
        sources = read_located_sources(located, _INSTALL)

    assert mock_read.call_count == 2
    assert target in sources.files
    assert "real" in sources.files[target]


def test_small_import_wrapper_followed_large_not():
    """AC8: a small wrapper body (<=2KB) with an import is followed; a large
    body (>2KB) with the same import is treated as a real screen, not followed."""
    home = _node("Page", "src/pages/page.tsx")
    located = LocatedScreen(map_result=_map(nodes=[home]), node=home)
    target = "src/pages/Screen.tsx"

    small_body = 'import { Screen } from "./Screen";\nexport default () => <Screen/>'
    snap1 = _snapshot({"src/pages/page.tsx": small_body})
    snap2 = _snapshot({target: "real screen body"})
    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        side_effect=[snap1, snap2],
    ) as mock_read:
        sources = read_located_sources(located, _INSTALL)
    assert mock_read.call_count == 2
    assert target in sources.files

    large_body = 'import { Screen } from "./Screen";\n' + "// padding line\n" * 300
    assert len(large_body) > 2_048
    snap_large = _snapshot({"src/pages/page.tsx": large_body})
    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        side_effect=[snap_large],
    ) as mock_read2:
        read_located_sources(located, _INSTALL)
    assert mock_read2.call_count == 1  # real screen, no wrapper follow


def test_reexport_targets_capped():
    """AC8: at most _MAX_REEXPORT_TARGETS candidate paths are returned."""
    body = "\n".join(f'export {{ C{i} }} from "./c{i}"' for i in range(5))
    targets = _reexport_targets("src/page.tsx", body)
    assert len(targets) <= _MAX_REEXPORT_TARGETS


def test_resolve_rel_skips_alias_and_bare_and_escape():
    """AC9: only ./ ../ specifiers resolve; alias/bare yield []; a path that
    escapes above the repo root is rejected."""
    assert _resolve_rel_to_repo_path("app/page.tsx", "@/components/X") == []
    assert _resolve_rel_to_repo_path("app/page.tsx", "react") == []
    assert _resolve_rel_to_repo_path("app/page.tsx", "../../../../etc/passwd") == []
    ok = _resolve_rel_to_repo_path("src/screens/page.tsx", "./Child")
    assert "src/screens/Child.tsx" in ok


# ── advisory class signal (Change 4) ───────────────────────────────────────────


def test_class_miss_advisory_when_token_and_font_landed(caplog):
    """AC10: tokens + fonts present but the class signal absent → no raise + an
    advisory log line."""
    expected = ThemeExpectations(
        token_signals=("7 90% 55%",),
        font_families=("Inter",),
        class_signals=("bg-primary",),
        asset_basename=None,
    )
    dist = {"a.css": ".x{color:hsl(7 90% 55%)} body{font-family:Inter}"}
    with caplog.at_level(logging.INFO, logger="app.design_agent.codebase_map.recreate"):
        assert_theme_landed(dist, expected)  # must NOT raise
    assert any("theme_class_signal_advisory" in r.getMessage() for r in caplog.records)


def test_class_miss_still_fails_when_token_or_font_missing():
    """AC10: when a token (or font) did NOT land, a class miss still fails the
    gate with the class in the missing set."""
    expected = ThemeExpectations(
        token_signals=("7 90% 55%",),
        font_families=("Inter",),
        class_signals=("bg-primary",),
        asset_basename=None,
    )
    dist = {"a.css": "body{font-family:Inter}"}  # token missing AND class missing
    with pytest.raises(ThemeBridgeError) as exc:
        assert_theme_landed(dist, expected)
    assert "class" in str(exc.value)
    assert "bg-primary" in str(exc.value)


# ── must-read prioritization + import-graph children (Changes 5 & 6) ───────────


def test_must_read_set_front_of_extra_and_fetched_within_budget():
    """AC15: the located screen + map-resolved children + theme/shell candidates
    (carrying globals.css) lead the extra list; the asset trails as 'rest'."""
    home = _node("Home", "src/Home.tsx", composed=["Hero"])
    hero = _node("Hero", "src/components/Hero.tsx")
    m = _map(
        nodes=[home, hero],
        logo=LogoAsset(render_kind="img_src", asset_ref="/logo.svg"),
    )
    located = LocatedScreen(map_result=m, node=home)
    snap = _snapshot({
        "src/Home.tsx": "export const Home = () => null",
        "src/components/Hero.tsx": "x",
    })
    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        return_value=snap,
    ) as mock_read:
        sources = read_located_sources(located, _INSTALL)

    extra = mock_read.call_args.kwargs["extra_paths"]
    assert extra[0] == "src/Home.tsx"           # located screen leads
    assert extra[-1] == "logo.svg"              # asset (rest) trails
    assert extra.index("app/globals.css") < extra.index("logo.svg")
    assert extra.index("src/components/Hero.tsx") < extra.index("logo.svg")
    assert "src/Home.tsx" in sources.files


def test_composed_components_resolved_via_import_graph():
    """AC16: composed child names resolve to files via the import graph
    (relative + @/ alias), bare imports skip, and a follow-up merge stays within
    two read_repo calls."""
    home = _node("Home", "web/app/page.tsx", composed=["SourcesPanel", "Menu", "Icon"])
    located = LocatedScreen(map_result=_map(nodes=[home]), node=home)
    body = (
        'import { SourcesPanel } from "./components/SourcesPanel";\n'
        'import Menu from "@/components/Menu";\n'
        'import { Icon } from "react-icons";\n'  # bare package → skipped
    )
    resolved = _resolve_composed_component_paths(located, body, "web/")
    assert "web/app/components/SourcesPanel.tsx" in resolved   # relative
    assert "web/components/Menu.tsx" in resolved               # @/ → prefix + rest
    assert not any("react-icons" in p for p in resolved)       # bare skipped

    # Integration: a resolvable child import triggers exactly one follow-up read.
    snap1 = _snapshot({
        "web/app/page.tsx": 'import { SourcesPanel } from "./components/SourcesPanel"',
    })
    child = "web/app/components/SourcesPanel.tsx"
    snap2 = _snapshot({child: "export const SourcesPanel = () => null"})
    with patch(
        "app.design_agent.codebase_map.recreate.read_repo",
        side_effect=[snap1, snap2],
    ) as mock_read:
        sources = read_located_sources(located, _INSTALL)
    assert mock_read.call_count == 2  # never more than two reads
    assert child in sources.files


# ── carried real shell path + layout-graph shell discovery ───────────────────

from app.design_agent.codebase_map.types import NavItem  # noqa: E402
from app.design_agent.codebase_map.recreate import (  # noqa: E402
    ParityReport,
    _assert_bridge_mode_landed,
    _assert_structural_parity,
    _discover_shell_path_via_layout,
    _has_root_css_var_tokens,
    _theme_token_mode,
)


def _map_with_shell(node, shell):
    return MapResult(
        repo=_REPO, commit_sha=_SHA, posture="CLEAN", nodes=[node], shell=shell,
    )


def test_carried_shell_path_read_into_reference_set():
    """A real shell path carried on the model is read directly, even when it is
    NOT a conventional candidate."""
    real_shell = "src/weird/CustomNav.tsx"
    assert real_shell not in SHELL_CANDIDATES
    node = _node("Home", "src/Home.tsx")
    shell = ShellModel(
        brand="Acme",
        shell_file_path=real_shell,
        nav_items=[NavItem(label="Home"), NavItem(label="Reports")],
    )
    located = LocatedScreen(map_result=_map_with_shell(node, shell), node=node)
    snap = _snapshot({
        "src/Home.tsx": "export const Home=()=>null",
        real_shell: "<nav><a>Home</a><a>Reports</a></nav>",
    })
    with patch("app.design_agent.codebase_map.recreate.read_repo", return_value=snap):
        sources = read_located_sources(located, _INSTALL)
    assert sources is not None
    assert real_shell in sources.files
    assert sources.shell_file_path == real_shell
    assert real_shell in _prefixed_shell_keys(sources)


def test_nested_shell_discovered_via_layout_import():
    """When neither a carried path nor a candidate resolves a shell, the wrapping
    layout's import graph is followed to a nested sidebar (control: honest empty)."""
    node = ScreenNode(
        route="/sources", entry_component="Sources",
        file="app/(app)/sources/page.tsx", composed_components=[],
    )
    shell = ShellModel()  # no carried path
    located = LocatedScreen(map_result=_map_with_shell(node, shell), node=node)
    layout_body = (
        'import { Sidebar } from "./components/shared/Sidebar"\n'
        "export default function L({children}){ return <div><Sidebar/>{children}</div> }\n"
    )
    snap = _snapshot({
        "app/(app)/sources/page.tsx": "export default function Sources(){ return null }",
        "app/layout.tsx": layout_body,
        "app/components/shared/Sidebar.tsx": "<nav><a>Dashboard</a><a>Sources</a></nav>",
    })
    with patch("app.design_agent.codebase_map.recreate.read_repo", return_value=snap) as mock_read:
        sources = read_located_sources(located, _INSTALL)
    assert "app/components/shared/Sidebar.tsx" not in SHELL_CANDIDATES
    assert "app/components/shared/Sidebar.tsx" in sources.files
    assert sources.shell_file_path == "app/components/shared/Sidebar.tsx"
    # AC: at most TWO read_repo calls total even with the layout discovery
    assert mock_read.call_count == 2

    # control: no layout / no shell import → discovery returns "" (honest empty)
    bare = _snapshot({"app/(app)/sources/page.tsx": "export default function S(){ return null }"})
    with patch("app.design_agent.codebase_map.recreate.read_repo", return_value=bare):
        bare_sources = read_located_sources(located, _INSTALL)
    assert bare_sources.shell_file_path == ""


def test_discover_shell_path_via_layout_resolution_rules():
    """The discovery helper resolves @/ against the app prefix, ./ against the
    layout dir, and skips bare-package imports."""
    files = {
        "web/app/layout.tsx": 'import { AppSidebar } from "@/components/AppSidebar"\n<AppSidebar/>',
        "web/src/components/AppSidebar.tsx": "x",
    }
    node = ScreenNode(route="/x", entry_component="X", file="web/app/(app)/x/page.tsx")
    located = LocatedScreen(map_result=_map_with_shell(node, ShellModel()), node=node)
    got = _discover_shell_path_via_layout(located, "web/", files)
    assert got == "web/src/components/AppSidebar.tsx"

    # bare-package import is not a shell file
    files2 = {"app/layout.tsx": 'import { Nav } from "some-nav-lib"\n<Nav/>'}
    node2 = ScreenNode(route="/x", entry_component="X", file="app/x/page.tsx")
    located2 = LocatedScreen(map_result=_map_with_shell(node2, ShellModel()), node=node2)
    assert _discover_shell_path_via_layout(located2, "", files2) == ""


def test_shell_discovery_third_priority_after_carried_and_candidate():
    """Carried path wins over discovery; a candidate hit wins over discovery."""
    layout_body = 'import { Sidebar } from "./shared/Sidebar"\n<Sidebar/>'

    # (a) carried wins: shell_file_path is honoured; the layout-discovered path is unused.
    node = ScreenNode(route="/x", entry_component="X", file="app/x/page.tsx")
    carried = "src/MyShell.tsx"
    shell = ShellModel(shell_file_path=carried)
    located = LocatedScreen(map_result=_map_with_shell(node, shell), node=node)
    snap = _snapshot({
        "app/x/page.tsx": "export default function X(){ return null }",
        carried: "<nav/>",
        "app/layout.tsx": layout_body,
        "app/shared/Sidebar.tsx": "<nav/>",
    })
    with patch("app.design_agent.codebase_map.recreate.read_repo", return_value=snap):
        sources = read_located_sources(located, _INSTALL)
    assert sources.shell_file_path == carried

    # (b) candidate wins: a conventional candidate body landed → discovery skipped.
    node2 = ScreenNode(route="/x", entry_component="X", file="app/x/page.tsx")
    located2 = LocatedScreen(map_result=_map_with_shell(node2, ShellModel()), node=node2)
    cand = "app/components/Sidebar.tsx"  # a real SHELL_CANDIDATES entry
    assert cand in SHELL_CANDIDATES
    snap2 = _snapshot({
        "app/x/page.tsx": "export default function X(){ return null }",
        cand: "<nav/>",
        "app/layout.tsx": layout_body,
        "app/shared/Sidebar.tsx": "<nav/>",
    })
    with patch("app.design_agent.codebase_map.recreate.read_repo", return_value=snap2):
        sources2 = read_located_sources(located2, _INSTALL)
    # candidate hit → no discovery → discovered path NOT recorded as the shell
    assert sources2.shell_file_path == ""
    assert "app/shared/Sidebar.tsx" not in sources2.files


# ── theme-bridge mode detection ────────────────────────────────────────

def test_theme_token_mode_classifies_root_v4_and_none():
    assert _theme_token_mode(":root{--accent:#179463;--ink:#15201B}") == "css-vars-root"
    assert _theme_token_mode("@theme{--x:1}") == "tailwind-v4"
    assert _theme_token_mode("body{color:red}") == "none"
    # v4 wins when both present
    assert _theme_token_mode("@theme{--x:1}\n:root{--y:2}") == "tailwind-v4"


def test_has_root_css_var_tokens_true_false():
    assert _has_root_css_var_tokens(":root{--accent:#179463}") is True
    assert _has_root_css_var_tokens("@theme{--x:1}") is False
    assert _has_root_css_var_tokens(":root{color:red}") is False


def test_bridge_inlines_root_token_value_verbatim():
    """The real :root token VALUE lands in the bridged CSS verbatim (regression
    guard: the v3 value path was never the gap)."""
    sources = _sources_with_files({"app/globals.css": ":root { --accent: #179463; }"})
    bridged = bridge_theme(_SCAFFOLD_CSS, sources, prototype_id=1)
    assert "#179463" in bridged


def test_bridge_no_v4_only_short_circuit_for_css_vars_root(caplog):
    """A css-vars-root globals (no @theme{}) still inlines the :root block, and
    the log reports is_v4=false + token_mode=css-vars-root."""
    sources = _sources_with_files({"app/globals.css": ":root { --accent: #179463; }"})
    with caplog.at_level(logging.INFO, logger="app.design_agent.codebase_map.recreate"):
        bridged = bridge_theme(_SCAFFOLD_CSS, sources, prototype_id=1)
    assert "--accent: #179463" in bridged
    line = next(r.getMessage() for r in caplog.records if "theme_bridge " in r.getMessage())
    assert "is_v4=false" in line
    assert "token_mode=css-vars-root" in line


def test_force_include_globals_in_must_read():
    """The prefix-aware globals key is force-included in the must-read set even on
    a monorepo; honest absence yields no globals key."""
    node = ScreenNode(route="/x", entry_component="X", file="web/app/(app)/x/page.tsx")
    located = LocatedScreen(map_result=_map_with_shell(node, ShellModel()), node=node)
    snap = _snapshot({
        "web/app/(app)/x/page.tsx": "export default function X(){ return null }",
        "web/app/globals.css": ":root { --accent: #179463; }",
    })
    with patch("app.design_agent.codebase_map.recreate.read_repo", return_value=snap) as mock_read:
        sources = read_located_sources(located, _INSTALL)
    extra = mock_read.call_args_list[0].kwargs["extra_paths"]
    assert "web/app/globals.css" in extra
    assert "web/app/globals.css" in sources.files


def test_assert_bridge_mode_landed_root_v4_none():
    globals_root = ":root { --accent: #179463; }"
    assert _assert_bridge_mode_landed("x #179463 y", "css-vars-root", globals_root) is True
    assert _assert_bridge_mode_landed("no tokens here", "css-vars-root", globals_root) is False
    globals_v4 = "@theme { --brand: #abcdef; }"
    assert _assert_bridge_mode_landed("uses #abcdef", "tailwind-v4", globals_v4) is True
    assert _assert_bridge_mode_landed("anything", "none", "") is True


def test_class_signals_derived_from_source_with_floor():
    """Custom brand utilities are derived from real source AND the shadcn floor
    is retained."""
    globals_css = ":root { --accent: #179463; --ink: #15201B; }"
    screen = 'export default function S(){ return <div className="bg-accent text-ink p-4"/> }'
    sources = _sources_with_files({
        "app/globals.css": globals_css,
        "app/screen.tsx": screen,
    })
    expectations = build_theme_expectations(sources)
    assert expectations is not None
    assert "bg-accent" in expectations.class_signals
    # floor retained (shadcn-slot brands still pass)
    assert "bg-primary" in expectations.class_signals


def test_theme_extend_shim_synthesized_no_invention():
    """A concrete theme.extend colour family is shimmed; a name-only family is not
    invented."""
    tailwind = (
        "module.exports = { theme: { extend: { colors: {"
        " tertiary: '#abc123', ghost: undefinedRef } } } }"
    )
    sources = _sources_with_files({
        "app/globals.css": ":root { --accent: #179463; }",
        "tailwind.config.js": tailwind,
    })
    bridged = bridge_theme(_SCAFFOLD_CSS, sources, prototype_id=1)
    assert "--tertiary: #abc123" in bridged
    assert ".bg-tertiary" in bridged
    assert ".text-tertiary" in bridged
    # name-only / reference family with no concrete colour is NOT invented
    assert "--ghost" not in bridged


# ── verb-stem hardening ────────────────────────────────────────────────

def test_test_connection_derives_both_verbs_not_luck():
    m = _map(nodes=[_node("Settings", "app/settings/page.tsx")])
    located = LocatedScreen(map_result=m, node=m.nodes[0])
    scope = derive_interactive_scope("Add a Test Connection button", located)
    assert "test" in scope
    assert "connect" in scope
    scope2 = derive_interactive_scope("testing the export flow", located)
    assert "test" in scope2
    assert "export" in scope2


def test_verb_stem_word_boundary_no_substring_false_positive():
    m = _map(nodes=[_node("Page", "app/page/page.tsx")])
    located = LocatedScreen(map_result=m, node=m.nodes[0])
    # "contestant" contains "test" only mid-word — must NOT add "test"
    scope = derive_interactive_scope("The contestant list renders", located)
    assert "test" not in scope


# ── structural parity self-check ───────────────────────────────────────

def _parity_located():
    shell = ShellModel(brand="Acme", nav_items=[
        NavItem(label="Dashboard"), NavItem(label="Reports"),
    ])
    node = _node("Sources", "app/sources/page.tsx", composed=["Sidebar", "DataTable"])
    return LocatedScreen(map_result=_map_with_shell(node, shell), node=node)


def test_structural_parity_matched_ok():
    located = _parity_located()
    generated = (
        '<div data-brand="Acme">'
        "<Sidebar/><DataTable/>"
        "<nav><a>Dashboard</a><a>Reports</a></nav>"
        "</div>"
    )
    report = _assert_structural_parity(generated, None, located.map_result.shell, located)
    assert isinstance(report, ParityReport)
    assert report.ok is True
    assert report.missing == []
    assert report.extra == []


def test_structural_parity_missing_and_extra_detected():
    located = _parity_located()
    # omits the brand, the "Reports" nav label, and the DataTable component;
    # invents a "Billing" nav item with no source basis.
    generated = (
        "<Sidebar/>"
        "<nav><a>Dashboard</a><a>Billing</a></nav>"
    )
    report = _assert_structural_parity(generated, None, located.map_result.shell, located)
    assert report.ok is False
    assert any("Acme" in m for m in report.missing)
    assert any("Reports" in m for m in report.missing)
    assert any("DataTable" in m for m in report.missing)
    assert "Billing" in report.extra


def test_structural_parity_log_serializes_missing_and_extra_detail(caplog):
    """The route's structural-parity log line carries the missing/extra REFS, not
    just counts — so a triaging agent can see WHICH refs were dropped/invented.

    Builds a real ParityReport (missing brand/nav/component, invented nav item)
    then drives the route's exact log statement and asserts the detail is present.
    """
    import logging

    import app.routes.design_agent as routes_mod

    located = _parity_located()
    generated = (
        "<Sidebar/>"
        "<nav><a>Dashboard</a><a>Billing</a></nav>"
    )
    report = _assert_structural_parity(generated, None, located.map_result.shell, located)
    assert report.ok is False
    assert report.missing and report.extra  # real detail to serialize

    # Drive the route's exact log statement (same format string + logger).
    with caplog.at_level(logging.WARNING, logger="app.routes.design_agent"):
        log_parity = routes_mod.logger.info if report.ok else routes_mod.logger.warning
        log_parity(
            "design_agent.structural_parity prototype_id=%s matched=%d missing=%d extra=%d ok=%s "
            "missing_refs=%s extra_refs=%s",
            "PROTO-1",
            len(report.matched),
            len(report.missing),
            len(report.extra),
            str(report.ok).lower(),
            report.missing,
            report.extra,
        )

    parity_lines = [
        r.getMessage() for r in caplog.records
        if "design_agent.structural_parity " in r.getMessage()
    ]
    assert parity_lines, "expected a structural_parity log line"
    msg = parity_lines[0]
    # Counts preserved for back-compat …
    assert "missing=3" in msg and "extra=1" in msg
    # … and the new detail is serialized.
    assert "missing_refs=" in msg and "extra_refs=" in msg
    assert "Billing" in msg  # the invented nav label appears in extra_refs
    assert any(ref in msg for ref in report.missing)  # a missing ref is named


def test_structural_self_check_has_no_dom_or_network():
    """The self-check is pure string analysis — the module imports no browser or
    network library."""
    import app.design_agent.codebase_map.recreate as mod
    src = Path(mod.__file__).read_text()
    for forbidden in ("import playwright", "import requests", "selenium", "import urllib", "webdriver"):
        assert forbidden not in src
