"""Unit tests for the screen-node extractor."""
import logging
import pathlib
import re

import pytest

from app.design_agent.codebase_map.edges import resolve_edges
from app.design_agent.codebase_map.nav_probe import ProbeResult, probe_nav_abstraction
from app.design_agent.codebase_map.nodes import extract_nodes
from app.design_agent.codebase_map.repo_reader import RepoSnapshot
from app.design_agent.codebase_map.shell import APP_SHELL_NODE_ID, APP_SHELL_ROUTE


def _snap(tree_paths: list[str] | None = None, **files) -> RepoSnapshot:
    paths = tree_paths if tree_paths is not None else list(files.keys())
    return RepoSnapshot(
        repo="test/repo",
        commit_sha="abc123",
        branch="main",
        tree_paths=paths,
        files=files,
    )


def _clean_probe(registry_file: str = "", route_table_files: list[str] | None = None) -> ProbeResult:
    return ProbeResult(
        posture="CLEAN",
        registry_file=registry_file,
        nav_primitive="goTo",
        route_table_files=route_table_files or [],
        router_convention="next-app",
    )


def _partial_probe(convention: str = "next-app") -> ProbeResult:
    return ProbeResult(
        posture="PARTIAL",
        registry_file="",
        nav_primitive="",
        route_table_files=[],
        router_convention=convention,
    )


# ── CLEAN node tests ───────────────────────────────────────────────────────────

def test_clean_node_route_and_entry_component():
    """CLEAN snapshot with a route-table and a next-app page file → correct node."""
    snap = _snap(
        **{
            "src/routes.ts": "export const ROUTES = { team: '/team' };\n",
            "app/team/page.tsx": "export default function TeamScreen() { return null; }\n",
        }
    )
    snap.tree_paths = ["src/routes.ts", "app/team/page.tsx"]
    probe = _clean_probe(route_table_files=["src/routes.ts"])
    nodes = extract_nodes(snap, probe)

    team_nodes = [n for n in nodes if n.route == "/team"]
    assert team_nodes, "Expected a /team node"
    node = team_nodes[0]
    assert node.entry_component == "TeamScreen"
    assert node.file == "app/team/page.tsx"


def test_route_state_node_from_registry():
    """A route-table entry with a query param emits a route-state ScreenNode."""
    snap = _snap(
        **{
            "src/routes.ts": (
                "export const ROUTES = {\n"
                "  team: '/team',\n"
                "  teamInvite: '/team?modal=invite',\n"
                "};\n"
            ),
            "app/team/page.tsx": "export default function TeamScreen() {}\n",
        }
    )
    snap.tree_paths = ["src/routes.ts", "app/team/page.tsx"]
    probe = _clean_probe(route_table_files=["src/routes.ts"])
    nodes = extract_nodes(snap, probe)

    state_nodes = [n for n in nodes if n.is_route_state]
    assert state_nodes, "Expected at least one route-state node"
    routes = [n.route for n in state_nodes]
    assert "/team?modal=invite" in routes


# ── PARTIAL node tests ─────────────────────────────────────────────────────────

def test_no_route_state_on_partial():
    """PARTIAL snapshot with searchParams usage emits no route-state nodes."""
    snap = _snap(
        tree_paths=["app/inbox/page.tsx"],
        **{
            "app/inbox/page.tsx": (
                "export default function InboxScreen() {\n"
                "  const view = searchParams.get('view');\n"
                "  return null;\n"
                "}\n"
            ),
        }
    )
    probe = _partial_probe()
    nodes = extract_nodes(snap, probe)

    assert all(not n.is_route_state for n in nodes), "No route-state nodes expected on PARTIAL"


def test_dynamic_segment_route_derivation():
    """app/users/[id]/page.tsx → route == '/users/:id'."""
    snap = _snap(
        tree_paths=["app/users/[id]/page.tsx"],
        **{
            "app/users/[id]/page.tsx": "export default function UserScreen() {}\n",
        }
    )
    probe = _partial_probe()
    nodes = extract_nodes(snap, probe)

    assert any(n.route == "/users/:id" for n in nodes), (
        f"Expected /users/:id, got {[n.route for n in nodes]}"
    )


# ── Composition / honesty tests ────────────────────────────────────────────────

