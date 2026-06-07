-- DS metrics — tiny rolling aggregates (the ONLY persistence for pilot-1).
--
-- Data-minimization rule (non-negotiable): structured connector rows are pulled
-- + computed TRANSIENTLY at refresh time and never bulk-copied into Sprntly. We
-- persist ONLY distilled outputs: Findings as kg_signal rows (anomalies) and
-- here, one number per metric per period per source. A weekly aggregate is a
-- single double — not the underlying deals/tasks/meetings.
--
-- "enterprise_id" in the design docs == companies.id here (same convention as
-- the kg_* tables in 20260603120000_kg_foundation.sql).
--
-- Refs: ~/sprntly-agent-design.md §6 (no raw-dump), design-v4 Dashboard page.

create table if not exists metric_points (
    id            bigserial primary key,
    enterprise_id uuid not null references companies (id) on delete cascade,
    metric        text not null,
    period_start  date not null,
    value         double precision not null,
    source        text not null,
    computed_at   timestamptz not null default now(),
    -- one number per metric per period per source; a re-run of the same week
    -- overwrites in place (idempotent refresh — never a duplicate row).
    unique (enterprise_id, metric, period_start, source)
);

-- Dashboard series reads scan a single metric for one enterprise, newest first.
create index if not exists metric_points_series_idx
    on metric_points (enterprise_id, metric, period_start desc);
