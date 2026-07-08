"""Unit tests for the codebase-map service orchestrator.

The four sub-steps (read_repo, probe_nav_abstraction, extract_nodes,
resolve_edges, extract_shell) are stubbed with MagicMocks — no real
network, no GitHub installation token required. The clock is patched for
TTL tests so the bounded TTL behaviour is observable without waiting.

Plain-engineering note: source files for this module must contain no
internal coordinates. The test_no_prohibited_tokens_in_source test
verifies this by assembling the pattern at runtime so the literals it
checks for are not themselves present in this file as continuous strings.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.design_agent.codebase_map import service
from app.design_agent.codebase_map.nav_probe import ProbeResult
from app.design_agent.codebase_map.repo_reader import RepoSnapshot
from app.design_agent.codebase_map.service import (
    _CACHE_MAX_ENTRIES,
    _CACHE_TTL_SECONDS,
    build_map,
    clear_map_cache,
)
from app.design_agent.codebase_map.types import (
    MapResult,
    NavEdge,
    NavItem,
    ScreenNode,
    ShellModel,
    UnresolvedEdge,
)


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _flush_cache():
    """Wipe the shared cache between tests so state never leaks."""
    clear_map_cache()
    yield
    clear_map_cache()


def _snapshot(commit_sha: str = "sha-aaa", repo: str = "org/repo") -> RepoSnapshot:
    return RepoSnapshot(
        repo=repo,
        commit_sha=commit_sha,
        branch="main",
        tree_paths=["app/page.tsx"],
        files={"app/page.tsx": "export default function Page() { return null; }"},
        truncated=False,
    )


def _probe(posture: str = "CLEAN") -> ProbeResult:
    return ProbeResult(
        posture=posture,
        registry_file="src/screens.ts",
        nav_primitive="goTo",
        route_table_files=["src/routes.tsx"],
        router_convention="react-router",
    )


def _nodes() -> list[ScreenNode]:
    return [
        ScreenNode(route="/team", entry_component="TeamScreen", file="src/screens/Team.tsx"),
        ScreenNode(route="/settings", entry_component="SettingsScreen", file="src/screens/Settings.tsx"),
    ]


def _edges() -> tuple[list[NavEdge], list[UnresolvedEdge]]:
    resolved = [
        NavEdge(from_route="/team", to_route="/settings", kind="literal", resolved=True, call_site="src/screens/Team.tsx:12"),
        NavEdge(from_route="/settings", to_route="", kind="dynamic", resolved=False, call_site="src/screens/Settings.tsx:8"),
    ]
    unresolved = [
        UnresolvedEdge(from_route="/settings", call_site="src/screens/Settings.tsx:8", reason="dynamic variable target"),
    ]
    return resolved, unresolved


def _shell() -> ShellModel:
    return ShellModel(
        brand="Acme",
        nav_items=[
            NavItem(label="Team", order=0, route="/team"),
            NavItem(label="Settings", order=1, route="/settings"),
        ],
        collapse_model="collapsible",
    )


def _patch_pipeline(
    snapshot: RepoSnapshot | None,
    probe: ProbeResult | None = None,
    nodes: list[ScreenNode] | None = None,
    edges: tuple[list[NavEdge], list[UnresolvedEdge]] | None = None,
    shell: ShellModel | None = None,
):
    """Patch every sub-step in the service module's namespace."""
    return (
        patch.object(service, "read_repo", MagicMock(return_value=snapshot)),
        patch.object(service, "probe_nav_abstraction", MagicMock(return_value=probe if probe is not None else _probe())),
        patch.object(service, "extract_nodes", MagicMock(return_value=nodes if nodes is not None else _nodes())),
        patch.object(service, "resolve_edges", MagicMock(return_value=edges if edges is not None else _edges())),
        patch.object(service, "extract_shell", MagicMock(return_value=shell if shell is not None else _shell())),
    )


# ── orchestration ────────────────────────────────────────────────────────────

def test_happy_build_assembles_map_result():
    snap = _snapshot(commit_sha="sha-clean")
    probe = _probe("CLEAN")
    nodes = _nodes()
    edges_pair = _edges()
    shell = _shell()
    patches = _patch_pipeline(snap, probe, nodes, edges_pair, shell)
    for p in patches:
        p.start()
    try:
        result = build_map(123, "org/repo", "org/repo@main")
    finally:
        for p in patches:
            p.stop()

    assert isinstance(result, MapResult)
    assert result.posture == "CLEAN"
    assert result.commit_sha == "sha-clean"
    assert result.repo == "org/repo"
    assert result.nodes == nodes
    assert result.edges == edges_pair[0]
    assert result.unresolved == edges_pair[1]
    assert result.shell == shell


