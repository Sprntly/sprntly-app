"""Unit tests for the shell extractor.

All tests use synthetic RepoSnapshot fixtures with hand-crafted shell bodies —
no network calls, no LLM, no filesystem reads beyond the source file itself
(for the integrity check).
"""
import importlib.util
import logging
import pathlib
import re

import pytest

from app.design_agent.codebase_map.repo_reader import RepoSnapshot
from app.design_agent.codebase_map.shell import (
    APP_SHELL_NODE_ID,
    APP_SHELL_ROUTE,
    _extract_nav_items,
    _locate_shell_file,
    build_app_shell_node,
    extract_shell,
)
from app.design_agent.codebase_map.types import LogoAsset, NavItem, ScreenNode, ShellModel


# ── Helpers ───────────────────────────────────────────────────────────────────

def _snapshot(files: dict[str, str]) -> RepoSnapshot:
    """Build a minimal synthetic snapshot with the given files dict."""
    paths = list(files.keys())
    return RepoSnapshot(
        repo="test/app",
        commit_sha="abc123",
        branch="main",
        tree_paths=paths,
        files=files,
    )


# ── Brand / nav ───────────────────────────────────────────────────────────────

def test_brand_and_nav_count():
    """Shell with brand span + 3 nav links yields brand and nav count."""
    body = """
import React from 'react';
import { Link } from 'next/link';

export function Sidebar() {
  return (
    <div>
      <div className="logo-region">
        <span>Acme</span>
      </div>
      <nav>
        <Link href="/home">Home</Link>
        <Link href="/team">Team</Link>
        <Link href="/settings">Settings</Link>
      </nav>
    </div>
  );
}
"""
    shell = extract_shell(_snapshot({"src/components/Sidebar.tsx": body}))
    assert shell.brand == "Acme"
    assert len(shell.nav_items) == 3


def test_nav_order_preserved():
    """Nav items extracted from inline JSX are returned in document order."""
    body = """
import { Link } from 'next/link';

export function Sidebar() {
  return (
    <nav>
      <Link href="/home">Home</Link>
      <Link href="/team">Team</Link>
      <Link href="/settings">Settings</Link>
    </nav>
  );
}
"""
    shell = extract_shell(_snapshot({"src/components/Sidebar.tsx": body}))
    assert len(shell.nav_items) == 3
    assert shell.nav_items[0].label == "Home"
    assert shell.nav_items[1].label == "Team"
    assert shell.nav_items[2].label == "Settings"
    assert shell.nav_items[0].order == 0
    assert shell.nav_items[1].order == 1
    assert shell.nav_items[2].order == 2


def test_nav_from_config_array_with_icons():
    """Nav-config array entries are parsed with label, icon, and route."""
    body = """
const NAV = [
  {label:"Home", icon:"House", href:"/"},
  {label:"Team", icon:"Users", href:"/team"},
];

export function Sidebar() {
  return (
    <nav>
      {NAV.map(item => (
        <a href={item.href} key={item.label}>{item.label}</a>
      ))}
    </nav>
  );
}
"""
    shell = extract_shell(_snapshot({"src/components/Sidebar.tsx": body}))
    assert len(shell.nav_items) == 2
    assert shell.nav_items[0].label == "Home"
    assert shell.nav_items[0].icon == "House"
    assert shell.nav_items[0].route == "/"
    assert shell.nav_items[1].label == "Team"
    assert shell.nav_items[1].icon == "Users"
    assert shell.nav_items[1].route == "/team"


# ── Strategy C: repeated custom nav component ─────────────────────────────────

