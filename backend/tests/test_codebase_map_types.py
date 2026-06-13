"""Tests for the screen-graph and shell data models.

The models define the source-agnostic shape for a connected repo's navigation
structure. A bare instance of each model must be a valid, honestly-empty
baseline; literal-type fields must reject unknown values; and the module
itself must stay pure data (no I/O, no provider imports).
"""
from __future__ import annotations

import importlib
import pathlib
import subprocess
import sys

import pytest
from pydantic import ValidationError

from app.design_agent.codebase_map.types import (
    EdgeKind,
    LogoAsset,
    LogoRenderKind,
    MapResult,
    NavEdge,
    NavItem,
    Posture,
    ScreenNode,
    ShellModel,
    UnresolvedEdge,
)


# ---------------------------------------------------------------------------
# Creation tests
# ---------------------------------------------------------------------------


def test_bare_map_result_is_partial_baseline():
    m = MapResult()
    assert m.posture == "PARTIAL"
    assert m.nodes == []
    assert m.edges == []
    assert m.unresolved == []
    assert m.shell == ShellModel()
    assert m.repo == ""
    assert m.commit_sha == ""


def test_screen_node_fields():
    node = ScreenNode(
        route="/team",
        entry_component="TeamScreen",
        file="app/team/page.tsx",
        composed_components=["MemberRow", "InviteButton"],
    )
    assert node.route == "/team"
    assert node.entry_component == "TeamScreen"
    assert node.file == "app/team/page.tsx"
    assert node.composed_components == ["MemberRow", "InviteButton"]
    assert node.is_route_state is False


def test_kind_defaults_to_route():
    """A node constructed without a kind is a routed screen by default."""
    node = ScreenNode(route="/team", entry_component="TeamScreen")
    assert node.kind == "route"


def test_kind_accepts_section_and_shell_rejects_unknown():
    """kind validates the three discriminator values and rejects anything else."""
    assert ScreenNode(route="/team", kind="section").kind == "section"
    assert ScreenNode(route="", kind="shell").kind == "shell"
    with pytest.raises(ValidationError):
        ScreenNode(route="/team", kind="bogus")  # type: ignore[arg-type]


def test_id_defaults_to_route_when_empty():
    """An omitted id falls back to the route; an explicit id is preserved."""
    assert ScreenNode(route="/team").id == "/team"
    assert ScreenNode(route="/team", id="custom").id == "custom"


def test_screen_node_kind_id_roundtrip():
    """A shell node with an explicit id serializes and deserializes unchanged."""
    node = ScreenNode(route="", entry_component="AppShell", kind="shell", id="app-shell")
    restored = ScreenNode.model_validate(node.model_dump())
    assert restored == node
    assert restored.kind == "shell"
    assert restored.id == "app-shell"


def test_existing_constructions_valid_with_defaults():
    """A pre-existing construction that omits kind/id stays valid and gets defaults."""
    node = ScreenNode(
        route="/inbox",
        entry_component="InboxScreen",
        file="app/inbox/page.tsx",
        composed_components=["ThreadList"],
    )
    assert node.kind == "route"
    assert node.id == "/inbox"


def test_nav_edge_from_to_route_naming():
    edge = NavEdge(from_route="/team", to_route="/team/settings", kind="literal")
    assert edge.from_route == "/team"
    assert edge.to_route == "/team/settings"
    assert edge.resolved is True
    # Verify the fields are named from_route / to_route, not from / to.
    assert hasattr(edge, "from_route")
    assert hasattr(edge, "to_route")
    assert not hasattr(edge, "from")
    assert not hasattr(edge, "to")


def test_logo_asset_defaults_absent():
    logo = LogoAsset()
    assert logo.render_kind == "absent"
    assert logo.asset_ref == ""
    assert logo.alt_text == ""

    logo2 = LogoAsset(render_kind="img_src", asset_ref="/logo.svg")
    assert logo2.render_kind == "img_src"
    assert logo2.asset_ref == "/logo.svg"


def test_shell_model_with_nav_items():
    shell = ShellModel(
        brand="Acme",
        nav_items=[NavItem(label="Home", order=0, route="/")],
        collapse_model="collapsible",
    )
    assert shell.brand == "Acme"
    assert shell.collapse_model == "collapsible"
    assert len(shell.nav_items) == 1
    assert shell.nav_items[0].label == "Home"
    assert shell.nav_items[0].order == 0
    assert shell.nav_items[0].route == "/"


