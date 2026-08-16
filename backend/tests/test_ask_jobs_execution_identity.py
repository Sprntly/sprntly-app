"""Tests for the `ask_jobs` execution-identity foundation.

`ask_jobs` is main chat's LIVE primitive; staging + prod share ONE Supabase
project, so the migration this ticket ships lands on the same table backing
main chat's ask/answer polling loop right now. This suite proves the whole
point of a migration-as-deliverable ticket: the new columns are purely
additive (nullable, no volatile default) and every existing helper's LOGIC —
including the `status == 'generating'` guards — is untouched.

The fake-Supabase tier below mirrors the migration's shape (see the
`ask_jobs` DDL in conftest.py, updated alongside
`20260816120000_ask_jobs_execution_identity.sql`) and proves the HELPER
round-trip + guard behavior fast and DB-agnostically. The real
Postgres-level proof (status CHECK constraint untouched, no table rewrite,
partial-unique index build timing) is the separate real-local-Supabase
verification this ticket's report pastes evidence for — a fake SQLite
shadow can't stand in for a live Postgres CHECK/rewrite claim.
"""
from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

from app.db.asks import (
    cancel_ask_job,
    complete_ask_job,
    fail_ask_job,
    get_ask_job,
    start_ask_job,
    touch_ask_job,
)
from app.db.client import require_client

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "supabase"
    / "migrations"
    / "20260816120000_ask_jobs_execution_identity.sql"
)


def _seed_company(company_id: str = "c-identity") -> str:
    require_client().table("companies").insert(
        {"id": company_id, "slug": f"{company_id}-slug", "display_name": company_id}
    ).execute()
    return company_id


# ── AC1/AC2 — the migration file itself: additive, no rewrite ──────────────


def test_ask_jobs_migration_applies_additive_no_rewrite(isolated_settings):
    """Static proof against the actual migration file (AC1/AC2): every new
    column is added `if not exists` with no volatile default (in particular
    no `default gen_random_uuid()` on `run_id`, which would force a full
    table rewrite of the live prod-shared table), the two new indexes are
    `if not exists`, and the file does not touch `status`/its CHECK. Then a
    fake-DB insert proves the existing-shape write still succeeds with the
    new columns present + nullable (the real Postgres CHECK-constraint-
    unchanged proof is the live-Supabase verification, not reproducible
    against a SQLite shadow)."""
    sql = _MIGRATION_PATH.read_text()
    # DDL-only view (strip full-line `--` comments) so the file's own prose
    # about "status" being untouched doesn't trip the assertion below on the
    # word itself — only on it appearing in an actual executed statement.
    ddl_only = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )

    for column in (
        "kind text",
        "project_id bigint",
        "source_turn_id bigint",
        "run_id uuid",
        "client_message_id text",
        "error_class text",
        "attempt int",
    ):
        assert f"add column if not exists {column}" in sql, column

    # No volatile default anywhere in the actual DDL — the whole point of
    # this ticket is that ADD COLUMN never rewrites the live table. (The
    # file's own comments explain this in prose, hence checking ddl_only.)
    assert "default" not in ddl_only.lower()
    assert "gen_random_uuid()" not in ddl_only

    assert "create unique index if not exists ask_jobs_client_message_id_uidx" in ddl_only
    assert "create index if not exists ask_jobs_source_turn_idx" in ddl_only

    # Must not touch the status vocabulary/constraint at all — checked
    # against the DDL only, not the file's explanatory comments (which
    # deliberately discuss "status" being untouched).
    assert "status" not in ddl_only.lower()
    assert "check (" not in ddl_only.lower()
    assert "drop constraint" not in ddl_only.lower()

    # Existing-shape insert still succeeds against the additive shadow shape.
    company_id = _seed_company()
    ask_id = start_ask_job(company_id=company_id, dataset="d", question="q")
    row = get_ask_job(ask_id)
    assert row["status"] == "generating"
    for col in (
        "kind", "project_id", "source_turn_id", "run_id",
        "client_message_id", "error_class", "attempt",
    ):
        assert row[col] is None


# ── AC3 — existing main/private flow byte-unchanged ─────────────────────────


def test_existing_ask_flow_unchanged_with_null_identity(isolated_settings):
    """A full create -> heartbeat -> complete round trip, with none of the
    new columns ever set, behaves identically to before this ticket — and
    every new column stays NULL throughout."""
    company_id = _seed_company()
    ask_id = start_ask_job(
        company_id=company_id, dataset="acme", question="q?", pinned_skill="s1",
    )
    row = get_ask_job(ask_id)
    assert row["status"] == "generating"

    assert touch_ask_job(ask_id) is True

    complete_ask_job(ask_id, {"answer": "ok", "citations": []})
    row = get_ask_job(ask_id)
    assert row["status"] == "ready"
    assert row["response"] == {"answer": "ok", "citations": []}
    for col in (
        "kind", "project_id", "source_turn_id", "run_id",
        "client_message_id", "error_class", "attempt",
    ):
        assert row[col] is None