def test_composed_components_imported_only():
    """Only PascalCase JSX tags that are also imported appear in composed_components."""
    body = (
        "import MemberRow from './MemberRow';\n"
        "import { InviteButton } from './InviteButton';\n"
        "export default function MembersScreen() {\n"
        "  return <div><MemberRow /><InviteButton /></div>;\n"
        "}\n"
    )
    snap = _snap(
        tree_paths=["app/members/page.tsx"],
        **{"app/members/page.tsx": body}
    )
    probe = _partial_probe()
    nodes = extract_nodes(snap, probe)

    assert nodes
    node = nodes[0]
    assert "MemberRow" in node.composed_components
    assert "InviteButton" in node.composed_components
    assert "div" not in node.composed_components


def test_unparseable_entry_component_still_emits_node():
    """A page file with no parseable default export still emits a node with file set."""
    snap = _snap(
        tree_paths=["app/mystery/page.tsx"],
        **{"app/mystery/page.tsx": "// no default export here\nconst x = 1;\n"}
    )
    probe = _partial_probe()
    nodes = extract_nodes(snap, probe)

    mystery = [n for n in nodes if n.file == "app/mystery/page.tsx"]
    assert mystery, "Node should still be emitted"
    assert mystery[0].entry_component == ""
    assert mystery[0].file != ""


# ── Determinism ────────────────────────────────────────────────────────────────

def test_extraction_is_deterministic():
    """Running extract_nodes twice on the same snapshot yields equal, route-sorted results."""
    snap = _snap(
        tree_paths=[
            "app/team/page.tsx",
            "app/settings/page.tsx",
            "app/users/[id]/page.tsx",
        ],
        **{
            "app/team/page.tsx": "export default function TeamScreen() {}",
            "app/settings/page.tsx": "export default function SettingsScreen() {}",
            "app/users/[id]/page.tsx": "export default function UserScreen() {}",
        }
    )
    probe = _partial_probe()
    nodes1 = extract_nodes(snap, probe)
    nodes2 = extract_nodes(snap, probe)

    assert nodes1 == nodes2, "Two identical runs must return identical node lists"
    routes = [n.route for n in nodes1]
    assert routes == sorted(routes), f"Nodes must be sorted by route, got {routes}"


# ── Node kind + stable id ───────────────────────────────────────────────────────

def test_enumerated_nodes_carry_route_kind_and_id():
    """Every node from a CLEAN and a PARTIAL fixture is kind="route" with id == route."""
    clean_snap = _snap(
        **{
            "src/routes.ts": "export const ROUTES = { team: '/team' };\n",
            "app/team/page.tsx": "export default function TeamScreen() {}\n",
        }
    )
    clean_snap.tree_paths = ["src/routes.ts", "app/team/page.tsx"]
    clean_nodes = extract_nodes(clean_snap, _clean_probe(route_table_files=["src/routes.ts"]))
    assert clean_nodes
    for n in clean_nodes:
        assert n.kind == "route"
        assert n.id == n.route

    partial_snap = _snap(
        tree_paths=["app/home/page.tsx", "app/users/[id]/page.tsx"],
        **{
            "app/home/page.tsx": "export default function HomeScreen() {}",
            "app/users/[id]/page.tsx": "export default function UserScreen() {}",
        }
    )
    partial_nodes = extract_nodes(partial_snap, _partial_probe())
    assert partial_nodes
    for n in partial_nodes:
        assert n.kind == "route"
        assert n.id == n.route


def test_enumeration_otherwise_byte_identical():
    """Beyond the two new fields, each enumerated node is the exact pre-existing shape."""
    snap = _snap(
        tree_paths=[
            "app/team/page.tsx",
            "app/settings/page.tsx",
            "app/users/[id]/page.tsx",
        ],
        **{
            "app/team/page.tsx": "export default function TeamScreen() {}",
            "app/settings/page.tsx": "export default function SettingsScreen() {}",
            "app/users/[id]/page.tsx": "export default function UserScreen() {}",
        }
    )
    nodes = extract_nodes(snap, _partial_probe())
    assert nodes
    for n in nodes:
        dumped = n.model_dump()
        # The two new fields carry the deterministic foundation values …
        assert dumped.pop("kind") == "route"
        assert dumped.pop("id") == n.route
        # … and every remaining field is exactly the pre-existing node shape.
        assert set(dumped.keys()) == {
            "route",
            "entry_component",
            "file",
            "composed_components",
            "is_route_state",
        }


# ── Observability ──────────────────────────────────────────────────────────────

