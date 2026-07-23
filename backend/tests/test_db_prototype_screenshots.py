"""Tests for the `prototype_screenshots` DB helpers + migration.

Sibling to `test_db_prototype_comments.py` (mirrors its SQLite-fake-DDL
convention, kept separate so this ticket's diff stays focused). Covers
`insert_screenshots`, `list_screenshot_keys`, `resolve_screenshot_keys`, the
new migration's idempotency shape, and workspace isolation.

Runs fully in isolation against the in-memory FakeSupabaseClient (no live
Supabase). We reuse conftest's `isolated_settings` fixture for env + module
reload + fake-client wiring, then add the `prototype_screenshots` table to the
already-seeded in-memory DB so we never touch the shared test scaffolding. The
migration's idempotency is verified at the SQL-string level (same convention
as the sibling tests — no live Postgres in this dev env).
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

# SQLite-compatible end-state of `prototype_screenshots`. Postgres-only
# constructs (bigint identity, timestamptz, RLS, the FK reference) are
# translated/omitted the same way the sibling test DDLs do — the fake
# exercises SQL semantics, not Postgres DDL. No FK to `prototypes` here: the
# fake schema for this file doesn't seed a `prototypes` table either (mirrors
# test_db_prototype_comments.py's convention exactly).
_SCREENSHOTS_DDL = """
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
    / "supabase" / "migrations" / "20260722150000_prototype_screenshots.sql"
)


