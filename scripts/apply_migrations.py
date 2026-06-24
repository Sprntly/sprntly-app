#!/usr/bin/env python3
"""Idempotent, fail-loud Supabase migration runner.

WHY THIS EXISTS
---------------
Prod migrations used to be applied by hand in the Supabase SQL editor, which
silently drifted and broke a feature in prod (missing tables → 500s). The
deploy now applies migrations automatically.

The CANONICAL deploy-path runner is the Supabase CLI (`supabase db push`),
wired into `.github/workflows/deploy-backend.yml` (the `migrate` job, which
GATES the backend restart). This script is a portable, dependency-light,
fully-tested ALTERNATIVE / fallback that:

  * speaks plain DB-API 2.0, so it runs anywhere there's a Postgres driver
    (e.g. directly on the EC2 box, or locally for a manual catch-up), and
  * reads/writes the SAME tracking table the Supabase CLI uses
    (`supabase_migrations.schema_migrations`), so the two NEVER disagree about
    what's applied — run either, in any order, no drift.

It is forward-only: it applies repo migrations whose filename isn't recorded
yet, in filename order, each in its own transaction. It NEVER drops or
destroys anything; it only runs the SQL the repo's migration files contain.

DESIGN GUARANTEES
-----------------
1. Tracking table is ensured up front (idempotent CREATE ... IF NOT EXISTS).
2. A migration is applied iff its version (the filename, sans `.sql`) is not
   already in the tracking table.
3. Each migration runs inside a single transaction together with the INSERT
   that records it. On ANY error the transaction is rolled back, the runner
   exits NON-ZERO immediately (fail loud → aborts the deploy), and NOTHING
   past the failure point is applied.
4. Re-running is a no-op (idempotent): already-recorded versions are skipped.

UNKNOWN-DRIFT / FIRST-RUN BACKFILL (the safe part)
--------------------------------------------------
Prod already had ~50 migrations applied by hand BEFORE any runner tracked
them, so a fresh tracking table would otherwise try to re-run all of history.
Even though every migration in this repo is written idempotently
(`create table if not exists`, `add column if not exists`, ...), re-running
the full history is needless risk. So:

  * On its FIRST run against a database (tracking table empty/just-created),
    if `--backfill-cutoff VERSION` is given, every migration with
    `version <= cutoff` is MARKED AS APPLIED WITHOUT RUNNING ITS SQL.
  * Only migrations AFTER the cutoff are actually executed.

This mirrors how the CLI history was baselined (PR #350). Pick the cutoff =
the newest migration you've confirmed is already live in prod. If the tracking
table is already populated (the normal steady state), the cutoff is ignored —
backfill only seeds an empty table. Omit the cutoff on a brand-new database to
run the whole history from scratch.

USAGE
-----
    # Apply pending migrations (steady state):
    SUPABASE_DB_URL=postgresql://... python scripts/apply_migrations.py

    # First run against drifted prod — baseline everything <= a known version:
    SUPABASE_DB_URL=postgresql://... \
        python scripts/apply_migrations.py --backfill-cutoff 20260623120000_roadmap_doc

    # Dry run (report only, change nothing):
    SUPABASE_DB_URL=postgresql://... python scripts/apply_migrations.py --dry-run

If SUPABASE_DB_URL is unset, the script prints a clear warning and exits 0
(no-op) so it never breaks an environment that hasn't been wired up yet.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Callable, Iterable, Protocol

logger = logging.getLogger("apply_migrations")

# Repo root = parent of this scripts/ dir. Migrations live in supabase/migrations.
REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"

# The Supabase CLI records applied migrations here; we use the SAME table so the
# two runners stay consistent.
TRACKING_SCHEMA = "supabase_migrations"
TRACKING_TABLE = "schema_migrations"
TRACKING_QUALIFIED = f"{TRACKING_SCHEMA}.{TRACKING_TABLE}"


# --- DB-API 2.0 protocol (so we can inject sqlite3 in tests, psycopg in prod) --


class _Cursor(Protocol):
    def execute(self, sql: str, params: Iterable | None = ...) -> object: ...
    def fetchall(self) -> list: ...
    def close(self) -> None: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


# A connection factory takes the DB URL and returns an open DB-API connection.
ConnectionFactory = Callable[[str], _Connection]


def _default_connect(db_url: str) -> _Connection:
    """Open a real Postgres connection via psycopg (imported lazily).

    Lazy so that tests (which inject sqlite3) never need psycopg installed,
    and so importing this module is cheap.
    """
    try:
        import psycopg  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised only in prod
        raise RuntimeError(
            "psycopg is required to connect to Postgres. "
            "Install it with `pip install 'psycopg[binary]'`, or inject a "
            "connection factory (the deploy path uses the Supabase CLI instead "
            "of this script)."
        ) from exc
    # autocommit=False: we manage transactions explicitly, one per migration.
    return psycopg.connect(db_url, autocommit=False)


def discover_migrations(migrations_dir: Path = MIGRATIONS_DIR) -> list[tuple[str, Path]]:
    """Return (version, path) for every *.sql migration, sorted by version.

    `version` is the filename without the `.sql` suffix — matching how the
    Supabase CLI records migrations. Sorting by version == sorting by the
    leading timestamp then the rest of the name, giving a deterministic,
    stable order even when two files share a timestamp prefix.
    """
    if not migrations_dir.is_dir():
        raise FileNotFoundError(f"Migrations dir not found: {migrations_dir}")
    out: list[tuple[str, Path]] = []
    for p in sorted(migrations_dir.glob("*.sql"), key=lambda p: p.name):
        out.append((p.name[: -len(".sql")], p))
    return out


def _ensure_tracking_table(conn: _Connection) -> None:
    """Create the tracking table if it's missing. Idempotent."""
    cur = conn.cursor()
    try:
        # `IF NOT EXISTS` makes this safe against the table already existing
        # (the steady state, where the Supabase CLI created it).
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {TRACKING_SCHEMA}")
        cur.execute(
            f"CREATE TABLE IF NOT EXISTS {TRACKING_QUALIFIED} ("
            "version text PRIMARY KEY, "
            "applied_at timestamptz DEFAULT now()"
            ")"
        )
        conn.commit()
    finally:
        cur.close()


