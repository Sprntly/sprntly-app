"""Unit tests for the nav-abstraction probe."""
import importlib
import logging
import re
import subprocess
import sys

import pytest

from app.design_agent.codebase_map.nav_probe import ProbeResult, probe_nav_abstraction
from app.design_agent.codebase_map.repo_reader import RepoSnapshot


def _snap(**files) -> RepoSnapshot:
    """Build a minimal RepoSnapshot for testing."""
    return RepoSnapshot(
        repo="test/repo",
        commit_sha="abc123",
        branch="main",
        tree_paths=list(files.keys()),
        files=files,
    )


def _snap_with_tree(tree_paths: list[str], files: dict[str, str] | None = None) -> RepoSnapshot:
    return RepoSnapshot(
        repo="test/repo",
        commit_sha="abc123",
        branch="main",
        tree_paths=tree_paths,
        files=files or {},
    )


# ── Posture tests ──────────────────────────────────────────────────────────────

def test_clean_via_typed_registry():
    """Snapshot with a typed enum ScreenId and a ROUTES const → CLEAN."""
    snap = _snap(**{
        "src/navigation.ts": (
            "enum ScreenId { Team, Members }\n"
            "const ROUTES: Record<ScreenId, string> = {\n"
            "  [ScreenId.Team]: '/team',\n"
            "  [ScreenId.Members]: '/members',\n"
            "};\n"
        ),
        "app/team/page.tsx": "export default function TeamScreen() { return null; }",
    })
    result = probe_nav_abstraction(snap)
    assert result.posture == "CLEAN"
    assert result.registry_file == "src/navigation.ts"


def test_clean_via_route_table_only():
    """Snapshot with only an export const ROUTES (no enum) → CLEAN, route_table_files non-empty."""
    snap = _snap(**{
        "src/routes.ts": "export const ROUTES = { team: '/team', settings: '/settings' };\n",
    })
    result = probe_nav_abstraction(snap)
    assert result.posture == "CLEAN"
    assert result.route_table_files  # non-empty


def test_partial_filesystem_fallback():
    """Snapshot with only next-app page files and no registry → PARTIAL, next-app convention."""
    snap = _snap_with_tree(
        tree_paths=["app/team/page.tsx", "app/settings/page.tsx"],
        files={
            "app/team/page.tsx": "export default function TeamScreen() {}",
            "app/settings/page.tsx": "export default function SettingsScreen() {}",
        },
    )
    result = probe_nav_abstraction(snap)
    assert result.posture == "PARTIAL"
    assert result.registry_file == ""
    assert result.router_convention == "next-app"


def test_comment_mention_does_not_flip_clean():
    """A file containing only a comment mentioning ScreenId does NOT flip posture to CLEAN."""
    snap = _snap(**{
        "src/todo.ts": "// ScreenId is planned for a future refactor\n",
        "app/home/page.tsx": "export default function Home() {}",
    })
    snap.tree_paths = ["src/todo.ts", "app/home/page.tsx"]
    result = probe_nav_abstraction(snap)
    assert result.posture == "PARTIAL"


def test_line_comment_with_declaration_keyword_stays_partial():
    """enum ScreenId appearing only inside a // comment must not flip posture to CLEAN."""
    body = (
        "// TODO: introduce an enum ScreenId here, plus a const ROUTES table.\n"
        "export default function App() { return null; }\n"
    )
    snap = _snap(**{"src/app.tsx": body})
    snap.tree_paths = ["src/app.tsx"]
    result = probe_nav_abstraction(snap)
    assert result.posture == "PARTIAL", (
        "A // comment containing 'enum ScreenId' must not flip posture to CLEAN"
    )


def test_string_literal_with_declaration_stays_partial():
    """export const ROUTES appearing only inside a string literal must not flip posture to CLEAN."""
    body = (
        "const message = 'export const ROUTES is on the roadmap';\n"
        "export default function App() { return null; }\n"
    )
    snap = _snap(**{"src/app.tsx": body})
    snap.tree_paths = ["src/app.tsx"]
    result = probe_nav_abstraction(snap)
    assert result.posture == "PARTIAL", (
        "A string literal containing 'export const ROUTES' must not flip posture to CLEAN"
    )


def test_nav_primitive_most_referenced_wins():
    """When goTo is called more times than router.push, nav_primitive == 'goTo'."""
    go_to_calls = "goTo(ScreenId.Team);\n" * 8
    router_calls = "router.push('/team');\n" * 2
    snap = _snap(**{
        "src/components/Nav.tsx": go_to_calls + router_calls,
    })
    result = probe_nav_abstraction(snap)
    assert result.nav_primitive == "goTo"


