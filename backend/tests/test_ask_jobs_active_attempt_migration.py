"""The `ask_jobs_active_attempt_uidx` migration (retry-claim atomicity).

Deterministic proofs (no real Postgres needed for these): the migration is
additive / partial / `if not exists` with NO table rewrite and NO status/CHECK
change (AC18), the partial-unique enforces at most one LIVE (`generating`)
attempt per `source_turn_id` while allowing terminal rows (AC12/AC18), and
every enumerated call site of the modified files still resolves with the two
dead ContextVar readers gone (AC19).

`ask_jobs` is a prod-shared table; the real `CREATE UNIQUE INDEX` lock-window /
idempotent double-apply is proven on the live rig in Live Verification and by
the ship-gate verifier (addendum §2 — migration = blast-radius, not full-suite).
"""
from __future__ import annotations

import pathlib
import sqlite3

import pytest

from app.db.workspaces import ensure_default_workspace

_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"
_MIGRATION_FILE = "20260816130000_ask_jobs_active_attempt_unique.sql"
_APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"


def _migration_sql() -> str:
    return (_MIGRATIONS_DIR / _MIGRATION_FILE).read_text().lower()


def test_active_attempt_index_additive_no_rewrite():
    """AC18: additive, partial, `if not exists`; no rewrite, no status/CHECK
    change, no new column."""
    sql = _migration_sql()
    assert "create unique index if not exists ask_jobs_active_attempt_uidx" in sql
    assert "on public.ask_jobs (source_turn_id)" in sql
    # PARTIAL — only currently-generating group runs are covered.
    assert "where status = 'generating'" in sql
    assert "source_turn_id is not null" in sql
    # Additive-only: never rewrites the table, changes the status vocabulary /
    # its CHECK, or adds a column.
    for forbidden in ("alter table", "add column", "drop table", "drop column", "check ("):
        assert forbidden not in sql, forbidden
    # No `alter ... type` rewrite either.
    assert "alter column" not in sql


def test_active_attempt_index_enforces_one_live_attempt(tenant_client, isolated_settings):
    """AC12/AC18: a SECOND `generating` insert for the same source_turn_id is
    rejected; a TERMINAL (ready) row for the same turn is allowed (partial)."""
    from app.db import conversations as conversations_db
    from app.db import projects as projects_db
    from app.db.asks import complete_ask_job, start_ask_job

    t = tenant_client.make(slug="acme")
    ws_id = ensure_default_workspace(t.company_id)["id"]
    project_id = projects_db.create_project(
        company_id=t.company_id, workspace_id=ws_id, name="mig", created_by=t.user_id,
    )["id"]
    conv = conversations_db.create_group_chat(project_id, t.user_id)
    turn = conversations_db.post_group_turn(conv["id"], t.user_id, "@Sprntly go")

    j1 = start_ask_job(
        company_id=t.company_id, dataset="acme", question="", conversation_id=conv["id"],
        kind="project_group", project_id=project_id, source_turn_id=turn["id"], run_id="r1",
    )
    # Second LIVE attempt for the same turn → rejected by the partial-unique.
    with pytest.raises(sqlite3.IntegrityError):
        start_ask_job(
            company_id=t.company_id, dataset="acme", question="", conversation_id=conv["id"],
            kind="project_group", project_id=project_id, source_turn_id=turn["id"], run_id="r2",
        )
    # Move the first row terminal, then a NEW live attempt is allowed (the
    # partial predicate no longer covers the ready row).
    complete_ask_job(j1, {"answer": "ok"})
    j3 = start_ask_job(
        company_id=t.company_id, dataset="acme", question="", conversation_id=conv["id"],
        kind="project_group", project_id=project_id, source_turn_id=turn["id"], run_id="r3",
    )
    assert j3 != j1


def _app_text(*relpaths: str) -> str:
    return "\n".join((_APP_DIR / p).read_text() for p in relpaths)


def test_modified_files_references_intact():
    """AC19: every enumerated call site still resolves, and `_project_scoped_
    ask` / `active_project_id` have zero remaining READERS after the swap."""
    ask_runner_src = (_APP_DIR / "ask_job_runner.py").read_text()
    routes_ask_src = (_APP_DIR / "routes" / "ask.py").read_text()
    projects_src = (_APP_DIR / "routes" / "projects.py").read_text()
    conversations_src = (_APP_DIR / "db" / "conversations.py").read_text()
    qa_src = (_APP_DIR / "qa_agent.py").read_text()

    # Arity-stable public seams still present.
    assert "run_ask_job(" in routes_ask_src
    assert "def run_ask_job(" in ask_runner_src
    assert "def run_execution_job(" in ask_runner_src
    # The group reply is now produced on the shared `/v1/ask` mount and
    # persisted+broadcast by `_persist_group_reply` (the "mount-not-scheduler"
    # Choice-A seam that replaced the deleted `_schedule_group_reply` /
    # `_respond_as_group_agent` in-band group-reply path).
    assert "def _persist_group_reply(" in ask_runner_src
    assert "def list_group_turns(" in conversations_src
    assert "def start_ask_job(" in (_APP_DIR / "db" / "asks.py").read_text()

    # `_project_scoped_ask` is fully removed — no def, no call, anywhere in app/.
    for path in _APP_DIR.rglob("*.py"):
        assert "_project_scoped_ask" not in path.read_text(), path

    # `active_project_id` has ZERO readers: it appears ONLY in ask_runner.py
    # (the ContextVar definition + its set/reset/getter), never read elsewhere.
    for path in _APP_DIR.rglob("*.py"):
        if path.name == "ask_runner.py":
            continue
        assert "active_project_id" not in path.read_text(), path

    # The connector gate now reads `scope`, not the ContextVar.
    assert "_skip_project_connectors(scope" in qa_src
