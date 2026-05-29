"""Tests for the `prototypes` + `prototype_checkpoints` DB helpers (P1-06).

Mirrors the round-trip / invalidation / workspace-isolation coverage the ticket
specifies. Runs fully in isolation against the in-memory FakeSupabaseClient — no
live Supabase required (the P1 dev env is mid-migration; integration is deferred
to the P1-11 smoke). We reuse conftest's `isolated_settings` fixture for env +
module-reload + fake-client wiring, then add the two new tables to the already
seeded in-memory DB so we never touch the shared test scaffolding
(`conftest.py` / `_fake_supabase.py`).
"""
from __future__ import annotations

import importlib
import logging
import re
from pathlib import Path

import pytest

# SQLite-compatible translation of supabase/migrations/20260528000000_design_agent_prototypes.sql.
# Postgres-only constructs (bigint identity, timestamptz, jsonb, RLS, the FK
# alter) are translated/omitted the same way conftest._FAKE_SCHEMA does for the
# existing tables — the fake exercises SQL semantics, not Postgres-specific DDL.
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
    completed_at           TEXT
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

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "supabase" / "migrations" / "20260528000000_design_agent_prototypes.sql"
)


@pytest.fixture
def proto(isolated_settings, monkeypatch):
    """The reloaded app.db.prototypes module wired to the fake Supabase, with the
    two new tables present in the in-memory DB and their jsonb columns registered.
    """
    from tests import _fake_supabase

    # Add the new tables on top of conftest's already-reset fake schema.
    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    # The fake only json-encodes columns it knows about; register ours so
    # prompt_history / comment_state round-trip as real lists.
    monkeypatch.setitem(
        _fake_supabase._JSONB_COLUMNS, "prototype_checkpoints",
        {"prompt_history", "comment_state"},
    )

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)  # rebind require_client/utc_now from the reloaded client
    return proto_mod


# ─── Migration file content (string-level — isolation-friendly) ──────────


def _migration_raw() -> str:
    return _MIGRATION_PATH.read_text()


def _migration_sql_only() -> str:
    """Migration content with `--` line comments stripped, lowercased."""
    lines = []
    for line in _migration_raw().splitlines():
        code = line.split("--", 1)[0]
        lines.append(code)
    return "\n".join(lines).lower()


def test_migration_file_exists_and_is_dated_correctly():
    # AC #11 — dated 20260528000000 under supabase/migrations/.
    assert _MIGRATION_PATH.exists()
    assert _MIGRATION_PATH.name == "20260528000000_design_agent_prototypes.sql"


def test_migration_applies_idempotently():
    # AC #1 — structural idempotency check. A temp-pg "apply twice" fixture is
    # deferred to the P1-11 smoke (no live Postgres in the P1 dev env); here we
    # verify idempotency *by construction*: every DDL statement guards itself.
    sql = _migration_sql_only()
    # Every CREATE TABLE / CREATE INDEX must be IF NOT EXISTS.
    for m in re.finditer(r"create\s+table\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent CREATE TABLE near offset {m.start()}")
    for m in re.finditer(r"create\s+index\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent CREATE INDEX near offset {m.start()}")
    # The added FK constraint must be preceded by a DROP CONSTRAINT IF EXISTS.
    assert "add constraint prototypes_current_checkpoint_id_fkey" in sql
    assert "drop constraint if exists prototypes_current_checkpoint_id_fkey" in sql
    assert sql.index("drop constraint if exists prototypes_current_checkpoint_id_fkey") < sql.index(
        "add constraint prototypes_current_checkpoint_id_fkey"
    )


def test_workspace_id_no_default_in_migration():
    # AC #2 — both tables: workspace_id TEXT NOT NULL with NO DEFAULT.
    sql = _migration_sql_only()
    assert re.search(r"workspace_id\s+text\s+not\s+null", sql), "workspace_id column missing"
    assert not re.search(r"workspace_id\s+text\s+not\s+null\s+default", sql), \
        "workspace_id must NOT carry a DEFAULT (Rule #20)"


