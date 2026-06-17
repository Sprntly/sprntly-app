"""Tests for the durable L2 codebase-map cache.

Two layers:

1. **DB-helper tests** (``app.db.design_agent_map_cache``) against the in-memory
   FakeSupabaseClient — round-trip, UPSERT refresh, TTL filter on read, the
   opportunistic sweep, the kill switch, and (load-bearing) fail-soft on a DB
   error.

2. **Service-integration tests** (``service.build_map``) that prove the L1/L2
   wiring: L2 hit after an L1 wipe serves WITHOUT rebuilding, a cold build
   write-throughs to both tiers, commit_sha keys the cache, and a raising L2
   helper degrades to today's in-process-only behavior (build still succeeds).

The map-cache table is added on top of conftest's already-reset fake schema in a
local fixture, mirroring ``test_db_prototype_comments.py``. The migration's
idempotency / shape is checked at the SQL-string level (no live Postgres in the
dev env), the same convention the sibling migration tests use.
"""
from __future__ import annotations

import importlib
import logging
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.design_agent.codebase_map import service
from app.design_agent.codebase_map.repo_reader import RepoSnapshot
from app.design_agent.codebase_map.service import build_map, clear_map_cache
from app.design_agent.codebase_map.types import (
    MapResult,
    NavEdge,
    NavItem,
    ScreenNode,
    ShellModel,
    UnresolvedEdge,
)

# SQLite end-state of design_agent_map_cache after the migration. Postgres
# constructs (bigint identity, timestamptz, jsonb, RLS, the policy) are
# translated/omitted as the sibling test DDLs do. `payload` is registered as a
# jsonb column in _fake_supabase so dicts round-trip; here it is a TEXT column
# (the fake JSON-encodes/decodes at the boundary).
_MAP_CACHE_DDL = """
CREATE TABLE design_agent_map_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    installation_id INTEGER NOT NULL,
    repo            TEXT NOT NULL,
    commit_sha      TEXT NOT NULL,
    payload         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (installation_id, repo, commit_sha)
);
"""

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260615120000_design_agent_map_cache.sql"
)


