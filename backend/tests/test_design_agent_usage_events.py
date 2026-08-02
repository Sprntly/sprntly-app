"""Tests for the per-action usage ledger (`design_agent_usage_events`).

Two layers, mirroring the sibling Design Agent suites:

1. **DB helpers** (`db/usage_events.py`, fake Supabase) — mirrors
   `test_db_prototypes.py`: a SQLite-translated copy of the migration DDL plus the
   reloaded helper module wired to the in-memory fake. Covers insert/finalize,
   token+cost write, unknown-model degrade, and workspace isolation.

2. **Route hooks** (`_run_generation_bg` / `_run_iterate_bg`, fake Supabase) —
   mirrors `test_design_agent_build_repair.py`'s `env` fixture: the runner is
   stubbed (no Anthropic) and the build/stage seams are faked, so the bg runners
   run to their terminals and we assert the ledger row transitions. Covers the
   generation/iteration start→succeeded/failed transitions, the repair-token
   rollup, the PLAN-mode no-row rule, the awaiting_clarification pause, and the
   fail-open contract.

Neutral names throughout — the table/column names are product language and no
internal identifiers appear.
"""
from __future__ import annotations

import importlib
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.llm_telemetry import RunUsage


# SQLite-compatible translation of
# supabase/migrations/20260622120000_design_agent_usage_events.sql — Postgres-only
# constructs (bigint identity, timestamptz, numeric, RLS, FK actions) are
# translated/omitted the same way the other fake DDLs are. The fake exercises SQL
# semantics, not Postgres-specific DDL.
_USAGE_EVENTS_DDL = """
CREATE TABLE design_agent_usage_events (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id                TEXT NOT NULL,
    prd_id                      INTEGER,
    prototype_id                INTEGER,
    kind                        TEXT NOT NULL
                                CHECK (kind IN ('full_generation', 'iteration')),
    status                      TEXT NOT NULL
                                CHECK (status IN ('started', 'succeeded', 'failed')),
    trigger_comment_id          INTEGER,
    model                       TEXT,
    input_tokens                INTEGER,
    output_tokens               INTEGER,
    cache_creation_input_tokens INTEGER,
    cache_read_input_tokens     INTEGER,
    est_cost_usd                REAL,
    error_class                 TEXT,
    created_at                  TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at                TEXT
);
"""

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
    share_mode             TEXT NOT NULL DEFAULT 'private',
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
CREATE TABLE prototype_screenshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id  INTEGER NOT NULL,
    workspace_id  TEXT NOT NULL,
    storage_key   TEXT NOT NULL,
    position      INTEGER NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260622120000_design_agent_usage_events.sql"
)

_MODEL = "claude-sonnet-4-6"


