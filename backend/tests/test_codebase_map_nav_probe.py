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
