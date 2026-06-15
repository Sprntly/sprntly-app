"""Tests for the iterate message queue (P3-06, AD11):

    supabase/migrations/20260601000100_design_agent_pending_iterations.sql
    backend/app/db/prototype_pending_iterations.py   (enqueue / dequeue / position /
                                                       mark / orphan-clear)
    backend/app/design_agent/runner.py               (drain_iteration_queue)
    backend/app/routes/design_agent.py               (POST /iterate enqueue + 429)
    backend/app/main.py                              (lifespan orphan-clear)

Three layers, matching the ticket's Unit Tests section:

- MIGRATION — static + behavioural assertions on the idempotency markers and the
  status CHECK constraint.
- DB HELPERS — enqueue/cap/position/dequeue/orphan against the in-memory
  FakeSupabaseClient (same fixture shape as test_design_agent_iterate.py).
- DRAIN + ROUTE — runner.drain_iteration_queue serial discipline + strong-ref
  task tracking, and the POST /iterate 429 + queue_position surface.

Reload note: the `env` fixture reloads the design-agent module stack, so the
queue module's `QueueFullError` is a fresh class per test — tests reference
`env.queue.QueueFullError`, never a stale top-level import.
"""
from __future__ import annotations

import asyncio
import importlib
import pathlib
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.design_agent import runner

from tests.conftest import _TEST_COMPANY_ID


_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260601000100_design_agent_pending_iterations.sql"
)