def test_existing_ask_flow_cancel_and_fail_unchanged_with_null_identity(isolated_settings):
    """The cancel + fail paths are equally untouched by the new columns."""
    company_id = _seed_company()

    cancel_id = start_ask_job(company_id=company_id, dataset="acme", question="q1?")
    assert cancel_ask_job(cancel_id) == "cancelled"
    assert get_ask_job(cancel_id)["status"] == "cancelled"

    fail_id = start_ask_job(company_id=company_id, dataset="acme", question="q2?")
    fail_ask_job(fail_id, "boom")
    row = get_ask_job(fail_id)
    assert row["status"] == "error"
    assert row["error"] == "boom"
    assert row["error_class"] is None


# ── AC4 — new columns writable + readable when set ──────────────────────────


def test_execution_identity_columns_roundtrip(isolated_settings):
    """A row with every new field populated round-trips through
    `start_ask_job` (creation-time identity) + `fail_ask_job` (error_class,
    set on failure — it's a typed error CATEGORY, not a creation input)."""
    company_id = _seed_company()
    ask_id = start_ask_job(
        company_id=company_id,
        dataset="acme",
        question="q?",
        kind="project_group",
        project_id=42,
        source_turn_id=7,
        run_id="11111111-1111-1111-1111-111111111111",
        client_message_id="cmid-abc",
        attempt=1,
    )
    row = get_ask_job(ask_id)
    assert row["kind"] == "project_group"
    assert row["project_id"] == 42
    assert row["source_turn_id"] == 7
    assert row["run_id"] == "11111111-1111-1111-1111-111111111111"
    assert row["client_message_id"] == "cmid-abc"
    assert row["attempt"] == 1
    assert row["error_class"] is None  # not set at creation

    fail_ask_job(ask_id, "timed out", error_class="timeout")
    row = get_ask_job(ask_id)
    assert row["status"] == "error"
    assert row["error_class"] == "timeout"
    # The creation-time identity fields survive the fail update untouched.
    assert row["kind"] == "project_group"
    assert row["client_message_id"] == "cmid-abc"


# ── AC5 — partial-unique client_message_id ──────────────────────────────────


def test_client_message_id_partial_unique(isolated_settings):
    """Two NULL `client_message_id` rows are both allowed (the existing
    main/private shape); a duplicate NON-NULL value is rejected."""
    company_id = _seed_company()

    # Two NULLs: must not collide with each other.
    start_ask_job(company_id=company_id, dataset="acme", question="q1?")
    start_ask_job(company_id=company_id, dataset="acme", question="q2?")

    start_ask_job(
        company_id=company_id, dataset="acme", question="q3?",
        client_message_id="cmid-dup",
    )
    with pytest.raises(sqlite3.IntegrityError):
        start_ask_job(
            company_id=company_id, dataset="acme", question="q4?",
            client_message_id="cmid-dup",
        )


# ── AC6 — helper LOGIC + status='generating' guards unchanged ──────────────


def test_asks_helpers_logic_unchanged(isolated_settings):
    """Source-scan pin (same technique as
    `test_ask_project_promotion.py::test_per_user_history_path_unchanged`):
    every guarded write still filters on `.eq("status", "generating")`, and
    `complete_ask_job`/heartbeat/reaper are byte-unchanged — only
    `start_ask_job`'s signature (append-only) and `fail_ask_job`'s update
    payload (one new optional key) were touched by this ticket."""
    from app.db import asks as asks_mod

    complete_src = inspect.getsource(complete_ask_job)
    assert '.eq("status", "generating")' in complete_src
    assert '"status": "ready"' in complete_src

    fail_src = inspect.getsource(fail_ask_job)
    assert '.eq("status", "generating")' in fail_src
    assert '"status": "error"' in fail_src

    touch_src = inspect.getsource(touch_ask_job)
    assert '.eq("status", "generating")' in touch_src

    cancel_src = inspect.getsource(cancel_ask_job)
    assert '.eq("status", "generating")' in cancel_src
    assert '"status": "cancelled"' in cancel_src

    reaper_src = inspect.getsource(asks_mod.fail_orphan_generating_ask_jobs)
    assert '.eq("status", "generating")' in reaper_src

    # start_ask_job's new params are all append-only optional kwargs so every
    # existing positional/keyword caller is unaffected.
    sig = inspect.signature(start_ask_job)
    params = list(sig.parameters.values())
    required = [p for p in params if p.default is inspect._empty]
    assert [p.name for p in required] == ["company_id", "dataset", "question"]
    for name in (
        "kind", "project_id", "source_turn_id", "run_id",
        "client_message_id", "attempt",
    ):
        assert sig.parameters[name].default is None
