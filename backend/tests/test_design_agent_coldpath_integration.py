"""Cold-path + cross-cutting integration proofs for the locate-reliability path.

Each piece of the locate path ships its own unit tests. Per the integration-test
principle — leaf/unit tests can stay green while the *composed* path is broken —
this file adds the proofs that span components and exercise the REAL durable
layer, then anchors the ship-gate regression.

The headline proof (1) drives ``service.build_map`` against the REAL L2 helper
(``app.db.design_agent_map_cache``) backed by the in-memory fake Supabase, NOT
the in-memory ``_FakeL2`` stand-in the durable-cache unit suite uses. That
distinction is the whole point: only a genuinely durable row can survive a
simulated deploy/restart (clear L1, keep the Supabase row) and prove the first
post-restart locate is warm without re-running the deterministic build.

Ship-gate coverage summary (what each section proves)
-----------------------------------------------------
* The durable map cache survives a deploy restart — the L2 hit serves without a
  rebuild. Covered here (real L2) and in the durable-cache unit suite (_FakeL2).
* A push (new commit_sha) naturally invalidates the cache without any explicit
  deletion. Covered here against real L2 and in the unit suite.
* The durable layer is purely additive / fail-soft: with L2 down the build still
  completes from the in-process path. Covered here (real raising L2) and across
  the durable-cache unit suite (get/put fail-soft on db error, missing table is
  a miss, get-raising degrades to in-process only).
* The L2 round-trip is lossless; TTL + kill switch bound staleness. Covered in
  the durable-cache db-helper unit suite.
* Async locate accepts immediately (202) then polls to done — the 504 fix.
  Covered in the async-locate suite (accept-before-heavy-work + poll-done).
* The async locate cold path builds + caches the map, and a second locate for
  the same key reuses it (no rebuild). Covered here, end to end.
* Locate fails open to an unmapped result on a map failure, telemetry kept.
  Covered in the async-locate suite.
* A draining process keeps /locate serving; with the worker OFF it falls back
  in-process. Covered in the async-locate + worker-queue suites; this file adds
  the light cross-cut that a plain locate is unaffected.
* Prewarm seeds the cache on connect; the repo reader bounds the snapshot.
  Covered in the prewarm + repo-reader suites.

Deliberate gaps (verified live, not in CI):
* The real-GitHub resolution + byte fetch in ``read_repo`` is exercised against a
  live installation, not in CI — every test here injects the snapshot/extractors.
  The cache wiring above it is what these proofs cover.
* The ``_locate_jobs`` store is process-local by design; cross-process job
  visibility is intentionally NOT a promise (the durable warmth lives in L2, which
  IS cross-process and is the thing proven here).
"""
from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.design_agent.codebase_map import service
from app.design_agent.codebase_map.repo_reader import RepoSnapshot
from app.design_agent.codebase_map.service import build_map, clear_map_cache
from app.design_agent.codebase_map.types import MapResult, ScreenNode, ShellModel
from tests.conftest import _TEST_COMPANY_ID

# Same SQLite end-state DDL the durable-cache unit suite uses for the L2 table;
# the table is NOT in conftest's base schema, so each L2-touching test seeds it
# onto the per-test fake (mirrors test_design_agent_map_cache._MAP_CACHE_DDL).
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


# ── shared snapshot + extractor instrumentation ───────────────────────────────


def _snapshot(commit_sha: str, repo: str = "org/repo") -> RepoSnapshot:
    return RepoSnapshot(
        repo=repo, commit_sha=commit_sha, branch="main",
        tree_paths=["app/page.tsx"],
        files={"app/page.tsx": "export default function Page(){return null;}"},
        truncated=False,
    )


