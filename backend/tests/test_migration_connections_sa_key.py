"""Structural check on the connections.sa_key_encrypted migration.

Column-add migrations run against a table that already has real rows (every
connection ever made), so the statement MUST be idempotent — a second
`supabase db push` re-running it (e.g. after a partial deploy) must not fail
with "column already exists". No DB is touched; this reads the SQL file.
"""
from __future__ import annotations

from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260807120000_connections_sa_key.sql"
)


def _sql() -> str:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION.name}"
    return MIGRATION.read_text(encoding="utf-8").lower()


def test_migration_adds_sa_key_encrypted_idempotently():
    sql = _sql()
    assert "alter table connections" in sql
    assert "add column if not exists" in sql
    assert "sa_key_encrypted" in sql
    # Nullable, no default: existing OAuth-only connections have no SA key,
    # and this must not fail/behave surprisingly against pre-existing rows.
    assert "not null" not in sql
    assert "default" not in sql


def test_migration_never_touches_the_oauth_token_column():
    # The whole point of a dedicated column is that it coexists with, and
    # never migrates data out of, the existing OAuth credential column — the
    # only executable statement is the one ADD COLUMN, whatever the
    # surrounding comment says.
    statements = [
        line.strip()
        for line in _sql().splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    assert statements == [
        "alter table connections add column if not exists sa_key_encrypted text;"
    ]
