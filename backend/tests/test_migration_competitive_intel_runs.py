"""Structural check on the competitive_intel_runs migration.

The migration is Apurva-gated (schema_migrations' primary key is the timestamp
alone, so a bad file blocks every backend deploy — see
tests/test_migrations_hygiene.py). This test pins the shape the pipeline reads
and writes, so a later edit that drops a column or the latest-run index fails
here rather than in production:

  - `state` IS the skill's ci-state.json (competitors/our_state/decisions),
  - `mode` is constrained to the two modes the skill defines,
  - the (company_id, id desc) index is the only access pattern (latest run),
  - RLS is on with no policies — service-role writes only.

No DB is touched; this reads the SQL file.
"""
from __future__ import annotations

from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260730163000_competitive_intel_runs.sql"
)


def _sql() -> str:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION.name}"
    return MIGRATION.read_text(encoding="utf-8").lower()


def test_migration_is_idempotent_and_cascades_on_company_delete():
    sql = _sql()
    assert "create table if not exists competitive_intel_runs" in sql
    assert "references companies (id) on delete cascade" in sql


def test_migration_declares_every_column_the_pipeline_writes():
    sql = _sql()
    for column in (
        "company_id", "mode", "question", "window_label", "competitor_set",
        "records", "state", "metadata", "html", "created_at",
    ):
        assert column in sql, f"migration is missing the {column!r} column"
    # The four jsonb payloads default to empty containers, so a partial save
    # never produces a NULL the reader has to guard.
    assert sql.count("'[]'::jsonb") >= 2   # competitor_set, records
    assert sql.count("'{}'::jsonb") >= 2   # state, metadata


def test_mode_is_constrained_to_scan_and_review():
    assert "check (mode in ('scan','review'))" in _sql()


def test_latest_run_index_and_rls_are_declared():
    sql = _sql()
    assert "create index if not exists competitive_intel_runs_company_idx" in sql
    assert "(company_id, id desc)" in sql
    assert "enable row level security" in sql
    # Service-role only: no policy is granted to anon/authenticated.
    assert "create policy" not in sql