def _build_internals():
    """Counters for every heavy build step, so a rebuild is observable and a
    no-rebuild (L2 hit) is assertable. read_repo returns the snapshot it is told
    to; one read_repo call == one cold build attempt."""
    from app.design_agent.codebase_map.nav_probe import ProbeResult

    return {
        # read_repo is set per-test (it carries the SHA that keys the cache).
        "probe": MagicMock(return_value=ProbeResult(posture="CLEAN")),
        "nodes": MagicMock(return_value=[
            ScreenNode(route="/team", entry_component="TeamScreen", file="team.tsx"),
        ]),
        "edges": MagicMock(return_value=([], [])),
        "shell": MagicMock(return_value=ShellModel(brand="Acme")),
    }


def _patch_internals(read_repo_mock, internals):
    return [
        patch.object(service, "read_repo", read_repo_mock),
        patch.object(service, "probe_nav_abstraction", internals["probe"]),
        patch.object(service, "extract_nodes", internals["nodes"]),
        patch.object(service, "resolve_edges", internals["edges"]),
        patch.object(service, "extract_shell", internals["shell"]),
    ]


@pytest.fixture
def real_l2(isolated_settings, monkeypatch):
    """The REAL durable L2 (app.db.design_agent_map_cache) wired to the per-test
    fake Supabase with its table present, and INJECTED into the service's L2 seam.

    Unlike the _FakeL2 stand-in, rows written here land in the in-memory
    SQLite that backs the fake client, so they survive a simulated restart (a
    clear of the in-process L1 only). Resets the service module's L1 + memoized
    L2 hook so the test starts from a truly cold process."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_MAP_CACHE_DDL)
    monkeypatch.delenv("DESIGN_AGENT_MAP_CACHE_TTL_SECONDS", raising=False)

    import app.db.design_agent_map_cache as l2_mod
    importlib.reload(l2_mod)  # rebind require_client/utc_now from the reloaded client

    # Inject the real helper as the service's resolved L2 (bypass the lazy import
    # so the test controls exactly which module the seam talks to).
    monkeypatch.setattr(service, "_l2_module", l2_mod)
    monkeypatch.setattr(service, "_l2_resolved", True)

    clear_map_cache()  # cold L1
    yield l2_mod
    clear_map_cache()


def _l2_row_count(installation_id: int, repo: str, commit_sha: str) -> int:
    from tests import _fake_supabase
    rows = _fake_supabase.get_fake_db().execute(
        "SELECT 1 FROM design_agent_map_cache "
        "WHERE installation_id=? AND repo=? AND commit_sha=?",
        [installation_id, repo, commit_sha],
    ).fetchall()
    return len(rows)


# ── 1. DURABLE-CACHE-SURVIVES-RESTART (the headline deploy-warmth proof) ───────


def test_durable_cache_survives_simulated_restart(real_l2):
    """A cold process builds + writes L1 and the DURABLE L2 row; a simulated
    deploy (clear L1, keep the Supabase row) serves the next locate from L2
    WITHOUT re-running the deterministic build. Proves a deploy no longer
    re-pays the cold build."""
    inst, repo, sha = 42, "org/repo", "sha-deploy"

    # ── Cold process #1: L1 empty, L2 empty → the build MUST run. ──
    internals = _build_internals()
    read_repo = MagicMock(return_value=_snapshot(sha))
    patches = _patch_internals(read_repo, internals)
    for p in patches:
        p.start()
    try:
        first = build_map(inst, repo, "org/repo@main")
    finally:
        for p in patches:
            p.stop()

    assert isinstance(first, MapResult)
    # The deterministic build ran exactly once.
    assert read_repo.call_count == 1
    assert internals["probe"].call_count == 1
    assert internals["nodes"].call_count == 1
    # BOTH tiers written: L1 in-process AND the durable L2 row.
    assert service._CACHE.get((inst, repo, sha)) is not None, "L1 must be warm"
    assert _l2_row_count(inst, repo, sha) == 1, "durable L2 row must persist"

    # ── Simulate a DEPLOY / process RESTART. ──
    # Wipe the in-process L1; the durable Supabase row is untouched. (The L2 hook
    # is re-resolved exactly as a fresh process would resolve it, pointing at the
    # SAME fake DB — the durable row is what survives.)
    clear_map_cache()
    assert service._CACHE.get((inst, repo, sha)) is None, "L1 wiped by the restart"
    assert _l2_row_count(inst, repo, sha) == 1, "L2 survives the restart"

    # ── Cold process #2 (post-restart): same key. The build internals are wired
    # to BLOW UP if invoked — the L2 hit must short-circuit the rebuild. ──
    boom = MagicMock(side_effect=AssertionError("must NOT rebuild — L2 should serve warm"))
    # read_repo still has to run (it supplies the SHA that keys the lookup); it is
    # the read-cheap step. The heavy deterministic build steps must NOT run.
    read_repo2 = MagicMock(return_value=_snapshot(sha))
    patches2 = _patch_internals(read_repo2, {
        "probe": boom, "nodes": boom, "edges": boom, "shell": boom,
    })
    for p in patches2:
        p.start()
    try:
        second = build_map(inst, repo, "org/repo@main")
    finally:
        for p in patches2:
            p.stop()

    assert second == first, "post-restart map must equal the original (served from L2)"
    boom.assert_not_called()  # NO deterministic rebuild after the restart
    # The L2 hit repopulated L1, so the rest of this process stays on the fast tier.
    assert service._CACHE.get((inst, repo, sha)) is not None, "L2 hit must rewarm L1"


# ── 2. COMMIT-SHA INVALIDATION end-to-end ─────────────────────────────────────


def test_commit_sha_invalidation_end_to_end(real_l2):
    """Same (installation, repo) but a NEW commit_sha is an L1+L2 miss → the build
    runs again, proving a push naturally invalidates the cache without any explicit
    deletion. The old SHA's row is left intact (history, not mutation)."""
    inst, repo = 42, "org/repo"

    # Build + cache at the old commit.
    internals_old = _build_internals()
    patches = _patch_internals(MagicMock(return_value=_snapshot("sha-old")), internals_old)
    for p in patches:
        p.start()
    try:
        build_map(inst, repo, "org/repo@main")
    finally:
        for p in patches:
            p.stop()
    assert _l2_row_count(inst, repo, "sha-old") == 1

    # Simulate a deploy (wipe L1) so the new build can't ride the in-process tier.
    clear_map_cache()

    # A push lands a NEW commit_sha → both tiers miss → the build MUST run again.
    internals_new = _build_internals()
    read_repo_new = MagicMock(return_value=_snapshot("sha-new"))
    patches = _patch_internals(read_repo_new, internals_new)
    for p in patches:
        p.start()
    try:
        rebuilt = build_map(inst, repo, "org/repo@main")
    finally:
        for p in patches:
            p.stop()

    assert internals_new["probe"].call_count == 1, "new commit_sha must rebuild"
    assert rebuilt.commit_sha == "sha-new"
    # Both rows now coexist — invalidation is by new key, not deletion of the old.
    assert _l2_row_count(inst, repo, "sha-old") == 1
    assert _l2_row_count(inst, repo, "sha-new") == 1


