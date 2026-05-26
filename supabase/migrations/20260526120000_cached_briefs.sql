-- cached_briefs: week-keyed Comprehensive Brief cache.
--
-- Spec source: Master PRD §4.2 — Comprehensive output is cached until
-- the next Monday morning run. Key: (workspace_id, week_start) where
-- week_start is the ISO-8601 date of the most recent Monday 00:00 UTC.
--
-- Lifecycle: row written when run_brief_comprehensive() finishes; the
-- next Monday's scheduled run produces a row keyed on a new week_start;
-- old rows stay around for history (manual lookups in the support tool
-- pull a tenant's last N weeks).
--
-- Mirrors the cached_asks pattern (status / response / generated_at),
-- but the cache key is week-aligned rather than question-aligned.

create table if not exists cached_briefs (
    id            bigint generated always as identity primary key,
    workspace_id  text not null,
    week_start    date not null,
    dataset_slug  text,
    payload       jsonb not null default '{}'::jsonb,
    status        text not null default 'ready',
    error         text,
    generated_at  timestamptz not null default now()
);

create unique index if not exists cached_briefs_workspace_week_idx
    on cached_briefs (workspace_id, week_start);

create index if not exists cached_briefs_status_idx
    on cached_briefs (status);

alter table cached_briefs enable row level security;
-- No policies — backend (service_role) only.
