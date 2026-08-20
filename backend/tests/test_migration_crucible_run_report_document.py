"""Structural check on the report-document migration.

Migrations are Apurva-gated — `schema_migrations`' primary key is the timestamp
alone, so a bad file blocks every backend deploy. This pins the two decisions
in this one that a later edit could quietly reverse, both of which fail in ways
nothing downstream would notice:

  * `on delete set null`, NOT cascade. The run is the immutable record and the
    document merely describes it, so deleting a document from the shared
    library must never take a multi-minute run's claims, findings, ledger and
    calibration history with it. Under cascade that is one click in the Others
    section, and it is silent.
  * `report_body_hash` exists and is NOT a boolean. Detachment is DERIVED from
    the stored body rather than declared by a flag, because the hand edit
    arrives through the generic custom-artifacts PATCH — a route that knows
    nothing about Goal Analysis. A flag would need every writer of that
    endpoint to remember to set it, and forgetting means a regeneration
    silently discarding somebody's edits.

No DB is touched; this reads the SQL file.
"""
from __future__ import annotations

import re
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations"
    / "20260820120000_crucible_run_report_document.sql"
)


def _ddl() -> str:
    """DDL only. The header explains at length WHY this is not a boolean flag,
    so a naive substring search for "boolean" finds the prose arguing against
    it and fails."""
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION.name}"
    out = []
    for line in MIGRATION.read_text(encoding="utf-8").lower().splitlines():
        code = line.split("--", 1)[0]
        if code.strip():
            out.append(code)
    return "\n".join(out)


def test_the_document_link_is_additive_and_nullable():
    """`add column if not exists`, so a re-run is a no-op and an old binary
    keeps working against the new schema. No table is created and no existing
    column is altered."""
    ddl = _ddl()
    assert "add column if not exists artifact_id" in ddl
    assert "add column if not exists report_body_hash" in ddl
    assert "create table" not in ddl
    assert "alter column" not in ddl
    # Both columns mean "not yet", so neither may be NOT NULL. Checked on the
    # `add column` statements alone — the partial index below is legitimately
    # `where artifact_id is not null`, and a whole-file substring search would
    # fail on the index instead of on a real constraint.
    for stmt in re.findall(r"add column if not exists [^;]*", ddl):
        assert "not null" not in stmt, stmt


def test_deleting_the_document_must_not_delete_the_run():
    """ON DELETE SET NULL is the whole reason the report can be a separate
    row. Cascade here would make an everyday editorial act destroy an
    analysis."""
    ddl = _ddl()
    fk = re.search(r"references\s+custom_artifacts\s*\(\s*id\s*\)[^;]*", ddl)
    assert fk, "artifact_id must reference custom_artifacts (id)"
    assert "on delete set null" in fk.group(0)
    assert "cascade" not in fk.group(0)


def test_the_detach_marker_is_a_hash_and_not_a_flag():
    ddl = _ddl()
    assert re.search(r"report_body_hash\s+text", ddl), (
        "report_body_hash must be text (a sha256 hex digest), never a boolean "
        "— see the module docstring for why it is derived, not declared"
    )


def test_the_reverse_lookup_is_indexed():
    """Given a document, which run does it belong to? That is what the chat
    edit tool asks on every call to resolve its target."""
    assert re.search(
        r"create index if not exists \w+ *\n? *on crucible_runs \(artifact_id\)",
        _ddl(),
    ), "artifact_id needs an index for the document -> run lookup"


def test_the_timestamp_is_newer_than_the_core_crucible_migration():
    """A duplicate or out-of-order timestamp blocks EVERY backend deploy — the
    primary key on `schema_migrations` is the timestamp alone."""
    core = MIGRATION.parent / "20260819100000_crucible_core.sql"
    assert core.is_file()
    assert MIGRATION.name > core.name
