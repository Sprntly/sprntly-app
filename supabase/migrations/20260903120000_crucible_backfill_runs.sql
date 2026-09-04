-- Audit trail for the deterministic commercial-figure backfill operator tool.
--
-- The backfill itself needs no per-signal claim/queue table: the unit of work
-- IS the kg_signal row, and its own `properties.amount` presence is the
-- completion marker (a signal already carrying `amount` is skipped, never
-- re-derived) — so re-running the same company is naturally idempotent and a
-- crashed run naturally resumes by simply re-scanning, with no separate
-- cursor to maintain. This table exists for the other half of that pattern:
-- a durable, queryable record of what a run actually did, mirroring
-- `crucible_runs`' own "the row is the job" posture so an operator can answer
-- "what changed, and when" without diffing kg_signal by hand.
--
-- Scoping mirrors crucible_runs / crucible_claims: company_id, filtered at
-- every read, RLS spelled out `to service_role` on every policy (never left
-- to default to PUBLIC).
create table if not exists crucible_backfill_runs (
    id                 bigint generated always as identity primary key,
    company_id         uuid        not null references companies (id) on delete cascade,
    -- Only one phase exists today. A second value is a future migration's
    -- job, not this one's — the CHECK stays narrow on purpose rather than
    -- pre-declaring a phase this ticket does not build.
    phase              text        not null default 'deterministic_sweep'
                         check (phase in ('deterministic_sweep')),
    mode               text        not null check (mode in ('dry_run', 'apply')),
    -- Which regex/parsing revision produced this run's numbers — so a report
    -- from an old run is never silently compared against a newer pattern.
    pattern_version    text        not null,
    status             text        not null default 'running'
                         check (status in ('running', 'completed', 'failed')),
    examined_count     integer     not null default 0,
    enriched_count     integer     not null default 0,
    -- Per-reason skip breakdown, e.g. {"already_has_amount": 3, "no_figure_found": 40,
    -- "ambiguous_multiple_figures": 2}. Keys are a closed vocabulary enforced
    -- in application code, not here.
    skipped_counts     jsonb       not null default '{}'::jsonb,
    error              text,
    started_at         timestamptz not null default now(),
    finished_at        timestamptz,
    created_by         uuid        references auth.users (id) on delete set null,
    updated_at         timestamptz not null default now()
);

create index if not exists crucible_backfill_runs_company_idx
    on crucible_backfill_runs (company_id, started_at desc);

alter table crucible_backfill_runs enable row level security;
drop policy if exists "srv_crucible_backfill_runs" on crucible_backfill_runs;
create policy "srv_crucible_backfill_runs" on crucible_backfill_runs
    for all to service_role using (true) with check (true);