def test_unreadable_repo_returns_none():
    patches = _patch_pipeline(None)
    for p in patches:
        p.start()
    try:
        assert build_map(123, "org/repo", "org/repo@main") is None
    finally:
        for p in patches:
            p.stop()


def test_sub_step_failure_degrades_gracefully(caplog):
    snap = _snapshot(commit_sha="sha-degrade")
    read_mock = MagicMock(return_value=snap)
    probe_mock = MagicMock(return_value=_probe("CLEAN"))
    nodes_mock = MagicMock(return_value=_nodes())
    edges_mock = MagicMock(return_value=_edges())
    shell_mock = MagicMock(side_effect=RuntimeError("boom"))

    with caplog.at_level(logging.WARNING, logger="app.design_agent.codebase_map.service"):
        with patch.object(service, "read_repo", read_mock), \
             patch.object(service, "probe_nav_abstraction", probe_mock), \
             patch.object(service, "extract_nodes", nodes_mock), \
             patch.object(service, "resolve_edges", edges_mock), \
             patch.object(service, "extract_shell", shell_mock):
            result = build_map(123, "org/repo", "org/repo@main")

    assert isinstance(result, MapResult)
    assert result.nodes == _nodes()
    assert result.edges == _edges()[0]
    assert result.shell == ShellModel()
    assert any("shell" in r.getMessage() and "extractor failed" in r.getMessage() for r in caplog.records)


# ── cache ────────────────────────────────────────────────────────────────────

def test_cache_hit_avoids_re_extraction():
    snap = _snapshot(commit_sha="sha-cache")
    read_mock = MagicMock(return_value=snap)
    probe_mock = MagicMock(return_value=_probe("CLEAN"))
    nodes_mock = MagicMock(return_value=_nodes())
    edges_mock = MagicMock(return_value=_edges())
    shell_mock = MagicMock(return_value=_shell())

    with patch.object(service, "read_repo", read_mock), \
         patch.object(service, "probe_nav_abstraction", probe_mock), \
         patch.object(service, "extract_nodes", nodes_mock), \
         patch.object(service, "resolve_edges", edges_mock), \
         patch.object(service, "extract_shell", shell_mock):
        first = build_map(123, "org/repo", "org/repo@main")
        second = build_map(123, "org/repo", "org/repo@main")

    assert read_mock.call_count == 2  # SHA always re-resolved
    assert probe_mock.call_count == 1  # extractors run only on the first miss
    assert nodes_mock.call_count == 1
    assert edges_mock.call_count == 1
    assert shell_mock.call_count == 1
    assert second is first  # identity-equal cached result


def test_new_commit_busts_cache():
    snap1 = _snapshot(commit_sha="sha-a")
    snap2 = _snapshot(commit_sha="sha-b")
    read_mock = MagicMock(side_effect=[snap1, snap2])
    probe_mock = MagicMock(return_value=_probe("CLEAN"))
    nodes_mock = MagicMock(return_value=_nodes())
    edges_mock = MagicMock(return_value=_edges())
    shell_mock = MagicMock(return_value=_shell())

    with patch.object(service, "read_repo", read_mock), \
         patch.object(service, "probe_nav_abstraction", probe_mock), \
         patch.object(service, "extract_nodes", nodes_mock), \
         patch.object(service, "resolve_edges", edges_mock), \
         patch.object(service, "extract_shell", shell_mock):
        first = build_map(123, "org/repo", "org/repo@main")
        second = build_map(123, "org/repo", "org/repo@main")

    assert probe_mock.call_count == 2
    assert nodes_mock.call_count == 2
    assert edges_mock.call_count == 2
    assert shell_mock.call_count == 2
    assert first.commit_sha == "sha-a"
    assert second.commit_sha == "sha-b"


def test_bounded_eviction_lru():
    # Module constant is the source of truth — guard against accidental retune.
    assert _CACHE_MAX_ENTRIES == 32

    snapshots = [_snapshot(commit_sha=f"sha-{i:03d}") for i in range(_CACHE_MAX_ENTRIES + 1)]
    read_mock = MagicMock(side_effect=list(snapshots))
    probe_mock = MagicMock(return_value=_probe())
    nodes_mock = MagicMock(return_value=_nodes())
    edges_mock = MagicMock(return_value=_edges())
    shell_mock = MagicMock(return_value=_shell())

    with patch.object(service, "read_repo", read_mock), \
         patch.object(service, "probe_nav_abstraction", probe_mock), \
         patch.object(service, "extract_nodes", nodes_mock), \
         patch.object(service, "resolve_edges", edges_mock), \
         patch.object(service, "extract_shell", shell_mock):
        for i in range(_CACHE_MAX_ENTRIES + 1):
            build_map(i, "org/repo", "org/repo@main")

    # Cache size never exceeds the bound.
    assert len(service._CACHE._entries) <= _CACHE_MAX_ENTRIES

    # The first-inserted key (installation 0) was the least-recently-used at
    # insertion of the 33rd entry, so it should have been evicted; re-asking
    # for it runs the extractors again.
    read_mock.side_effect = None
    read_mock.return_value = snapshots[0]
    probe_calls_before = probe_mock.call_count
    with patch.object(service, "read_repo", read_mock), \
         patch.object(service, "probe_nav_abstraction", probe_mock), \
         patch.object(service, "extract_nodes", nodes_mock), \
         patch.object(service, "resolve_edges", edges_mock), \
         patch.object(service, "extract_shell", shell_mock):
        build_map(0, "org/repo", "org/repo@main")
    assert probe_mock.call_count == probe_calls_before + 1