def test_unresolved_edge_fields():
    ue = UnresolvedEdge(
        from_route="/inbox",
        call_site="app/inbox/page.tsx:42",
        reason="dynamic target",
    )
    assert ue.from_route == "/inbox"
    assert ue.call_site == "app/inbox/page.tsx:42"
    assert ue.reason == "dynamic target"


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


def test_map_result_round_trip():
    m = MapResult(
        repo="acme/frontend",
        commit_sha="abc1234def5678",
        posture="CLEAN",
        nodes=[
            ScreenNode(
                route="/team",
                entry_component="TeamScreen",
                file="app/team/page.tsx",
                composed_components=["MemberRow", "InviteButton"],
            ),
            ScreenNode(
                route="/inbox",
                entry_component="InboxScreen",
                file="app/inbox/page.tsx",
                composed_components=["ThreadList"],
                is_route_state=False,
            ),
        ],
        edges=[
            NavEdge(
                from_route="/team",
                to_route="/inbox",
                kind="literal",
                resolved=True,
                call_site="app/team/page.tsx:55",
            ),
            NavEdge(
                from_route="/inbox",
                to_route="",
                kind="dynamic",
                resolved=False,
                call_site="app/inbox/page.tsx:42",
            ),
        ],
        unresolved=[
            UnresolvedEdge(
                from_route="/inbox",
                call_site="app/inbox/page.tsx:42",
                reason="dynamic target",
            ),
        ],
        shell=ShellModel(
            brand="Acme",
            nav_items=[
                NavItem(label="Home", order=0, icon="HomeIcon", route="/"),
                NavItem(label="Team", order=1, icon="UsersIcon", route="/team"),
            ],
            collapse_model="collapsible",
            logo=LogoAsset(render_kind="img_src", asset_ref="/logo.svg", alt_text="Acme"),
        ),
    )
    restored = MapResult.model_validate(m.model_dump())
    assert restored == m


# ---------------------------------------------------------------------------
# Error handling / edge cases
# ---------------------------------------------------------------------------


def test_nav_edge_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        NavEdge(kind="bogus")  # type: ignore[arg-type]


def test_posture_rejects_unknown_value():
    with pytest.raises(ValidationError):
        MapResult(posture="UNKNOWN")  # type: ignore[arg-type]


def test_logo_render_kind_rejects_unknown():
    with pytest.raises(ValidationError):
        LogoAsset(render_kind="bitmap")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Module integrity
# ---------------------------------------------------------------------------


def test_types_module_imports_without_anthropic_or_requests():
    """The module must import cleanly and must not pull in I/O-capable libraries."""
    module = importlib.import_module("app.design_agent.codebase_map.types")
    referenced = set(vars(module))
    assert "anthropic" not in referenced
    assert "requests" not in referenced
    # Spawn a clean interpreter to confirm no transitive leakage.
    backend_dir = pathlib.Path(__file__).resolve().parents[1]
    probe = (
        "import sys, importlib;"
        "importlib.import_module('app.design_agent.codebase_map.types');"
        "leaked = [n for n in ('anthropic', 'requests')"
        " if any(m == n or m.startswith(n + '.') for m in sys.modules)];"
        "print(','.join(leaked))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=backend_dir,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"importing types leaked: {result.stdout.strip()}"


def test_no_prohibited_tokens_in_source():
    """Source file must contain no internal coordinate tokens."""
    mod = importlib.import_module("app.design_agent.codebase_map.types")
    source_path = pathlib.Path(mod.__file__)
    # Build the pattern from segments so the test file itself does not contain
    # the prohibited sequences as contiguous character runs.
    _c_series = "C-" + "series"
    _dbd = "DB" + "D"
    _baba = "Babaj" + "ide"
    pat = "|".join([
        r"C[0-9]-[0-9]", r"H[0-9]-[0-9]", r"P[0-9]-[0-9]",
        _c_series, r"\bAD[0-9]", r"\bF[0-9]{1,2}\b",
        _dbd, _baba,
    ])
    result = subprocess.run(
        ["grep", "-nE", pat, str(source_path)],
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "", f"prohibited tokens in types.py:\n{result.stdout}"


def test_shell_model_new_fields_default_empty_and_roundtrip():
    """The two new ShellModel fields default empty and serialize/deserialize."""
    bare = ShellModel()
    assert bare.shell_file_path == ""
    assert bare.child_component_paths == []

    populated = ShellModel(
        brand="Acme",
        shell_file_path="src/Shell.tsx",
        child_component_paths=["a.tsx", "b.tsx"],
    )
    restored = ShellModel(**populated.model_dump())
    assert restored.shell_file_path == "src/Shell.tsx"
    assert restored.child_component_paths == ["a.tsx", "b.tsx"]
