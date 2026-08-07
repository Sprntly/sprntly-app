# Database migrations

How schema changes in `supabase/migrations/*.sql` reach the Supabase database,
and what you must do to keep auto-apply working.

## TL;DR

* **Staging and prod share ONE Supabase database.** `deploy-backend.yml` points
  both branches at the same `SUPABASE_DB_URL`, so a migration merged to `main`
  hits the database prod is reading from, immediately — long before any prod
  cutover. There is no staging-only rehearsal for schema. Treat every migration
  as prod-affecting.
* Adding a `supabase/migrations/<timestamp>_<name>.sql` file and merging to
  `main` is enough — the deploy applies it automatically, **before** the backend
  restarts, so code never goes live against a schema missing its tables.
* **Pick a timestamp no other file claims — on `main` OR in any open PR — and
  re-check it immediately before merge.** This is the single most common way to
  break the deploy pipeline in this repo (see
  [Timestamps are a primary key, not a sort order](#timestamps-are-a-primary-key-not-a-sort-order)).
* Migrations are **forward-only**. The pipeline never drops or destroys
  anything — it only runs the SQL in the repo's migration files.

## Why this exists

Prod migrations used to be applied **by hand** in the Supabase SQL editor. That
silently drifted: a release shipped code expecting `multi_agent_docs`,
`ticket_data`, and drip tables that nobody had created in prod, and PRD
generation 500'd. Auto-applying repo migrations on deploy prevents that class of
incident.

## How it works in CI (the canonical path)

`.github/workflows/deploy-backend.yml` has two jobs:

1. **`migrate`** — runs on the GitHub runner (the EC2 box can't reach Postgres
   on 5432). It runs
   `supabase db push --db-url "$SUPABASE_DB_URL" --yes --include-all`, which
   compares the repo's migrations against the
   `supabase_migrations.schema_migrations` tracking table and applies only the
   ones not yet recorded. If it fails, the deploy is aborted.

   `--include-all` is load-bearing: it also applies pending migrations whose
   version sorts *before* the newest already-applied one. Without it, a
   migration merged with an earlier timestamp than one already applied is
   "out of order" and `db push` aborts with *"Found local migration files to be
   inserted before the last migration on remote database"*, blocking the whole
   deploy. Because of this flag, **merge order between branches does not
   matter** — only timestamp uniqueness does.
2. **`deploy`** — `needs: migrate`, so it runs **only if migrations succeeded**.
   SSHes to EC2, fast-forwards the on-box clone, reinstalls, restarts
   `sprintly.service`.

The trigger paths include `supabase/migrations/**`, so a migration-only change
also deploys.

### Safe rollout / no-op when the secret is absent

If `SUPABASE_DB_URL` is unset, the `migrate` step prints a GitHub Actions
warning and exits 0 — the deploy proceeds unchanged. This makes auto-apply
**opt-in**: existing deploys keep working exactly as before until the secret is
added.

## What YOU must do to activate auto-apply (one-time)

You (a maintainer with Supabase + GitHub admin) must add one repo secret.

1. **Get the connection string.** Supabase dashboard → your project →
   **Project Settings → Database → Connection pooling**. Choose **Mode:
   Session** and copy the connection string. It looks like:

   ```
   postgresql://postgres.<project-ref>:<DB-PASSWORD>@aws-0-<region>.pooler.supabase.com:6543/postgres
   ```

   * Use the **Session** (not Transaction) pooler — `supabase db push` and DDL
     need session semantics.
   * Use the **pooler** host, not the direct `db.<ref>.supabase.co` host: the
     GitHub runner is IPv4-only and the direct host is IPv6-only.
   * `<DB-PASSWORD>` is the database password from the same Database settings
     page (reset it there if you don't have it). URL-encode any special
     characters.

2. **Add it as a GitHub Actions secret.** Repo → **Settings → Secrets and
   variables → Actions → New repository secret**:
   * Name: `SUPABASE_DB_URL`
   * Value: the Session-mode pooler string from step 1.

3. **Baseline the history (one-time, only if not already done).** Prod already
   had ~50 migrations applied by hand before any runner tracked them. If you
   point a runner at that database with an empty tracking table it would try to
   re-run all of history. PR #350 already baselined prod (marked all existing
   migrations applied) and validated with `supabase db push --dry-run` →
   "Remote database is up to date", so **no action is needed for the current
   prod database.**

   If you ever wire up a **fresh** database that already has some schema applied
   out-of-band, baseline it before the first push (see backfill below).

That's it. After the secret exists, every merge to `main` that adds a migration
applies it to prod automatically.

## The standalone runner (`scripts/apply_migrations.py`) — DO NOT RUN

> **Do not run this script against the shared database.** It is retained for
> its tests and for possible future repair, not for use. The paragraph that
> used to live here claimed it and the CLI "never disagree about what's
> applied" — that claim is false, and acting on it would be destructive.
>
> **The bug:** the script compares **full filename stems**
> (`20260623120000_roadmap_doc`) against the tracking table's
> **timestamp-only** `version` rows (`20260623120000`). Nothing ever matches,
> so it reads every migration in the repo — all ~148 of them — as unapplied and
> will attempt to re-run the entire history against a fully-migrated database.
> Our migrations are written idempotently, which limits but does not eliminate
> the damage: any migration whose SQL is not perfectly re-runnable, or whose
> backfill is not idempotent, executes a second time against live data.
>
> **Use `supabase db push` instead** — via the deploy pipeline, which is the
> only sanctioned path to the shared DB. Applying a migration by hand outside
> that path is what creates phantom migrations (see
> [Diagnosing a red deploy](#diagnosing-a-red-deploy)).

The historical description follows, for context only.

A dependency-light, fully-tested alternative to the CLI for **manual / local /
on-box** use (e.g. a catch-up from a shell, or an environment without the
Supabase CLI). It speaks plain DB-API 2.0 and reads/writes the **same**
`supabase_migrations.schema_migrations` table the CLI uses.

```bash
# Steady state: apply anything pending.
SUPABASE_DB_URL='postgresql://...pooler.supabase.com:6543/postgres' \
  python scripts/apply_migrations.py

# Dry run — report only, change nothing.
SUPABASE_DB_URL='...' python scripts/apply_migrations.py --dry-run

# First run against a DRIFTED database — baseline everything already live by
# marking migrations <= a known version as applied WITHOUT running their SQL:
SUPABASE_DB_URL='...' \
  python scripts/apply_migrations.py \
    --backfill-cutoff 20260623120000_roadmap_doc
```

Set the cutoff to the **newest migration you've confirmed is already live** in
that database. The backfill only seeds an **empty** tracking table; once it's
populated the cutoff is ignored. Requires `psycopg` (`pip install
'psycopg[binary]'`) for the real Postgres connection.

### Guarantees

* Each migration runs in **its own transaction** together with the bookkeeping
  insert. On any error it rolls back, exits **non-zero**, and applies nothing
  past the failure (fail loud → aborts the deploy).
* Re-running is a **no-op** for already-recorded migrations (idempotent).
* If `SUPABASE_DB_URL` is unset, it warns and exits 0 (no-op), mirroring CI.

Tests live in `backend/tests/test_apply_migrations.py` (run with `pytest`):
applies a pending migration, skips a tracked one, fails loud on bad SQL, is
idempotent on re-run, and exercises the first-run backfill.

## Authoring migrations

* Filename: `supabase/migrations/<UTC-timestamp>_<snake_name>.sql`, e.g.
  `20260623120000_roadmap_doc.sql`. Timestamp format `YYYYMMDDHHMMSS`.
* **The 14-digit timestamp must be globally unique.** See the next section —
  this is not a style preference, it is the difference between a working deploy
  pipeline and a stopped one.
* Write migrations **idempotently** (`create table if not exists`,
  `add column if not exists`, `create policy ... ` guarded, etc.) so a re-run or
  partial-drift recovery is harmless.
* Backend tables use **service-role RLS** (`srv_*` policies, `using (true)`):
  the backend connects via PostgREST with the service-role key and cannot run
  DDL — which is exactly why DDL is applied through the pooler connection above,
  not the app.
* Forward-only. Don't write destructive `drop`/`delete` migrations against prod
  data without an explicit, reviewed plan.
* Any migration that **renames or tightens** a value the browser still writes
  must stay backward-compatible until the prod frontend catches up — staging and
  prod share this database, so tightening a constraint against `main`'s frontend
  breaks prod's. This is not hypothetical: `20260723140000_insight_type_prefs.sql`
  renamed brief insight slugs and broke prod onboarding until bridge migration
  `20260726120000_bridge_insight_type_constraint.sql` re-widened the constraint.

## Timestamps are a primary key, not a sort order

`supabase_migrations.schema_migrations` has the **14-digit timestamp ALONE** as
its primary key; the descriptive name is a separate column that is not part of
the key. Two migration files sharing one timestamp are therefore fatal, not
ambiguous. `db push` applies the first and records its row; the second runs its
SQL and then dies on the bookkeeping INSERT:

```
duplicate key value violates unique constraint "schema_migrations_pkey"
Key (version)=(20260719120000) already exists.
```

Because `migrate` gates `deploy`, that failure blocks **every backend deploy off
that branch** — not just the PR that introduced the file, and, since the two
environments share a database, staging *and* prod. A second, quieter
consequence: the losing file's table or column **is never created**, because its
version already reads as applied. Code then ships against schema that does not
exist.

This has fired four times: **#802** (2026-07-20,
`20260719120000_evidences_theme_id.sql` vs
`20260719120000_ask_jobs_cancelled.sql`, held back three PRs), **#972**
(2026-07-31), **#1105** (2026-08-07), and the several phantom-migration
incidents below.

An earlier version of this document claimed a shared timestamp prefix was
survivable because "the suffix breaks the tie deterministically", citing
`20260623120000_connection_health.sql` and `20260623120000_roadmap_doc.sql`.
**That guidance was wrong and has been removed.** Those two files did not
coexist safely — `roadmap_doc` was renumbered to `20260623130000`, which is
where it sits today. Do not reintroduce the pattern.

### Verify the timestamp immediately BEFORE merge, not at branch-cut time

This is the specific rule that would have caught #972 and #1105. Both were
long-lived branches that picked a **free** timestamp when they were cut; a
*different* migration then claimed that timestamp and merged to `main` while the
branch sat open. Neither author did anything careless — the collision was
created by the passage of time.

**CI does not save you here.** `backend/tests/test_migrations_hygiene.py`
enforces uniqueness, but it only sees the migrations directory *on the branch
under test*. A branch that does not yet contain `main`'s colliding file passes
green, and the conflict only materialises on `main` after the merge — at which
point the next deploy's `migrate` job dies. #1105 is exactly this shape: its
branch is based before the colliding file landed, so its CI is green and its
merge would still stop the pipeline. Rebase onto current `main` before trusting
the hygiene test.

Run this against **current** `origin/main` right before merging:

```bash
git fetch origin

# 1. Every timestamp on main (the authoritative claimed set).
git ls-tree -r --name-only origin/main supabase/migrations/ \
  | sed 's|.*/||' | cut -d_ -f1 | sort > /tmp/on-main.txt

# 2. Collisions on main itself — must print nothing.
uniq -d /tmp/on-main.txt

# 3. The timestamps your branch ADDS must not already be on main.
#    Note --diff-filter=A and the three-dot range: compare only what the branch
#    introduces since the merge-base. Listing the branch's whole migrations
#    directory instead would match all of main's history and tell you nothing.
git diff --diff-filter=A --name-only origin/main...HEAD -- supabase/migrations/ \
  | sed 's|.*/||' | cut -d_ -f1 | sort -u | comm -12 - /tmp/on-main.txt

# 4. And must not collide with any OTHER open PR.
gh pr list --state open --limit 200 --json number -q '.[].number' \
  | xargs -I{} sh -c \
    'gh pr view {} --json files -q ".files[].path" | grep -q "^supabase/migrations/" \
      && echo "PR #{}: $(gh pr view {} --json files -q ".files[].path" | grep "^supabase/migrations/")"'
```

Steps 2 and 3 printing nothing is the go signal. When several branches are
merging in one batch, reserve a distinct timestamp per branch up front — because
`--include-all` is set, the order they merge in does not matter, only that no
two claim the same version.

## Diagnosing a red deploy

All of these surface in the **`migrate`** job, before `deploy` ever starts. If
`migrate` succeeded and `deploy` failed, it is not a schema problem — do not
touch migrations.

| What you see in the `migrate` log | What it means | Fix |
|---|---|---|
| `duplicate key value violates unique constraint "schema_migrations_pkey"` | Two files on the branch claim one timestamp. | Renumber the **newer** file to a later, unused timestamp and merge that. Do **not** edit the tracking table. |
| `Remote migration versions not found in local migrations directory: <ts>` | **Phantom migration** — someone applied SQL to the shared DB by hand and never merged the file. | Merge the PR carrying that file, or `supabase migration repair --status reverted <ts>`. Maintainer-only. |
| `Found local migration files to be inserted before the last migration on remote database` | Out-of-order migration *and* `--include-all` has been dropped from the workflow. | Restore the flag in `deploy-backend.yml`. |
| `Remote database is up to date.` | Zero drift — nothing pending, no phantoms. | Nothing. This is the healthy steady state. |

**Never hand-apply a migration to the shared database without merging its file
in the same breath.** That is the root cause of every phantom incident this repo
has had (`onboarding_v7` 2026-07-21; `reports`/`reports_share` 2026-07-30;
`custom_skills_no_builtin_override` and `ticket_edits_lifecycle` 2026-07-31).
The deploy pipeline is the only sanctioned writer.