def test_ttl_expiry_rebuilds():
    snap = _snapshot(commit_sha="sha-ttl")
    read_mock = MagicMock(return_value=snap)
    probe_mock = MagicMock(return_value=_probe())
    nodes_mock = MagicMock(return_value=_nodes())
    edges_mock = MagicMock(return_value=_edges())
    shell_mock = MagicMock(return_value=_shell())

    fake_now = [1000.0]

    def _clock() -> float:
        return fake_now[0]

    with patch.object(service.time, "monotonic", side_effect=_clock), \
         patch.object(service, "read_repo", read_mock), \
         patch.object(service, "probe_nav_abstraction", probe_mock), \
         patch.object(service, "extract_nodes", nodes_mock), \
         patch.object(service, "resolve_edges", edges_mock), \
         patch.object(service, "extract_shell", shell_mock):
        build_map(123, "org/repo", "org/repo@main")
        # Within TTL — cache hit, no re-extraction.
        fake_now[0] = 1000.0 + _CACHE_TTL_SECONDS - 1
        build_map(123, "org/repo", "org/repo@main")
        assert probe_mock.call_count == 1
        # Past TTL — treated as a miss and rebuilt.
        fake_now[0] = 1000.0 + _CACHE_TTL_SECONDS + 1
        build_map(123, "org/repo", "org/repo@main")
        assert probe_mock.call_count == 2


def test_clear_map_cache_scoped_and_all():
    snap_a = _snapshot(commit_sha="sha-a")
    snap_b = _snapshot(commit_sha="sha-b")
    read_mock = MagicMock(side_effect=[snap_a, snap_b, snap_a, snap_b, snap_a, snap_b])
    probe_mock = MagicMock(return_value=_probe())
    nodes_mock = MagicMock(return_value=_nodes())
    edges_mock = MagicMock(return_value=_edges())
    shell_mock = MagicMock(return_value=_shell())

    with patch.object(service, "read_repo", read_mock), \
         patch.object(service, "probe_nav_abstraction", probe_mock), \
         patch.object(service, "extract_nodes", nodes_mock), \
         patch.object(service, "resolve_edges", edges_mock), \
         patch.object(service, "extract_shell", shell_mock):
        build_map(123, "org/repo", "org/repo@main")
        build_map(456, "org/repo", "org/repo@main")
        assert probe_mock.call_count == 2

        # Scoped clear: only installation 123 is dropped; 456 still cached.
        clear_map_cache(123)
        build_map(123, "org/repo", "org/repo@main")  # miss → extractors run
        build_map(456, "org/repo", "org/repo@main")  # hit → no extraction
        assert probe_mock.call_count == 3

        # Full clear: every entry dropped.
        clear_map_cache()
        build_map(123, "org/repo", "org/repo@main")
        build_map(456, "org/repo", "org/repo@main")
        assert probe_mock.call_count == 5


def test_clear_map_cache_scoped_calls_l2_delete(monkeypatch):
    """A scoped clear also invalidates the durable tier: it calls the L2
    delete-by-installation helper with the same installation_id."""
    fake_l2 = MagicMock()
    monkeypatch.setattr(service, "_l2", lambda: fake_l2)
    clear_map_cache(123)
    fake_l2.delete_cached_maps_for_installation.assert_called_once_with(123)


def test_clear_map_cache_unscoped_does_not_call_l2_delete(monkeypatch):
    """The bare (unscoped) clear used for test isolation touches L1 only — it
    must NOT reach the durable tier, preserving every existing bare-call site."""
    fake_l2 = MagicMock()
    monkeypatch.setattr(service, "_l2", lambda: fake_l2)
    clear_map_cache()
    fake_l2.delete_cached_maps_for_installation.assert_not_called()