def _usage(*, input_tokens=0, output_tokens=0, cache_creation=0, cache_read=0):
    return RunUsage(
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _rows():
    from tests import _fake_supabase

    return _fake_supabase.get_fake_db().execute(
        "SELECT * FROM design_agent_usage_events ORDER BY id"
    ).fetchall()


# ─── DB helper fixture (mirrors test_db_prototypes.py::proto) ─────────────────


@pytest.fixture
def ledger(isolated_settings, monkeypatch):
    """The reloaded app.db.usage_events module wired to the fake Supabase, with the
    ledger table present in the in-memory DB."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_USAGE_EVENTS_DDL)

    import app.db.usage_events as ue_mod
    importlib.reload(ue_mod)
    return ue_mod


# ─── Migration file content (string-level) ───────────────────────────────────


def _migration_sql_only() -> str:
    lines = []
    for line in _MIGRATION_PATH.read_text().splitlines():
        lines.append(line.split("--", 1)[0])
    return "\n".join(lines).lower()


def test_migration_file_exists_and_is_dated_correctly():
    assert _MIGRATION_PATH.exists()
    assert _MIGRATION_PATH.name == "20260622120000_design_agent_usage_events.sql"


def test_migration_applies_twice_no_error():
    # Idempotency by construction: every CREATE guards itself with IF NOT EXISTS,
    # so a re-apply is a no-op (no live Postgres in this dev env).
    sql = _migration_sql_only()
    for m in re.finditer(r"create\s+table\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent CREATE TABLE near offset {m.start()}")
    for m in re.finditer(r"create\s+index\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent CREATE INDEX near offset {m.start()}")


def test_migration_workspace_id_not_null_no_default():
    sql = _migration_sql_only()
    assert re.search(r"workspace_id\s+text\s+not\s+null", sql), "workspace_id missing"
    assert not re.search(r"workspace_id\s+text\s+not\s+null\s+default", sql), \
        "workspace_id must NOT carry a DEFAULT"


def test_migration_fks_are_set_null_and_rls_enabled():
    sql = _migration_sql_only()
    # Both FKs on delete set null (ledger survives prototype/PRD deletion).
    assert sql.count("on delete set null") == 2
    assert "on delete cascade" not in sql
    assert "enable row level security" in sql
    assert "create policy" not in sql


# ─── Creation ────────────────────────────────────────────────────────────────


def test_start_usage_event_inserts_started_row(ledger):
    eid = ledger.start_usage_event(workspace_id="app", kind="full_generation", prd_id=7)
    assert isinstance(eid, int) and eid > 0
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["kind"] == "full_generation"
    assert rows[0]["status"] == "started"
    assert rows[0]["workspace_id"] == "app"
    assert rows[0]["completed_at"] is None


def test_finalize_succeeded_writes_tokens_and_cost(ledger):
    eid = ledger.start_usage_event(workspace_id="app", kind="full_generation")
    ledger.finalize_usage_event(
        event_id=eid,
        workspace_id="app",
        status="succeeded",
        usage=_usage(input_tokens=1000, output_tokens=500),
        model=_MODEL,
        prototype_id=42,
    )
    row = _rows()[0]
    assert row["status"] == "succeeded"
    assert row["input_tokens"] == 1000
    assert row["output_tokens"] == 500
    assert row["est_cost_usd"] is not None and row["est_cost_usd"] > 0
    assert row["model"] == _MODEL
    assert row["prototype_id"] == 42
    assert row["completed_at"] is not None


# ─── Retrieval / round-trip + isolation ──────────────────────────────────────


def test_finalize_filters_by_workspace(ledger):
    # AC10: a finalize under workspace B does not touch a row written under A.
    eid = ledger.start_usage_event(workspace_id="app", kind="full_generation")
    ledger.finalize_usage_event(
        event_id=eid, workspace_id="demo", status="succeeded",
        usage=_usage(input_tokens=10), model=_MODEL,
    )
    row = _rows()[0]
    assert row["status"] == "started"  # untouched — wrong workspace
    assert row["input_tokens"] is None


def test_unknown_model_persists_tokens_null_cost(ledger):
    # AC12: an unpriced model stores tokens, leaves est_cost_usd null, no raise.
    eid = ledger.start_usage_event(workspace_id="app", kind="full_generation")
    ledger.finalize_usage_event(
        event_id=eid, workspace_id="app", status="succeeded",
        usage=_usage(input_tokens=100, output_tokens=50),
        model="some-unpriced-model",
    )
    row = _rows()[0]
    assert row["status"] == "succeeded"
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 50
    assert row["est_cost_usd"] is None
    assert row["model"] == "some-unpriced-model"


def test_finalize_failed_records_error_class_without_tokens(ledger):
    eid = ledger.start_usage_event(workspace_id="app", kind="full_generation")
    ledger.finalize_usage_event(
        event_id=eid, workspace_id="app", status="failed", error_class="BadRequestError",
    )
    row = _rows()[0]
    assert row["status"] == "failed"
    assert row["error_class"] == "BadRequestError"
    assert row["input_tokens"] is None


# ─── Route-hook fixture (mirrors test_design_agent_build_repair.py::env) ──────


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """Fake-Supabase DB (prototypes + ledger) + reloaded design-agent route module.
    The runner is stubbed per-test; build/stage seams are faked so the bg runners
    reach their terminals."""
    from tests import _fake_supabase

    db = _fake_supabase.get_fake_db()
    db.executescript(_PROTOTYPE_DDL)
    db.executescript(_USAGE_EVENTS_DDL)
    monkeypatch.setitem(
        _fake_supabase._JSONB_COLUMNS, "prototype_checkpoints",
        {"prompt_history", "comment_state"},
    )
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")
    monkeypatch.delenv("SUPABASE_STORAGE_BUCKET", raising=False)

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.db.usage_events as ue_mod
    importlib.reload(ue_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    return SimpleNamespace(proto=proto_mod, ue=ue_mod, routes=routes_mod)


def _async_return(value):
    async def _f(*args, **kwargs):
        return value
    return _f


def _fake_result(*, status="complete", usage=None, iters=1, error_class=None):
    return SimpleNamespace(
        status=status,
        iters=iters,
        usage=usage if usage is not None else _usage(),
        final_content=[],
        error_class=error_class,
        error_message=None,
        theme_expectations=None,
    )


def _stub_generation_seams(env, monkeypatch, *, result, virtual_fs):
    """Stub generate_prototype + the build/stage seams so _run_generation_bg runs
    to its success terminal (a clean build, no repair)."""
    monkeypatch.setattr(env.routes, "_load_prd_body", lambda prd_id: "# PRD")
    monkeypatch.setattr(env.routes, "_extract_website_sample", _async_return(None))
    monkeypatch.setattr(env.routes, "generate_prototype", _async_return((result, virtual_fs)))
    # Clean build: vite_build_with_repair returns (dist, unchanged source).
    monkeypatch.setattr(
        env.routes, "vite_build_with_repair",
        lambda vfs: _build_clean(vfs),
    )
    monkeypatch.setattr(env.routes, "stage_bundle", _async_return("file:///x/index.html"))
    monkeypatch.setattr(env.routes, "reconcile_comments_on_checkpoint", lambda **kw: None)


def _build_clean(vfs):
    async def _f():
        return {"index.html": "<html>built</html>"}, dict(vfs)
    return _f()


# ─── Generation hooks ────────────────────────────────────────────────────────


async def test_generate_emits_started_then_succeeded_with_prototype_id(env, monkeypatch):
    # AC3: a successful generation → one full_generation row, started → succeeded,
    # with non-null tokens/cost/model/prototype_id.
    result = _fake_result(usage=_usage(input_tokens=2000, output_tokens=900))
    _stub_generation_seams(env, monkeypatch, result=result, virtual_fs={"src/App.tsx": "x"})

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    eid = env.ue.start_usage_event(
        workspace_id="app", kind="full_generation", prd_id=1, prototype_id=pid,
    )
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=1,
        target_platform="both", instructions="", figma_file_key=None,
        event_id=eid,
    )
    rows = _rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "full_generation"
    assert row["status"] == "succeeded"
    assert row["input_tokens"] == 2000
    assert row["output_tokens"] == 900
    assert row["est_cost_usd"] is not None and row["est_cost_usd"] > 0
    assert row["model"] == _MODEL
    assert row["prototype_id"] == pid


async def test_generation_failure_marks_failed(env, monkeypatch):
    # AC5: a non-complete run → failed row with error_class.
    result = _fake_result(status="error", error_class="BadRequestError", usage=_usage())
    monkeypatch.setattr(env.routes, "_load_prd_body", lambda prd_id: "# PRD")
    monkeypatch.setattr(env.routes, "_extract_website_sample", _async_return(None))
    monkeypatch.setattr(env.routes, "generate_prototype", _async_return((result, {})))

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    eid = env.ue.start_usage_event(
        workspace_id="app", kind="full_generation", prd_id=1, prototype_id=pid,
    )
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=1,
        target_platform="both", instructions="", figma_file_key=None,
        event_id=eid,
    )
    row = _rows()[0]
    assert row["status"] == "failed"
    assert row["error_class"] == "BadRequestError"


async def test_succeeded_row_includes_repair_tokens(env, monkeypatch):
    # AC4: when a build repair runs, succeeded tokens = primary + repair sum.
    import app.design_agent.storage as storage

    primary = _usage(input_tokens=1000, output_tokens=400)
    result = _fake_result(usage=primary)

    monkeypatch.setattr(env.routes, "_load_prd_body", lambda prd_id: "# PRD")
    monkeypatch.setattr(env.routes, "_extract_website_sample", _async_return(None))
    monkeypatch.setattr(env.routes, "generate_prototype", _async_return((result, {"src/App.tsx": "x"})))
    monkeypatch.setattr(env.routes, "stage_bundle", _async_return("file:///x/index.html"))
    monkeypatch.setattr(env.routes, "reconcile_comments_on_checkpoint", lambda **kw: None)

    # Force the first build to fail (routes into _build_repair_loop), then succeed.
    state = {"calls": 0}

    async def _build_with_repair(vfs):
        state["calls"] += 1
        if state["calls"] == 1:
            raise storage.ViteBuildError("vite build exit=1: SyntaxError")
        return {"index.html": "<html>built</html>"}, dict(vfs)

    monkeypatch.setattr(env.routes, "vite_build_with_repair", _build_with_repair)

    # The repair sub-agent: each pass adds 300/100 tokens; one pass converges.
    async def _repair_run(**kwargs):
        return _fake_result(usage=_usage(input_tokens=300, output_tokens=100)), {"src/App.tsx": "fixed"}

    monkeypatch.setattr(env.routes, "repair_build_run", _repair_run)

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    eid = env.ue.start_usage_event(
        workspace_id="app", kind="full_generation", prd_id=1, prototype_id=pid,
    )
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=1,
        target_platform="both", instructions="", figma_file_key=None,
        event_id=eid,
    )
    row = _rows()[0]
    assert row["status"] == "succeeded"
    # primary (1000/400) + one repair pass (300/100) = 1300/500.
    assert row["input_tokens"] == 1300
    assert row["output_tokens"] == 500


async def test_ledger_write_failure_does_not_break_generation(env, monkeypatch):
    # AC9: a ledger raise (start/finalize) does not break generation — the
    # prototype still reaches 'ready'.
    result = _fake_result(usage=_usage(input_tokens=10))
    _stub_generation_seams(env, monkeypatch, result=result, virtual_fs={"src/App.tsx": "x"})

    def _boom(*a, **kw):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(env.routes, "finalize_usage_event", _boom)

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    eid = env.ue.start_usage_event(
        workspace_id="app", kind="full_generation", prd_id=1, prototype_id=pid,
    )
    # Must not raise.
    await env.routes._run_generation_bg(
        prototype_id=pid, workspace_id="app", prd_id=1,
        target_platform="both", instructions="", figma_file_key=None,
        event_id=eid,
    )
    proto = env.proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert proto["status"] == "ready"  # generation completed despite ledger failure


# ─── Iteration hooks ─────────────────────────────────────────────────────────


def _seed_ready_prototype(env, *, workspace_id="app"):
    pid = env.proto.start_prototype(prd_id=1, workspace_id=workspace_id, template_version=1)
    env.proto.complete_prototype(
        prototype_id=pid, workspace_id=workspace_id, bundle_url="file:///b", current_checkpoint_id=None,
    )
    return pid


def _iterate_body(env, *, mode="execute", applied_comment_id=None):
    return env.routes.IterateRequest(prompt="tweak it", applied_comment_id=applied_comment_id, mode=mode)


def _stub_iterate_seams(env, monkeypatch, *, result, virtual_fs):
    monkeypatch.setattr(env.routes, "read_source_files_for_checkpoint", _async_return({}))
    monkeypatch.setattr(env.routes, "list_comments", lambda **kw: [])
    monkeypatch.setattr(env.routes, "iterate_prototype", _async_return((result, virtual_fs)))
    monkeypatch.setattr(env.routes, "vite_build", _async_return({"index.html": "<html>built</html>"}))
    monkeypatch.setattr(env.routes, "stage_bundle", _async_return("file:///x/index.html"))
    monkeypatch.setattr(env.routes, "reconcile_comments_on_checkpoint", lambda **kw: None)


async def test_iterate_execute_emits_iteration_row_with_trigger_comment(env, monkeypatch):
    # AC6: an EXECUTE iterate → one iteration row, started → succeeded, with
    # trigger_comment_id set.
    result = _fake_result(usage=_usage(input_tokens=500, output_tokens=200))
    _stub_iterate_seams(env, monkeypatch, result=result, virtual_fs={"src/App.tsx": "x"})

    pid = _seed_ready_prototype(env)
    await env.routes._run_iterate_bg(
        prototype_id=pid, workspace_id="app",
        body=_iterate_body(env, mode="execute", applied_comment_id=77),
    )
    rows = _rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "iteration"
    assert row["status"] == "succeeded"
    assert row["trigger_comment_id"] == 77
    assert row["input_tokens"] == 500
    assert row["est_cost_usd"] is not None and row["est_cost_usd"] > 0


async def test_iterate_plan_mode_emits_no_row(env, monkeypatch):
    # AC7: PLAN mode bills nothing → zero rows.
    result = _fake_result(usage=_usage(input_tokens=300))
    _stub_iterate_seams(env, monkeypatch, result=result, virtual_fs={})
    monkeypatch.setattr(env.routes, "set_iteration_plan", lambda **kw: None)

    pid = _seed_ready_prototype(env)
    await env.routes._run_iterate_bg(
        prototype_id=pid, workspace_id="app",
        body=_iterate_body(env, mode="plan"),
        iteration_id=1,
    )
    assert _rows() == []


async def test_iterate_awaiting_clarification_leaves_row_open(env, monkeypatch):
    # AC8: an awaiting_clarification EXECUTE iterate leaves its started row OPEN.
    result = _fake_result(status="awaiting_clarification", usage=_usage(input_tokens=200))
    _stub_iterate_seams(env, monkeypatch, result=result, virtual_fs={})

    pid = _seed_ready_prototype(env)
    await env.routes._run_iterate_bg(
        prototype_id=pid, workspace_id="app",
        body=_iterate_body(env, mode="execute"),
    )
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["status"] == "started"  # NOT finalized — pause, not completion
    assert rows[0]["completed_at"] is None


async def test_iterate_failure_marks_failed(env, monkeypatch):
    result = _fake_result(status="error", error_class="Boom", usage=_usage())
    _stub_iterate_seams(env, monkeypatch, result=result, virtual_fs={})

    pid = _seed_ready_prototype(env)
    await env.routes._run_iterate_bg(
        prototype_id=pid, workspace_id="app",
        body=_iterate_body(env, mode="execute"),
    )
    row = _rows()[0]
    assert row["status"] == "failed"
    assert row["error_class"] == "Boom"


# ─── Shared failure-terminal finalizer ───────────────────────────────────────
#
# The generation and iteration failure terminals share one fail-open finalizer.
# These cover the helper directly (no-op guard, success path, fail-open log with
# the caller-supplied kind) and then prove — end-to-end — that each terminal
# passes the correct kind through.
_FINALIZER_LOGGER = "app.routes.design_agent"


def _boom_ledger(**kwargs):
    raise RuntimeError("ledger down")


def test_finalize_usage_event_failed_noop_on_none_event_id(env, monkeypatch):
    # A None event_id (PLAN mode, or a failure before the ledger row opened) is a
    # no-op: finalize_usage_event is never called and nothing raises.
    calls = []
    monkeypatch.setattr(
        env.routes, "finalize_usage_event", lambda **kw: calls.append(kw)
    )
    env.routes._finalize_usage_event_failed(
        event_id=None,
        workspace_id="app",
        prototype_id=1,
        error_class="BadRequestError",
        kind="full_generation",
    )
    assert calls == []


def test_finalize_usage_event_failed_calls_finalize_with_failed_status(env, monkeypatch):
    # A real event_id → finalize_usage_event called once with status='failed',
    # the given error_class, and the given workspace_id.
    calls = []
    monkeypatch.setattr(
        env.routes, "finalize_usage_event", lambda **kw: calls.append(kw)
    )
    env.routes._finalize_usage_event_failed(
        event_id=99,
        workspace_id="ws-1",
        prototype_id=5,
        error_class="BadRequestError",
        kind="iteration",
    )
    assert len(calls) == 1
    assert calls[0]["status"] == "failed"
    assert calls[0]["error_class"] == "BadRequestError"
    assert calls[0]["workspace_id"] == "ws-1"
    assert calls[0]["event_id"] == 99


def test_finalize_usage_event_failed_generation_kind_logs_on_ledger_error(
    env, monkeypatch, caplog
):
    # finalize_usage_event raises → the exception is swallowed and a WARNING is
    # logged with kind=full_generation. The exception does not propagate.
    monkeypatch.setattr(env.routes, "finalize_usage_event", _boom_ledger)
    with caplog.at_level(logging.WARNING, logger=_FINALIZER_LOGGER):
        env.routes._finalize_usage_event_failed(
            event_id=7,
            workspace_id="app",
            prototype_id=3,
            error_class="BadRequestError",
            kind="full_generation",
        )
    msgs = [r.getMessage() for r in caplog.records]
    assert any("usage_event_finalize_failed" in m for m in msgs)
    assert any("kind=full_generation" in m for m in msgs)


def test_finalize_usage_event_failed_iteration_kind_logs_on_ledger_error(
    env, monkeypatch, caplog
):
    # Same fail-open path, kind=iteration.
    monkeypatch.setattr(env.routes, "finalize_usage_event", _boom_ledger)
    with caplog.at_level(logging.WARNING, logger=_FINALIZER_LOGGER):
        env.routes._finalize_usage_event_failed(
            event_id=7,
            workspace_id="app",
            prototype_id=3,
            error_class="Boom",
            kind="iteration",
        )
    msgs = [r.getMessage() for r in caplog.records]
    assert any("usage_event_finalize_failed" in m for m in msgs)
    assert any("kind=iteration" in m for m in msgs)


async def test_generation_failure_terminal_calls_shared_finalizer_full_generation_kind(
    env, monkeypatch, caplog
):
    # Driving _run_generation_bg to a failure terminal with a raising ledger
    # proves the generation terminal passes kind='full_generation' to the shared
    # finalizer (the fail-open WARNING carries it).
    result = _fake_result(status="error", error_class="BadRequestError", usage=_usage())
    monkeypatch.setattr(env.routes, "_load_prd_body", lambda prd_id: "# PRD")
    monkeypatch.setattr(env.routes, "_extract_website_sample", _async_return(None))
    monkeypatch.setattr(env.routes, "generate_prototype", _async_return((result, {})))
    monkeypatch.setattr(env.routes, "finalize_usage_event", _boom_ledger)

    pid = env.proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    eid = env.ue.start_usage_event(
        workspace_id="app", kind="full_generation", prd_id=1, prototype_id=pid,
    )
    with caplog.at_level(logging.WARNING, logger=_FINALIZER_LOGGER):
        await env.routes._run_generation_bg(
            prototype_id=pid, workspace_id="app", prd_id=1,
            target_platform="both", instructions="", figma_file_key=None,
            event_id=eid,
        )
    finalize_logs = [
        r.getMessage() for r in caplog.records
        if "usage_event_finalize_failed" in r.getMessage()
    ]
    assert finalize_logs, "expected the shared finalizer WARNING to fire"
    assert all("kind=full_generation" in m for m in finalize_logs)
    assert not any("kind=iteration" in m for m in finalize_logs)


async def test_iteration_failure_terminal_calls_shared_finalizer_iteration_kind(
    env, monkeypatch, caplog
):
    # Same, for _run_iterate_bg: the iteration terminal passes kind='iteration'.
    result = _fake_result(status="error", error_class="Boom", usage=_usage())
    _stub_iterate_seams(env, monkeypatch, result=result, virtual_fs={})
    monkeypatch.setattr(env.routes, "finalize_usage_event", _boom_ledger)

    pid = _seed_ready_prototype(env)
    with caplog.at_level(logging.WARNING, logger=_FINALIZER_LOGGER):
        await env.routes._run_iterate_bg(
            prototype_id=pid, workspace_id="app",
            body=_iterate_body(env, mode="execute"),
        )
    finalize_logs = [
        r.getMessage() for r in caplog.records
        if "usage_event_finalize_failed" in r.getMessage()
    ]
    assert finalize_logs, "expected the shared finalizer WARNING to fire"
    assert all("kind=iteration" in m for m in finalize_logs)
    assert not any("kind=full_generation" in m for m in finalize_logs)
