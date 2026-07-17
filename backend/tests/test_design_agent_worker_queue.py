"""Tier 2 — opt-in worker queue for generation isolation.

The heavy generation (LLM recreate loop + vite build + Chromium) runs inside the
API request process today; on the prod t3.micro that starves /locate (the 504s).
Tier 2 moves it onto a separate `python -m app.worker` process draining a
Supabase queue. Because that needs a 2nd systemd unit (the client's deploy
action), it is OPT-IN behind DESIGN_AGENT_WORKER_ENABLED with a test-proven
fallback to today's in-process path.

THE FALLBACK IS THE LOAD-BEARING SAFETY (tester's explicit hard requirement):
a box that has not deployed the worker unit must behave EXACTLY as today. These
tests prove all three fallback paths plus the queue path, the worker run loop,
the atomic claim, the orphan-requeue sweep, and the payload round-trip.
"""
from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest

from tests.conftest import _TEST_COMPANY_ID

# Local SQLite-compatible DDL (per-file convention so module reloads stay
# independent). prototypes/checkpoints mirror the other DA route suites; the two
# Tier-2 tables mirror supabase/migrations/20260616100000_design_agent_jobs.sql.
_DDL = """
CREATE TABLE prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    variant                TEXT NOT NULL DEFAULT 'v1',
    template_version       INTEGER NOT NULL,
    instructions           TEXT,
    target_platform        TEXT NOT NULL DEFAULT 'both',
    figma_file_key         TEXT,
    website_url            TEXT,
    github_installation_id INTEGER,
    created_by_user_id     TEXT,
    bundle_url             TEXT,
    current_checkpoint_id  INTEGER,
    error                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT,
    share_mode             TEXT NOT NULL DEFAULT 'private'
                           CHECK (share_mode IN ('private', 'public', 'passcode')),
    share_token            TEXT UNIQUE,
    share_passcode_hash    TEXT
);
CREATE TABLE prototype_checkpoints (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id      INTEGER NOT NULL,
    workspace_id      TEXT NOT NULL,
    bundle_url        TEXT,
    prd_revision_hash TEXT,
    figma_frame_hash  TEXT,
    prompt_history    TEXT NOT NULL DEFAULT '[]',
    comment_state     TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE design_agent_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id INTEGER NOT NULL UNIQUE,
    workspace_id TEXT NOT NULL,
    payload      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'queued',
    claimed_by   TEXT,
    claimed_at   TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE design_agent_worker_heartbeat (
    id         INTEGER PRIMARY KEY,
    worker_id  TEXT,
    updated_at TEXT
);
"""


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """Feature flag ON + the prototypes + Tier-2 tables + the DA module stack
    reloaded in dependency order so the route module's `settings` binding and its
    request-time gates are fresh."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_DDL)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.config as _config_mod
    importlib.reload(_config_mod)
    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.db.design_agent_jobs as jobs_mod
    importlib.reload(jobs_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    import app.worker as worker_mod
    importlib.reload(worker_mod)

    import app.db as db_mod
    return SimpleNamespace(
        config=_config_mod, proto=proto_mod, jobs=jobs_mod,
        routes=routes_mod, main=main_mod, worker=worker_mod, db=db_mod,
    )


@pytest.fixture
def client(env, company_client):
    """Bearer-authed TestClient under the test workspace (resolves to co-test)."""
    return company_client


def _db():
    """The fake Supabase client (same one the helpers use via require_client)."""
    from app.db.client import require_client

    return require_client()


def _seed_prd(db_mod, body: str = "# PRD body") -> int:
    prd_id = db_mod.start_prd(
        brief_id=1, insight_index=0, title="t", template_version=1, variant="v2",
    )
    db_mod.complete_prd(prd_id, title="t", md=body)
    return prd_id


def _fresh_heartbeat(env, worker_id: str = "host:1") -> None:
    """Write a now() heartbeat so worker_heartbeat_fresh() returns True."""
    env.jobs.write_heartbeat(worker_id=worker_id)


def _spy_create_task(monkeypatch, routes_mod):
    """Replace asyncio.create_task (as the route module sees it) with a spy that
    records calls and immediately cancels the coroutine so no real generation
    fires. Returns the call-count list."""
    calls: list = []
    real = asyncio.create_task

    def _spy(coro, *a, **k):
        calls.append(coro)
        # Wrap in a task so the route's add_done_callback works, but the coro
        # body never runs anything heavy: _run_generation_bg is stubbed below.
        return real(coro, *a, **k)

    monkeypatch.setattr(routes_mod.asyncio, "create_task", _spy)
    return calls


def _stub_bg(monkeypatch, routes_mod):
    """Stub _run_generation_bg so neither the inline nor worker path runs real
    generation. Returns the captured-kwargs list."""
    captured: list[dict] = []

    async def _fake_bg(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(routes_mod, "_run_generation_bg", _fake_bg)
    return captured


# ── FALLBACK (critical): a box without the worker unit must behave as today ──


def test_flag_off_uses_inprocess_not_queue(env, client, monkeypatch):
    """Flag OFF → /generate uses the in-process create_task path and NEVER
    enqueues. (Default: DESIGN_AGENT_WORKER_ENABLED unset.)"""
    monkeypatch.delenv("DESIGN_AGENT_WORKER_ENABLED", raising=False)
    captured = _stub_bg(monkeypatch, env.routes)
    create_calls = _spy_create_task(monkeypatch, env.routes)

    enqueue_calls: list = []
    monkeypatch.setattr(
        env.routes, "enqueue_job",
        lambda **kw: enqueue_calls.append(kw) or {"id": 1},
    )

    prd_id = _seed_prd(env.db)
    resp = client.post("/v1/design-agent/generate", json={"prd_id": prd_id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "generating"
    assert enqueue_calls == [], "flag off must NOT enqueue"
    assert len(create_calls) == 1, "flag off must run in-process via create_task"


def test_flag_on_stale_heartbeat_falls_back_inprocess(env, client, monkeypatch, caplog):
    """Flag ON but heartbeat ABSENT (never written) → falls back to in-process
    and logs a warning; does NOT enqueue."""
    monkeypatch.setenv("DESIGN_AGENT_WORKER_ENABLED", "1")
    _stub_bg(monkeypatch, env.routes)
    create_calls = _spy_create_task(monkeypatch, env.routes)
    enqueue_calls: list = []
    monkeypatch.setattr(
        env.routes, "enqueue_job",
        lambda **kw: enqueue_calls.append(kw) or {"id": 1},
    )

    prd_id = _seed_prd(env.db)
    with caplog.at_level("WARNING"):
        resp = client.post("/v1/design-agent/generate", json={"prd_id": prd_id})
    assert resp.status_code == 200, resp.text
    assert enqueue_calls == [], "stale/absent heartbeat must NOT enqueue"
    assert len(create_calls) == 1, "must fall back to in-process create_task"
    assert any("no_heartbeat" in r.message for r in caplog.records), \
        "a fallback warning must be logged when the heartbeat is stale/absent"


def test_flag_on_enqueue_raises_falls_back_never_500(env, client, monkeypatch):
    """Flag ON + fresh heartbeat but enqueue_job FAILS (raises / table missing) →
    enqueue_job is fail-soft (returns None) so /generate falls back to in-process
    and NEVER 500s. We simulate the table-missing case by making enqueue_job
    return None (its real fail-soft contract)."""
    monkeypatch.setenv("DESIGN_AGENT_WORKER_ENABLED", "1")
    _fresh_heartbeat(env)
    _stub_bg(monkeypatch, env.routes)
    create_calls = _spy_create_task(monkeypatch, env.routes)
    monkeypatch.setattr(env.routes, "enqueue_job", lambda **kw: None)

    prd_id = _seed_prd(env.db)
    resp = client.post("/v1/design-agent/generate", json={"prd_id": prd_id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "generating"
    assert len(create_calls) == 1, "a failed enqueue must fall back to in-process"


# ── QUEUE PATH: flag on + fresh heartbeat → enqueue, no create_task ──────────


def test_flag_on_fresh_heartbeat_enqueues_not_inprocess(env, client, monkeypatch):
    """Flag ON + fresh heartbeat → /generate enqueues a job and does NOT spawn an
    in-process create_task. Returns status 'generating' (transparent to the
    frontend, which polls the prototype row)."""
    monkeypatch.setenv("DESIGN_AGENT_WORKER_ENABLED", "1")
    _fresh_heartbeat(env)
    _stub_bg(monkeypatch, env.routes)
    create_calls = _spy_create_task(monkeypatch, env.routes)

    prd_id = _seed_prd(env.db)
    resp = client.post("/v1/design-agent/generate", json={"prd_id": prd_id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "generating"
    # No in-process generation task spawned.
    assert create_calls == [], "queue path must NOT use create_task"
    # A real job row landed, status queued, payload reconstructs the prototype id.
    pid = resp.json()["prototype_id"]
    rows = (
        _db().table("design_agent_jobs")
        .select("*").eq("prototype_id", pid).execute().data
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "queued"
    assert rows[0]["payload"]["prototype_id"] == pid


# ── PAYLOAD round-trip ──────────────────────────────────────────────────────


def test_payload_json_serializable_and_round_trips(env):
    """The enqueued payload is JSON-serializable (manual_design Pydantic model →
    dict) AND reconstructs identical generation inputs — proving the worker path
    and inline path call _run_generation_bg with the same kwargs."""
    import json as _json

    md = env.routes.ManualDesignInput(primary_color="#3b82f6", font_family="Inter")
    bg_kwargs = dict(
        prototype_id=7, workspace_id=_TEST_COMPANY_ID, prd_id=3,
        target_platform="both", instructions="hi", figma_file_key=None,
        figma_node_id=None, website_url="https://x.test", manual_design=md,
        github_repo="org/repo", github_installation_id=42,
        design_source="website", chosen_screen_route="/team",
        chosen_screen_id="n1", map_commit_sha="sha",
    )
    payload = env.routes._serialize_generation_payload(bg_kwargs)
    # JSON-serializable end to end (jsonb requirement).
    round = _json.loads(_json.dumps(payload))
    assert round["manual_design"] == {"primary_color": "#3b82f6", "font_family": "Inter"}

    # The worker reconstructs identical typed inputs.
    restored = env.routes._deserialize_generation_payload(round)
    assert isinstance(restored["manual_design"], env.routes.ManualDesignInput)
    assert restored["manual_design"].primary_color == "#3b82f6"
    # Everything else is identical.
    for k, v in bg_kwargs.items():
        if k == "manual_design":
            continue
        assert restored[k] == v


# ── WORKER: claim, run, complete/fail, orphan-requeue ───────────────────────


def test_claim_next_job_atomic_single_winner(env):
    """A claimed job is not re-claimable: a second claim_next_job returns None
    (the conditional-update CAS keeps a claimed row out of the queued set)."""
    job = env.jobs.enqueue_job(
        prototype_id=1, workspace_id=_TEST_COMPANY_ID, payload={"prototype_id": 1},
    )
    assert job is not None
    first = env.jobs.claim_next_job(worker_id="w1")
    assert first is not None and first["claimed_by"] == "w1"
    second = env.jobs.claim_next_job(worker_id="w2")
    assert second is None, "an already-claimed job must not be re-claimable"


async def test_worker_runs_shared_body_and_marks_done(env, monkeypatch):
    """The worker claims a job, runs the SHARED _run_generation_bg body with the
    payload kwargs, and marks the job done."""
    captured = _stub_bg(monkeypatch, env.routes)
    md = env.routes.ManualDesignInput(primary_color="#000000", font_family="Inter")
    payload = env.routes._serialize_generation_payload(dict(
        prototype_id=5, workspace_id=_TEST_COMPANY_ID, prd_id=2,
        target_platform="both", instructions="", figma_file_key=None,
        manual_design=md,
    ))
    env.jobs.enqueue_job(prototype_id=5, workspace_id=_TEST_COMPANY_ID, payload=payload)

    job = env.jobs.claim_next_job(worker_id="w1")
    await env.worker._run_one(job)

    # The shared body ran with reconstructed typed kwargs.
    assert len(captured) == 1
    assert captured[0]["prototype_id"] == 5
    assert isinstance(captured[0]["manual_design"], env.routes.ManualDesignInput)
    # Job marked done.
    rows = (
        _db().table("design_agent_jobs")
        .select("status").eq("id", job["id"]).execute().data
    )
    assert rows[0]["status"] == "done"


async def test_worker_failure_marks_job_error(env, monkeypatch):
    """A generation that raises → fail_job records the error; the job is 'error'
    and the loop survives (one bad job does not stop the worker)."""
    async def _boom(**kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(env.routes, "_run_generation_bg", _boom)
    env.jobs.enqueue_job(
        prototype_id=9, workspace_id=_TEST_COMPANY_ID, payload={"prototype_id": 9},
    )
    job = env.jobs.claim_next_job(worker_id="w1")
    await env.worker._run_one(job)  # must not raise

    rows = (
        _db().table("design_agent_jobs")
        .select("status, error").eq("id", job["id"]).execute().data
    )
    assert rows[0]["status"] == "error"
    assert "kaboom" in (rows[0]["error"] or "")


def test_requeue_orphan_claimed_jobs(env):
    """A job left 'claimed' by a dead worker is re-queued by the lifespan sweep,
    with attempts bumped, so a fresh worker can pick it up."""
    env.jobs.enqueue_job(
        prototype_id=3, workspace_id=_TEST_COMPANY_ID, payload={"prototype_id": 3},
    )
    claimed = env.jobs.claim_next_job(worker_id="dead-worker")
    assert claimed["status"] == "claimed"

    n = env.jobs.requeue_orphan_claimed_jobs()
    assert n == 1
    rows = (
        _db().table("design_agent_jobs")
        .select("status, attempts, claimed_by").eq("id", claimed["id"]).execute().data
    )
    assert rows[0]["status"] == "queued"
    assert rows[0]["claimed_by"] is None
    assert rows[0]["attempts"] == 2  # 1 on claim, +1 on orphan-requeue
    # And it is claimable again.
    again = env.jobs.claim_next_job(worker_id="fresh")
    assert again is not None and again["id"] == claimed["id"]


# ── heartbeat freshness ─────────────────────────────────────────────────────


def test_heartbeat_fresh_true_when_recent_false_when_absent(env):
    """worker_heartbeat_fresh: True right after a write; False when no row exists
    (the absent-worker fallback signal)."""
    assert env.jobs.worker_heartbeat_fresh(within_seconds=30) is False  # no row yet
    env.jobs.write_heartbeat(worker_id="host:1")
    assert env.jobs.worker_heartbeat_fresh(within_seconds=30) is True


def test_heartbeat_stale_is_not_fresh(env):
    """A heartbeat older than the window reads as not-fresh → in-process fallback."""
    env.jobs.write_heartbeat(worker_id="host:1")
    # Backdate the row well beyond the window.
    _db().table("design_agent_worker_heartbeat").update(
        {"updated_at": "2000-01-01T00:00:00+00:00"}
    ).eq("id", 1).execute()
    assert env.jobs.worker_heartbeat_fresh(within_seconds=30) is False
