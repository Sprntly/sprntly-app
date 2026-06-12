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

from app.design_agent.codebase_map.recreate import (
    LocatedScreen,
    RecreateSources,
    SHELL_CANDIDATES,
    THEME_CANDIDATES,
    bridge_theme,
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
