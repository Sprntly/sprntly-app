-- Backlog items — the sequenced, ranked product backlog (the "sequence the rest
-- into a backlog" half of prioritization).
--
-- Synthesis ranks every theme by goal_adjusted_score (convergence base × goal
-- factor) and selects the top-N for the weekly brief. The REST of those ranked
-- candidates don't disappear — they are sequenced into this backlog so a single
-- synthesis run yields BOTH the brief AND a prioritized backlog behind it.
--
-- One row = one theme NOT in the current brief top-N, carrying its rank, score,
-- and a one-line triage rationale (from the backlog-triage skill). The unique
-- key (enterprise_id, theme_id) makes a re-run idempotent: re-sequencing the
-- same theme refreshes its rank/score in place rather than appending a duplicate.
--
-- "enterprise_id" == companies.id (same convention as the kg_* tables and
-- metric_points; see 20260603120000_kg_foundation.sql / 20260607000000_metric_points.sql).

create table if not exists backlog_items (
    id            uuid primary key default gen_random_uuid(),
    enterprise_id uuid not null references companies (id) on delete cascade,
    theme_id      text not null,
    hypothesis_id text,
    title         text not null,
    tag           text,
    rank          int not null,
    score         double precision not null,
    status        text not null default 'backlog'
                  check (status in ('backlog', 'in_progress', 'done', 'dismissed')),
    reasoning     text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    -- one backlog row per theme per enterprise; a re-run upserts in place.
    unique (enterprise_id, theme_id)
);

-- The backlog list reads one enterprise's items in rank order.
create index if not exists backlog_items_rank_idx
    on backlog_items (enterprise_id, rank);