def test_nav_parser_railitem_custom_component():
    """A repeated custom component with string-literal labels yields nav items.

    The shell uses NEITHER a nav-config array NOR inline <Link>/<a> elements — it
    repeats a custom <RailItem … label="…" /> per item. Strategy C recovers the
    labels in source order and skips commented-out occurrences.
    """
    body = """
export function Rail() {
  return (
    <nav>
      <RailItem screen="brief" icon={<IconMessageCircle/>} label="Weekly brief" />
      <RailItem screen="chats" icon={<IconChats/>} label="All chats" />
      <RailItem screen="backlog" icon={<IconList/>} label="Backlog Projects" />
      <RailItem screen="templates" icon={<IconStar/>} label="Templates · what good looks like" />
      <RailItem screen="sources" icon={<IconDb/>} label="Sources" />
      <RailItem screen="settings" icon={<IconGear/>} label="Settings" />
      {/* <RailItem screen="proto" icon={<IconBolt/>} label="Prototype" /> */}
      // <RailItem screen="hidden" label="Hidden Item" />
    </nav>
  );
}
"""
    items = _extract_nav_items(body)
    labels = [it.label for it in items]
    assert labels[:4] == [
        "Weekly brief",
        "All chats",
        "Backlog Projects",
        "Templates · what good looks like",
    ]
    assert "Sources" in labels
    assert "Settings" in labels
    # Commented-out occurrences must not leak.
    assert "Prototype" not in labels
    assert "Hidden Item" not in labels
    # Orders are 0-based in source order.
    assert [it.order for it in items] == list(range(len(items)))


def test_locate_shell_selects_custom_nav_over_navless_layout():
    """SELECTION layer (the live-pipeline gap a body-only test misses): when the
    repo has BOTH a navless `layout.tsx` and a `Sidebar.tsx` whose nav is custom
    <RailItem label="…"> components (zero standard links), shell-file selection
    must pick the Sidebar, not the layout — otherwise `_extract_nav_items` runs on
    the navless file and yields n_nav=0. Ranking folds custom-nav count into the
    score so the RailItem sidebar wins.
    """
    navless_layout = """
import { ReactNode } from "react"
export default function AppLayout({ children }: { children: ReactNode }) {
  return <div className="app"><main>{children}</main></div>
}
"""
    railitem_sidebar = """
export function Sidebar() {
  return (
    <nav>
      <RailItem screen="brief" icon={<IconMessageCircle/>} label="Weekly brief" />
      <RailItem screen="chats" icon={<IconChats/>} label="All chats" />
      <RailItem screen="backlog" icon={<IconList/>} label="Backlog Projects" />
      <RailItem screen="templates" icon={<IconStar/>} label="Templates" />
      <RailItem screen="settings" icon={<IconGear/>} label="Settings" />
    </nav>
  );
}
"""
    snap = _snapshot(
        {
            "web/app/(app)/layout.tsx": navless_layout,
            "web/app/components/shared/Sidebar.tsx": railitem_sidebar,
        }
    )
    path, _body = _locate_shell_file(snap)
    assert path == "web/app/components/shared/Sidebar.tsx"
    # …and the full pipeline therefore recovers the real labels (not n_nav=0).
    shell = extract_shell(snap)
    labels = [it.label for it in shell.nav_items]
    assert "Weekly brief" in labels
    assert "All chats" in labels
    assert "Backlog Projects" in labels
    assert "Templates" in labels


def test_nav_parser_strategy_c_does_not_fire_when_config_present():
    """When a Strategy-A config array exists, the repeated custom component is
    ignored — the config wins, proving Strategy C is purely additive."""
    body = """
const NAV = [
  {label:"Home", icon:"House", href:"/"},
  {label:"Team", icon:"Users", href:"/team"},
];

export function Rail() {
  return (
    <nav>
      <RailItem label="Weekly brief" />
      <RailItem label="All chats" />
    </nav>
  );
}
"""
    items = _extract_nav_items(body)
    labels = [it.label for it in items]
    assert labels == ["Home", "Team"]
    assert "Weekly brief" not in labels


def test_nav_parser_strategy_c_does_not_fire_when_links_present():
    """When inline <Link> elements exist (Strategy B), the repeated custom
    component is ignored — links win, proving Strategy C is purely additive."""
    body = """
export function Rail() {
  return (
    <nav>
      <Link href="/home">Home</Link>
      <Link href="/team">Team</Link>
      <RailItem label="Weekly brief" />
      <RailItem label="All chats" />
    </nav>
  );
}
"""
    items = _extract_nav_items(body)
    labels = [it.label for it in items]
    assert labels == ["Home", "Team"]
    assert "Weekly brief" not in labels


