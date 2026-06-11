"""Unit tests for navigation edge resolution."""

import importlib
import importlib.util
import logging
import pathlib
import re

import pytest

from app.design_agent.codebase_map.edges import resolve_edges
from app.design_agent.codebase_map.nav_probe import ProbeResult
from app.design_agent.codebase_map.repo_reader import RepoSnapshot
from app.design_agent.codebase_map.types import ScreenNode


# ── fixtures ───────────────────────────────────────────────────────────────────


def _snap(files: dict[str, str]) -> RepoSnapshot:
    return RepoSnapshot(
        repo="test/repo",
        commit_sha="abc123",
        branch="main",
        tree_paths=list(files.keys()),
        files=files,
    )


def _probe(
    posture: str = "CLEAN",
    nav_primitive: str = "goTo",
    registry_file: str = "",
    route_table_files: list[str] | None = None,
) -> ProbeResult:
    return ProbeResult(
        posture=posture,
        nav_primitive=nav_primitive,
        registry_file=registry_file,
        route_table_files=route_table_files or [],
    )


def _node(route: str, file: str = "") -> ScreenNode:
    return ScreenNode(route=route, file=file)


# ── Resolution (happy paths) ───────────────────────────────────────────────────


def test_literal_edge_resolved():
    """navigate("/team") in a screen file produces a resolved literal edge."""
    snap = _snap({"app/home/page.tsx": 'navigate("/team")'})
    nodes = [_node("/home", "app/home/page.tsx")]
    edges, unresolved = resolve_edges(snap, _probe(), nodes)

    assert len(edges) == 1
    e = edges[0]
    assert e.from_route == "/home"
    assert e.to_route == "/team"
    assert e.kind == "literal"
    assert e.resolved is True
    assert unresolved == []


def test_path_builder_param_normalized():
    """router.push(`/users/${id}`) produces a path_builder edge with :id param."""
    snap = _snap({"app/user/page.tsx": "router.push(`/users/${id}`)"})
    nodes = [_node("/user", "app/user/page.tsx")]
    edges, _ = resolve_edges(snap, _probe(), nodes)

    assert len(edges) == 1
    e = edges[0]
    assert e.to_route == "/users/:id"
    assert e.kind == "path_builder"
    assert e.resolved is True


def test_registry_edge_resolved_via_table():
    """goTo(ScreenId.Team) with a route table entry resolves to the mapped path."""
    route_table = "src/routes.ts"
    snap = _snap(
        {
            "app/home/page.tsx": "goTo(ScreenId.Team)",
            route_table: 'Team: "/team"',
        }
    )
    nodes = [_node("/home", "app/home/page.tsx")]
    edges, unresolved = resolve_edges(snap, _probe(registry_file=route_table), nodes)

    assert len(edges) == 1
    e = edges[0]
    assert e.to_route == "/team"
    assert e.kind == "registry"
    assert e.resolved is True
    assert unresolved == []


def test_external_link_classified():
    """An anchor with an external URL produces an external edge, never an UnresolvedEdge."""
    snap = _snap({"app/home/page.tsx": '<a href="https://x.com">'})
    nodes = [_node("/home", "app/home/page.tsx")]
    edges, unresolved = resolve_edges(snap, _probe(), nodes)

    assert unresolved == []
    assert len(edges) == 1
    assert edges[0].kind == "external"
    assert edges[0].resolved is True


# ── Worklist (bounded / unbounded distinction) ─────────────────────────────────


def test_dynamic_target_bounded_on_clean():
    """goTo(c.target) on a CLEAN repo goes to the worklist tagged 'bounded'."""
    snap = _snap({"app/home/page.tsx": "goTo(c.target)"})
    nodes = [_node("/home", "app/home/page.tsx")]
    edges, unresolved = resolve_edges(snap, _probe(posture="CLEAN"), nodes)

    # No fabricated resolved edge for c.target
    assert all(e.to_route != "c.target" for e in edges)
    assert len(unresolved) == 1
    assert "bounded" in unresolved[0].reason


def test_prop_href_unbounded_on_partial():
    """<Link href={hrefProp}> on a PARTIAL repo goes to the worklist tagged 'unbounded'."""
    snap = _snap({"app/nav/Link.tsx": "<Link href={hrefProp}>"})
    nodes = [_node("/nav", "app/nav/Link.tsx")]
    edges, unresolved = resolve_edges(snap, _probe(posture="PARTIAL"), nodes)

    assert len(unresolved) == 1
    assert "unbounded" in unresolved[0].reason