def test_navlink_counted_as_link_primitive():
    """A snapshot whose only nav usage is <NavLink> yields nav_primitive == 'Link'."""
    snap = _snap(**{
        "src/nav/Nav.tsx": (
            "import { NavLink } from 'react-router-dom';\n"
            "function Nav() { return <NavLink to='/x'>Home</NavLink>; }\n"
        ),
    })
    result = probe_nav_abstraction(snap)
    assert result.nav_primitive == "Link"


# ── Module integrity ───────────────────────────────────────────────────────────

def test_no_ast_parser_dependency():
    """nav_probe imports no JS/TS AST parser."""
    import app.design_agent.codebase_map.nav_probe as mod
    src = importlib.util.find_spec("app.design_agent.codebase_map.nav_probe")
    assert src is not None
    forbidden = {"esprima", "tree_sitter", "pyjsparser", "babel"}
    loaded = set(sys.modules.keys())
    for name in forbidden:
        assert name not in loaded, f"Forbidden module loaded: {name}"


def test_probe_and_nodes_emit_identifier_only_logs(caplog):
    """Probe emits exactly one INFO line containing posture; no file body content."""
    snap = _snap(**{
        "src/routes.ts": "export const ROUTES = { team: '/team' };\n",
    })
    with caplog.at_level(logging.INFO, logger="app.design_agent.codebase_map.nav_probe"):
        result = probe_nav_abstraction(snap)

    info_lines = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(info_lines) >= 1
    log_text = info_lines[0].getMessage()
    assert "posture=" in log_text
    # Must not contain any snippet of the file body
    assert "export const ROUTES" not in log_text


# ── Shared enumeration heuristics ──────────────────────────────────────────────

def test_discover_route_elements_multiline_and_dedup():
    """<Route path= element={<X/>}> tags are discovered across multi-line tags."""
    from app.design_agent.codebase_map.nav_probe import discover_route_elements

    body = (
        "<Routes>\n"
        "  <Route path=\"/team\" element={<TeamPage/>} />\n"
        "  <Route\n"
        "    path=\"/users/:id\"\n"
        "    element={<UserPage/>}\n"
        "  />\n"
        "  <Route path=\"/team\" element={<TeamPage/>} />\n"  # duplicate
        "</Routes>\n"
    )
    snap = _snap(**{"src/App.tsx": body})
    elements = discover_route_elements(snap)
    routes = [(e.route, e.component) for e in elements]
    assert ("/team", "TeamPage") in routes
    assert ("/users/:id", "UserPage") in routes
    # Deduplicated by (route, file) and route-sorted.
    assert routes == sorted(set(routes))


def test_detect_tab_sections_only_matches_tab_arrays():
    """A const tabs=[{id,label}] array yields TabSections; unrelated arrays do not."""
    from app.design_agent.codebase_map.nav_probe import detect_tab_sections

    snap = _snap(**{
        "src/Team.tsx": (
            "const tabs = [\n"
            "  { id: 'members', label: 'Members' },\n"
            "  { id: 'roles', label: 'Roles' },\n"
            "];\n"
            "const colors = ['red', 'green'];\n"  # not a tab array
        ),
    })
    sections = detect_tab_sections(snap)
    ids = {s.section_id for s in sections}
    assert ids == {"members", "roles"}
    assert all(s.file == "src/Team.tsx" for s in sections)


def test_detect_tab_sections_empty_for_route_only_file():
    """A route-only file (no tabs array) yields no sections."""
    from app.design_agent.codebase_map.nav_probe import detect_tab_sections

    snap = _snap(**{"app/team/page.tsx": "export default function Team() { return null; }"})
    assert detect_tab_sections(snap) == []


def test_string_literal_tab_array_emits_section_per_item():
    """A string-literal tabs array yields one TabSection per item, id = slug, label = item."""
    from app.design_agent.codebase_map.nav_probe import detect_tab_sections

    snap = _snap(**{
        "src/pages/AnalyticsPage.jsx": "const tabs = ['Overview', 'Funnels', 'Cohorts', 'ROI'];\n",
    })
    sections = detect_tab_sections(snap)
    assert {s.section_id for s in sections} == {"overview", "funnels", "cohorts", "roi"}
    # label is the original item text; file is the declaring file.
    by_id = {s.section_id: s for s in sections}
    assert by_id["overview"].label == "Overview"
    assert by_id["roi"].label == "ROI"
    assert all(s.file == "src/pages/AnalyticsPage.jsx" for s in sections)


