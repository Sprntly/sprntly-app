"""Migration filename hygiene — guards the deploy pipeline.

`supabase db push` records applied migrations in
`supabase_migrations.schema_migrations`, whose primary key is the **timestamp
prefix alone** (the name is a separate column). Two migration files sharing one
timestamp are therefore fatal: the first applies and records its row, and the
second runs its SQL and then dies inserting bookkeeping —

    duplicate key value violates unique constraint "schema_migrations_pkey"
    Key (version)=(20260719120000) already exists.

Because deploy-backend.yml's `migrate` job gates `deploy`, that failure blocks
EVERY backend deploy from the branch, not just the PR that added the file.
`db push --dry-run` does not catch it (the conflict only surfaces on the
bookkeeping INSERT), so this test is the cheap pre-merge check.

Happened for real on 2026-07-20: `20260719120000_evidences_theme_id.sql`
collided with `20260719120000_ask_jobs_cancelled.sql` and held back three PRs.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


def test_migration_timestamps_are_unique():
    by_version: dict[str, list[str]] = defaultdict(list)
    for path in MIGRATIONS.glob("*.sql"):
        by_version[path.name.split("_", 1)[0]].append(path.name)

    dupes = {v: sorted(names) for v, names in by_version.items() if len(names) > 1}
    assert not dupes, (
        "migration timestamps must be unique — schema_migrations' primary key is "
        f"the timestamp alone, so these would break every backend deploy: {dupes}. "
        "Renumber the newer file to a later, unused timestamp."
    )


def test_migration_filenames_are_well_formed():
    """Each file is `<14-digit timestamp>_<name>.sql` — the shape db push parses."""
    bad = [
        p.name
        for p in MIGRATIONS.glob("*.sql")
        if not (
            len(prefix := p.name.split("_", 1)[0]) == 14
            and prefix.isdigit()
            and "_" in p.name
        )
    ]
    assert not bad, f"malformed migration filenames: {sorted(bad)}"