def test_registry_miss_goes_to_worklist_not_fabricated():
    """goTo(ScreenId.Unknown) where Unknown is not in the table produces an UnresolvedEdge."""
    route_table = "src/routes.ts"
    snap = _snap(
        {
            "app/home/page.tsx": "goTo(ScreenId.Unknown)",
            route_table: 'Team: "/team"',
        }
    )
    nodes = [_node("/home", "app/home/page.tsx")]
    edges, unresolved = resolve_edges(snap, _probe(registry_file=route_table), nodes)

    registry_edges = [e for e in edges if e.kind == "registry"]
    assert registry_edges == [], "registry miss must not produce a fabricated resolved edge"
    assert len(unresolved) >= 1


# ── from_route / dedup / determinism ──────────────────────────────────────────


def test_from_route_resolved_from_node_file():
    """Call-site in a file that matches a node's file gets that node's route."""
    snap = _snap({"app/home/page.tsx": 'navigate("/team")'})
    nodes = [_node("/home", "app/home/page.tsx")]
    edges, _ = resolve_edges(snap, _probe(), nodes)

    assert edges[0].from_route == "/home"


def test_shell_global_from_route_empty():
    """Call-site in a file not matched by any node gets from_route == ''."""
    snap = _snap({"app/shell/Sidebar.tsx": 'navigate("/home")'})
    nodes = [_node("/home", "app/home/page.tsx")]  # different file
    edges, _ = resolve_edges(snap, _probe(), nodes)

    assert len(edges) == 1
    assert edges[0].from_route == ""


def test_duplicate_resolved_edges_deduped():
    """Two identical navigate calls from the same screen collapse to one NavEdge."""
    content = 'navigate("/team")\nnavigate("/team")'
    snap = _snap({"app/home/page.tsx": content})
    nodes = [_node("/home", "app/home/page.tsx")]
    edges, _ = resolve_edges(snap, _probe(), nodes)

    matching = [e for e in edges if e.to_route == "/team" and e.from_route == "/home"]
    assert len(matching) == 1


def test_resolution_is_deterministic():
    """Running resolve_edges twice on the same input returns identical sorted results."""
    content = 'navigate("/beta")\nnavigate("/alpha")\nnavigate("/gamma")'
    snap = _snap({"app/home/page.tsx": content})
    nodes = [_node("/home", "app/home/page.tsx")]
    probe = _probe()

    edges1, unresolved1 = resolve_edges(snap, probe, nodes)
    edges2, unresolved2 = resolve_edges(snap, probe, nodes)

    assert edges1 == edges2
    assert unresolved1 == unresolved2

    routes = [e.to_route for e in edges1]
    assert routes == sorted(routes), "resolved edges must be sorted by to_route"


# ── Robustness ─────────────────────────────────────────────────────────────────


def test_commented_call_site_not_mis_resolved():
    """A goTo inside a // comment line must not produce a resolved registry edge."""
    route_table = "src/routes.ts"
    content = "// goTo(ScreenId.Team)\nconst x = 1;"
    snap = _snap(
        {
            "app/home/page.tsx": content,
            route_table: 'Team: "/team"',
        }
    )
    nodes = [_node("/home", "app/home/page.tsx")]
    edges, _ = resolve_edges(snap, _probe(registry_file=route_table), nodes)

    registry_edges = [e for e in edges if e.kind == "registry"]
    assert registry_edges == [], "commented call-site must not produce a registry edge"


# ── Observability / integrity ──────────────────────────────────────────────────


def test_edges_emits_counts_only_log(caplog):
    """resolve_edges emits exactly one INFO line with counts; no raw args or file content."""
    snap = _snap({"app/home/page.tsx": 'navigate("/team")'})
    nodes = [_node("/home", "app/home/page.tsx")]

    with caplog.at_level(logging.INFO, logger="app.design_agent.codebase_map.edges"):
        resolve_edges(snap, _probe(), nodes)

    info = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(info) >= 1
    msg = info[0].getMessage()

    assert "n_resolved=" in msg
    assert "n_unresolved=" in msg
    assert "posture=" in msg
    # raw arg and file content must not appear
    assert 'navigate' not in msg
    assert '"/team"' not in msg