# ── 3. FAIL-SOFT cold path (durable layer is purely additive) ─────────────────


def test_fail_soft_cold_path_l2_down(real_l2, monkeypatch):
    """With the durable L2 layer raising on every call, the cold build still
    returns a correct map from the in-process path — proving L2 is purely
    additive and can never break locate."""
    inst, repo, sha = 42, "org/repo", "sha-l2down"

    # Make the REAL helper's DB client raise so both get and put blow up at the
    # source. The service seam guards get; the helper itself swallows put.
    monkeypatch.setattr(
        real_l2, "require_client", MagicMock(side_effect=RuntimeError("supabase down")),
    )

    internals = _build_internals()
    read_repo = MagicMock(return_value=_snapshot(sha))
    patches = _patch_internals(read_repo, internals)
    for p in patches:
        p.start()
    try:
        result = build_map(inst, repo, "org/repo@main")
    finally:
        for p in patches:
            p.stop()

    assert isinstance(result, MapResult)
    assert result.commit_sha == sha
    assert internals["probe"].call_count == 1, "cold build still runs with L2 down"
    # L1 still populated (in-process path unaffected by the dead L2).
    assert service._CACHE.get((inst, repo, sha)) is not None
    # And no durable row was written (the put fail-softed).
    assert _l2_row_count(inst, repo, sha) == 0


