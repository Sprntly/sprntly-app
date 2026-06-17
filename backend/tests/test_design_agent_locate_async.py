"""Tests for the async locate accept + poll contract.

POST /v1/design-agent/locate no longer runs the map → locate LLM → gate pipeline
inline. Under generation load the single API process is CPU-saturated, so the
synchronous request hung past the proxy read timeout and returned 504; the
frontend then silently collapsed to the PRD. The endpoint now:

  - validates + authorizes inline (feature gate, PRD ownership, installation),
  - mints a process-local job record,
  - kicks the pipeline into a background task registered for graceful drain, and
  - returns a job id IMMEDIATELY (HTTP 202, status "running").

The client polls GET /v1/design-agent/locate/jobs/{job_id} until status is
"done" (carrying the full LocateResponse) or "error".

This suite covers the async-specific behaviours: immediate return without
awaiting the heavy work, the job store running→done/error transitions, the
full result payload, the error path with telemetry preserved, cross-workspace
isolation (404, not 403), in-flight registration for drain, and TTL sweep.
The response-shape / gate-decision coverage lives in
test_design_agent_locate_route.py (driven through this same async path).
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import _TEST_COMPANY_ID


# ── fixtures (mirror the route suite) ─────────────────────────────────────────


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """Feature flag ON + DA route stack reloaded so module globals (job store,
    _inflight_tasks) start clean for this test."""
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    return SimpleNamespace(routes=routes_mod, main=main_mod)


@pytest.fixture
def client(company_client) -> TestClient:
    """Bearer-authed TestClient; workspace_id == _TEST_COMPANY_ID."""
    return company_client


# ── seed helpers ──────────────────────────────────────────────────────────────


def _seed_prd(*, prd_id: int = 1, payload_md: str = "Login screen for the test product") -> None:
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


def _make_map_result(route: str = "/home", entry_component: str = "HomeScreen",
                     confidence: int = 90, composed_components: list | None = None):
    from app.design_agent.codebase_map.types import MapResult, ScreenNode, ShellModel
    from app.design_agent.codebase_map.locate import LocateResult, LocateCandidate

    node = ScreenNode(
        route=route, entry_component=entry_component,
        composed_components=composed_components or ["Header", "Footer"],
    )
    map_result = MapResult(
        repo="org/repo", commit_sha="sha-abc",
        posture="CLEAN", nodes=[node], shell=ShellModel(),  # type: ignore[arg-type]
    )
    candidate = LocateCandidate(
        route=route, entry_component=entry_component,
        confidence=confidence, rationale="Main screen", ambiguous=False,
    )
    return map_result, LocateResult(candidates=[candidate])


# ── 1. POST returns immediately, BEFORE the heavy work completes ───────────────


def test_post_returns_before_heavy_work_completes(client, env, monkeypatch):
    """POST mints a job and returns 202 running while build_map is still blocked —
    proving the request no longer awaits the pipeline (the 504 root cause)."""
    _seed_prd()
    _mock_installation(monkeypatch)
    release = asyncio.Event()

    async def _blocking_to_thread(func, *args, **kwargs):
        if func.__name__ == "build_map":
            await release.wait()  # never released during the POST → heavy work hangs
            return None
        return None

    with patch("asyncio.to_thread", new=_blocking_to_thread):
        resp = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo"},
        )

    # The POST returned even though build_map is still parked on release.wait().
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "running"
    assert isinstance(body["job_id"], str) and body["job_id"]

    job_id = body["job_id"]
    assert env.routes._locate_jobs[job_id]["status"] == "running"

    # Clean up the parked background task so it does not leak into other tests.
    release.set()
    pending = [t for t in env.routes._inflight_tasks if not t.done()]
    for t in pending:
        t.cancel()


# ── 2. Background task populates the store; poll transitions running→done ───────


def test_background_task_populates_store_poll_done(client, env, monkeypatch):
    """Running the background task to completion transitions the job to done and
    the poll returns the FULL LocateResponse payload (every field)."""
    _seed_prd()
    _mock_installation(monkeypatch)
    fake_map, fake_locate = _make_map_result(
        confidence=90, composed_components=["Header", "Hero", "Footer"]
    )

    async def _fake_to_thread(func, *args, **kwargs):
        if func.__name__ == "build_map":
            return fake_map
        return fake_locate

    async def _noop_bg(**_kw):
        return None

    with patch.object(env.routes, "_run_locate_bg", new=_noop_bg):
        accepted = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo"},
        )
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]

    # Poll while still running → status running, no result yet.
    running = client.get(f"/v1/design-agent/locate/jobs/{job_id}")
    assert running.status_code == 200
    assert running.json()["status"] == "running"
    assert running.json()["result"] is None

    rec = env.routes._locate_jobs[job_id]
    with patch("asyncio.to_thread", new=_fake_to_thread):
        asyncio.run(env.routes._run_locate_bg(
            job_id=job_id, workspace_id=rec["workspace_id"],
            github_repo="org/repo", ref=None, prd_text="", installation_id=42,
        ))

    done = client.get(f"/v1/design-agent/locate/jobs/{job_id}")
    assert done.status_code == 200
    poll = done.json()
    assert poll["status"] == "done"
    assert poll["error"] is None
    result = poll["result"]
    # Full LocateResponse schema — every field present.
    assert result["decision"] == "auto_proceed"
    assert len(result["chosen"]) == 1
    assert result["chosen"][0]["route"] == "/home"
    assert result["chosen"][0]["component_count"] == 3
    assert len(result["ranked"]) == 1
    assert result["top_confidence"] == 90
    assert isinstance(result["threshold"], int)
    assert result["repo"] == "org/repo"
    assert result["posture"] == "CLEAN"
    assert result["unmapped"] is False
    assert result["commit_sha"] == "sha-abc"


# ── 3. Error path: heavy work raises → poll error; telemetry preserved ─────────


def test_error_path_poll_returns_error(client, env, monkeypatch):
    """locate_screen raising surfaces through the poll as status error with a
    message; the job never carries a fabricated result."""
    _seed_prd()
    _mock_installation(monkeypatch)
    fake_map, _ = _make_map_result()

    async def _raise_locate(func, *args, **kwargs):
        if func.__name__ == "build_map":
            return fake_map
        raise RuntimeError("Anthropic API error")

    async def _noop_bg(**_kw):
        return None

    with patch.object(env.routes, "_run_locate_bg", new=_noop_bg):
        accepted = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo"},
        )
    job_id = accepted.json()["job_id"]
    rec = env.routes._locate_jobs[job_id]

    with patch("asyncio.to_thread", new=_raise_locate):
        asyncio.run(env.routes._run_locate_bg(
            job_id=job_id, workspace_id=rec["workspace_id"],
            github_repo="org/repo", ref=None, prd_text="", installation_id=42,
        ))

    poll = client.get(f"/v1/design-agent/locate/jobs/{job_id}").json()
    assert poll["status"] == "error"
    assert poll["result"] is None
    assert "Anthropic API error" in (poll["error"] or "")


def test_map_failure_fails_open_done_with_telemetry(client, env, monkeypatch, caplog):
    """build_map raising degrades to a DONE unmapped result (not error), and the
    locate telemetry still emits on that fail-open path — behaviour identical to
    the old synchronous unmapped degradation."""
    _seed_prd()
    _mock_installation(monkeypatch)

    async def _raise_build(func, *args, **kwargs):
        if func.__name__ == "build_map":
            raise RuntimeError("network error")
        return None

    async def _noop_bg(**_kw):
        return None

    with patch.object(env.routes, "_run_locate_bg", new=_noop_bg):
        accepted = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo"},
        )
    job_id = accepted.json()["job_id"]
    rec = env.routes._locate_jobs[job_id]

    with caplog.at_level(logging.INFO, logger="app.design_agent.codebase_map.locate"):
        with patch("asyncio.to_thread", new=_raise_build):
            asyncio.run(env.routes._run_locate_bg(
                job_id=job_id, workspace_id=rec["workspace_id"],
                github_repo="org/repo", ref=None, prd_text="", installation_id=42,
            ))

    poll = client.get(f"/v1/design-agent/locate/jobs/{job_id}").json()
    assert poll["status"] == "done"
    assert poll["result"]["unmapped"] is True
    telemetry = [r for r in caplog.records if "codebase_map.locate" in r.getMessage()]
    assert len(telemetry) == 1, "fail-open unmapped path must still emit telemetry"


# ── 4. Cross-workspace poll → 404 (not 403, no existence leak) ─────────────────


def test_cross_workspace_poll_404(client, env, monkeypatch):
    """A job minted under one workspace is not pollable by another — the poll for
    a foreign job id returns 404, never the result."""
    _seed_prd()
    _mock_installation(monkeypatch)

    async def _noop_bg(**_kw):
        return None

    with patch.object(env.routes, "_run_locate_bg", new=_noop_bg):
        accepted = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo"},
        )
    job_id = accepted.json()["job_id"]

    # Re-tag the stored job as belonging to a different workspace; the poll by the
    # _TEST_COMPANY_ID caller must now 404.
    env.routes._locate_jobs[job_id]["workspace_id"] = "some-other-company"
    resp = client.get(f"/v1/design-agent/locate/jobs/{job_id}")
    assert resp.status_code == 404


def test_unknown_job_id_404(client, env):
    """An entirely unknown job id 404s (never minted / already swept)."""
    resp = client.get("/v1/design-agent/locate/jobs/deadbeefdeadbeef")
    assert resp.status_code == 404


# ── 5. The locate background task registers in _inflight_tasks (drain) ─────────


def test_locate_task_registered_in_inflight(client, env, monkeypatch):
    """The POST handler registers the locate background task in _inflight_tasks so
    graceful drain awaits an in-flight locate instead of letting it die mid-flight.

    Driven by calling the handler coroutine directly on a single explicit loop —
    the sync TestClient's portal loop tears down the parked task before a
    test-thread inspection can see it, which would make a post-hoc membership
    check racy; calling the handler on our own loop is deterministic."""
    from app.auth import CompanyContext
    from app.routes.design_agent import LocateRequest

    _seed_prd()
    _mock_installation(monkeypatch)
    release = asyncio.Event()

    async def _blocking_to_thread(func, *args, **kwargs):
        if func.__name__ == "build_map":
            await release.wait()  # park the heavy work so the task stays in-flight
            return None
        return None

    company = CompanyContext(company_id=_TEST_COMPANY_ID, role="owner", user_id="u1")
    body = LocateRequest(prd_id=1, github_repo="org/repo")

    async def _drive():
        with patch("asyncio.to_thread", new=_blocking_to_thread):
            accepted = await env.routes.locate(body, company=company)
            # Handler returned immediately; the background task is parked + registered.
            assert accepted.status == "running"
            pending = [t for t in env.routes._inflight_tasks if not t.done()]
            assert pending, "locate background task must be registered in _inflight_tasks"
            assert all(isinstance(t, asyncio.Task) for t in pending)
            # The done-callback wiring: discarded from the set on completion.
            release.set()
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            assert not [t for t in env.routes._inflight_tasks if not t.done()]

    asyncio.run(_drive())


def test_locate_not_rejected_while_draining(client, env, monkeypatch):
    """Unlike /generate, /locate keeps serving while the process is draining —
    it is the lightweight path. (An already-running locate is still awaited by
    drain because it is registered in _inflight_tasks.)"""
    _seed_prd()
    _mock_installation(monkeypatch)
    env.routes.request_shutdown()  # mark draining

    async def _noop_bg(**_kw):
        return None

    with patch.object(env.routes, "_run_locate_bg", new=_noop_bg):
        resp = client.post(
            "/v1/design-agent/locate",
            json={"prd_id": 1, "github_repo": "org/repo"},
        )
    assert resp.status_code == 202, "locate must not 503 while draining"
    assert resp.json()["status"] == "running"


# ── 6. TTL sweep drops stale entries on access ─────────────────────────────────


def test_ttl_sweep_drops_stale_entries(env):
    """An entry older than the TTL is dropped by the opportunistic sweep so the
    process-local store cannot grow unbounded."""
    routes = env.routes
    routes._locate_jobs.clear()
    now = time.monotonic()
    routes._locate_jobs["fresh"] = {
        "status": "done", "workspace_id": "co", "created_at": now,
    }
    routes._locate_jobs["stale"] = {
        "status": "done", "workspace_id": "co",
        "created_at": now - routes._LOCATE_JOB_TTL_SECONDS - 1,
    }

    routes._sweep_locate_jobs(now=now)

    assert "fresh" in routes._locate_jobs
    assert "stale" not in routes._locate_jobs, "TTL sweep must drop expired entries"


def test_poll_after_ttl_sweep_404(client, env):
    """A job that has aged past the TTL is swept on the next poll access and the
    poll then 404s (treated as gone, like an unknown id)."""
    routes = env.routes
    routes._locate_jobs.clear()
    routes._locate_jobs["aged"] = {
        "status": "done", "workspace_id": _TEST_COMPANY_ID,
        "created_at": time.monotonic() - routes._LOCATE_JOB_TTL_SECONDS - 1,
    }
    resp = client.get("/v1/design-agent/locate/jobs/aged")
    assert resp.status_code == 404
    assert "aged" not in routes._locate_jobs
