"""Tier 1 — generation concurrency guard.

The HEAVY section of ``_run_generation_bg`` (the LLM recreate loop + vite build +
screenshot) pins both cores on the 2-vCPU prod box; admitting a second concurrent
heavy run is the 504-under-load contention. A process-wide
``asyncio.Semaphore(settings.design_agent_generation_concurrency)`` (default 1)
serialises that section so a concurrent ``/locate`` keeps CPU headroom.

These tests prove the serialisation deterministically — no sleeps-as-sync. We
stub ``generate_prototype`` (the heavy step) with an asyncio barrier so we can
observe exactly how many invocations are inside the guard at once. The setting is
read at CALL-TIME (lazy-init), so the concurrency=2 test reloads config with the
override and gets a 2-permit semaphore without an import-time freeze.
"""
from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest

from tests import _fake_supabase

# Local SQLite-compatible prototypes DDL (each design-agent route test keeps its
# own copy so reloads stay independent — per the per-file-DDL convention).
_PROTOTYPE_DDL = """
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
"""


def _build_env(isolated_settings, monkeypatch, *, concurrency: int):
    """Feature flag ON + a given generation-concurrency setting + the prototypes
    tables + the design-agent module stack reloaded in dependency order so the
    route module's ``settings`` binding and its module-level semaphore/flag are
    fresh for this test."""
    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    monkeypatch.setenv("DESIGN_AGENT_GENERATION_CONCURRENCY", str(concurrency))

    import app.config as _config_mod
    importlib.reload(_config_mod)
    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)

    import app.db as db_mod
    return SimpleNamespace(config=_config_mod, proto=proto_mod, routes=routes_mod, db=db_mod)


def _seed_prd(db_mod, body: str = "# PRD body") -> int:
    prd_id = db_mod.start_prd(
        brief_id=1, insight_index=0, title="t", template_version=1, variant="v2",
    )
    db_mod.complete_prd(prd_id, title="t", md=body)
    return prd_id


def _new_prototype(env) -> int:
    prd_id = _seed_prd(env.db)
    return env.proto.start_prototype(
        prd_id=prd_id, workspace_id="app", template_version=1,
    )


async def _run(env, pid: int, prd_id: int) -> None:
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=prd_id,
        target_platform="both", instructions="", figma_file_key=None,
    )


async def test_concurrency_one_serialises_heavy_section(isolated_settings, monkeypatch):
    """concurrency=1: two concurrent _run_generation_bg invocations never run
    their heavy (generate_prototype) section simultaneously. Proven with a
    barrier the stubbed heavy step blocks on — if both entered at once, the
    observed peak would be 2."""
    env = _build_env(isolated_settings, monkeypatch, concurrency=1)

    inside = 0
    peak = 0
    entered = asyncio.Event()          # fires when the FIRST run is inside the guard
    release = asyncio.Event()          # the test releases the held run
    second_attempted = asyncio.Event()  # fires when the 2nd run reaches the heavy step

    async def _fake_generate(**kwargs):
        nonlocal inside, peak
        inside += 1
        peak = max(peak, inside)
        if not entered.is_set():
            # First arrival: signal we're holding the single permit, then wait.
            entered.set()
            await release.wait()
        else:
            second_attempted.set()
        inside -= 1
        return SimpleNamespace(status="complete", iters=1, theme_expectations=None), {"src/App.tsx": "x"}

    monkeypatch.setattr(env.routes, "generate_prototype", _fake_generate)
    # Stage step is a no-op; we only care about the guarded heavy step.
    async def _noop_stage(**kwargs):
        return None
    monkeypatch.setattr(env.routes, "_stage_complete_run", _noop_stage)

    prd_id = _seed_prd(env.db)
    pid_a = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)
    pid_b = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)

    t1 = asyncio.create_task(_run(env, pid_a, prd_id))
    await entered.wait()  # run #1 holds the only permit and is parked in the heavy step

    t2 = asyncio.create_task(_run(env, pid_b, prd_id))
    # Give run #2 every chance to (wrongly) enter the heavy section. With a
    # 1-permit semaphore it CANNOT — it blocks on acquire. We assert it did NOT
    # reach the heavy step while #1 holds the permit.
    await asyncio.sleep(0)  # yield; lets t2 progress to the semaphore acquire
    await asyncio.sleep(0)
    assert not second_attempted.is_set(), "second generation entered the heavy section while the first held the permit"
    assert peak == 1, f"heavy-section concurrency peaked at {peak}, expected 1"

    # The queued prototype's row is still 'generating' while it waits (nothing
    # flips it before the guard).
    row_b = env.proto.get_prototype(prototype_id=pid_b, workspace_id="app")
    assert row_b["status"] == "generating"

    # Release #1; #2 then proceeds.
    release.set()
    await asyncio.gather(t1, t2)
    assert second_attempted.is_set(), "second generation never ran after the permit freed"
    assert peak == 1, "heavy section was never concurrent — serialisation held"


async def test_concurrency_two_allows_two_heavy_sections(isolated_settings, monkeypatch):
    """concurrency=2: two heavy sections DO run at once (peak == 2). Proves the
    setting is read at call-time and the semaphore admits up to the limit."""
    env = _build_env(isolated_settings, monkeypatch, concurrency=2)

    inside = 0
    peak = 0
    both_in = asyncio.Event()
    release = asyncio.Event()

    async def _fake_generate(**kwargs):
        nonlocal inside, peak
        inside += 1
        peak = max(peak, inside)
        if inside == 2:
            both_in.set()
        await release.wait()  # hold both runs inside the guard simultaneously
        inside -= 1
        return SimpleNamespace(status="complete", iters=1, theme_expectations=None), {"src/App.tsx": "x"}

    monkeypatch.setattr(env.routes, "generate_prototype", _fake_generate)
    async def _noop_stage(**kwargs):
        return None
    monkeypatch.setattr(env.routes, "_stage_complete_run", _noop_stage)

    prd_id = _seed_prd(env.db)
    pid_a = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)
    pid_b = env.proto.start_prototype(prd_id=prd_id, workspace_id="app", template_version=1)

    t1 = asyncio.create_task(_run(env, pid_a, prd_id))
    t2 = asyncio.create_task(_run(env, pid_b, prd_id))

    # Both should reach the heavy section together with a 2-permit semaphore.
    await asyncio.wait_for(both_in.wait(), timeout=2.0)
    assert peak == 2, f"heavy-section concurrency peaked at {peak}, expected 2"

    release.set()
    await asyncio.gather(t1, t2)


def test_zero_concurrency_falls_back_to_one(isolated_settings, monkeypatch):
    """A non-positive setting must not yield a 0-permit (deadlocking) semaphore —
    it falls back to 1."""
    env = _build_env(isolated_settings, monkeypatch, concurrency=0)
    sem = env.routes._get_generation_semaphore()
    assert sem._value == 1, "concurrency<=0 must fall back to a 1-permit semaphore"
