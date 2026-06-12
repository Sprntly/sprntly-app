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
    LocatedScreen,
    RecreateSources,
    SHELL_CANDIDATES,
    THEME_CANDIDATES,
    ThemeExpectations,
    _SCAFFOLD_DEFAULT_VALUES,
    assert_theme_landed,
    bridge_theme,
    build_theme_expectations,
    carry_brand_asset,
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
    assert extra == sorted(extra)  # sorted

    expected = (
        {"src/Home.tsx", "src/components/Hero.tsx", "src/components/Footer.tsx"}
        | set(SHELL_CANDIDATES)
        | set(THEME_CANDIDATES)
        | {"logo.svg"}
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

    bridge_records = [
        r for r in caplog.records
        if r.levelno == logging.INFO and "theme_bridge" in r.getMessage()
    ]
    assert len(bridge_records) == 1, f"expected 1 theme_bridge log, got {len(bridge_records)}"

    msg = bridge_records[0].getMessage()
    assert "has_globals=true" in msg
    assert "n_font_imports=1" in msg
    assert "is_v4=" in msg
    assert "has_tailwind_extend=" in msg

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