# SQLite-compatible mirror of the prototypes end-state (P1-06 + P2-06) plus the
# P3-06 queue table. Only the columns the helpers touch are needed.
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
    bundle_url             TEXT,
    preview_image_url      TEXT,
    current_checkpoint_id  INTEGER,
    error                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT,
    share_mode             TEXT NOT NULL DEFAULT 'private'
                           CHECK (share_mode IN ('private', 'public', 'passcode')),
    share_token            TEXT UNIQUE,
    share_passcode_hash    TEXT,
    is_complete            INTEGER NOT NULL DEFAULT 0,
    complete_checkpoint_id INTEGER
);
CREATE TABLE prototype_pending_iterations (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id       INTEGER NOT NULL,
    workspace_id       TEXT NOT NULL,
    prompt             TEXT NOT NULL,
    applied_comment_id INTEGER,
    mode               TEXT NOT NULL DEFAULT 'execute',
    status             TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'running', 'done', 'failed')),
    error              TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    started_at         TEXT,
    finished_at        TEXT
);
"""


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + prototype/queue tables + feature flag ON, with the
    design-agent module stack reloaded in dependency order (db → routes → main)."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_DDL)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.db.prototype_comments as comments_mod
    importlib.reload(comments_mod)
    import app.db.prototype_pending_iterations as queue_mod
    importlib.reload(queue_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    return SimpleNamespace(
        proto=proto_mod, comments=comments_mod, queue=queue_mod,
        routes=routes_mod, main=main_mod,
    )


@pytest.fixture
def client(company_client) -> TestClient:
    """Bearer-authed TestClient (require_company) — see conftest.company_client."""
    return company_client


# ─── helpers ────────────────────────────────────────────────────────────────


def _seed_ready(env, *, workspace_id: str = _TEST_COMPANY_ID, current_checkpoint_id=None) -> int:
    """Insert a ready, unlocked prototype (status='ready', is_complete=0)."""
    pid = env.proto.start_prototype(prd_id=1, workspace_id=workspace_id, template_version=1)
    env.proto.complete_prototype(
        prototype_id=pid, workspace_id=workspace_id,
        bundle_url="https://bundle/original", current_checkpoint_id=current_checkpoint_id,
    )
    return pid


def _all_rows(pid: int) -> list[dict]:
    """Read every queue row for a prototype directly from the fake DB, id-asc."""
    from tests import _fake_supabase
    db = _fake_supabase.get_fake_db()
    cur = db.execute(
        "SELECT * FROM prototype_pending_iterations WHERE prototype_id = ? ORDER BY id ASC",
        (pid,),
    )
    return [dict(r) for r in cur.fetchall()]


async def _drain_to_completion(env, pid: int, workspace_id: str = _TEST_COMPANY_ID) -> None:
    """Kick the drain like the route does, then await chained drains until idle."""
    inflight = env.routes._inflight_tasks
    t = asyncio.create_task(
        runner.drain_iteration_queue(prototype_id=pid, workspace_id=workspace_id)
    )
    inflight.add(t)
    t.add_done_callback(inflight.discard)
    await t
    for _ in range(50):  # safety bound; the chain terminates when the queue empties
        pending = list(inflight)
        if not pending:
            break
        await asyncio.gather(*pending, return_exceptions=True)


# ═══════════════════════════════════════════════════════════════════════════
# Migration
# ═══════════════════════════════════════════════════════════════════════════


def test_migration_applies_idempotently():
    # AC1: the migration is idempotent — create-if-not-exists table + indexes,
    # drop-if-exists before the CHECK add; FK to prototype_comments (P3-01);
    # workspace_id NOT NULL with NO default (Rule #20).
    sql = _MIGRATION.read_text()
    assert "create table if not exists prototype_pending_iterations" in sql
    assert sql.count("create index if not exists") == 3
    assert "enable row level security" in sql
    assert "drop constraint if exists pending_iterations_status_check" in sql
    assert "references prototype_comments(id)" in sql
    assert "workspace_id       text   not null" in sql


def test_status_check_rejects_invalid(env):
    # AC1: status is constrained to the four legal values. Behavioural check
    # against the fake DDL's CHECK (mirrors the migration's constraint).
    from tests import _fake_supabase
    db = _fake_supabase.get_fake_db()
    pid = _seed_ready(env)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO prototype_pending_iterations "
            "(prototype_id, workspace_id, prompt, status) VALUES (?, 'app', 'x', 'bogus')",
            (pid,),
        )
    assert "check (status in ('pending', 'running', 'done', 'failed'))" in _MIGRATION.read_text()


# ═══════════════════════════════════════════════════════════════════════════
# Enqueue / cap
# ═══════════════════════════════════════════════════════════════════════════


def test_enqueue_inserts_pending_row(env):
    # AC2: enqueue inserts a 'pending' row and returns it with a derived position.
    pid = _seed_ready(env)
    row = env.queue.enqueue_iteration(
        prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt="make it blue",
        applied_comment_id=None, mode="execute",
    )
    assert row["status"] == "pending"
    assert row["prompt"] == "make it blue"
    assert row["mode"] == "execute"
    assert row["queue_position"] == 1
    stored = _all_rows(pid)
    assert len(stored) == 1 and stored[0]["status"] == "pending"


def test_enqueue_sixth_raises_queue_full(env):
    # AC2: a 6th enqueue while 5 are pending/running raises QueueFullError and
    # never lands a row.
    pid = _seed_ready(env)
    for i in range(5):
        env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt=f"p{i}")
    with pytest.raises(env.queue.QueueFullError):
        env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt="sixth")
    assert env.queue.count_pending(prototype_id=pid, workspace_id=_TEST_COMPANY_ID) == 5


def test_post_iterate_queue_full_returns_429(env, client):
    # AC3: POST /iterate against a full queue → 429 with the queue_full detail.
    pid = _seed_ready(env)
    for i in range(5):
        env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt=f"p{i}")
    resp = client.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": "sixth"})
    assert resp.status_code == 429
    assert resp.json()["detail"] == {"error": "queue_full", "max": 5}


def test_post_iterate_returns_queue_position(env, client, monkeypatch):
    # AC3: a successful POST returns {prototype_id, status:'generating', queue_position}.
    # Stub the drain so the kicked bg task does no real LLM/storage work.
    async def _noop(**kwargs):
        return None

    monkeypatch.setattr(env.routes, "drain_iteration_queue", _noop)
    pid = _seed_ready(env)
    resp = client.post(f"/v1/design-agent/{pid}/iterate", json={"prompt": "first"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["prototype_id"] == pid
    assert body["status"] == "generating"
    assert body["queue_position"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# Dequeue / serial drain
# ═══════════════════════════════════════════════════════════════════════════


def test_dequeue_marks_oldest_running(env):
    # AC4: dequeue marks the OLDEST pending row 'running' and returns it. While that
    # row is still running, a second dequeue must NO-OP (concurrency guard: at most
    # one iteration running per prototype) — it does NOT advance to the next pending.
    # Only once the running row leaves the active set does dequeue advance to B.
    pid = _seed_ready(env)
    a = env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt="a")["id"]
    b = env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt="b")["id"]
    first = env.queue.dequeue_next(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert first["id"] == a
    assert first["status"] == "running"
    by_id = {r["id"]: r for r in _all_rows(pid)}
    assert by_id[a]["status"] == "running"
    assert by_id[a]["started_at"]
    # A is still running → a fresh dequeue (e.g. from a kick fired by enqueuing B)
    # must NOT promote B; it returns None so B does not run concurrently with A.
    assert env.queue.dequeue_next(prototype_id=pid, workspace_id=_TEST_COMPANY_ID) is None
    assert {r["id"]: r["status"] for r in _all_rows(pid)}[b] == "pending"  # B untouched
    # A finishes → B becomes dequeuable.
    env.queue.mark_iteration_done(iteration_id=a, workspace_id=_TEST_COMPANY_ID)
    second = env.queue.dequeue_next(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert second["id"] == b               # advanced to the next-oldest pending
    env.queue.mark_iteration_done(iteration_id=b, workspace_id=_TEST_COMPANY_ID)
    assert env.queue.dequeue_next(prototype_id=pid, workspace_id=_TEST_COMPANY_ID) is None


@pytest.mark.asyncio
async def test_drain_runs_all_serially_no_concurrency(env, monkeypatch):
    # AC5: enqueue 3, drain → all reach 'done' and at NO point are 2 rows running
    # at once (mocked _run_one_iteration records the concurrent-running count).
    pid = _seed_ready(env)
    for p in ("a", "b", "c"):
        env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt=p)

    running = 0
    max_running = 0

    async def fake_one(row):
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        await asyncio.sleep(0)            # yield: any overlap would show up here
        running -= 1

    monkeypatch.setattr(env.routes, "_run_one_iteration", fake_one)
    await _drain_to_completion(env, pid)

    assert [r["status"] for r in _all_rows(pid)] == ["done", "done", "done"]
    assert max_running == 1


def test_dequeue_noop_when_running_row_exists(env):
    # Concurrency-guard regression (the BUG): enqueuing iteration #2 while #1 is
    # running used to let a fresh drain's dequeue_next pick up #2 and run it
    # CONCURRENTLY with #1 (lost update on the shared bundle). dequeue_next must
    # no-op (return None) whenever a 'running' row already exists for the prototype.
    pid = _seed_ready(env)
    a = env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt="a")["id"]
    running = env.queue.dequeue_next(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert running["id"] == a and running["status"] == "running"
    # #2 enqueued while #1 runs.
    b = env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt="b")["id"]
    # The kick fired on that enqueue calls dequeue_next again — it must NOT promote B.
    assert env.queue.dequeue_next(prototype_id=pid, workspace_id=_TEST_COMPANY_ID) is None
    assert {r["id"]: r["status"] for r in _all_rows(pid)}[b] == "pending"
    # Guard is per-prototype: a DIFFERENT prototype's queue is unaffected.
    pid2 = _seed_ready(env)
    c = env.queue.enqueue_iteration(prototype_id=pid2, workspace_id=_TEST_COMPANY_ID, prompt="c")["id"]
    other = env.queue.dequeue_next(prototype_id=pid2, workspace_id=_TEST_COMPANY_ID)
    assert other["id"] == c and other["status"] == "running"


@pytest.mark.asyncio
async def test_concurrent_drain_kicks_do_not_double_run(env, monkeypatch):
    # End-to-end: enqueue #1, start a long-running drain, then enqueue #2 and fire a
    # SECOND drain (exactly what POST /iterate does on every enqueue). The second
    # drain must NOT run #2 concurrently — max concurrent runs stays 1 throughout.
    pid = _seed_ready(env)
    env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt="a")

    running = 0
    max_running = 0
    first_started = asyncio.Event()
    release = asyncio.Event()

    async def fake_one(row):
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        if row["prompt"] == "a":
            first_started.set()
            await release.wait()      # hold #1 'running' while we kick a 2nd drain
        running -= 1

    monkeypatch.setattr(env.routes, "_run_one_iteration", fake_one)
    inflight = env.routes._inflight_tasks
    t1 = asyncio.create_task(runner.drain_iteration_queue(prototype_id=pid, workspace_id=_TEST_COMPANY_ID))
    inflight.add(t1); t1.add_done_callback(inflight.discard)
    await first_started.wait()        # #1 is now running and parked

    # #2 enqueued + a fresh drain kicked while #1 is still running.
    env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt="b")
    t2 = asyncio.create_task(runner.drain_iteration_queue(prototype_id=pid, workspace_id=_TEST_COMPANY_ID))
    inflight.add(t2); t2.add_done_callback(inflight.discard)
    await t2                          # the 2nd kick must no-op (guard), not run #2
    assert max_running == 1           # #2 did NOT start concurrently

    release.set()                     # let #1 finish; its chained drain runs #2
    await t1
    for _ in range(50):
        pend = list(inflight)
        if not pend:
            break
        await asyncio.gather(*pend, return_exceptions=True)
    assert [r["status"] for r in _all_rows(pid)] == ["done", "done"]
    assert max_running == 1           # never two at once across the whole sequence


@pytest.mark.asyncio
async def test_drain_continues_after_failed_iteration(env, monkeypatch):
    # AC6: the 2nd of 3 raises → row 1 done, row 2 failed (with error), row 3 done.
    pid = _seed_ready(env)
    ids = [
        env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt=p)["id"]
        for p in ("a", "b", "c")
    ]

    async def fake_one(row):
        if row["prompt"] == "b":
            raise RuntimeError("bad prompt b")

    monkeypatch.setattr(env.routes, "_run_one_iteration", fake_one)
    await _drain_to_completion(env, pid)

    by_id = {r["id"]: r for r in _all_rows(pid)}
    assert by_id[ids[0]]["status"] == "done"
    assert by_id[ids[1]]["status"] == "failed"
    assert "RuntimeError" in (by_id[ids[1]]["error"] or "")
    assert by_id[ids[2]]["status"] == "done"


@pytest.mark.asyncio
async def test_drain_marks_done_and_failed_correctly(env, monkeypatch):
    # AC5/AC6: done + failed both stamp finished_at; the failed row carries the
    # error class.
    pid = _seed_ready(env)
    ok = env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt="ok")["id"]
    bad = env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt="bad")["id"]

    async def fake_one(row):
        if row["prompt"] == "bad":
            raise ValueError("nope")

    monkeypatch.setattr(env.routes, "_run_one_iteration", fake_one)
    await _drain_to_completion(env, pid)

    by_id = {r["id"]: r for r in _all_rows(pid)}
    assert by_id[ok]["status"] == "done" and by_id[ok]["finished_at"]
    assert by_id[bad]["status"] == "failed" and by_id[bad]["finished_at"]
    assert "ValueError" in (by_id[bad]["error"] or "")


@pytest.mark.asyncio
async def test_drain_task_held_in_inflight_set(env, monkeypatch):
    # AC9: every chaining drain task is held in the route's _inflight_tasks set
    # while it runs, and discarded via add_done_callback on completion.
    pid = _seed_ready(env)
    for p in ("a", "b"):
        env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt=p)

    held: list[bool] = []

    async def fake_one(row):
        # While this iteration runs, its own drain task must be in the set.
        held.append(len(env.routes._inflight_tasks) >= 1)

    monkeypatch.setattr(env.routes, "_run_one_iteration", fake_one)
    await _drain_to_completion(env, pid)

    assert held == [True, True]                       # held while each ran
    assert env.routes._inflight_tasks == set()        # all discarded at the end


# ═══════════════════════════════════════════════════════════════════════════
# Position (derived, never stored)
# ═══════════════════════════════════════════════════════════════════════════


def test_queue_position_derived_from_earlier_pending(env):
    # AC7: A=1, B=2, C=3 (1-based rank among the active set, id-ordered).
    pid = _seed_ready(env)
    a = env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt="a")
    b = env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt="b")
    c = env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt="c")
    assert (a["queue_position"], b["queue_position"], c["queue_position"]) == (1, 2, 3)
    # Re-derived via the standalone helper (proves it is computed, not stored).
    assert env.queue.queue_position(prototype_id=pid, iteration_id=a["id"], workspace_id=_TEST_COMPANY_ID) == 1
    assert env.queue.queue_position(prototype_id=pid, iteration_id=c["id"], workspace_id=_TEST_COMPANY_ID) == 3


def test_queue_position_decreases_after_head_finishes(env):
    # AC7: a running head is 0; rows behind it keep their slot until it FINISHES,
    # then move up.
    pid = _seed_ready(env)
    a = env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt="a")
    b = env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt="b")
    assert env.queue.queue_position(prototype_id=pid, iteration_id=b["id"], workspace_id=_TEST_COMPANY_ID) == 2

    env.queue.dequeue_next(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)  # A → running
    assert env.queue.queue_position(prototype_id=pid, iteration_id=a["id"], workspace_id=_TEST_COMPANY_ID) == 0
    assert env.queue.queue_position(prototype_id=pid, iteration_id=b["id"], workspace_id=_TEST_COMPANY_ID) == 2

    env.queue.mark_iteration_done(iteration_id=a["id"], workspace_id=_TEST_COMPANY_ID)  # A leaves active set
    assert env.queue.queue_position(prototype_id=pid, iteration_id=b["id"], workspace_id=_TEST_COMPANY_ID) == 1


# ═══════════════════════════════════════════════════════════════════════════
# Orphan / lifespan
# ═══════════════════════════════════════════════════════════════════════════


def test_invalidate_orphan_running_flips_to_failed(env):
    # AC8: a stuck 'running' row → 'failed'; returns the count.
    pid = _seed_ready(env)
    row = env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt="x")
    env.queue.dequeue_next(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)  # → running
    n = env.queue.invalidate_orphan_running_iterations()
    assert n == 1
    by_id = {r["id"]: r for r in _all_rows(pid)}
    assert by_id[row["id"]]["status"] == "failed"
    assert "orphaned" in (by_id[row["id"]]["error"] or "")


def test_invalidate_orphan_running_crosses_workspaces(env):
    # AC8: the sweep is system-wide (Rule #23) — no workspace filter; running rows
    # in BOTH workspaces are flipped.
    p_app = _seed_ready(env, workspace_id=_TEST_COMPANY_ID)
    p_demo = _seed_ready(env, workspace_id="demo")
    env.queue.enqueue_iteration(prototype_id=p_app, workspace_id=_TEST_COMPANY_ID, prompt="a")
    env.queue.dequeue_next(prototype_id=p_app, workspace_id=_TEST_COMPANY_ID)
    env.queue.enqueue_iteration(prototype_id=p_demo, workspace_id="demo", prompt="b")
    env.queue.dequeue_next(prototype_id=p_demo, workspace_id="demo")
    assert env.queue.invalidate_orphan_running_iterations() == 2


def test_main_lifespan_wires_orphan_iteration_clear(env):
    # AC8/AC11: the lifespan imports + calls invalidate_orphan_running_iterations,
    # and `from app.main import app` still works after the append.
    main_src = (
        pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"
    ).read_text()
    assert (
        "from app.db.prototype_pending_iterations import invalidate_orphan_running_iterations"
        in main_src
    )
    assert "invalidate_orphan_running_iterations()" in main_src
    from app.main import app as fastapi_app
    assert fastapi_app is not None


# ═══════════════════════════════════════════════════════════════════════════
# Workspace isolation
# ═══════════════════════════════════════════════════════════════════════════


def test_count_pending_workspace_filtered(env):
    # AC10: enqueue under 'app' → count_pending for 'demo' is 0.
    pid = _seed_ready(env, workspace_id=_TEST_COMPANY_ID)
    env.queue.enqueue_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, prompt="x")
    assert env.queue.count_pending(prototype_id=pid, workspace_id=_TEST_COMPANY_ID) == 1
    assert env.queue.count_pending(prototype_id=pid, workspace_id="demo") == 0