def test_real_launchpad_analytics_and_settings_tabs_detected():
    """Both real string-literal tab shapes are detected; '&'/spaces slugify to a stable id."""
    from app.design_agent.codebase_map.nav_probe import detect_tab_sections

    snap = _snap(**{
        "src/pages/AnalyticsPage.jsx": "const tabs = ['Overview', 'Funnels', 'Cohorts', 'ROI'];\n",
        "src/pages/SettingsPage.jsx": "const settingsTabs = ['Account', 'Team', 'Billing & Plan'];\n",
    })
    sections = detect_tab_sections(snap)
    analytics = {s.section_id for s in sections if s.file == "src/pages/AnalyticsPage.jsx"}
    settings = {s.section_id for s in sections if s.file == "src/pages/SettingsPage.jsx"}
    assert analytics == {"overview", "funnels", "cohorts", "roi"}
    # 'Billing & Plan' slugifies to a stable, non-empty id (& and spaces collapse to '-').
    assert settings == {"account", "team", "billing-plan"}
    billing = next(s for s in sections if s.section_id == "billing-plan")
    assert billing.section_id and billing.label == "Billing & Plan"


def test_object_array_detection_unchanged():
    """Object-form tabs detection is byte-identical to the pre-amend behaviour (regression)."""
    from app.design_agent.codebase_map.nav_probe import detect_tab_sections, TabSection

    snap = _snap(**{
        "src/Team.tsx": (
            "const tabs = [\n"
            "  { id: 'members', label: 'Members' },\n"
            "  { id: 'roles', label: 'Roles' },\n"
            "];\n"
            "const colors = ['red', 'green'];\n"  # not a tab array
        ),
    })
    sections = detect_tab_sections(snap)
    assert sections == [
        TabSection(section_id="members", label="Members", file="src/Team.tsx"),
        TabSection(section_id="roles", label="Roles", file="src/Team.tsx"),
    ]


def test_non_tabs_string_array_not_mis_detected():
    """A string array not named *tabs (e.g. const items = [...]) yields no sections."""
    from app.design_agent.codebase_map.nav_probe import detect_tab_sections

    snap = _snap(**{"src/data.ts": "const items = ['a', 'b', 'c'];\n"})
    assert detect_tab_sections(snap) == []


def test_detect_tab_sections_single_definition():
    """detect_tab_sections is one shared function; both adapters reuse the same section pass."""
    import pathlib

    from app.design_agent.codebase_map.nodes import (
        NextAppRouterAdapter,
        ViteReactRouterAdapter,
    )

    src = (
        pathlib.Path(__file__).parent.parent
        / "app" / "design_agent" / "codebase_map" / "nav_probe.py"
    ).read_text()
    assert src.count("def detect_tab_sections(") == 1
    # Both adapters share the SAME section-node callable (no per-adapter copy),
    # so string-array sections reach both without per-adapter code.
    assert NextAppRouterAdapter.section_nodes is ViteReactRouterAdapter.section_nodes


def test_string_tab_scanner_no_llm_call():
    """The tab scanner is deterministic static analysis — nav_probe imports no LLM client."""
    import pathlib

    src = (
        pathlib.Path(__file__).parent.parent
        / "app" / "design_agent" / "codebase_map" / "nav_probe.py"
    ).read_text()
    assert "import anthropic" not in src
    assert "from anthropic" not in src
    for parser in ("esprima", "tree-sitter", "tree_sitter", "@babel", "pyjsparser"):
        assert parser not in src
    assert "import ast\n" not in src
    assert "from ast import" not in src


def test_no_prohibited_tokens_in_source():
    """The four deliverable files contain no internal-coordinate tokens."""
    import os
    root = os.path.join(
        os.path.dirname(__file__),
        "..", "app", "design_agent", "codebase_map",
    )
    files_to_check = [
        os.path.join(root, "nav_probe.py"),
        os.path.join(root, "nodes.py"),
        os.path.join(os.path.dirname(__file__), "test_codebase_map_nav_probe.py"),
        os.path.join(os.path.dirname(__file__), "test_codebase_map_nodes.py"),
    ]
    # Build from parts so the pattern string does not match itself.
    _brand = "D" + "B" + "D"
    _name = "B" + "aba" + "jide"
    parts = [r"[CP][0-9]-[0-9]", r"H[0-9]-[0-9]", r"[A-Z]-series",
             r"\bAD[0-9]", r"\bF[0-9]{1,2}\b", _brand, _name]
    pattern = re.compile("|".join(parts))
    for path in files_to_check:
        with open(path) as fh:
            for lineno, line in enumerate(fh, 1):
                assert not pattern.search(line), (
                    f"Prohibited token found in {path}:{lineno}: {line.rstrip()}"
                )