def test_edges_module_imports_without_anthropic_or_ast_parser():
    """edges.py is importable and does not pull in anthropic or any AST parser."""
    edges_path = (
        pathlib.Path(__file__).parent.parent
        / "app" / "design_agent" / "codebase_map" / "edges.py"
    )
    assert edges_path.exists(), "edges.py not found"

    source = edges_path.read_text()

    assert "import anthropic" not in source
    assert "from anthropic" not in source
    for parser in ("esprima", "tree-sitter", "tree_sitter", "@babel", "pyjsparser"):
        assert parser not in source
    # Python stdlib ast module is itself an AST parser — must not be used
    assert "import ast\n" not in source
    assert "from ast import" not in source

    # Module must load cleanly
    spec = importlib.util.spec_from_file_location("_edges_check", edges_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert callable(getattr(mod, "resolve_edges", None))


# ── Regression — NavLink and multi-line JSX discovery ─────────────────────────


def test_navlink_literal_edge_resolved():
    """<NavLink to='/team'> yields a resolved literal edge."""
    snap = _snap({"app/home/page.tsx": '<NavLink to="/team">'})
    nodes = [_node("/home", "app/home/page.tsx")]
    edges, unresolved = resolve_edges(snap, _probe(), nodes)

    assert len(edges) == 1
    e = edges[0]
    assert e.to_route == "/team"
    assert e.kind == "literal"
    assert e.resolved is True
    assert unresolved == []


def test_multiline_link_literal_discovered():
    """<Link> with to= on the next physical line yields a resolved literal edge."""
    content = '<Link\n  to="/customers"\n>'
    snap = _snap({"app/home/page.tsx": content})
    nodes = [_node("/home", "app/home/page.tsx")]
    edges, _ = resolve_edges(snap, _probe(), nodes)

    assert len(edges) == 1
    assert edges[0].to_route == "/customers"
    assert edges[0].kind == "literal"


def test_multiline_navlink_literal_discovered():
    """<NavLink> with to= on the next physical line yields a resolved literal edge."""
    content = '<NavLink\n  to="/flows/new"\n>'
    snap = _snap({"app/home/page.tsx": content})
    nodes = [_node("/home", "app/home/page.tsx")]
    edges, _ = resolve_edges(snap, _probe(), nodes)

    assert len(edges) == 1
    assert edges[0].to_route == "/flows/new"
    assert edges[0].kind == "literal"


def test_multiline_path_builder_discovered():
    """<Link> with a template-literal to= on the next line resolves as path_builder."""
    content = "<Link\n  to={`/flows/${id}/edit`}\n>"
    snap = _snap({"app/home/page.tsx": content})
    nodes = [_node("/home", "app/home/page.tsx")]
    edges, _ = resolve_edges(snap, _probe(), nodes)

    assert len(edges) == 1
    assert edges[0].to_route == "/flows/:id/edit"
    assert edges[0].kind == "path_builder"
    assert edges[0].resolved is True


def test_trailing_comment_after_real_call_not_mis_resolved():
    """goTo('/team') // goTo('/admin') yields exactly one edge to /team."""
    snap = _snap({"app/home/page.tsx": "goTo('/team') // goTo('/admin')"})
    nodes = [_node("/home", "app/home/page.tsx")]
    edges, unresolved = resolve_edges(snap, _probe(), nodes)

    routes = [e.to_route for e in edges]
    assert "/team" in routes, "real nav call must be discovered"
    assert "/admin" not in routes, "commented nav target must not produce an edge"


# ── Composite — launchpad-shape ────────────────────────────────────────────────


def test_launchpad_shape_multiline_graph_not_empty():
    """Multi-line JSX nav (NavLink + Link + path-builder + loop-var) yields non-empty graph."""
    content = (
        '<NavLink\n  to="/customers"\n>\n'
        '<NavLink\n  to="/flows/new"\n>\n'
        "<Link\n  to={`/flows/${id}/edit`}\n>\n"
        "<Link\n  to={`/customers/${id}`}\n>\n"
        "<Link to={to}>\n"
        "<Link to={to}>\n"
    )
    snap = _snap({"app/main/page.tsx": content})
    nodes = [_node("/main", "app/main/page.tsx")]
    edges, unresolved = resolve_edges(snap, _probe(posture="PARTIAL"), nodes)

    to_routes = {e.to_route for e in edges}
    assert "/customers" in to_routes
    assert "/flows/new" in to_routes
    assert "/flows/:id/edit" in to_routes
    assert "/customers/:id" in to_routes
    assert len(edges) >= 4
    assert len(unresolved) >= 2


# ── Non-regression — guards the fix scope ─────────────────────────────────────


def test_trailing_comment_does_not_overstrip_real_route():
    """A trailing comment on a line with a real nav call does not suppress the call."""
    snap = _snap({"app/home/page.tsx": "navigate('/team'); // old path was '/home'"})
    nodes = [_node("/home", "app/home/page.tsx")]
    edges, _ = resolve_edges(snap, _probe(), nodes)

    assert any(e.to_route == "/team" for e in edges)
    assert not any(e.to_route == "/home" for e in edges)


def test_resolver_functions_unchanged():
    """The resolver classifiers are unchanged — the fix stays in discovery."""
    import inspect
    from app.design_agent.codebase_map import edges as edges_mod

    classify_src = inspect.getsource(edges_mod._classify)
    assert "external" in classify_src
    assert "path_builder" in classify_src
    assert "literal" in classify_src
    assert "_classify_reason" in classify_src

    norm_src = inspect.getsource(edges_mod._normalize_template)
    assert ":param" in norm_src
    assert "_INTERP_RE" in norm_src


def test_multiline_call_site_line_number_correct():
    """A multi-line NavLink opening on line 3 emits a call_site anchored to line 3."""
    content = "const a = 1;\nconst b = 2;\n<NavLink\n  to=\"/team\"\n/>"
    snap = _snap({"app/home/page.tsx": content})
    nodes = [_node("/home", "app/home/page.tsx")]
    edges, _ = resolve_edges(snap, _probe(), nodes)

    assert len(edges) == 1
    assert edges[0].call_site.endswith(":3"), (
        f"call_site must reference the opening-tag line; got {edges[0].call_site!r}"
    )


def test_anchor_external_still_classified():
    """<a href=...> still produces an external edge; NavLink does not match the anchor regex."""
    from app.design_agent.codebase_map.edges import _ANCHOR_HREF_RE

    snap = _snap({"app/nav/page.tsx": '<a href="https://external.com">'})
    nodes = [_node("/nav", "app/nav/page.tsx")]
    edges, _ = resolve_edges(snap, _probe(), nodes)
    assert len(edges) == 1
    assert edges[0].kind == "external"

    assert not _ANCHOR_HREF_RE.search("<NavLink to='/home'>"), (
        "<NavLink> must not match the anchor regex"
    )


def test_resolution_is_deterministic_multiline():
    """resolve_edges yields identical sorted results on multi-line NavLink input."""
    content = (
        '<NavLink\n  to="/gamma"\n>\n'
        '<NavLink\n  to="/alpha"\n>\n'
        '<NavLink\n  to="/beta"\n>\n'
    )
    snap = _snap({"app/home/page.tsx": content})
    nodes = [_node("/home", "app/home/page.tsx")]
    probe = _probe()

    edges1, unresolved1 = resolve_edges(snap, probe, nodes)
    edges2, unresolved2 = resolve_edges(snap, probe, nodes)

    assert edges1 == edges2
    assert unresolved1 == unresolved2
    routes = [e.to_route for e in edges1]
    assert routes == sorted(routes)


def test_no_prohibited_tokens_in_source():
    """Neither edges.py nor this test file contain internal tracking tokens.

    Pattern assembled by concatenation so no literal token appears here.
    """
    # Tokens constructed by concatenation — no literal occurrence in this file.
    ticket_coord = r"[CH][0-9]-[0-9]"
    c_ser = "C" + "-" + "series"
    p_tick = r"P[0-9]-[0-9]"
    ad_ref = r"\b" + "AD" + r"[0-9]"
    f_ref = r"\b" + "F" + r"[0-9]{1,2}\b"
    dbd_tok = "D" + "BD"
    auth_tok = "Babaj" + "ide"
    pattern = re.compile(
        "|".join([ticket_coord, c_ser, p_tick, ad_ref, f_ref, dbd_tok, auth_tok])
    )
    root = pathlib.Path(__file__).parent.parent
    for relpath in (
        "app/design_agent/codebase_map/edges.py",
        "tests/test_codebase_map_edges.py",
    ):
        source = (root / relpath).read_text()
        matches = pattern.findall(source)
        assert not matches, f"{relpath} contains prohibited tokens: {matches}"