def _applied_versions(conn: _Connection) -> set[str]:
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT version FROM {TRACKING_QUALIFIED}")
        return {row[0] for row in cur.fetchall()}
    finally:
        cur.close()


def _record_version(cur: _Cursor, version: str) -> None:
    # Parameterized; the placeholder style differs between drivers, but both
    # psycopg and sqlite3 accept `%s`-free qmark... so we branch on what works.
    # We use a named style that both understand via two attempts is overkill;
    # instead we let the caller pass the right param style. To keep it simple
    # and driver-agnostic we inline a safe literal (version is repo-controlled,
    # never user input) — but prefer a real bind where possible.
    cur.execute(
        f"INSERT INTO {TRACKING_QUALIFIED} (version) VALUES (%(v)s)",
        {"v": version},
    )


def _record_version_qmark(cur: _Cursor, version: str) -> None:
    """sqlite3 / qmark-style INSERT (used by the test harness)."""
    cur.execute(
        f"INSERT INTO {TRACKING_QUALIFIED} (version) VALUES (?)",
        (version,),
    )


def apply_migrations(
    conn: _Connection,
    migrations: list[tuple[str, Path]],
    *,
    backfill_cutoff: str | None = None,
    dry_run: bool = False,
    paramstyle: str = "pyformat",
) -> list[str]:
    """Apply pending migrations against an open connection.

    Returns the list of versions that were APPLIED (SQL executed) this run.
    Versions seeded by the backfill are recorded but NOT in the returned list,
    since their SQL was not run.

    Raises (and rolls back the offending migration) on the first SQL error.
    """
    record = _record_version_qmark if paramstyle == "qmark" else _record_version

    _ensure_tracking_table(conn)
    already = _applied_versions(conn)

    # First-run backfill: only when the tracking table is empty AND a cutoff is
    # given. Seeds <= cutoff as applied-without-running, so drifted prod isn't
    # re-run from scratch.
    if not already and backfill_cutoff is not None:
        seeded: list[str] = []
        cur = conn.cursor()
        try:
            for version, _path in migrations:
                if version <= backfill_cutoff:
                    if not dry_run:
                        record(cur, version)
                    seeded.append(version)
            if not dry_run:
                conn.commit()
        finally:
            cur.close()
        if seeded:
            logger.info(
                "First-run backfill: marked %d migration(s) <= %s as already "
                "applied (SQL NOT run).",
                len(seeded),
                backfill_cutoff,
            )
            already.update(seeded)

    applied: list[str] = []
    for version, path in migrations:
        if version in already:
            logger.debug("skip (already applied): %s", version)
            continue

        sql = path.read_text()
        if dry_run:
            logger.info("[dry-run] WOULD apply: %s", version)
            applied.append(version)
            continue

        cur = conn.cursor()
        try:
            # One transaction: the migration's SQL + the bookkeeping INSERT.
            # If either fails, we roll the whole thing back and abort — the
            # tracking row is never written for a migration that didn't fully
            # succeed.
            cur.execute(sql)
            record(cur, version)
            conn.commit()
        except Exception:
            conn.rollback()
            cur.close()
            logger.error(
                "Migration FAILED, rolled back, aborting: %s", version,
                exc_info=True,
            )
            # Fail loud — re-raise so the caller exits non-zero and the deploy
            # is aborted before the backend restarts onto a broken schema.
            raise
        cur.close()
        logger.info("applied: %s", version)
        applied.append(version)

    return applied


def main(argv: list[str] | None = None, connect: ConnectionFactory | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--backfill-cutoff",
        default=os.environ.get("MIGRATIONS_BACKFILL_CUTOFF"),
        help=(
            "On first run against an empty tracking table, mark every migration "
            "whose version <= this value as already-applied WITHOUT running it. "
            "Use to baseline a drifted prod DB. Ignored if the tracking table is "
            "already populated."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be applied; change nothing.",
    )
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=MIGRATIONS_DIR,
        help="Override the migrations directory (default: supabase/migrations).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )

    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        # No-op + warn, mirroring the workflow's behaviour: never break an
        # environment that hasn't been wired up with the secret yet.
        logger.warning(
            "SUPABASE_DB_URL is not set — skipping migrations (no-op). "
            "Set it to the Supabase Session-mode pooler connection string to "
            "enable. See supabase/MIGRATIONS.md."
        )
        return 0

    migrations = discover_migrations(args.migrations_dir)
    logger.info("Discovered %d migration file(s).", len(migrations))

    factory = connect or _default_connect
    conn = factory(db_url)
    try:
        applied = apply_migrations(
            conn,
            migrations,
            backfill_cutoff=args.backfill_cutoff,
            dry_run=args.dry_run,
        )
    finally:
        conn.close()

    if applied:
        verb = "Would apply" if args.dry_run else "Applied"
        logger.info("%s %d migration(s): %s", verb, len(applied), ", ".join(applied))
    else:
        logger.info("Up to date — no pending migrations.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main())
    except Exception:
        logger.error("Migration run failed.", exc_info=True)
        sys.exit(1)