def test_nav_parser_ignores_aria_label_and_expressions():
    """Strategy C ignores aria-label and expression props — only string-literal
    label=/title= props on a repeated component qualify."""
    body = """
export function Rail() {
  return (
    <nav>
      <RailItem aria-label="Weekly brief" />
      <RailItem aria-label="All chats" />
      <RailItem label={dynamicLabel} />
      <RailItem label={t('nav.sources')} />
    </nav>
  );
}
"""
    items = _extract_nav_items(body)
    assert items == []


# ── Logo render kinds ─────────────────────────────────────────────────────────

def test_logo_img_src():
    """Literal <img src> yields img_src render kind with correct fields."""
    body = """
export function Sidebar() {
  return (
    <div>
      <img src="/logo.svg" alt="Acme"/>
      <Link href="/home">Home</Link>
      <Link href="/team">Team</Link>
      <Link href="/settings">Settings</Link>
    </div>
  );
}
"""
    shell = extract_shell(_snapshot({"src/components/Sidebar.tsx": body}))
    assert shell.logo.render_kind == "img_src"
    assert shell.logo.asset_ref == "/logo.svg"
    assert shell.logo.alt_text == "Acme"


def test_logo_inline_svg():
    """Literal <svg> block yields inline_svg render kind."""
    body = """
export function Sidebar() {
  return (
    <div>
      <svg viewBox="0 0 24 24" aria-label="Logo"><path d="M0 0"/></svg>
      <Link href="/home">Home</Link>
      <Link href="/team">Team</Link>
      <Link href="/settings">Settings</Link>
    </div>
  );
}
"""
    shell = extract_shell(_snapshot({"src/components/Sidebar.tsx": body}))
    assert shell.logo.render_kind == "inline_svg"
    assert shell.logo.asset_ref == ""


def test_logo_imported_asset():
    """Import + <img src={var}> yields imported_asset with import source path."""
    body = """
import logo from "./brand/logo.svg";

export function Sidebar() {
  return (
    <div>
      <img src={logo} alt="Acme" />
      <Link href="/home">Home</Link>
      <Link href="/team">Team</Link>
      <Link href="/settings">Settings</Link>
    </div>
  );
}
"""
    shell = extract_shell(_snapshot({"src/components/Sidebar.tsx": body}))
    assert shell.logo.render_kind == "imported_asset"
    assert shell.logo.asset_ref == "./brand/logo.svg"


def test_logo_text_badge():
    """Styled letter-badge container yields text render kind with badge content."""
    body = """
export function Sidebar() {
  return (
    <div>
      <div className="w-8 h-8 bg-blue-600 rounded-md flex items-center justify-center">
        S
      </div>
      <Link href="/home">Home</Link>
      <Link href="/team">Team</Link>
      <Link href="/settings">Settings</Link>
    </div>
  );
}
"""
    shell = extract_shell(_snapshot({"src/components/Sidebar.tsx": body}))
    assert shell.logo.render_kind == "text"
    assert shell.logo.asset_ref == "S"


def test_logo_precedence_imported_over_inline():
    """When both an imported-asset usage and a stray inline <svg> exist, imported_asset wins."""
    body = """
import logo from "./logo.svg";

export function Sidebar() {
  return (
    <div>
      <img src={logo} alt="MyApp" />
      <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="8"/></svg>
      <Link href="/home">Home</Link>
      <Link href="/team">Team</Link>
      <Link href="/settings">Settings</Link>
    </div>
  );
}
"""
    shell = extract_shell(_snapshot({"src/components/Sidebar.tsx": body}))
    assert shell.logo.render_kind == "imported_asset"
    assert shell.logo.asset_ref == "./logo.svg"


# ── Collapse / absence / determinism ─────────────────────────────────────────

def test_collapse_model_collapsible_vs_static():
    """Collapse-toggle reference → 'collapsible'; fixed-width with no toggle → 'static'."""
    body_collapsible = """
import { useState } from 'react';
export function Sidebar() {
  const [isCollapsed, setIsCollapsed] = useState(false);
  return (
    <div>
      <span>Brand</span>
      <Link href="/home">Home</Link>
      <Link href="/settings">Settings</Link>
    </div>
  );
}
"""
    body_static = """
export function Sidebar() {
  return (
    <div style={{ width: '240px' }}>
      <span>Brand</span>
      <Link href="/home">Home</Link>
      <Link href="/settings">Settings</Link>
    </div>
  );
}
"""
    shell_c = extract_shell(_snapshot({"src/components/Sidebar.tsx": body_collapsible}))
    assert shell_c.collapse_model == "collapsible"

    shell_s = extract_shell(_snapshot({"src/components/Sidebar.tsx": body_static}))
    assert shell_s.collapse_model == "static"


