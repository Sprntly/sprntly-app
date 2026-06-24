"""Tests for the standalone migration runner (scripts/apply_migrations.py).

The runner speaks DB-API 2.0, so we exercise it against an in-memory SQLite
database injected via the connection factory — no Postgres, no network. SQLite
understands the qmark param style and `CREATE TABLE IF NOT EXISTS`, which is all
the runner needs from the DB for these contracts.

Covers the four behaviours the runner promises:
  1. applies a pending migration (and records it),
  2. skips an already-tracked migration,
  3. fails LOUD (raises / non-zero) on bad SQL and applies nothing past it,
  4. is idempotent on re-run,
plus the first-run backfill that baselines a drifted DB.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

# Load scripts/apply_migrations.py by path — it lives outside the backend
# package, so a normal import won't find it.
_RUNNER_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "apply_migrations.py"
)
_spec = importlib.util.spec_from_file_location("apply_migrations", _RUNNER_PATH)
assert _spec and _spec.loader
am = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(am)


# --- helpers ---------------------------------------------------------------


def _write_migration(d: Path, name: str, sql: str) -> None:
    (d / name).write_text(sql)


def _sqlite_factory(conn: sqlite3.Connection):
    """A connection factory that always hands back the given sqlite conn."""

    def factory(_db_url: str) -> sqlite3.Connection:
        return conn

    return factory


def _apply(conn: sqlite3.Connection, migrations_dir: Path, **kwargs):
    migrations = am.discover_migrations(migrations_dir)
    return am.apply_migrations(conn, migrations, paramstyle="qmark", **kwargs)


def _tracked(conn: sqlite3.Connection) -> set[str]:
    cur = conn.cursor()
    cur.execute(
        f"SELECT version FROM {am.TRACKING_QUALIFIED.replace('.', '_')}"
    )
    rows = {r[0] for r in cur.fetchall()}
    cur.close()
    return rows


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    )
    found = cur.fetchone() is not None
    cur.close()
    return found


@pytest.fixture()
def conn(monkeypatch):
    """In-memory SQLite with the dotted tracking name flattened.

    SQLite has no schemas, so `supabase_migrations.schema_migrations` would be
    read as schema.table. We monkeypatch the qualified name to a flat
    underscore name for the duration of the test.
    """
    flat = "supabase_migrations_schema_migrations"
    monkeypatch.setattr(am, "TRACKING_QUALIFIED", flat)
    # The real _ensure_tracking_table runs `CREATE SCHEMA` + Postgres-only
    # types, which sqlite rejects — the `patched_ensure` fixture swaps in a
    # sqlite-friendly equivalent for every test that creates the table.
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


@pytest.fixture()
def patched_ensure(monkeypatch, conn):
    """Replace _ensure_tracking_table with a sqlite-friendly version.

    The production version issues `CREATE SCHEMA` + `timestamptz`/`now()`,
    which sqlite doesn't support. We swap in an equivalent that creates the
    flat table with sqlite-compatible types.
    """

    def _ensure(c):
        cur = c.cursor()
        cur.execute(
            f"CREATE TABLE IF NOT EXISTS {am.TRACKING_QUALIFIED} ("
            "version text PRIMARY KEY, "
            "applied_at text DEFAULT (datetime('now'))"
            ")"
        )
        c.commit()
        cur.close()

    monkeypatch.setattr(am, "_ensure_tracking_table", _ensure)


# --- tests -----------------------------------------------------------------


def test_applies_pending_migration(tmp_path, conn, patched_ensure):
    _write_migration(
        tmp_path, "20260101000000_widgets.sql",
        "CREATE TABLE widgets (id integer primary key);",
    )

    applied = _apply(conn, tmp_path)

    assert applied == ["20260101000000_widgets"]
    assert _table_exists(conn, "widgets")
    assert "20260101000000_widgets" in _tracked(conn)


def test_skips_already_tracked(tmp_path, conn, patched_ensure):
    _write_migration(
        tmp_path, "20260101000000_a.sql",
        "CREATE TABLE a (id integer primary key);",
    )
    _write_migration(
        tmp_path, "20260102000000_b.sql",
        "CREATE TABLE b (id integer primary key);",
    )

    # First pass applies both.
    assert _apply(conn, tmp_path) == ["20260101000000_a", "20260102000000_b"]

    # Add a third; only it should apply on the next pass.
    _write_migration(
        tmp_path, "20260103000000_c.sql",
        "CREATE TABLE c (id integer primary key);",
    )
    assert _apply(conn, tmp_path) == ["20260103000000_c"]
    assert _tracked(conn) == {
        "20260101000000_a",
        "20260102000000_b",
        "20260103000000_c",
    }


def test_fails_loud_on_bad_sql(tmp_path, conn, patched_ensure):
    # A good migration, then a broken one, then one that must NEVER run.
    _write_migration(
        tmp_path, "20260101000000_ok.sql",
        "CREATE TABLE ok (id integer primary key);",
    )
    _write_migration(
        tmp_path, "20260102000000_bad.sql",
        "CREATE TABLE bad (;;; this is not valid sql",
    )
    _write_migration(
        tmp_path, "20260103000000_never.sql",
        "CREATE TABLE never_runs (id integer primary key);",
    )

    with pytest.raises(sqlite3.Error):
        _apply(conn, tmp_path)

    # The good one committed; the bad one rolled back; the later one never ran.
    assert _table_exists(conn, "ok")
    assert not _table_exists(conn, "bad")
    assert not _table_exists(conn, "never_runs")
    assert _tracked(conn) == {"20260101000000_ok"}


class _NoCloseConn:
    """Delegates everything to a real sqlite conn but no-ops close().

    `main()` closes the connection in a finally block; we wrap so the test can
    still inspect the DB afterwards. sqlite3.Connection.close is read-only, so
    we can't monkeypatch it directly.
    """

    def __init__(self, real):
        self._real = real

    def cursor(self):
        return self._real.cursor()

    def commit(self):
        return self._real.commit()

    def rollback(self):
        return self._real.rollback()

    def close(self):  # no-op
        pass


def test_main_exits_nonzero_on_bad_sql(tmp_path, conn, monkeypatch, patched_ensure):
    monkeypatch.setenv("SUPABASE_DB_URL", "sqlite://test")
    _write_migration(
        tmp_path, "20260102000000_bad.sql", "TOTALLY NOT SQL ((("
    )
    wrapped = _NoCloseConn(conn)

    # main() lets the SQL error propagate out of the run; the process-level
    # __main__ guard turns that into sys.exit(1). We assert the raise here.
    with pytest.raises(sqlite3.Error):
        am.main(
            ["--migrations-dir", str(tmp_path)],
            connect=_sqlite_factory(wrapped),
        )

    # Nothing from the bad migration was recorded.
    assert _tracked(conn) == set()


def test_idempotent_on_rerun(tmp_path, conn, patched_ensure):
    _write_migration(
        tmp_path, "20260101000000_a.sql",
        "CREATE TABLE a (id integer primary key);",
    )
    first = _apply(conn, tmp_path)
    second = _apply(conn, tmp_path)
    third = _apply(conn, tmp_path)

    assert first == ["20260101000000_a"]
    assert second == []  # nothing pending the second time
    assert third == []
    assert _tracked(conn) == {"20260101000000_a"}


def test_no_op_when_secret_absent(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)

    def _explode(_url):  # the factory must never be called
        raise AssertionError("should not connect when secret is absent")

    rc = am.main(["--migrations-dir", str(tmp_path)], connect=_explode)
    assert rc == 0  # clean no-op, deploy not broken


def test_first_run_backfill_baselines_drifted_db(tmp_path, conn, patched_ensure):
    # Simulate a drifted prod: three historical migrations already live, one new.
    _write_migration(
        tmp_path, "20260101000000_hist1.sql",
        "CREATE TABLE hist1 (id integer primary key);",
    )
    _write_migration(
        tmp_path, "20260102000000_hist2.sql",
        "CREATE TABLE hist2 (id integer primary key);",
    )
    _write_migration(
        tmp_path, "20260103000000_new.sql",
        "CREATE TABLE new_table (id integer primary key);",
    )

    applied = _apply(
        conn, tmp_path, backfill_cutoff="20260102000000_hist2"
    )

    # Only the post-cutoff migration's SQL actually ran...
    assert applied == ["20260103000000_new"]
    assert _table_exists(conn, "new_table")
    # ...the historical ones were marked applied WITHOUT running (no tables).
    assert not _table_exists(conn, "hist1")
    assert not _table_exists(conn, "hist2")
    # ...but all three are tracked, so they never re-run.
    assert _tracked(conn) == {
        "20260101000000_hist1",
        "20260102000000_hist2",
        "20260103000000_new",
    }


def test_backfill_ignored_when_table_already_populated(tmp_path, conn, patched_ensure):
    # Pre-seed the tracking table (steady state — CLI already baselined).
    am._ensure_tracking_table(conn)
    cur = conn.cursor()
    cur.execute(
        f"INSERT INTO {am.TRACKING_QUALIFIED} (version) VALUES (?)",
        ("20260101000000_existing",),
    )
    conn.commit()
    cur.close()

    _write_migration(
        tmp_path, "20260100000000_old.sql",
        "CREATE TABLE old_table (id integer primary key);",
    )
    _write_migration(
        tmp_path, "20260101000000_existing.sql",
        "CREATE TABLE existing (id integer primary key);",
    )
    _write_migration(
        tmp_path, "20260102000000_fresh.sql",
        "CREATE TABLE fresh (id integer primary key);",
    )

    # Backfill cutoff would have swept up `old` — but the table is populated, so
    # backfill is skipped: `old` (untracked, pre-cutoff) is actually APPLIED,
    # `existing` is skipped (tracked), `fresh` is applied.
    applied = _apply(conn, tmp_path, backfill_cutoff="20260101000000_existing")

    assert applied == ["20260100000000_old", "20260102000000_fresh"]
    assert _table_exists(conn, "old_table")
    assert _table_exists(conn, "fresh")
    assert not _table_exists(conn, "existing")  # was tracked, never ran


def test_dry_run_changes_nothing(tmp_path, conn, patched_ensure):
    _write_migration(
        tmp_path, "20260101000000_a.sql",
        "CREATE TABLE a (id integer primary key);",
    )
    applied = _apply(conn, tmp_path, dry_run=True)

    assert applied == ["20260101000000_a"]  # reported...
    assert not _table_exists(conn, "a")  # ...but not created
    assert _tracked(conn) == set()  # ...and not recorded


def test_discover_orders_by_full_filename(tmp_path):
    # Two files share a timestamp prefix (this really happens in the repo:
    # 20260623120000_connection_health + 20260623120000_roadmap_doc). Ordering
    # must be deterministic on the full name.
    _write_migration(tmp_path, "20260623120000_roadmap_doc.sql", "select 1;")
    _write_migration(tmp_path, "20260623120000_connection_health.sql", "select 1;")
    _write_migration(tmp_path, "20260101000000_first.sql", "select 1;")

    versions = [v for v, _ in am.discover_migrations(tmp_path)]
    assert versions == [
        "20260101000000_first",
        "20260623120000_connection_health",  # 'c' < 'r'
        "20260623120000_roadmap_doc",
    ]