def test_probe_and_nodes_emit_identifier_only_logs(caplog):
    """extract_nodes emits one INFO line with counts; no file body content."""
    snap = _snap(
        tree_paths=["app/home/page.tsx"],
        **{"app/home/page.tsx": "export default function HomeScreen() {}"}
    )
    probe = _partial_probe()
    with caplog.at_level(logging.INFO, logger="app.design_agent.codebase_map.nodes"):
        nodes = extract_nodes(snap, probe)

    info_lines = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(info_lines) >= 1
    log_text = info_lines[0].getMessage()
    assert "n_nodes=" in log_text
    assert "export default function HomeScreen" not in log_text


# ── Adapter dispatch: Next byte-identical + Vite enumeration ────────────────────

def test_next_adapter_node_set_byte_identical_to_pre_ticket():
    """A Next-shape repo enumerated through the registry yields the exact pre-adapter
    node set (route-only screens, no in-page tabs), including the kind/id fields."""
    snap = _snap(
        tree_paths=[
            "package.json",
            "app/team/page.tsx",
            "app/settings/page.tsx",
            "app/users/[id]/page.tsx",
        ],
        **{
            "package.json": '{"dependencies": {"next": "15.0.0"}}',
            "app/team/page.tsx": "export default function TeamScreen() {}",
            "app/settings/page.tsx": "export default function SettingsScreen() {}",
            "app/users/[id]/page.tsx": "export default function UserScreen() {}",
        },
    )
    nodes = extract_nodes(snap, _partial_probe())

    # The golden pre-ticket Next-App enumeration: route-sorted, kind="route",
    # id == route. The registry dispatch must not add or drop a node.
    golden = [
        ("/settings", "SettingsScreen", "app/settings/page.tsx"),
        ("/team", "TeamScreen", "app/team/page.tsx"),
        ("/users/:id", "UserScreen", "app/users/[id]/page.tsx"),
    ]
    assert [(n.route, n.entry_component, n.file) for n in nodes] == golden
    assert all(n.kind == "route" and n.id == n.route for n in nodes)
    # No spurious section nodes for a route-only Next app.
    assert all(n.kind != "section" for n in nodes)


def test_vite_adapter_enumerates_second_stack_shape():
    """A Vite + react-router repo enumerates <Route> route nodes + tab-section nodes."""
    app_tsx = (
        "import TeamPage from './pages/TeamPage';\n"
        "import BillingPage from './pages/BillingPage';\n"
        "export default function App() {\n"
        "  return (\n"
        "    <Routes>\n"
        "      <Route path=\"/team\" element={<TeamPage/>} />\n"
        "      <Route path=\"/billing\" element={<BillingPage/>} />\n"
        "    </Routes>\n"
        "  );\n"
        "}\n"
    )
    team_page = (
        "export default function TeamPage() {\n"
        "  const tabs = [\n"
        "    { id: 'members', label: 'Members' },\n"
        "    { id: 'roles', label: 'Roles' },\n"
        "  ];\n"
        "  return null;\n"
        "}\n"
    )
    snap = _snap(
        tree_paths=[
            "package.json",
            "src/App.tsx",
            "src/pages/TeamPage.tsx",
            "src/pages/BillingPage.tsx",
            "src/components/Sidebar.tsx",
        ],
        **{
            "package.json": '{"dependencies": {"vite": "5.0.0", "react-router-dom": "6.0.0"}}',
            "src/App.tsx": app_tsx,
            "src/pages/TeamPage.tsx": team_page,
            "src/pages/BillingPage.tsx": "export default function BillingPage() {}",
            "src/components/Sidebar.tsx": "export default function Sidebar() {}",
        },
    )
    nodes = extract_nodes(snap, _partial_probe(convention="react-router"))

    route_nodes = {n.route for n in nodes if n.kind == "route"}
    assert "/team" in route_nodes
    assert "/billing" in route_nodes

    section_ids = {n.id for n in nodes if n.kind == "section"}
    assert any("members" in sid for sid in section_ids)
    assert any("roles" in sid for sid in section_ids)

    # The /team route node resolves its element component to the page file.
    team = next(n for n in nodes if n.route == "/team")
    assert team.entry_component == "TeamPage"
    assert team.file == "src/pages/TeamPage.tsx"


