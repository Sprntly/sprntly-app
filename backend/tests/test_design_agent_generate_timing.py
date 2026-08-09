"""Kickoff-timing observability for POST /generate.

Nothing previously recorded how long kickoff actually takes, despite the
handler's own docstring committing to a <200ms target. These tests prove the
route now emits exactly one structured timing line on every exit path —
early rejections included, not just the happy path — and that the three
success shapes (dedupe short-circuit, Tier-2 worker-enqueue, in-process
insert) are kept distinguishable rather than averaged into one number.

No behaviour, response body, or status code assertion here is new; this file
only asserts the log record. Response-shape coverage for these same paths
already lives in the drain and worker-queue suites.
"""
from __future__ import annotations

import importlib
import logging
from types import SimpleNamespace

import pytest

from tests.conftest import _TEST_COMPANY_ID

# Local SQLite-compatible DDL (per-file convention — mirrors the Tier-2
# worker-queue suite's schema; prd_id carries no FK so a bare int is a valid
# stand-in and no prds row needs seeding).
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

_LOGGER_NAME = "app.routes.design_agent"
_LINE = "design_agent_generate_kickoff"


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """Feature flag ON + the prototypes + Tier-2 tables + the DA module stack
    reloaded in dependency order (mirrors the Tier-2 worker-queue suite's
    fixture) so the route module's request-time gates start clean."""
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

    return SimpleNamespace(config=_config_mod, proto=proto_mod, jobs=jobs_mod, routes=routes_mod)


def _stub_bg(monkeypatch, routes_mod) -> None:
    """Stub _run_generation_bg so the in-process path never runs real
    generation — only the kickoff itself (route body up to the return) is
    under test here."""

    async def _fake_bg(**kwargs):
        pass

    monkeypatch.setattr(routes_mod, "_run_generation_bg", _fake_bg)


def _company(company_id: str = _TEST_COMPANY_ID):
    from app.auth import CompanyContext

    return CompanyContext(company_id=company_id, role="owner", user_id="u1")


def _kickoff_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if _LINE in r.getMessage()]


def _field(record: logging.LogRecord, key: str) -> str:
    """Pull a `key=value` token out of the record's key=value-convention
    message without over-parsing the rest of the line."""
    for token in record.getMessage().split():
        if token.startswith(f"{key}="):
            return token.split("=", 1)[1]
    raise AssertionError(f"no {key}= field in: {record.getMessage()!r}")


# ── T1: successful in-process kickoff ────────────────────────────────────────


async def test_inprocess_kickoff_emits_one_line_with_duration_and_marker(
    env, monkeypatch, caplog
):
    """A kickoff that performs a real insert (no dedupe hit, worker flag off)
    emits exactly one timing line carrying a parseable duration and the
    in-process outcome marker."""
    monkeypatch.delenv("DESIGN_AGENT_WORKER_ENABLED", raising=False)
    _stub_bg(monkeypatch, env.routes)

    body = env.routes.GenerateRequest(prd_id=1)
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        resp = await env.routes.generate(body=body, company=_company())

    assert resp.status == "generating"
    records = _kickoff_records(caplog)
    assert len(records) == 1, "exactly one timing line must be emitted"
    assert _field(records[0], "outcome") == "in_process"
    assert float(_field(records[0], "elapsed_ms")) >= 0


# ── T2: dedupe short-circuit ─────────────────────────────────────────────────


async def test_dedupe_short_circuit_emits_one_line_distinguishable_from_inprocess(
    env, monkeypatch, caplog
):
    """The dedupe short-circuit emits exactly one timing line, marked as a
    dedupe hit — a value distinct from the in-process marker in T1."""
    monkeypatch.setattr(
        env.routes, "find_existing_prototype",
        lambda **k: {"id": 7, "status": "ready"},
    )

    body = env.routes.GenerateRequest(prd_id=1)
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        resp = await env.routes.generate(body=body, company=_company())

    assert resp.prototype_id == 7
    records = _kickoff_records(caplog)
    assert len(records) == 1, "exactly one timing line must be emitted"
    outcome = _field(records[0], "outcome")
    assert outcome == "dedupe"
    assert outcome != "in_process"


# ── T3: the path that would otherwise ship uncovered — Tier-2 enqueue ───────


async def test_worker_enqueue_kickoff_emits_one_line_distinguishable_from_others(
    env, monkeypatch, caplog
):
    """With the worker flag on, a fresh heartbeat, and a successful enqueue,
    the Tier-2 early return emits exactly one timing line marked as
    worker-enqueued — distinguishable from BOTH the dedupe and in-process
    markers. Without this test, this exit path (behind a flag that defaults
    off) ships uncovered and only breaks once the flag is flipped."""
    monkeypatch.setenv("DESIGN_AGENT_WORKER_ENABLED", "1")
    env.jobs.write_heartbeat(worker_id="host:1")  # fresh heartbeat
    _stub_bg(monkeypatch, env.routes)

    body = env.routes.GenerateRequest(prd_id=1)
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        resp = await env.routes.generate(body=body, company=_company())

    assert resp.status == "generating"
    records = _kickoff_records(caplog)
    assert len(records) == 1, "exactly one timing line must be emitted"
    outcome = _field(records[0], "outcome")
    assert outcome == "worker_enqueued"
    assert outcome not in ("dedupe", "in_process")


# ── T4: an early-rejection path (raises, not just returns) ──────────────────


async def test_draining_rejection_emits_a_timing_line(env, caplog):
    """AC1 covers raises, not only returns: the cheapest early rejection to
    drive — the draining 503 — still emits a timing line before propagating
    the HTTPException."""
    from fastapi import HTTPException

    env.routes.request_shutdown()
    body = env.routes.GenerateRequest(prd_id=1)

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        with pytest.raises(HTTPException) as exc_info:
            await env.routes.generate(body=body, company=_company())

    assert exc_info.value.status_code == 503
    records = _kickoff_records(caplog)
    assert len(records) == 1, "a raise must still emit exactly one timing line"
    assert float(_field(records[0], "elapsed_ms")) >= 0
