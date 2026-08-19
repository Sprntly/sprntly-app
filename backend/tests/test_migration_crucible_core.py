"""Structural check on the crucible_core migration.

Migrations are Apurva-gated — `schema_migrations`' primary key is the timestamp
alone, so a bad file blocks every backend deploy (see
tests/test_migrations_hygiene.py). This pins the shape PR4-PR9 will read and
write, so an edit that drops a column, a constraint or an index fails here
rather than in production.

The three things worth failing over are the ones the schema enforces that the
Python types cannot, because a backfill or a psql session does not go through
them:

  * I9 — a goal definition cannot be `locked` without a recorded human
    confirmation, and there is no `inferred` origin;
  * I3 — every measured quantity is nullable with NO default, so "not measured"
    survives storage instead of becoming a confident zero;
  * RLS carries the `TO service_role` clause, without which the policy defaults
    to PUBLIC — including `anon`, whose key ships in the web bundle.

No DB is touched; this reads the SQL file.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260819100000_crucible_core.sql"
)

TABLES = (
    "crucible_goal_definitions",
    "crucible_runs",
    "crucible_claims",
    "crucible_findings",
    "crucible_ledger",
    "crucible_predictions",
)


def _sql() -> str:
    assert MIGRATION.is_file(), f"missing migration: {MIGRATION.name}"
    return MIGRATION.read_text(encoding="utf-8").lower()


def _sql_without_comments() -> str:
    """DDL only. The header explains at length that a definition is never
    `inferred`, so a naive substring check for that word finds the prose that
    forbids it and fails."""
    out = []
    for line in _sql().splitlines():
        code = line.split("--", 1)[0]
        if code.strip():
            out.append(code)
    return "\n".join(out)


@pytest.mark.parametrize("table", TABLES)
def test_every_table_is_idempotent_and_company_scoped(table):
    sql = _sql()
    assert f"create table if not exists {table}" in sql
    # Denormalised company_id on the children too: a tenant filter that depends
    # on a join is one bad query away from crossing tenants.
    section = sql.split(f"create table if not exists {table}", 1)[1].split(");", 1)[0]
    assert "company_id" in section, f"{table} is not company-scoped"
    assert "references companies (id) on delete cascade" in section


@pytest.mark.parametrize("table", TABLES)
def test_rls_is_enabled_with_an_explicit_service_role_clause(table):
    """Omitting `TO service_role` defaults the policy to PUBLIC — every role,
    including anon, whose key is public and inlined into the web bundle. That
    is the Class B defect 20260812170000 exists to close fleet-wide."""
    sql = _sql()
    assert f"alter table {table} enable row level security" in sql
    assert re.search(
        rf'create policy "srv_{table}" on {table}\s+for all to service_role', sql
    ), f"{table}'s policy is missing the TO service_role clause"


def test_i9_locked_goal_definition_requires_human_confirmation():
    """The one invariant worth a CHECK constraint: a wrong goal definition
    produces a fully coherent answer to the wrong question, and nothing
    downstream can detect it."""
    sql = _sql()
    assert "constraint crucible_goal_locked_needs_confirmation" in sql
    constraint = sql.split("crucible_goal_locked_needs_confirmation", 1)[1][:400]
    assert "status <> 'locked'" in constraint
    assert "confirmed_by_user_at is not null" in constraint
    assert "confirmed_by_user_id is not null" in constraint
    assert "origin in ('adopted', 'elicited')" in constraint


def test_i9_there_is_no_inferred_goal_origin():
    """The absence is the invariant. If a future edit adds it, this fails and
    the reviewer has to argue for it explicitly.

    SCOPED TO THE ORIGIN CHECK, not the whole file. `inferred` is also a
    perfectly legitimate EVIDENCE STRENGTH on `crucible_claims` — the spec's
    ladder is causally_tested / measured / correlated / inferred / reported.
    Two different words that happen to be spelled the same: a claim may be
    inferred, a goal definition may never be.
    """
    ddl = _sql_without_comments()
    assert "check (origin in ('adopted', 'elicited'))" in ddl

    origin_check = re.search(r"check \(origin in \([^)]*\)\)", ddl)
    assert origin_check, "no origin CHECK constraint found"
    assert "inferred" not in origin_check.group(0)

    # ...and the strength ladder still carries it, so this test cannot be
    # "fixed" by deleting the wrong one.
    assert "'inferred', 'reported'" in ddl


@pytest.mark.parametrize("column", [
    "impact_value", "population_value", "magnitude",
    "predicted_value", "range_low", "range_high", "confidence_score",
])
def test_i3_measured_quantities_are_nullable_with_no_default(column):
    """A `default 0` on any of these silently turns an unmeasured segment into
    a worthless one, which is the failure I3 exists to prevent — and it is
    silent, because the arithmetic keeps working."""
    sql = _sql()
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith(column + " ") or stripped.startswith(column + "\t"):
            assert "not null" not in stripped, f"{column} must stay nullable"
            assert "default" not in stripped, f"{column} must have no default"
            return
    pytest.fail(f"{column} not found in the migration")


def test_i8_assumed_params_defaults_to_empty_not_null():
    """A renderer must not be able to mistake "no assumptions recorded" for
    "none made" and skip the disclosure."""
    sql = _sql()
    assert "assumed_params  jsonb       not null default '[]'::jsonb" in sql


def test_run_status_covers_both_human_gates():
    sql = _sql()
    for state in (
        "draft", "resolving_goal", "awaiting_confirmation", "planning",
        "awaiting_approval", "running", "ready", "failed", "cancelled",
    ):
        assert f"'{state}'" in sql, f"run status is missing {state!r}"


def test_claim_and_strength_vocabularies_match_the_types_module():
    """The CHECK constraints and `app.crucible.types` must not drift — a claim
    the code can build but the database rejects fails at write time, deep
    inside a run."""
    # PR2 lands BEFORE PR1's module, deliberately: the schema goes in first so
    # every later PR is a pure application change, and each PR in the sequence
    # has to be independently mergeable. Once `app.crucible` exists this starts
    # enforcing; until then there is nothing to drift from.
    types = pytest.importorskip(
        "app.crucible.types",
        reason="app.crucible ships in PR1 (#1229); this check arms when it lands",
    )
    CLAIM_TYPES, EVIDENCE_STRENGTHS = types.CLAIM_TYPES, types.EVIDENCE_STRENGTHS

    sql = _sql()
    for value in CLAIM_TYPES:
        assert f"'{value}'" in sql, f"claim type {value!r} missing from the CHECK"
    for value in EVIDENCE_STRENGTHS:
        assert f"'{value}'" in sql, f"strength {value!r} missing from the CHECK"


def test_observed_at_is_required():
    """A claim with no date cannot be aged, and defaulting it to now() would
    make stale evidence look fresh."""
    sql = _sql()
    assert "observed_at        timestamptz not null," in sql
    assert "observed_at        timestamptz not null default" not in sql


def test_raw_payload_is_retained_on_every_claim():
    """Spec acceptance criterion 6. A disputed finding has to be traceable to
    what the source actually said."""
    assert "raw                jsonb       not null default '{}'::jsonb" in _sql()


def test_the_orphan_sweep_has_a_partial_index():
    """The sweep reads unfinished runs with a quiet heartbeat; a full index
    would grow with every finished run forever."""
    sql = _sql()
    assert "crucible_runs_inflight_idx" in sql
    assert "where status in ('resolving_goal', 'planning', 'running')" in sql


def test_conversations_gains_crucible_mode_off_by_default():
    """Every existing conversation must be off with no backfill, and the column
    is what makes a message bypass the intent envelope."""
    sql = _sql()
    assert "add column if not exists crucible_mode boolean not null default false" in sql


def test_findings_sort_puts_unsizeable_last_without_calling_them_zero():
    sql = _sql()
    assert "impact_value desc nulls last" in sql


def test_migration_timestamp_is_unique_and_newest():
    """A duplicate timestamp blocks EVERY backend deploy from the branch —
    schema_migrations' primary key is the timestamp alone. Cheapest possible
    check, and this repo has been bitten twice."""
    migrations = sorted(MIGRATION.parent.glob("*.sql"))
    stamps = [p.name.split("_", 1)[0] for p in migrations]
    assert stamps.count("20260819100000") == 1, "timestamp collision"
    assert max(stamps) == "20260819100000", (
        "this migration must be the newest; an out-of-order file applies after "
        "migrations that already ran and can fail on a fresh database"
    )
