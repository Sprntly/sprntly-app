"""Unit tests for the screen-node extractor."""
import logging
import re

import pytest

from app.design_agent.codebase_map.nav_probe import ProbeResult, probe_nav_abstraction
from app.design_agent.codebase_map.nodes import extract_nodes
from app.design_agent.codebase_map.repo_reader import RepoSnapshot


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