def test_string_array_section_flows_through_section_nodes():
    """A string-literal tab array flows through _section_nodes to kind=section nodes (AC3)."""
    from app.design_agent.codebase_map.nodes import _section_nodes

    snap = _snap(**{
        "src/pages/AnalyticsPage.jsx": "const tabs = ['Overview', 'Funnels', 'Cohorts', 'ROI'];\n",
    })
    nodes = _section_nodes(snap)
    assert {n.id for n in nodes} == {
        "AnalyticsPage#overview",
        "AnalyticsPage#funnels",
        "AnalyticsPage#cohorts",
        "AnalyticsPage#roi",
    }
    for n in nodes:
        assert n.kind == "section"
        assert n.route == ""
        assert n.is_route_state is False
        assert n.file == "src/pages/AnalyticsPage.jsx"


def test_string_array_sections_do_not_pollute_edges():
    """String-array section nodes (route="") are inert in edge resolution (AC7)."""
    snap = _snap(
        tree_paths=["app/analytics/page.tsx", "app/team/page.tsx"],
        **{
            "app/analytics/page.tsx": (
                "const tabs = ['Overview', 'Funnels'];\n"
                "export default function AnalyticsScreen() {\n"
                '  function go() { navigate("/team"); }\n'
                "  return null;\n"
                "}\n"
            ),
            "app/team/page.tsx": "export default function TeamScreen() {}",
        },
    )
    probe = _partial_probe()
    nodes = extract_nodes(snap, probe)
    # Sanity: the string-array section nodes ARE present in the resolved-against set.
    assert any(n.kind == "section" for n in nodes)
    nodes_without_sections = [n for n in nodes if n.kind != "section"]

    resolved_with, _ = resolve_edges(snap, probe, nodes)
    resolved_without, _ = resolve_edges(snap, probe, nodes_without_sections)

    # The resolved edge set is byte-identical with vs without the section nodes.
    assert resolved_with == resolved_without
    # (a) No edge originates from a section's empty route.
    assert all(e.from_route != "" for e in resolved_with)
    # (b) The real /analytics → /team edge still resolves exactly as before.
    assert any(
        e.from_route == "/analytics" and e.to_route == "/team"
        for e in resolved_with
    )


# ── App-shell node enumeration ──────────────────────────────────────────────────

# A shell whose nav comes from a config array (no <Link>/<a href>/navigate
# call-sites) is non-bare for extract_shell yet contributes zero resolvable
# navigation sites — the realistic chrome shape that keeps edge resolution inert.
_SHELL_BODY = (
    "const NAV = [\n"
    '  {label:"Home", icon:"House", href:"/"},\n'
    '  {label:"Team", icon:"Users", href:"/team"},\n'
    "];\n"
    "export function Sidebar() {\n"
    "  return (\n"
    '    <div className="shell">\n'
    "      <span>Acme</span>\n"
    "      <nav>{NAV.map(item => <span key={item.label}>{item.label}</span>)}</nav>\n"
    "    </div>\n"
    "  );\n"
    "}\n"
)

# A route screen with one literal navigation → a resolvable edge in the set.
_DASHBOARD_BODY = (
    "export default function DashboardScreen() {\n"
    '  function go() { navigate("/team"); }\n'
    "  return null;\n"
    "}\n"
)


def _snap_with_shell() -> RepoSnapshot:
    return _snap(
        tree_paths=[
            "app/dashboard/page.tsx",
            "app/team/page.tsx",
            "src/components/Sidebar.tsx",
        ],
        **{
            "app/dashboard/page.tsx": _DASHBOARD_BODY,
            "app/team/page.tsx": "export default function TeamScreen() {}",
            "src/components/Sidebar.tsx": _SHELL_BODY,
        },
    )


def _snap_without_shell() -> RepoSnapshot:
    return _snap(
        tree_paths=["app/dashboard/page.tsx", "app/team/page.tsx"],
        **{
            "app/dashboard/page.tsx": _DASHBOARD_BODY,
            "app/team/page.tsx": "export default function TeamScreen() {}",
        },
    )


def test_shell_node_appended_when_shell_exists():
    """A snapshot with an identifiable shell yields exactly one app-shell node (AC2)."""
    nodes = extract_nodes(_snap_with_shell(), _partial_probe())

    shell_nodes = [n for n in nodes if n.kind == "shell"]
    assert len(shell_nodes) == 1
    node = shell_nodes[0]
    assert node.id == APP_SHELL_NODE_ID == "app-shell"
    assert node.route == APP_SHELL_ROUTE
    assert node.file == "src/components/Sidebar.tsx"
    # Reuses the shell's nav-item icon component names.
    assert node.composed_components == ["House", "Users"]