def test_clear_map_cache_l2_unavailable_still_clears_l1(monkeypatch):
    """When the durable tier is unavailable (_l2() returns None), a scoped clear
    still drops the in-process entry and does not raise — mirrors the module's
    existing degrade-to-L1-only contract."""
    monkeypatch.setattr(service, "_l2", lambda: None)
    snap = _snapshot(commit_sha="sha-noL2clear")
    patches = _patch_pipeline(snap)
    for p in patches:
        p.start()
    try:
        build_map(123, "org/repo", "org/repo@main")
    finally:
        for p in patches:
            p.stop()
    assert service._CACHE.get((123, "org/repo", "sha-noL2clear")) is not None
    clear_map_cache(123)  # must not raise even though L2 is unavailable
    assert service._CACHE.get((123, "org/repo", "sha-noL2clear")) is None


def test_cross_installation_no_key_collision():
    snap = _snapshot(commit_sha="sha-shared")
    read_mock = MagicMock(return_value=snap)
    probe_mock = MagicMock(return_value=_probe())
    nodes_mock = MagicMock(return_value=_nodes())
    edges_mock = MagicMock(return_value=_edges())
    shell_mock = MagicMock(return_value=_shell())

    with patch.object(service, "read_repo", read_mock), \
         patch.object(service, "probe_nav_abstraction", probe_mock), \
         patch.object(service, "extract_nodes", nodes_mock), \
         patch.object(service, "resolve_edges", edges_mock), \
         patch.object(service, "extract_shell", shell_mock):
        a = build_map(123, "org/repo", "org/repo@main")
        b = build_map(456, "org/repo", "org/repo@main")

    assert probe_mock.call_count == 2  # both installations ran extractors
    assert a is not b  # distinct cached entries


# ── persistence boundary ─────────────────────────────────────────────────────

def test_no_db_or_migration_in_service():
    source = (Path(__file__).parent.parent / "app" / "design_agent" / "codebase_map" / "service.py").read_text()
    for forbidden in ("workspace_id", "CREATE TABLE", "supabase", "from app.db", "supabase/migrations"):
        assert forbidden not in source, f"Forbidden persistence token '{forbidden}' in service.py"


# ── observability / integrity ────────────────────────────────────────────────

def test_build_emits_structured_timing_log(caplog):
    snap = _snapshot(commit_sha="sha-log")
    read_mock = MagicMock(return_value=snap)
    probe_mock = MagicMock(return_value=_probe("CLEAN"))
    nodes_mock = MagicMock(return_value=_nodes())
    edges_mock = MagicMock(return_value=_edges())
    shell_mock = MagicMock(return_value=_shell())
    secret_token = "ghs_super_secret_installation_token_value"
    file_body = "export default function Page() { return null; }"  # matches snapshot

    with caplog.at_level(logging.INFO, logger="app.design_agent.codebase_map.service"):
        with patch.object(service, "read_repo", read_mock), \
             patch.object(service, "probe_nav_abstraction", probe_mock), \
             patch.object(service, "extract_nodes", nodes_mock), \
             patch.object(service, "resolve_edges", edges_mock), \
             patch.object(service, "extract_shell", shell_mock):
            build_map(123, "org/repo", "org/repo@main")
            build_map(123, "org/repo", "org/repo@main")

    messages = [r.getMessage() for r in caplog.records if r.name.startswith("app.design_agent.codebase_map.service")]
    miss_lines = [m for m in messages if "cache=miss" in m]
    hit_lines = [m for m in messages if "cache=hit" in m]

    assert len(miss_lines) == 1, f"Expected exactly 1 cache=miss line, got {len(miss_lines)}: {messages}"
    miss = miss_lines[0]
    assert "repo=org/repo" in miss
    assert "sha=sha-log" in miss
    assert "posture=CLEAN" in miss
    assert "n_nodes=2" in miss
    assert "n_edges=2" in miss
    assert "n_resolved=1" in miss
    assert "n_unresolved=1" in miss
    assert "n_nav_items=2" in miss
    assert "duration_ms=" in miss

    assert len(hit_lines) == 1
    hit = hit_lines[0]
    assert "cache=hit" in hit
    assert "repo=org/repo" in hit
    assert "sha=sha-log" in hit

    # Neither line carries a file body or the installation token.
    for m in miss_lines + hit_lines:
        assert file_body not in m
        assert secret_token not in m


def test_service_module_imports_without_anthropic():
    result = subprocess.run(
        [sys.executable, "-c",
         "import app.design_agent.codebase_map.service; "
         "import sys; assert 'anthropic' not in sys.modules"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_no_prohibited_tokens_in_source():
    """Neither deliverable file contains internal engagement coordinates.

    The pattern is assembled at runtime from split parts so the literals
    being checked do not appear verbatim in this test file itself.
    """
    repo_root = Path(__file__).parent.parent
    targets = [
        repo_root / "app" / "design_agent" / "codebase_map" / "service.py",
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
