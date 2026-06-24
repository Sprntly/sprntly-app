# Database migrations

How schema changes in `supabase/migrations/*.sql` reach the **prod** Supabase
database, and what you must do to keep auto-apply working.

## TL;DR

* Adding a `supabase/migrations/<timestamp>_<name>.sql` file and merging to
  `main` is enough — the deploy applies it to prod automatically, **before**
  the backend restarts, so code never goes live against a schema missing its
  tables.
* This only works once the **`SUPABASE_DB_URL`** GitHub Actions secret is set.
  Until then the migrate step is a clear **no-op + warning** and deploys are
  unaffected.
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
   on 5432). It runs `supabase db push --db-url "$SUPABASE_DB_URL" --yes`, which
   compares the repo's migrations against the
   `supabase_migrations.schema_migrations` tracking table in prod and applies
   only the ones not yet recorded. If it fails, the deploy is aborted.
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

## The standalone runner (`scripts/apply_migrations.py`)

A dependency-light, fully-tested alternative to the CLI for **manual / local /
on-box** use (e.g. a catch-up from a shell, or an environment without the
Supabase CLI). It speaks plain DB-API 2.0 and reads/writes the **same**
`supabase_migrations.schema_migrations` table the CLI uses, so the two never
disagree about what's applied — run either, in any order, no drift.

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
* Order is the **full filename**, ascending — so if two migrations share a
  timestamp prefix (it has happened:
  `20260623120000_connection_health.sql` and
  `20260623120000_roadmap_doc.sql`), the suffix breaks the tie deterministically
  (`connection_health` before `roadmap_doc`). Prefer **unique timestamps** to
  avoid relying on this.
* Write migrations **idempotently** (`create table if not exists`,
  `add column if not exists`, `create policy ... ` guarded, etc.) so a re-run or
  partial-drift recovery is harmless.
* Backend tables use **service-role RLS** (`srv_*` policies, `using (true)`):
  the backend connects via PostgREST with the service-role key and cannot run
  DDL — which is exactly why DDL is applied through the pooler connection above,
  not the app.
* Forward-only. Don't write destructive `drop`/`delete` migrations against prod
  data without an explicit, reviewed plan.
