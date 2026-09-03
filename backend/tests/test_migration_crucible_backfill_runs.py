"""Structural check on the backfill-audit migration. No DB is touched; this
reads the SQL file, same convention as
`test_migration_crucible_run_report_document.py`."""
from __future__ import annotations

from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations"
    / "20260903120000_crucible_backfill_runs.sql"
)


def _ddl() -> str:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION.name}"
    out = []
    for line in MIGRATION.read_text(encoding="utf-8").lower().splitlines():
        code = line.split("--", 1)[0]
        if code.strip():
            out.append(code)
    return "\n".join(out)


def test_migration_is_idempotent_shape():
    """`create table if not exists` + `create index if not exists` +
    `drop policy if exists` before `create policy` — a re-apply is a no-op,
    matching every sibling crucible migration's own convention."""
    ddl = _ddl()
    assert "create table if not exists crucible_backfill_runs" in ddl
    assert "create index if not exists crucible_backfill_runs_company_idx" in ddl
    assert "drop policy if exists" in ddl
    assert "create policy" in ddl


def test_migration_scopes_to_company_with_no_default():
    ddl = _ddl()
    assert "company_id         uuid        not null references companies (id) on delete cascade" in ddl


def test_migration_gates_mode_and_status_closed_vocabularies():
    ddl = _ddl()
    assert "mode               text        not null check (mode in ('dry_run', 'apply'))" in ddl
    assert (
        "status             text        not null default 'running'" in ddl
    )
    assert "check (status in ('running', 'completed', 'failed'))" in ddl


def test_migration_rls_is_spelled_to_service_role_only():
    """Omitting `to service_role` on a policy defaults it to PUBLIC — the
    exact defect class `20260819100000_crucible_core.sql`'s own header
    warns about. Every policy in this file must spell it out."""
    ddl = _ddl()
    assert "for all to service_role using (true) with check (true)" in ddl
    assert "to public" not in ddl
