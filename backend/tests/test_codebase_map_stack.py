"""Unit tests for stack detection + the pluggable enumerator-adapter registry.

Covers deterministic per-stack detection, alias-root capture, adapter selection,
per-stack import resolution, the unknown-stack low-confidence fallback, the
unreadable-stack loud decline, the shared-heuristic single-definition invariant,
and the committed-code integrity grep.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.design_agent.codebase_map import nav_probe, service
from app.design_agent.codebase_map.nodes import (
    NextAppRouterAdapter,
    ViteReactRouterAdapter,
)
from app.design_agent.codebase_map.repo_reader import RepoSnapshot
from app.design_agent.codebase_map.service import build_map, clear_map_cache
from app.design_agent.codebase_map.stack import (
    ADAPTERS,
    EnumeratorAdapter,
    LLMDiscoveryFallbackAdapter,
    StackProfile,
    UnreadableStackError,
    detect_stack,
    select_adapter,
)


def _snap(tree_paths: list[str] | None = None, **files) -> RepoSnapshot:
    paths = tree_paths if tree_paths is not None else list(files.keys())
    return RepoSnapshot(
        repo="test/repo",
        commit_sha="sha-aaa",
        branch="main",
        tree_paths=paths,
        files=files,
    )


@pytest.fixture(autouse=True)
def _flush_cache():
    clear_map_cache()
    yield
    clear_map_cache()


# ── detection ──────────────────────────────────────────────────────────────────

def test_detect_next_app():
    """package.json with next + app/**/page.tsx → next-app, high, ts."""
    snap = _snap(
        tree_paths=["package.json", "app/dashboard/page.tsx"],
        **{
            "package.json": '{"dependencies": {"next": "15.0.0", "react": "19.0.0"}}',
            "app/dashboard/page.tsx": "export default function Dashboard() {}",
        },
    )
    profile = detect_stack(snap)
    assert profile.stack == "next-app"
    assert profile.confidence == "high"
    assert profile.language == "ts"


def test_detect_vite_react_router():
    """package.json with vite + react-router-dom + a <Route path=> table → vite, high."""
    snap = _snap(
        tree_paths=["package.json", "src/App.tsx"],
        **{
            "package.json": '{"dependencies": {"vite": "5.0.0", "react-router-dom": "6.0.0"}}',
            "src/App.tsx": (
                "export default function App() {\n"
                "  return <Routes>\n"
                "    <Route path=\"/team\" element={<TeamPage/>} />\n"
                "  </Routes>;\n"
                "}\n"
            ),
        },
    )
    profile = detect_stack(snap)
    assert profile.stack == "vite-react-router"
    assert profile.confidence == "high"


def test_detect_unknown_js_ts():
    """A JS/TS package.json matching no first-class adapter → unknown-js-ts, low."""
    snap = _snap(
        tree_paths=["package.json", "src/index.js", "src/server.js"],
        **{
            "package.json": '{"dependencies": {"express": "4.0.0"}}',
            "src/index.js": "const app = require('express')();\n",
            "src/server.js": "module.exports = {};\n",
        },
    )
    profile = detect_stack(snap)
    assert profile.stack == "unknown-js-ts"
    assert profile.confidence == "low"
    assert profile.reason  # non-empty capability signal


def test_detect_unreadable_non_js_ts():
    """A Rails-shaped repo with no JS/TS app → unreadable, non-js-ts."""
    snap = _snap(
        tree_paths=["Gemfile", "config/routes.rb", "app/controllers/home_controller.rb"],
        **{
            "Gemfile": "source 'https://rubygems.org'\ngem 'rails'\n",
            "config/routes.rb": "Rails.application.routes.draw do\n  root 'home#index'\nend\n",
        },
    )
    profile = detect_stack(snap)
    assert profile.stack == "unreadable"
    assert profile.language == "non-js-ts"
    assert profile.reason


def test_detect_unreadable_other_markers():
    """Django and Flutter markers also classify unreadable."""
    django = _snap(
        tree_paths=["manage.py", "app/templates/home.html"],
        **{"manage.py": "import django\n"},
    )
    assert detect_stack(django).stack == "unreadable"

    flutter = _snap(
        tree_paths=["pubspec.yaml", "lib/main.dart"],
        **{"pubspec.yaml": "name: my_app\n"},
    )
    assert detect_stack(flutter).stack == "unreadable"


def test_alias_roots_from_tsconfig():
    """tsconfig baseUrl + paths populate alias_roots; absent tsconfig yields {}."""
    with_ts = _snap(
        tree_paths=["package.json", "tsconfig.json", "app/page.tsx"],
        **{
            "package.json": '{"dependencies": {"next": "15.0.0"}}',
            "tsconfig.json": (
                "{\n"
                "  // path aliases\n"
                '  "compilerOptions": { "baseUrl": ".", "paths": { "@/*": ["src/*"] } },\n'
                "}\n"
            ),
            "app/page.tsx": "export default function Home() {}",
        },
    )
    profile = detect_stack(with_ts)
    assert profile.alias_roots == {"@/*": "src/*"}

    without_ts = _snap(
        tree_paths=["package.json", "app/page.tsx"],
        **{
            "package.json": '{"dependencies": {"next": "15.0.0"}}',
            "app/page.tsx": "export default function Home() {}",
        },
    )
    assert detect_stack(without_ts).alias_roots == {}


def test_detection_is_deterministic():
    snap = _snap(
        tree_paths=["package.json", "app/team/page.tsx"],
        **{
            "package.json": '{"dependencies": {"next": "15.0.0"}}',
            "app/team/page.tsx": "export default function Team() {}",
        },
    )
    assert detect_stack(snap) == detect_stack(snap)


# ── adapter selection + protocol ───────────────────────────────────────────────

def test_select_adapter_per_stack():
    assert select_adapter(StackProfile(stack="next-app")) is ADAPTERS["next-app"]
    assert select_adapter(StackProfile(stack="next-pages")) is ADAPTERS["next-app"]
    assert select_adapter(StackProfile(stack="vite-react-router")) is ADAPTERS["vite-react-router"]
    assert isinstance(select_adapter(StackProfile(stack="unknown-js-ts")), LLMDiscoveryFallbackAdapter)
    # Empty/neutral sentinel (detection errored) → deterministic Next default.
    assert select_adapter(StackProfile(stack="")) is ADAPTERS["next-app"]


def test_adapters_satisfy_protocol():
    assert isinstance(NextAppRouterAdapter(), EnumeratorAdapter)
    assert isinstance(ViteReactRouterAdapter(), EnumeratorAdapter)
    assert isinstance(LLMDiscoveryFallbackAdapter(), EnumeratorAdapter)


# ── import resolution (AC8) ────────────────────────────────────────────────────

def test_resolve_import_per_stack_alias_relative_skip_bare():
    snap = _snap(
        tree_paths=[
            "src/components/Button.tsx",
            "src/widgets/Card.tsx",
            "src/screens/MemberRow.tsx",
        ],
        **{
            "src/components/Button.tsx": "export default function Button() {}",
            "src/widgets/Card.tsx": "export default function Card() {}",
            "src/screens/MemberRow.tsx": "export default function MemberRow() {}",
        },
    )
    alias_roots = {"@/*": "src/*"}
    next_adapter = NextAppRouterAdapter()
    vite_adapter = ViteReactRouterAdapter()

    # Alias resolution against the baseUrl/alias root.
    assert next_adapter.resolve_import(
        "@/components/Button", "app/team/page.tsx", alias_roots, snap,
    ) == "src/components/Button.tsx"
    assert vite_adapter.resolve_import(
        "@/widgets/Card", "src/App.tsx", alias_roots, snap,
    ) == "src/widgets/Card.tsx"

    # Relative resolution against the from-file dir with the extension list.
    assert next_adapter.resolve_import(
        "./MemberRow", "src/screens/Team.tsx", {}, snap,
    ) == "src/screens/MemberRow.tsx"
    assert vite_adapter.resolve_import(
        "../widgets/Card", "src/screens/Team.tsx", {}, snap,
    ) == "src/widgets/Card.tsx"

    # Bare package specifiers are not repo files → None.
    assert next_adapter.resolve_import("react", "src/App.tsx", alias_roots, snap) is None
    assert vite_adapter.resolve_import("lucide-react", "src/App.tsx", alias_roots, snap) is None


# ── unknown-stack fallback (AC9) ───────────────────────────────────────────────

def test_unknown_stack_low_confidence_partial_posture_signal():
    """unknown-js-ts: low-confidence fallback nodes, posture PARTIAL, reason surfaced."""
    snap = _snap(
        tree_paths=["package.json", "src/index.js"],
        **{
            "package.json": '{"dependencies": {"express": "4.0.0"}}',
            "src/index.js": "const app = require('express')();\n",
        },
    )
    profile = detect_stack(snap)
    assert profile.confidence == "low"
    assert profile.reason

    # The fallback adapter enumerates via injected discovery (no network).
    def _fake_discover(_snapshot):
        return [
            {"route": "/home", "entry_component": "Home", "file": "src/Home.js"},
            {"route": "/about", "entry_component": "About", "file": "src/About.js"},
        ]

    adapter = LLMDiscoveryFallbackAdapter(discover=_fake_discover)
    nodes = adapter.enumerate_nodes(snap, nav_probe.ProbeResult())
    assert [n.route for n in nodes] == ["/home", "/about"]
    assert all(n.kind == "route" for n in nodes)

    # build_map forces PARTIAL for an unknown-js-ts repo regardless of the probe.
    read_mock = MagicMock(return_value=snap)
    nodes_mock = MagicMock(return_value=nodes)  # avoid any real LLM call
    with patch.object(service, "read_repo", read_mock), \
         patch.object(service, "extract_nodes", nodes_mock):
        result = build_map(123, "test/repo", "test/repo@main")
    assert result is not None
    assert result.posture == "PARTIAL"


# ── unreadable decline (AC10) ──────────────────────────────────────────────────

def test_unreadable_stack_declines_loudly_no_nodes():
    """An unreadable stack emits no nodes and build_map raises a typed decline."""
    snap = _snap(
        tree_paths=["Gemfile", "config/routes.rb"],
        **{
            "Gemfile": "gem 'rails'\n",
            "config/routes.rb": "Rails.application.routes.draw do\nend\n",
        },
    )
    profile = detect_stack(snap)
    assert profile.stack == "unreadable"

    # Direct enumeration via the selected adapter emits nothing.
    adapter = select_adapter(profile)
    assert adapter.enumerate_nodes(snap, nav_probe.ProbeResult()) == []

    # build_map surfaces the loud decline as a typed error (callers degrade).
    read_mock = MagicMock(return_value=snap)
    with patch.object(service, "read_repo", read_mock):
        with pytest.raises(UnreadableStackError) as exc:
            build_map(123, "test/repo", "test/repo@main")
    assert str(exc.value)  # carries a non-empty reason


# ── shared heuristics single definition (AC11) ─────────────────────────────────

def test_shared_heuristics_single_definition():
    """The section + resolver utilities are single shared functions both adapters call."""
    # Both adapters reference the SAME shared function objects, not per-adapter copies.
    assert NextAppRouterAdapter.section_nodes is ViteReactRouterAdapter.section_nodes
    assert NextAppRouterAdapter.resolver is ViteReactRouterAdapter.resolver

    # The tab-section + route-element heuristics are defined exactly once across
    # the whole package (no duplicate definitions in another module).
    pkg_dir = Path(nav_probe.__file__).parent
    for fn_name in ("def detect_tab_sections", "def discover_route_elements"):
        hits = [p for p in pkg_dir.glob("*.py") if fn_name in p.read_text()]
        assert hits == [pkg_dir / "nav_probe.py"], (
            f"{fn_name} must be defined once, in nav_probe.py; found in {hits}"
        )


# ── integrity ──────────────────────────────────────────────────────────────────

def test_no_prohibited_tokens_in_source():
    """The new module + changed regions + this test carry no internal coordinates."""
    pkg_dir = Path(nav_probe.__file__).parent
    targets = [
        pkg_dir / "stack.py",
        pkg_dir / "nodes.py",
        pkg_dir / "nav_probe.py",
        Path(__file__),
    ]
    # Assemble the pattern from parts so the literals are not present verbatim here.
    parts = [
        r"[CP][0-9]-[0-9]",
        "C" + "-series",
        r"H[0-9]-[0-9]",
        r"\bAD[0-9]",
        r"\bF[0-9]{1,2}\b",
        "DB" + "D",
        "Babaji" + "de",
        "spi" + "ke",
    ]
    pattern = re.compile("|".join(parts))
    for target in targets:
        for lineno, line in enumerate(target.read_text().splitlines(), 1):
            assert not pattern.search(line), (
                f"Prohibited token in {target.name}:{lineno}: {line.strip()}"
            )