def test_no_shell_file_returns_bare_model():
    """Snapshot with no shell-like file returns a bare ShellModel with defaults."""
    snap = _snapshot({"src/pages/index.tsx": "export default function Home() { return <div/>; }"})
    shell = extract_shell(snap)
    assert shell.brand == ""
    assert shell.nav_items == []
    assert shell.logo.render_kind == "absent"


def test_shell_extraction_deterministic():
    """Running extract_shell twice on the same snapshot yields equal ShellModels."""
    body = """
import logo from "./assets/logo.png";

export function Sidebar() {
  return (
    <div>
      <img src={logo} alt="Demo" />
      <Link href="/home">Home</Link>
      <Link href="/team">Team</Link>
    </div>
  );
}
"""
    snap = _snapshot({"src/components/Sidebar.tsx": body})
    first = extract_shell(snap)
    second = extract_shell(snap)
    assert first == second


# ── App-shell node construction ─────────────────────────────────────────────────

def test_build_app_shell_node_shape():
    """build_app_shell_node on a populated ShellModel yields the chrome node shape."""
    shell = ShellModel(
        brand="Acme",
        nav_items=[
            NavItem(label="Home", order=0, icon="House", route="/"),
            NavItem(label="Team", order=1, icon="Users", route="/team"),
        ],
        collapse_model="collapsible",
        logo=LogoAsset(render_kind="img_src", asset_ref="/logo.svg"),
    )

    node = build_app_shell_node(shell, shell_file_path="src/app/Shell.tsx")

    assert isinstance(node, ScreenNode)
    assert node.id == "app-shell"
    assert node.id == APP_SHELL_NODE_ID
    assert node.kind == "shell"
    assert node.route == APP_SHELL_ROUTE
    # Component name derives from the located file (leading-uppercase stem).
    assert node.entry_component == "Shell"
    assert node.file == "src/app/Shell.tsx"
    # Nav-item icon component names back the composed list.
    assert node.composed_components == ["House", "Users"]


def test_build_app_shell_node_empty_path_is_honest():
    """No located file → empty file + empty component (still locatable by id)."""
    shell = ShellModel(brand="Acme", nav_items=[NavItem(label="Home", route="/")])

    node = build_app_shell_node(shell)

    assert node.id == APP_SHELL_NODE_ID
    assert node.kind == "shell"
    assert node.file == ""
    assert node.entry_component == ""
    # Nav item with no icon contributes no composed component name.
    assert node.composed_components == []


def test_app_shell_node_makes_no_repo_read():
    """build_app_shell_node takes no snapshot — it cannot read the repo (AC8)."""
    import inspect

    params = set(inspect.signature(build_app_shell_node).parameters)
    assert params == {"shell", "shell_file_path"}
    # No RepoSnapshot reference in the constructor's own source.
    src = inspect.getsource(build_app_shell_node)
    assert "RepoSnapshot" not in src
    assert "read_repo" not in src
    # And it returns purely from its inputs, with no snapshot in scope.
    node = build_app_shell_node(
        ShellModel(brand="X", nav_items=[NavItem(label="A", route="/a")]),
        shell_file_path="src/AppShell.tsx",
    )
    assert node.entry_component == "AppShell"


def test_kind_shell_accepted_and_roundtrips():
    """A kind="shell" node validates and serialization round-trips the value (AC7)."""
    node = build_app_shell_node(
        ShellModel(brand="Acme"), shell_file_path="src/app/AppLayout.tsx"
    )
    assert node.kind == "shell"

    dumped = node.model_dump()
    assert dumped["kind"] == "shell"
    assert dumped["id"] == "app-shell"

    rebuilt = ScreenNode(**dumped)
    assert rebuilt.kind == "shell"
    assert rebuilt.id == "app-shell"
    assert rebuilt == node


# ── Observability / integrity ─────────────────────────────────────────────────

