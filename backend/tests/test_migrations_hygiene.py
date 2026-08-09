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

import subprocess
import warnings
from collections import defaultdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"


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


# ---------------------------------------------------------------------------
# Cross-branch collisions — the hole the two tests above cannot cover.
#
# Both tests above only ever look at THIS branch's migrations directory. That
# makes a whole class of collision structurally invisible to them: a branch cut
# when timestamp T was free, left open while a DIFFERENT migration claiming T
# merges to main. The branch's own directory still has exactly one file at T, so
# the uniqueness test passes green — and merging it puts two files on main
# claiming one version, which stops every backend deploy.
#
# This is not theoretical and it is not rare. It is how #972 (2026-07-31) and
# #1105 (2026-08-07) both happened, neither through any carelessness by their
# authors. #1105's branch is based before its colliding file landed, so its CI
# is green while being a guaranteed pipeline-stopper. "CI passed" was never
# evidence of migration safety; this test is what makes it evidence.
#
# WHY SET ARITHMETIC AND NOT `git diff origin/main...HEAD`. The three-dot diff
# needs a merge-base, which needs history. CI checks out with
# `actions/checkout@v4` and no `fetch-depth`, i.e. a DEPTH-1 SHALLOW CLONE with
# no common ancestor to find and, by default, no `origin/main` ref at all. So we
# ask a question that needs no history instead: "which migration files exist
# here but not on main, and does any of them claim a timestamp main has already
# taken?" That is answerable from two flat file listings, so a `--depth=1` fetch
# of main is enough.
#
# WHY THIS SKIPS RATHER THAN FAILS when main is unreachable: a network blip must
# not become a merge-blocker on PRs that touch no migrations at all. A skip that
# says why is honest; a red X on unrelated work would get the test deleted.
#
# WHY IT IS NOT MARKED `integration` despite touching the network. The marker
# covers tests that "hit the real network", and this one does — one `git fetch
# --depth=1`, bounded and degrading to a skip. It is deliberately left unmarked
# so it runs in the FAST lane: its entire value is being a cheap pre-merge
# answer to "is this timestamp still free?", and the integration lane takes ~40
# minutes. Move it if the fast lane ever loses network egress.
# ---------------------------------------------------------------------------

FETCH_TIMEOUT_SECONDS = 30


def _git(*args: str, timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _resolve_main_ref() -> str | None:
    """A ref for main we can `ls-tree` — fetched fresh, because stale means wrong.

    FETCH FIRST, ALWAYS. It is tempting to reuse a local ref when one exists and
    only fetch as a fallback, but every local ref can lag: this repo's own
    canonical clone at ~/Sprntly sits on a `main` branch **590 commits behind**
    origin, and `refs/remotes/origin/main` is only as fresh as the last fetch.
    Answering "is this timestamp taken?" against a stale main returns a FALSE
    GREEN — precisely the failure this test exists to prevent, wearing a passing
    checkmark. A one-commit `--depth=1` fetch costs nothing next to that.

    `refs/heads/main` (a purely local branch) is never consulted at all.
    """
    if _git("remote", "get-url", "origin").returncode == 0:
        try:
            fetched = _git(
                "fetch", "--depth=1", "origin", "main", timeout=FETCH_TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired:
            fetched = None
        if fetched is not None and fetched.returncode == 0:
            return "FETCH_HEAD"

    # Offline fallback: the last-fetched remote-tracking ref. Possibly stale, so
    # say so — a quiet pass here is exactly what we must not produce.
    if _git("rev-parse", "--verify", "--quiet", "refs/remotes/origin/main").returncode == 0:
        warnings.warn(
            "could not fetch origin/main; comparing against the last-fetched "
            "refs/remotes/origin/main, which may be stale. Run `git fetch origin` "
            "and re-run before relying on this result to merge.",
            stacklevel=2,
        )
        return "refs/remotes/origin/main"

    return None


def _migration_filenames(ref: str) -> set[str]:
    result = _git("ls-tree", "-r", "--name-only", ref, "supabase/migrations/")
    if result.returncode != 0:
        return set()
    return {
        line.rsplit("/", 1)[-1]
        for line in result.stdout.splitlines()
        if line.endswith(".sql")
    }


def test_added_migrations_do_not_reuse_a_timestamp_already_on_main():
    """A migration this branch ADDS must not claim a timestamp main already has.

    Run this the moment before merging, not when the branch was cut — the whole
    point is that main moves underneath long-lived branches.
    """
    if _git("rev-parse", "--git-dir").returncode != 0:
        pytest.skip("not a git checkout — cannot compare against main")

    main_ref = _resolve_main_ref()
    if main_ref is None:
        pytest.skip(
            "cannot resolve origin/main (no remote, offline, or shallow clone "
            "without network) — the cross-branch collision check did NOT run. "
            "Re-run it before merging any PR that adds a migration."
        )

    on_main = _migration_filenames(main_ref)
    if not on_main:
        pytest.skip(f"no migrations found at {main_ref} — unexpected tree shape")

    here = {p.name for p in MIGRATIONS.glob("*.sql")}

    # "Added" = present here, absent on main, keyed by FULL filename. A file
    # modified in place is on both sides and is not a new claim on its own
    # timestamp; a renumbered file correctly reads as an addition under its new
    # name. On main itself this set is empty and the test is trivially green.
    added = here - on_main
    claimed = {name.split("_", 1)[0] for name in on_main}

    collisions = {
        name: sorted(m for m in on_main if m.split("_", 1)[0] == name.split("_", 1)[0])
        for name in sorted(added)
        if name.split("_", 1)[0] in claimed
    }

    assert not collisions, (
        "these migrations claim a timestamp that is ALREADY TAKEN on main:\n"
        + "\n".join(
            f"  {added_name}  collides with  {', '.join(existing)}"
            for added_name, existing in collisions.items()
        )
        + "\n\nschema_migrations' primary key is the 14-digit timestamp alone, so "
        "merging this would fail the deploy pipeline's migrate job with:\n"
        '  duplicate key value violates unique constraint "schema_migrations_pkey"\n'
        "and block EVERY backend deploy — staging and prod, which share one "
        "database. The added migration's schema would also never be created, "
        "because its version already reads as applied.\n"
        "Fix: renumber the file(s) above to a later, unused timestamp. See "
        "supabase/MIGRATIONS.md."
    )