@pytest.fixture
def screenshots(isolated_settings, monkeypatch):
    """The reloaded app.db.prototype_screenshots module wired to the fake
    Supabase, with the prototype_screenshots table present in the in-memory DB."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_SCREENSHOTS_DDL)

    import app.db.prototype_screenshots as screenshots_mod
    importlib.reload(screenshots_mod)  # rebind require_client from the reloaded client
    return screenshots_mod


# ─── Migration file (string-level — isolation-friendly, no live Postgres) ──


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
    assert _MIGRATION_PATH.exists()
    assert _MIGRATION_PATH.name == "20260722150000_prototype_screenshots.sql"


def test_prototype_screenshots_migration_is_idempotent():
    # AC19 — structural idempotency (apply-twice verified at the SQL-string
    # level; a live-Postgres apply-twice is deferred to a phase smoke, same
    # convention as the sibling migration tests).
    sql = _migration_sql_only()
    for m in re.finditer(r"create\s+table\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent CREATE TABLE near offset {m.start()}")
    for m in re.finditer(r"create\s+index\s+(?!if\s+not\s+exists)", sql):
        pytest.fail(f"non-idempotent CREATE INDEX near offset {m.start()}")


def test_prototype_screenshots_migration_has_no_default_on_workspace_id():
    # AC19 / Rule #20 — workspace_id TEXT NOT NULL with NO DEFAULT.
    sql = _migration_sql_only()
    assert re.search(r"workspace_id\s+text\s+not\s+null", sql), "workspace_id column missing"
    assert not re.search(r"workspace_id\s+text\s+not\s+null\s+default", sql), \
        "workspace_id must NOT carry a DEFAULT (Rule #20)"


def test_migration_uses_rls_no_policies():
    sql = _migration_sql_only()
    assert sql.count("enable row level security") == 1
    assert "create policy" not in sql


def test_migration_prototypes_untouched():
    # This migration is purely additive: no ALTER on the existing `prototypes`
    # table, no DROP, no destructive statement anywhere in the file.
    sql = _migration_sql_only()
    assert "alter table prototypes" not in sql
    for forbidden in ("drop table", "drop column", "delete from", "update "):
        assert forbidden not in sql


# ─── Creation / persistence (insert_screenshots / list_screenshot_keys) ────


def test_insert_screenshots_persists_all_keys_in_position_order(screenshots):
    # AC4 — 3 keys in -> 3 rows out, position 0/1/2 matching submitted order.
    screenshots.insert_screenshots(
        prototype_id=1, workspace_id="app",
        storage_keys=["uploads/app/a.png", "uploads/app/b.png", "uploads/app/c.png"],
    )
    keys = screenshots.list_screenshot_keys(prototype_id=1, workspace_id="app")
    assert keys == ["uploads/app/a.png", "uploads/app/b.png", "uploads/app/c.png"]


def test_insert_screenshots_empty_list_is_a_noop(screenshots):
    # Edge case — storage_keys=[] inserts zero rows, no exception.
    screenshots.insert_screenshots(prototype_id=1, workspace_id="app", storage_keys=[])
    assert screenshots.list_screenshot_keys(prototype_id=1, workspace_id="app") == []


def test_list_screenshot_keys_returns_position_ordered(screenshots):
    # AC4 — rows inserted out of a contrived DB order still return in
    # position order (insert a batch, then a second batch with lower
    # positions is not how this API is used — instead we directly assert the
    # ordering column, not insertion order, drives the read).
    from tests import _fake_supabase

    db = _fake_supabase.get_fake_db()
    db.execute(
        "INSERT INTO prototype_screenshots (prototype_id, workspace_id, storage_key, position) "
        "VALUES (?, ?, ?, ?)",
        (2, "app", "uploads/app/third.png", 2),
    )
    db.execute(
        "INSERT INTO prototype_screenshots (prototype_id, workspace_id, storage_key, position) "
        "VALUES (?, ?, ?, ?)",
        (2, "app", "uploads/app/first.png", 0),
    )
    db.execute(
        "INSERT INTO prototype_screenshots (prototype_id, workspace_id, storage_key, position) "
        "VALUES (?, ?, ?, ?)",
        (2, "app", "uploads/app/second.png", 1),
    )
    db.commit()
    keys = screenshots.list_screenshot_keys(prototype_id=2, workspace_id="app")
    assert keys == ["uploads/app/first.png", "uploads/app/second.png", "uploads/app/third.png"]


def test_list_screenshot_keys_missing_prototype_returns_empty(screenshots):
    # Edge case — no rows for the id -> [], no exception.
    assert screenshots.list_screenshot_keys(prototype_id=999, workspace_id="app") == []


# ─── Workspace isolation (required per surface table) ──────────────────────


def test_list_screenshot_keys_workspace_isolation_round_trip(screenshots):
    # AC18 — rows inserted under workspace_a for prototype_id=1 are invisible
    # to list_screenshot_keys(prototype_id=1, workspace_id="workspace_b"),
    # even though the prototype_id matches.
    screenshots.insert_screenshots(
        prototype_id=1, workspace_id="workspace_a",
        storage_keys=["uploads/workspace_a/x.png"],
    )
    assert screenshots.list_screenshot_keys(prototype_id=1, workspace_id="workspace_b") == []
    assert screenshots.list_screenshot_keys(prototype_id=1, workspace_id="workspace_a") == [
        "uploads/workspace_a/x.png"
    ]


# ─── Legacy fallback resolution (resolve_screenshot_keys) ──────────────────


def test_resolve_screenshot_keys_prefers_join_table_over_legacy(screenshots):
    # AC16 — a prototype with 2 join-table rows AND a non-null legacy
    # screenshot_key resolves to the 2 join-table keys only.
    screenshots.insert_screenshots(
        prototype_id=3, workspace_id="app",
        storage_keys=["uploads/app/one.png", "uploads/app/two.png"],
    )
    resolved = screenshots.resolve_screenshot_keys(
        prototype_id=3, workspace_id="app",
        legacy_screenshot_key="uploads/app/legacy.png",
    )
    assert resolved == ["uploads/app/one.png", "uploads/app/two.png"]


def test_resolve_screenshot_keys_falls_back_to_legacy_when_join_table_empty(screenshots):
    # AC15 — zero join-table rows + a non-null legacy key resolves to a
    # 1-item list containing exactly that key.
    resolved = screenshots.resolve_screenshot_keys(
        prototype_id=4, workspace_id="app",
        legacy_screenshot_key="uploads/app/legacy-only.png",
    )
    assert resolved == ["uploads/app/legacy-only.png"]


def test_resolve_screenshot_keys_returns_empty_with_neither(screenshots):
    # Edge case — no join-table rows and a null legacy key -> [].
    resolved = screenshots.resolve_screenshot_keys(
        prototype_id=5, workspace_id="app", legacy_screenshot_key=None,
    )
    assert resolved == []