def test_no_shell_node_when_no_chrome():
    """A snapshot with no identifiable chrome yields zero shell nodes (AC3)."""
    nodes = extract_nodes(_snap_without_shell(), _partial_probe())
    assert [n for n in nodes if n.kind == "shell"] == []
    # And the route nodes are still enumerated.
    assert {n.route for n in nodes} == {"/dashboard", "/team"}


def test_shell_node_emitted_once():
    """Assembly never produces more than one app-shell node (AC4)."""
    nodes = extract_nodes(_snap_with_shell(), _partial_probe())
    assert sum(1 for n in nodes if n.kind == "shell") == 1
    # Re-running is idempotent: still exactly one per call (deterministic).
    again = extract_nodes(_snap_with_shell(), _partial_probe())
    assert sum(1 for n in again if n.kind == "shell") == 1
    assert nodes == again


def test_route_section_nodes_unchanged_by_shell_append():
    """Route/section nodes are byte-identical with vs without the shell append (AC5)."""
    with_shell = extract_nodes(_snap_with_shell(), _partial_probe())
    # The route/section node set the assembly would have produced absent any shell.
    baseline = extract_nodes(_snap_without_shell(), _partial_probe())

    non_shell = [n for n in with_shell if n.kind != "shell"]
    assert non_shell == baseline
    # The only delta the append introduces is the single shell node.
    assert len(with_shell) == len(baseline) + 1


def test_resolved_edge_set_unchanged_by_shell_node():
    """The shell node's synthetic route/file are inert in edge resolution (AC6)."""
    snap = _snap_with_shell()
    probe = _partial_probe()
    nodes = extract_nodes(snap, probe)
    nodes_without_shell = [n for n in nodes if n.kind != "shell"]

    # Sanity: the shell node IS present in the resolved-against set.
    assert any(n.kind == "shell" for n in nodes)

    resolved_with, _ = resolve_edges(snap, probe, nodes)
    resolved_without, _ = resolve_edges(snap, probe, nodes_without_shell)

    # The resolved edge set is byte-identical with vs without the shell node.
    assert resolved_with == resolved_without
    # (a) No edge originates from the synthetic chrome route.
    assert all(e.from_route != APP_SHELL_ROUTE for e in resolved_with)
    # (b) The shell file/route did not displace a real call-site's resolution:
    #     the real /dashboard → /team edge resolves exactly as before.
    assert any(
        e.from_route == "/dashboard" and e.to_route == "/team"
        for e in resolved_with
    )


def test_no_prohibited_tokens_in_source():
    """Neither nodes.py nor this test file contain internal tracking tokens (AC9).

    Pattern is assembled by concatenation so the test source itself is clean.
    """
    ticket_id = r'[CH][0-9]-[0-9]'
    c_ser = 'C' + '-' + 'series'
    h_ser = 'H' + '-' + 'series'
    p_tick = r'P[0-9]-[0-9]'
    ad_ref = r'\b' + 'AD' + r'[0-9]'
    f_ref = r'\b' + 'F' + r'[0-9]{1,2}\b'
    dbd_tok = 'D' + 'BD'
    auth_tok = 'Babaj' + 'ide'
    spk_tok = 'spi' + 'ke'
    pattern = re.compile(
        '|'.join([ticket_id, c_ser, h_ser, p_tick, ad_ref, f_ref, dbd_tok, auth_tok, spk_tok])
    )
    root = pathlib.Path(__file__).parent.parent
    for relpath in (
        "app/design_agent/codebase_map/nodes.py",
        "tests/test_codebase_map_nodes.py",
    ):
        source = (root / relpath).read_text()
        matches = pattern.findall(source)
        assert not matches, f"{relpath} contains prohibited tokens: {matches}"


# ── route-group strip (runtime route normalization) ──────────────────────────