def test_no_scenario_column_in_migration():
    # AC #13 — scenario is derived, never a column. Check the SQL (comments
    # stripped — they legitimately discuss the scenario model).
    sql = _migration_sql_only()
    assert not re.search(r"\bscenario\b", sql), "no `scenario` column may exist on prototypes"


def test_migration_uses_rls_no_policies():
    # AC #14 conformance — enable row level security on both tables, no policies.
    sql = _migration_sql_only()
    assert sql.count("enable row level security") == 2
    assert "create policy" not in sql


# ─── Creation ────────────────────────────────────────────────────────────


def test_start_prototype_returns_int_id(proto):
    row_id = proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    assert isinstance(row_id, int)
    assert row_id > 0


def test_start_prototype_sets_status_generating(proto):
    row_id = proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    row = proto.get_prototype(prototype_id=row_id, workspace_id="app")
    assert row["status"] == "generating"


# ─── Serialization / retrieval ─────────────────────────────────────────────


def test_get_prototype_round_trip(proto):
    row_id = proto.start_prototype(
        prd_id=7, workspace_id="app", template_version=3,
        variant="v1", instructions="make it pop", target_platform="mobile",
        figma_file_key="FIG123",
    )
    row = proto.get_prototype(prototype_id=row_id, workspace_id="app")
    assert row["id"] == row_id
    assert row["prd_id"] == 7
    assert row["template_version"] == 3
    assert row["target_platform"] == "mobile"
    assert row["figma_file_key"] == "FIG123"
    assert row["instructions"] == "make it pop"


def test_find_existing_prototype_returns_most_recent(proto):
    proto.start_prototype(prd_id=5, workspace_id="app", template_version=1)
    proto.start_prototype(prd_id=5, workspace_id="app", template_version=1)
    last = proto.start_prototype(prd_id=5, workspace_id="app", template_version=1)
    found = proto.find_existing_prototype(prd_id=5, workspace_id="app", template_version=1)
    assert found is not None
    assert found["id"] == last  # highest id wins


def test_find_existing_prototype_filters_by_template_version(proto):
    proto.start_prototype(prd_id=5, workspace_id="app", template_version=1)
    # Querying for a newer template_version must not match the older row.
    assert proto.find_existing_prototype(prd_id=5, workspace_id="app", template_version=2) is None