@pytest.fixture
def l2(isolated_settings, monkeypatch):
    """The reloaded app.db.design_agent_map_cache module wired to the fake
    Supabase, with the design_agent_map_cache table present. Also clears the L2
    TTL env override so each test starts from the default."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_MAP_CACHE_DDL)
    monkeypatch.delenv("DESIGN_AGENT_MAP_CACHE_TTL_SECONDS", raising=False)

    import app.db.design_agent_map_cache as l2_mod
    importlib.reload(l2_mod)  # rebind require_client/utc_now from the reloaded client
    return l2_mod


# ── sample map ────────────────────────────────────────────────────────────────

def _sample_map(commit_sha: str = "sha-aaa", repo: str = "org/repo") -> MapResult:
    return MapResult(
        repo=repo,
        commit_sha=commit_sha,
        posture="CLEAN",
        nodes=[
            ScreenNode(route="/team", entry_component="TeamScreen", file="src/Team.tsx"),
            ScreenNode(route="/settings", entry_component="SettingsScreen", file="src/Settings.tsx"),
        ],
        edges=[
            NavEdge(from_route="/team", to_route="/settings", kind="literal",
                    resolved=True, call_site="src/Team.tsx:12"),
        ],
        shell=ShellModel(
            brand="Acme",
            nav_items=[NavItem(label="Team", order=0, route="/team")],
            collapse_model="collapsible",
        ),
        unresolved=[
            UnresolvedEdge(from_route="/settings", call_site="src/Settings.tsx:8",
                           reason="dynamic target"),
        ],
    )


# ── migration (string-level) ──────────────────────────────────────────────────

def _migration_sql_only() -> str:
    lines = [line.split("--", 1)[0] for line in _MIGRATION_PATH.read_text().splitlines()]
    return "\n".join(lines).lower()


def test_migration_file_exists_and_dated():
    assert _MIGRATION_PATH.exists()
    assert _MIGRATION_PATH.name == "20260615120000_design_agent_map_cache.sql"


def test_migration_declares_columns_and_indexes():
    sql = _migration_sql_only()
    assert "create table if not exists design_agent_map_cache" in sql
    for col in ("installation_id", "repo", "commit_sha", "payload", "created_at", "updated_at"):
        assert col in sql, f"migration missing column {col}"
    assert "installation_id bigint" in sql
    assert "payload         jsonb" in sql
    # The cache key is a UNIQUE index on (installation_id, repo, commit_sha).
    assert "unique index" in sql
    assert "(installation_id, repo, commit_sha)" in sql
    # A created_at index supports the TTL sweep.
    assert "(created_at)" in sql


def test_migration_idempotent_and_rls():
    sql = _migration_sql_only()
    for m in re.finditer(r"create\s+table\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent CREATE TABLE near offset {m.start()}")
    for m in re.finditer(r"create\s+index\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent CREATE INDEX near offset {m.start()}")
    assert "create unique index if not exists" in sql
    assert sql.count("enable row level security") == 1
    assert 'create policy "srv_design_agent_map_cache"' in sql
    assert "for all using (true) with check (true)" in sql


# ── db helper: round-trip / upsert / ttl / sweep ─────────────────────────────

def test_round_trip_lossless(l2):
    m = _sample_map(commit_sha="sha-rt")
    l2.put_cached_map(123, "org/repo", "sha-rt", m.model_dump(mode="json"))
    payload = l2.get_cached_map(123, "org/repo", "sha-rt")
    assert payload is not None
    revived = MapResult.model_validate(payload)
    assert revived == m  # lossless round-trip


def test_get_miss_returns_none(l2):
    assert l2.get_cached_map(123, "org/repo", "never-cached") is None


def test_upsert_refreshes_in_place(l2):
    m1 = _sample_map(commit_sha="sha-x")
    l2.put_cached_map(7, "org/repo", "sha-x", m1.model_dump(mode="json"))
    # Same key, different payload (a same-SHA force-push) → refresh, not a dup.
    m2 = _sample_map(commit_sha="sha-x")
    m2.posture = "PARTIAL"
    l2.put_cached_map(7, "org/repo", "sha-x", m2.model_dump(mode="json"))

    from tests import _fake_supabase
    rows = _fake_supabase.get_fake_db().execute(
        "SELECT * FROM design_agent_map_cache WHERE installation_id=7 AND commit_sha='sha-x'"
    ).fetchall()
    assert len(rows) == 1, "UPSERT must refresh the single row, not duplicate"
    payload = l2.get_cached_map(7, "org/repo", "sha-x")
    assert MapResult.model_validate(payload).posture == "PARTIAL"


def test_commit_sha_keys_the_cache(l2):
    l2.put_cached_map(1, "org/repo", "sha-a", _sample_map("sha-a").model_dump(mode="json"))
    assert l2.get_cached_map(1, "org/repo", "sha-a") is not None
    # A different commit_sha is a miss.
    assert l2.get_cached_map(1, "org/repo", "sha-b") is None


def test_installation_and_repo_key_the_cache(l2):
    l2.put_cached_map(1, "org/repo", "sha", _sample_map("sha").model_dump(mode="json"))
    assert l2.get_cached_map(2, "org/repo", "sha") is None      # other installation
    assert l2.get_cached_map(1, "org/other", "sha") is None     # other repo


def test_expired_row_is_a_miss(l2, monkeypatch):
    l2.put_cached_map(1, "org/repo", "sha-old", _sample_map("sha-old").model_dump(mode="json"))
    # Make the TTL tiny + the row "old" by patching _age_seconds to report a
    # large age regardless of the stored timestamp.
    monkeypatch.setenv("DESIGN_AGENT_MAP_CACHE_TTL_SECONDS", "10")
    monkeypatch.setattr(l2, "_age_seconds", lambda _ts: 999.0)
    assert l2.get_cached_map(1, "org/repo", "sha-old") is None


def test_fresh_row_within_ttl_is_a_hit(l2, monkeypatch):
    l2.put_cached_map(1, "org/repo", "sha-fresh", _sample_map("sha-fresh").model_dump(mode="json"))
    monkeypatch.setattr(l2, "_age_seconds", lambda _ts: 5.0)
    assert l2.get_cached_map(1, "org/repo", "sha-fresh") is not None


def test_ttl_zero_disables_l2_reads(l2, monkeypatch):
    l2.put_cached_map(1, "org/repo", "sha-k", _sample_map("sha-k").model_dump(mode="json"))
    monkeypatch.setenv("DESIGN_AGENT_MAP_CACHE_TTL_SECONDS", "0")
    # Kill switch: reads return None even though the row exists + is fresh.
    assert l2.get_cached_map(1, "org/repo", "sha-k") is None


def test_sweep_deletes_only_expired(l2, monkeypatch):
    # Insert a fresh row + a stale row (stale created_at written directly).
    l2.put_cached_map(1, "org/repo", "sha-fresh", _sample_map("sha-fresh").model_dump(mode="json"))
    from tests import _fake_supabase
    db = _fake_supabase.get_fake_db()
    db.execute(
        "INSERT INTO design_agent_map_cache (installation_id, repo, commit_sha, payload, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        [1, "org/repo", "sha-stale", "{}", "2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00"],
    )
    db.commit()
    deleted = l2.sweep_expired_map_cache()
    assert deleted == 1
    remaining = db.execute("SELECT commit_sha FROM design_agent_map_cache").fetchall()
    shas = {r["commit_sha"] for r in remaining}
    assert shas == {"sha-fresh"}


# ── db helper: FAIL-SOFT (load-bearing) ──────────────────────────────────────

def test_get_fail_soft_on_db_error(l2, monkeypatch, caplog):
    # A DB error in get_cached_map → warning + None, never an exception.
    monkeypatch.setattr(l2, "require_client", MagicMock(side_effect=RuntimeError("db down")))
    with caplog.at_level(logging.WARNING, logger="app.db.design_agent_map_cache"):
        out = l2.get_cached_map(1, "org/repo", "sha")
    assert out is None
    assert any("l2 read failed" in r.getMessage() for r in caplog.records)


def test_put_fail_soft_on_db_error(l2, monkeypatch, caplog):
    # A DB error in put_cached_map → warning + no-op, never an exception.
    monkeypatch.setattr(l2, "require_client", MagicMock(side_effect=RuntimeError("db down")))
    with caplog.at_level(logging.WARNING, logger="app.db.design_agent_map_cache"):
        l2.put_cached_map(1, "org/repo", "sha", {"any": "payload"})  # must not raise
    assert any("l2 write failed" in r.getMessage() for r in caplog.records)


def test_sweep_fail_soft_on_db_error(l2, monkeypatch):
    monkeypatch.setattr(l2, "require_client", MagicMock(side_effect=RuntimeError("db down")))
    assert l2.sweep_expired_map_cache() == 0  # never raises


def test_missing_table_is_a_miss_not_a_crash(isolated_settings, monkeypatch):
    # No DDL added → the table does not exist. get/put must degrade to a miss.
    monkeypatch.delenv("DESIGN_AGENT_MAP_CACHE_TTL_SECONDS", raising=False)
    import app.db.design_agent_map_cache as l2_mod
    importlib.reload(l2_mod)
    assert l2_mod.get_cached_map(1, "org/repo", "sha") is None
    l2_mod.put_cached_map(1, "org/repo", "sha", {"x": 1})  # must not raise


# ── service integration: L1 + L2 wiring ──────────────────────────────────────

def _snapshot(commit_sha: str = "sha-aaa", repo: str = "org/repo") -> RepoSnapshot:
    return RepoSnapshot(
        repo=repo, commit_sha=commit_sha, branch="main",
        tree_paths=["app/page.tsx"],
        files={"app/page.tsx": "export default function Page(){return null;}"},
        truncated=False,
    )


@pytest.fixture(autouse=True)
def _flush_l1():
    clear_map_cache()
    yield
    clear_map_cache()


class _FakeL2:
    """In-memory stand-in for the L2 helper module, injected into service._l2."""

    def __init__(self):
        self.store: dict[tuple, dict] = {}
        self.get_calls = 0
        self.put_calls = 0

    def get_cached_map(self, installation_id, repo, commit_sha):
        self.get_calls += 1
        return self.store.get((installation_id, repo, commit_sha))

    def put_cached_map(self, installation_id, repo, commit_sha, payload):
        self.put_calls += 1
        self.store[(installation_id, repo, commit_sha)] = payload


def _patch_build(snapshot, *, build_mock):
    """Patch read_repo to return `snapshot` and every extractor with build_mock
    side effects so a cold build is observable / assertable-not-called."""
    return [
        patch.object(service, "read_repo", MagicMock(return_value=snapshot)),
        patch.object(service, "probe_nav_abstraction", build_mock["probe"]),
        patch.object(service, "extract_nodes", build_mock["nodes"]),
        patch.object(service, "resolve_edges", build_mock["edges"]),
        patch.object(service, "extract_shell", build_mock["shell"]),
    ]


def _build_mocks():
    from app.design_agent.codebase_map.nav_probe import ProbeResult
    return {
        "probe": MagicMock(return_value=ProbeResult(posture="CLEAN")),
        "nodes": MagicMock(return_value=[ScreenNode(route="/team", entry_component="T", file="t.tsx")]),
        "edges": MagicMock(return_value=([], [])),
        "shell": MagicMock(return_value=ShellModel(brand="Acme")),
    }


def _inject_l2(monkeypatch, fake):
    """Force service._l2() to return `fake` (resolved + memoized)."""
    monkeypatch.setattr(service, "_l2_module", fake)
    monkeypatch.setattr(service, "_l2_resolved", True)


def test_cold_build_writes_through_to_both_tiers(monkeypatch):
    fake_l2 = _FakeL2()
    _inject_l2(monkeypatch, fake_l2)
    mocks = _build_mocks()
    snap = _snapshot("sha-wt")
    patches = _patch_build(snap, build_mock=mocks)
    for p in patches:
        p.start()
    try:
        result = build_map(123, "org/repo", "org/repo@main")
    finally:
        for p in patches:
            p.stop()
    assert isinstance(result, MapResult)
    # L1 populated.
    assert service._CACHE.get((123, "org/repo", "sha-wt")) is not None
    # L2 written-through with a JSON-serializable payload.
    assert fake_l2.put_calls == 1
    assert (123, "org/repo", "sha-wt") in fake_l2.store
    payload = fake_l2.store[(123, "org/repo", "sha-wt")]
    assert MapResult.model_validate(payload) == result


def test_l2_hit_after_l1_wipe_serves_without_rebuild(monkeypatch):
    fake_l2 = _FakeL2()
    _inject_l2(monkeypatch, fake_l2)
    snap = _snapshot("sha-warm")

    # Cold build #1 populates L1 + L2.
    mocks1 = _build_mocks()
    patches = _patch_build(snap, build_mock=mocks1)
    for p in patches:
        p.start()
    try:
        first = build_map(123, "org/repo", "org/repo@main")
    finally:
        for p in patches:
            p.stop()
    assert mocks1["probe"].call_count == 1

    # Simulate a deploy/restart: wipe the in-process L1 only.
    clear_map_cache()

    # Build #2 — the extractors are wired to FAIL if called. The L2 hit must
    # short-circuit the rebuild entirely.
    boom = MagicMock(side_effect=AssertionError("must not rebuild — L2 should serve"))
    mocks2 = {"probe": boom, "nodes": boom, "edges": boom, "shell": boom}
    patches = _patch_build(snap, build_mock=mocks2)
    for p in patches:
        p.start()
    try:
        second = build_map(123, "org/repo", "org/repo@main")
    finally:
        for p in patches:
            p.stop()

    assert second == first
    boom.assert_not_called()
    # L1 was repopulated from the L2 hit (warm for the rest of this process).
    assert service._CACHE.get((123, "org/repo", "sha-warm")) is not None


def test_different_commit_sha_is_an_l2_miss_and_rebuilds(monkeypatch):
    fake_l2 = _FakeL2()
    _inject_l2(monkeypatch, fake_l2)

    # Build at sha-a populates L2 for sha-a only.
    mocks_a = _build_mocks()
    patches = _patch_build(_snapshot("sha-a"), build_mock=mocks_a)
    for p in patches:
        p.start()
    try:
        build_map(123, "org/repo", "org/repo@main")
    finally:
        for p in patches:
            p.stop()
    clear_map_cache()  # wipe L1

    # Build at sha-b: L2 miss (different key) → must rebuild.
    mocks_b = _build_mocks()
    patches = _patch_build(_snapshot("sha-b"), build_mock=mocks_b)
    for p in patches:
        p.start()
    try:
        result_b = build_map(123, "org/repo", "org/repo@main")
    finally:
        for p in patches:
            p.stop()
    assert mocks_b["probe"].call_count == 1  # rebuilt
    assert result_b.commit_sha == "sha-b"


def test_l2_unavailable_degrades_to_in_process_only(monkeypatch):
    # _l2() returns None (e.g. import failed) → build runs L1-only, exactly as
    # the historical behavior. The build must still succeed.
    monkeypatch.setattr(service, "_l2_module", None)
    monkeypatch.setattr(service, "_l2_resolved", True)
    mocks = _build_mocks()
    patches = _patch_build(_snapshot("sha-noL2"), build_mock=mocks)
    for p in patches:
        p.start()
    try:
        result = build_map(123, "org/repo", "org/repo@main")
    finally:
        for p in patches:
            p.stop()
    assert isinstance(result, MapResult)
    assert service._CACHE.get((123, "org/repo", "sha-noL2")) is not None  # L1 still works


def test_l2_get_raising_does_not_break_build(monkeypatch):
    # FAIL-SOFT at the service seam: even a misbehaving hook whose get_cached_map
    # RAISES must not propagate — the build treats it as a miss and rebuilds.
    raising_l2 = MagicMock()
    raising_l2.get_cached_map = MagicMock(side_effect=RuntimeError("l2 exploded"))
    raising_l2.put_cached_map = MagicMock()  # may also be exercised on write-through
    _inject_l2(monkeypatch, raising_l2)
    mocks = _build_mocks()
    patches = _patch_build(_snapshot("sha-raise"), build_mock=mocks)
    for p in patches:
        p.start()
    try:
        result = build_map(123, "org/repo", "org/repo@main")
    finally:
        for p in patches:
            p.stop()
    assert isinstance(result, MapResult)
    assert mocks["probe"].call_count == 1  # rebuilt after the raised-get miss
    assert result.commit_sha == "sha-raise"