# ── 4. ASYNC-LOCATE COLD PATH through the endpoint ───────────────────────────


@pytest.fixture
def da_env(isolated_settings, monkeypatch):
    """Feature flag ON + DA route stack reloaded (clean job store / inflight set).
    Mirrors test_design_agent_locate_async.env."""
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    return SimpleNamespace(routes=routes_mod, main=main_mod)


@pytest.fixture
def da_client(da_env, isolated_settings, monkeypatch) -> TestClient:
    """Bearer-authed client against the reloaded DA app (mirrors company_client,
    but composes on the local da_env so the reloaded module globals are the ones
    the client hits)."""
    from tests.conftest import (
        _enable_supabase_bearer,
        _mint_supabase_token,
        _seed_company_membership,
    )

    _enable_supabase_bearer(monkeypatch)
    _seed_company_membership(isolated_settings["supabase"])
    c = TestClient(da_env.main.app)
    c.headers["Authorization"] = f"Bearer {_mint_supabase_token()}"
    return c


def _seed_prd(prd_id: int = 1, payload_md: str = "Login screen for the test product") -> None:
    from tests import _fake_supabase
    db = _fake_supabase.get_fake_db()
    workspace_slug = f"slug-{_TEST_COMPANY_ID}"
    db.execute(
        "INSERT INTO briefs (id, dataset, payload, is_current) VALUES (1, ?, '{}', 1)",
        (workspace_slug,),
    )
    db.execute(
        "INSERT INTO prds (id, brief_id, insight_index, title, payload_md, status)"
        " VALUES (?, 1, 0, 'Test PRD', ?, 'ready')",
        (prd_id, payload_md),
    )
    db.commit()


def _mock_installation(monkeypatch, installation_id: int = 42) -> None:
    monkeypatch.setattr(
        "app.routes.design_agent._resolve_github_installation_id_for_repo",
        lambda *a, **kw: installation_id,
    )


def _wire_real_l2_into_reloaded_service():
    """Reload the durable L2 against the per-test fake (table present) and inject
    it into the service seam. Returns the reloaded helper module. Used inside the
    async-endpoint test, after da_env has reloaded the route stack — the service
    module is NOT in the conftest reload order, so its _l2 memoization persists
    across the reload and must be reset here."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_MAP_CACHE_DDL)
    import app.db.design_agent_map_cache as l2_mod
    importlib.reload(l2_mod)
    service._l2_module = l2_mod
    service._l2_resolved = True
    clear_map_cache()
    return l2_mod


def _run_bg(routes, job_id: str, rec: dict, installation_id: int = 42) -> None:
    """Drive the locate background coroutine to completion on an explicit loop —
    the deterministic pattern the async-locate suite uses to step a job to its terminal
    state without racing the TestClient's portal loop."""
    asyncio.run(routes._run_locate_bg(
        job_id=job_id, workspace_id=rec["workspace_id"],
        github_repo="org/repo", ref=None, prd_text="login screen", installation_id=installation_id,
    ))