def test_route_group_stripped_single_and_nested_and_dynamic():
    """Next (group) folders are stripped from the runtime route/id; nested groups
    and dynamic segments handled; a non-group route is unchanged."""
    snap = _snap(tree_paths=[
        "app/(app)/sources/page.tsx",
        "app/(marketing)/(app)/pricing/page.tsx",
        "app/(app)/users/[id]/page.tsx",
        "app/dashboard/page.tsx",
    ], **{
        "app/(app)/sources/page.tsx": "export default function S(){return null}",
        "app/(marketing)/(app)/pricing/page.tsx": "export default function P(){return null}",
        "app/(app)/users/[id]/page.tsx": "export default function U(){return null}",
        "app/dashboard/page.tsx": "export default function D(){return null}",
    })
    nodes = extract_nodes(snap, _partial_probe("next-app"))
    route_nodes = [n for n in nodes if n.kind == "route"]
    routes = {n.route for n in route_nodes}
    assert "/sources" in routes
    assert "/pricing" in routes
    assert "/users/:id" in routes
    assert "/dashboard" in routes
    for n in route_nodes:
        assert "(" not in n.route and "(" not in n.id
        assert n.id == n.route


def test_route_group_strip_preserves_file_path():
    """The on-disk file path keeps the real (group) folder; only route/id normalize."""
    snap = _snap(tree_paths=["app/(app)/sources/page.tsx"], **{
        "app/(app)/sources/page.tsx": "export default function S(){return null}",
    })
    nodes = extract_nodes(snap, _partial_probe("next-app"))
    src = next(n for n in nodes if n.route == "/sources")
    assert src.file == "app/(app)/sources/page.tsx"


# ── child-component path resolution (deep-read seam) ─────────────────────────

def test_resolve_child_paths_relative_and_alias_skip_bare():
    from app.design_agent.codebase_map.nodes import _resolve_child_component_paths
    body = (
        "import { Menu } from './components/Menu'\n"
        "import Sidebar from '@/components/Sidebar'\n"
        "import { useState } from 'react'\n"
        "import { Star } from 'lucide-react'\n"
        "export default function Screen(){ return (<div><Menu/><Sidebar/><Star/></div>) }\n"
    )
    snap = _snap(**{
        "src/screens/Screen.tsx": body,
        "src/screens/components/Menu.tsx": "export const Menu=()=>null",
        "src/components/Sidebar.tsx": "export const Sidebar=()=>null",
    })
    paths = _resolve_child_component_paths("src/screens/Screen.tsx", snap, {"@/*": "src/*"})
    assert "src/screens/components/Menu.tsx" in paths
    assert "src/components/Sidebar.tsx" in paths
    # bare-package imports skipped even when rendered (Star from lucide-react)
    assert not any("lucide" in p or p == "react" for p in paths)


def test_resolve_child_paths_only_rendered_imports_and_capped():
    from app.design_agent.codebase_map.nodes import (
        _resolve_child_component_paths,
        _MAX_COMPOSED,
    )
    # imported-but-not-rendered components are NOT followed
    body = (
        "import { Used } from './Used'\n"
        "import { Unused } from './Unused'\n"
        "export default function S(){ return <Used/> }\n"
    )
    snap = _snap(**{
        "a/S.tsx": body,
        "a/Used.tsx": "export const Used=()=>null",
        "a/Unused.tsx": "export const Unused=()=>null",
    })
    paths = _resolve_child_component_paths("a/S.tsx", snap)
    assert "a/Used.tsx" in paths
    assert "a/Unused.tsx" not in paths

    # capped at _MAX_COMPOSED
    n = _MAX_COMPOSED + 5
    imports = "".join(f"import {{ C{i} }} from './C{i}'\n" for i in range(n))
    tags = "".join(f"<C{i}/>" for i in range(n))
    files = {f"b/C{i}.tsx": "export const x=1" for i in range(n)}
    files["b/S.tsx"] = f"{imports}export default function S(){{return <div>{tags}</div>}}"
    snap2 = _snap(**files)
    capped = _resolve_child_component_paths("b/S.tsx", snap2)
    assert len(capped) <= _MAX_COMPOSED


def test_extract_composed_components_name_output_unchanged():
    """The NAME list (consumed by edge/route code) is the sorted import∩tag set."""
    from app.design_agent.codebase_map.nodes import _extract_composed_components
    body = (
        "import { Hero } from './Hero'\n"
        "import Footer from './Footer'\n"
        "import { useState } from 'react'\n"
        "export default function S(){ return (<div><Hero/><Footer/></div>) }\n"
    )
    snap = _snap(**{"a/S.tsx": body})
    assert _extract_composed_components("a/S.tsx", snap) == ["Footer", "Hero"]