def test_create_checkpoint_round_trip(proto):
    pid = proto.start_prototype(prd_id=5, workspace_id="app", template_version=1)
    cid = proto.create_checkpoint(
        prototype_id=pid, workspace_id="app",
        bundle_url="https://cdn/x.zip",
        prd_revision_hash="abc", figma_frame_hash="def",
        prompt_history=[{"role": "user", "text": "hi"}],
    )
    assert isinstance(cid, int) and cid > 0
    from tests import _fake_supabase
    rows = _fake_supabase.get_fake_db().execute(
        "SELECT * FROM prototype_checkpoints WHERE id = ?", [cid]
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["prototype_id"] == pid
    assert rows[0]["workspace_id"] == "app"


# ─── Workspace isolation (Rules #20-#23) ───────────────────────────────────


def test_workspace_isolation_round_trip(proto):
    proto.start_prototype(prd_id=9, workspace_id="app", template_version=1)
    # The insert under 'app' is invisible to a query under 'demo'.
    assert proto.find_existing_prototype(prd_id=9, workspace_id="demo", template_version=1) is None


def test_get_prototype_blocks_cross_workspace(proto):
    pid = proto.start_prototype(prd_id=9, workspace_id="app", template_version=1)
    assert proto.get_prototype(prototype_id=pid, workspace_id="demo") is None
    assert proto.get_prototype(prototype_id=pid, workspace_id="app") is not None


def test_complete_prototype_workspace_mismatch_no_update(proto):
    pid = proto.start_prototype(prd_id=9, workspace_id="app", template_version=1)
    # Completing under the wrong workspace must not touch the row.
    proto.complete_prototype(prototype_id=pid, workspace_id="demo", bundle_url="https://x")
    row = proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "generating"
    assert row["bundle_url"] is None


def test_invalidate_orphan_operates_across_all_workspaces(proto):
    a = proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    d = proto.start_prototype(prd_id=2, workspace_id="demo", template_version=1)
    count = proto.invalidate_orphan_generating_prototypes()
    assert count == 2
    assert proto.get_prototype(prototype_id=a, workspace_id="app")["status"] == "failed"
    assert proto.get_prototype(prototype_id=d, workspace_id="demo")["status"] == "failed"


# ─── Update paths ──────────────────────────────────────────────────────────


def test_complete_prototype_sets_ready_and_bundle(proto):
    # AC #4
    pid = proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    proto.complete_prototype(prototype_id=pid, workspace_id="app", bundle_url="https://x.zip")
    row = proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "ready"
    assert row["bundle_url"] == "https://x.zip"
    assert row["completed_at"] is not None


def test_complete_prototype_for_unknown_id_no_op(proto):
    # No matching row → no exception, nothing updated.
    proto.complete_prototype(prototype_id=999999, workspace_id="app", bundle_url="https://x")
    assert proto.get_prototype(prototype_id=999999, workspace_id="app") is None


# ─── Error handling ────────────────────────────────────────────────────────


def test_fail_prototype_sets_failed_and_message(proto):
    # AC #5
    pid = proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    proto.fail_prototype(prototype_id=pid, workspace_id="app", error="ValueError: boom")
    row = proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["status"] == "failed"
    assert row["error"] == "ValueError: boom"


def test_fail_prototype_truncates_error_at_500_chars(proto):
    pid = proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    proto.fail_prototype(prototype_id=pid, workspace_id="app", error="x" * 1000)
    row = proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert len(row["error"]) == 500


# ─── Invalidation ──────────────────────────────────────────────────────────


def test_invalidate_stale_filters_by_variant(proto):
    pid_v2 = proto.start_prototype(prd_id=1, workspace_id="app", template_version=1, variant="v2")
    proto.complete_prototype(prototype_id=pid_v2, workspace_id="app", bundle_url="https://x")
    # Demote v1 stale rows only; the v2 row must be untouched.
    proto.invalidate_stale_prototypes(current_version=2, variant="v1")
    assert proto.get_prototype(prototype_id=pid_v2, workspace_id="app")["status"] == "ready"


def test_invalidate_stale_demotes_old_ready_rows(proto):
    # AC #10 — ready row at template_version=1 → invalidated when current=2.
    pid = proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    proto.complete_prototype(prototype_id=pid, workspace_id="app", bundle_url="https://x")
    count = proto.invalidate_stale_prototypes(current_version=2, variant="v1")
    assert count == 1
    assert proto.get_prototype(prototype_id=pid, workspace_id="app")["status"] == "invalidated"


def test_invalidate_stale_leaves_current_version(proto):
    # AC #10 — a ready row already at the current version is not touched.
    pid = proto.start_prototype(prd_id=1, workspace_id="app", template_version=2)
    proto.complete_prototype(prototype_id=pid, workspace_id="app", bundle_url="https://x")
    proto.invalidate_stale_prototypes(current_version=2, variant="v1")
    assert proto.get_prototype(prototype_id=pid, workspace_id="app")["status"] == "ready"


def test_invalidate_stale_only_demotes_ready_rows(proto):
    # A 'generating' row with an old template_version is the orphan helper's job,
    # not the stale helper's — stale must leave it alone.
    pid = proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    proto.invalidate_stale_prototypes(current_version=2, variant="v1")
    assert proto.get_prototype(prototype_id=pid, workspace_id="app")["status"] == "generating"


# ─── Edge cases ────────────────────────────────────────────────────────────


def test_start_prototype_with_null_figma_file_key(proto):
    pid = proto.start_prototype(prd_id=1, workspace_id="app", template_version=1, figma_file_key=None)
    row = proto.get_prototype(prototype_id=pid, workspace_id="app")
    assert row["figma_file_key"] is None


def test_create_checkpoint_default_comment_state_empty_list(proto):
    pid = proto.start_prototype(prd_id=1, workspace_id="app", template_version=1)
    cid = proto.create_checkpoint(
        prototype_id=pid, workspace_id="app", bundle_url=None,
        prd_revision_hash=None, figma_frame_hash=None, prompt_history=[],
    )
    from tests import _fake_supabase
    row = _fake_supabase.get_fake_db().execute(
        "SELECT comment_state FROM prototype_checkpoints WHERE id = ?", [cid]
    ).fetchone()
    # comment_state is registered jsonb but we read raw here; default is '[]'.
    assert row["comment_state"] == "[]"


def test_logger_records_identifiers_only(proto, caplog):
    # AC #12 / Rule #24 — no PII / secret input values in logs across a full
    # start → complete cycle. Only ids + derived scenario labels.
    secret_instructions = "TOP_SECRET_INSTRUCTIONS_VALUE"
    secret_figma = "SECRET_FIGMA_FILE_KEY_VALUE"
    with caplog.at_level(logging.INFO, logger="app.db.prototypes"):
        pid = proto.start_prototype(
            prd_id=1, workspace_id="app", template_version=1,
            instructions=secret_instructions, figma_file_key=secret_figma,
        )
        proto.complete_prototype(prototype_id=pid, workspace_id="app", bundle_url="https://x")
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "prototype_created" in blob
    assert "prototype_completed" in blob
    assert secret_instructions not in blob
    assert secret_figma not in blob


# ─── Scenario inference (pure helper — no DB hit) ──────────────────────────


def test_infer_scenario_from_inputs_a_only(proto):
    assert proto.infer_scenario_from_inputs(
        figma_file_key="abc", website_url=None,
        github_installation_id=None, prd_references_codebase=False,
    ) == frozenset({"A"})


def test_infer_scenario_from_inputs_b_only(proto):
    assert proto.infer_scenario_from_inputs(
        figma_file_key=None, website_url="https://x",
        github_installation_id=None, prd_references_codebase=False,
    ) == frozenset({"B"})


def test_infer_scenario_from_inputs_b_suppressed_by_figma(proto):
    assert proto.infer_scenario_from_inputs(
        figma_file_key="abc", website_url="https://x",
        github_installation_id=None, prd_references_codebase=False,
    ) == frozenset({"A"})


def test_infer_scenario_from_inputs_c_requires_prd_reference(proto):
    without = proto.infer_scenario_from_inputs(
        figma_file_key=None, website_url=None,
        github_installation_id=42, prd_references_codebase=False,
    )
    assert "C" not in without
    with_ref = proto.infer_scenario_from_inputs(
        figma_file_key=None, website_url=None,
        github_installation_id=42, prd_references_codebase=True,
    )
    assert "C" in with_ref


def test_infer_scenario_from_inputs_a_plus_c_additive(proto):
    assert proto.infer_scenario_from_inputs(
        figma_file_key="abc", website_url=None,
        github_installation_id=42, prd_references_codebase=True,
    ) == frozenset({"A", "C"})


def test_infer_scenario_from_inputs_zero_when_no_inputs(proto):
    assert proto.infer_scenario_from_inputs(
        figma_file_key=None, website_url=None,
        github_installation_id=None, prd_references_codebase=False,
    ) == frozenset({"0"})


def test_infer_scenario_with_prd_none_defaults_c_off(proto):
    result = proto.infer_scenario({"github_installation_id": 42}, None)
    assert "C" not in result