def test_async_locate_cold_then_warm_through_endpoint(da_client, da_env, monkeypatch):
    """End-to-end cold path through the REAL endpoint: POST /locate → 202 → run the
    bg pipeline (real build_map with patched internals + REAL durable L2) → poll
    done with a gate decision, and the map was built + cached in BOTH tiers. A
    SECOND locate for the same key then serves the cached map — the build internals
    are wired to fail, proving no rebuild. Ties async accept/poll to durable cache
    reuse through the composed flow."""
    _seed_prd()
    _mock_installation(monkeypatch)
    l2 = _wire_real_l2_into_reloaded_service()
    routes = da_env.routes

    # Patch ONLY build_map's heavy internals + locate_screen — build_map itself
    # runs for real so the cache wiring is exercised. read_repo carries the SHA.
    internals = _build_internals()
    read_repo = MagicMock(return_value=_snapshot("sha-async"))

    from app.design_agent.codebase_map.locate import LocateResult, LocateCandidate
    fake_locate = LocateResult(candidates=[LocateCandidate(
        route="/team", entry_component="TeamScreen",
        confidence=90, rationale="main", ambiguous=False,
    )])

    async def _noop_bg(**_kw):
        return None  # the POST registers but doesn't auto-run; we drive it explicitly

    # ── Cold POST: accept immediately. ──
    with patch.object(routes, "_run_locate_bg", new=_noop_bg):
        accepted = da_client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo"},
        )
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job_id"]
    rec = routes._locate_jobs[job_id]

    # ── Run the real pipeline: real build_map (patched internals) + patched locate. ──
    patches = _patch_internals(read_repo, internals)
    for p in patches:
        p.start()
    try:
        with patch("app.design_agent.codebase_map.locate.locate_screen", return_value=fake_locate):
            _run_bg(routes, job_id, rec)
    finally:
        for p in patches:
            p.stop()

    poll = da_client.get(f"/v1/design-agent/locate/jobs/{job_id}").json()
    assert poll["status"] == "done", poll
    assert poll["error"] is None
    result = poll["result"]
    assert result["unmapped"] is False
    assert result["repo"] == "org/repo"
    assert result["commit_sha"] == "sha-async"
    assert result["decision"] in {"auto_proceed", "confirm", "decline"}
    assert read_repo.call_count == 1, "cold path built the map exactly once"

    # Both tiers warmed by the cold build.
    assert service._CACHE.get((42, "org/repo", "sha-async")) is not None
    assert _l2_row_count(42, "org/repo", "sha-async") == 1

    # ── SECOND locate, same key — simulate a fresh process by wiping L1; the
    # durable L2 must serve and the build internals must NOT run. ──
    clear_map_cache()
    boom = MagicMock(side_effect=AssertionError("second locate must reuse the cached map"))
    read_repo2 = MagicMock(return_value=_snapshot("sha-async"))
    patches2 = _patch_internals(read_repo2, {
        "probe": boom, "nodes": boom, "edges": boom, "shell": boom,
    })

    with patch.object(routes, "_run_locate_bg", new=_noop_bg):
        accepted2 = da_client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo"},
        )
    job_id2 = accepted2.json()["job_id"]
    rec2 = routes._locate_jobs[job_id2]

    for p in patches2:
        p.start()
    try:
        with patch("app.design_agent.codebase_map.locate.locate_screen", return_value=fake_locate):
            _run_bg(routes, job_id2, rec2)
    finally:
        for p in patches2:
            p.stop()

    boom.assert_not_called()  # no rebuild — the durable cache served the second locate
    poll2 = da_client.get(f"/v1/design-agent/locate/jobs/{job_id2}").json()
    assert poll2["status"] == "done"
    assert poll2["result"]["commit_sha"] == "sha-async"


# ── 5. Light combined regression: worker-OFF + not-draining don't break locate ──


def test_normal_locate_unaffected_by_worker_off_and_not_draining(da_client, da_env, monkeypatch):
    """A normal locate works end-to-end with DESIGN_AGENT_WORKER_ENABLED unset
    (Tier-2 worker OFF → in-process fallback) and the process NOT draining
    (Tier-0). The Tier-2 fallback paths + drain semantics have dedicated tests
    (test_design_agent_worker_queue / _drain); this is the light cross-cut proving
    they don't interfere with a plain locate. Kept minimal by design."""
    monkeypatch.delenv("DESIGN_AGENT_WORKER_ENABLED", raising=False)
    _seed_prd()
    _mock_installation(monkeypatch)
    routes = da_env.routes
    assert not routes._shutting_down, "precondition: process is not draining"

    async def _noop_bg(**_kw):
        return None

    with patch.object(routes, "_run_locate_bg", new=_noop_bg):
        resp = da_client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo"},
        )
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "running"