def test_shell_emits_identifier_only_log(caplog):
    """One INFO line is emitted with brand/n_nav/logo_kind; no file body content."""
    body = """
export function Sidebar() {
  return (
    <div>
      <span>Omega</span>
      <img src="/wordmark.svg" alt="Omega"/>
      <Link href="/home">Home</Link>
      <Link href="/team">Team</Link>
      <Link href="/dash">Dashboard</Link>
    </div>
  );
}
"""
    snap = _snapshot({"src/components/Sidebar.tsx": body})
    with caplog.at_level(logging.INFO, logger="codebase_map.shell"):
        shell = extract_shell(snap)

    info_lines = [r.message for r in caplog.records if r.levelname == "INFO"]
    assert len(info_lines) == 1, f"Expected 1 INFO line, got: {info_lines}"
    log_line = info_lines[0]

    # Brand name, n_nav count, and logo kind must appear.
    assert "Omega" in log_line
    assert "n_nav=3" in log_line
    assert "logo_kind=" in log_line

    # File body substrings must NOT appear in any log output.
    full_log = "\n".join(r.message for r in caplog.records)
    assert "<svg" not in full_log
    assert "export function" not in full_log
    assert "className" not in full_log


def test_shell_module_imports_without_anthropic_or_ast_parser():
    """The shell module is importable and does not import anthropic or AST parsers."""
    shell_path = (
        pathlib.Path(__file__).parent.parent
        / "app" / "design_agent" / "codebase_map" / "shell.py"
    )
    assert shell_path.exists(), "shell.py not found"

    source = shell_path.read_text()

    # Module must be importable with extract_shell exposed.
    spec = importlib.util.spec_from_file_location("_shell_integrity_check", shell_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert callable(getattr(mod, "extract_shell", None))

    # Prohibited imports must not appear.
    assert "import anthropic" not in source
    assert "from anthropic" not in source
    for parser in ("esprima", "tree-sitter", "tree_sitter", "@babel", "pyjsparser"):
        assert parser not in source
    # Python's own ast module is also an AST parser — must not be used.
    assert "import ast\n" not in source
    assert "from ast import" not in source


def test_no_prohibited_tokens_in_source():
    """Neither shell.py nor this test file contain internal tracking tokens.

    Pattern is assembled by concatenation so the test source itself is clean.
    """
    # Tokens constructed by concatenation — no literal occurrence in this source.
    ticket_id = r'[CH][0-9]-[0-9]'
    c_ser = 'C' + '-' + 'series'
    h_ser = 'H' + '-' + 'series'
    p_tick = r'P[0-9]-[0-9]'
    ad_ref = r'\b' + 'AD' + r'[0-9]'
    f_ref = r'\b' + 'F' + r'[0-9]{1,2}\b'
    dbd_tok = 'D' + 'BD'
    auth_tok = 'Babaj' + 'ide'
    pattern = re.compile(
        '|'.join([ticket_id, c_ser, h_ser, p_tick, ad_ref, f_ref, dbd_tok, auth_tok])
    )
    root = pathlib.Path(__file__).parent.parent
    for relpath in (
        "app/design_agent/codebase_map/shell.py",
        "tests/test_codebase_map_shell.py",
    ):
        source = (root / relpath).read_text()
        matches = pattern.findall(source)
        assert not matches, f"{relpath} contains prohibited tokens: {matches}"


# ── carried shell file path ──────────────────────────────────────────────────

def test_extract_shell_populates_shell_file_path():
    """extract_shell records the located shell file's real path."""
    body = (
        "import { Link } from 'next/link';\n"
        "export default function Sidebar(){ return <nav>"
        "<Link href='/team'>Team</Link><Link href='/settings'>Settings</Link>"
        "</nav> }\n"
    )
    shell = extract_shell(_snapshot({"src/components/layout/Sidebar.tsx": body}))
    assert shell.shell_file_path == "src/components/layout/Sidebar.tsx"


def test_extract_shell_no_file_leaves_path_empty():
    """A snapshot with no shell file → bare ShellModel with empty path."""
    shell = extract_shell(_snapshot({"src/util.ts": "export const x = 1\n"}))
    assert shell.shell_file_path == ""
