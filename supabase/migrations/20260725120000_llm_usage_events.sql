-- Unified LLM usage ledger: one row per model call, across EVERY surface
-- (PRD generation, prototype generation/iteration, chat, ideation, brief,
-- evidence, research, onboarding, KG ingest, embeddings).
--
-- Why a new table rather than reusing what exists:
--   * `agent_decision_log` is a JSONB audit spine — the gateway already writes
--     token/cost there, but it is not shaped for time-bucketed aggregation and
--     it only covers gateway callers.
--   * `design_agent_usage_events` is a per-ACTION lifecycle ledger (started →
--     succeeded/failed) for one surface. It stays as-is for that purpose.
--     This table is the per-CALL analytics grain and is the single source of
--     truth for the usage dashboard. Never sum the two together.
--
-- Rows are written from a metering proxy wrapped around the Anthropic client
-- factories, so a new feature is metered without touching the write path.
--
-- Workspace isolation per the new-table convention: `workspace_id text not null`
-- with NO default — the runtime populates it from the acting company id.
--
-- `est_cost_usd` is ESTIMATED (tokens x app.llm_telemetry.MODEL_PRICING), not a
-- billed amount: the provider APIs return token counts only, never dollars. An
-- unpriced model leaves this null and keeps the tokens, which are ground truth.

create table if not exists llm_usage_events (
  id                          bigint generated always as identity primary key,
  workspace_id                text not null,          -- NO default; from the acting company id
  user_id                     text,                   -- acting user when known; null for scheduled/background work
  feature                     text not null,          -- coarse surface, e.g. 'prd', 'design_agent', 'chat'
  operation                   text,                   -- finer action within the feature, e.g. 'generate', 'iterate'
  provider                    text not null default 'anthropic',
  model                       text,
  key_mode                    text not null default 'unknown'
                                check (key_mode in ('customer', 'platform', 'unknown')),
  input_tokens                bigint not null default 0,
  output_tokens               bigint not null default 0,
  cache_creation_input_tokens bigint not null default 0,
  cache_read_input_tokens     bigint not null default 0,
  est_cost_usd                numeric,                -- null when the model is unpriced
  latency_ms                  integer,
  status                      text not null default 'succeeded'
                                check (status in ('succeeded', 'failed')),
  error_class                 text,
  created_at                  timestamptz not null default now()
);

-- The dashboard's access pattern is always "this workspace, this date range",
-- then group in memory. One composite index serves every query shape.
create index if not exists llm_usage_events_workspace_created_idx
  on llm_usage_events (workspace_id, created_at desc);
-- Secondary: per-feature filtering/drilldown within a workspace.
create index if not exists llm_usage_events_workspace_feature_idx
  on llm_usage_events (workspace_id, feature);

alter table llm_usage_events enable row level security;
-- No policies (matches the existing pattern — the backend uses the service-role
-- key and bypasses RLS; the browser has no direct table access).


-- Server-side rollup for the dashboard. Pulling raw rows into the backend and
-- summing them in Python would move tens of thousands of rows per request for a
-- busy workspace; this collapses them in Postgres and returns at most
-- days x features x models x key_modes rows (a few thousand worst case, usually
-- far fewer). The API then slices that small result into the totals / daily
-- series / per-feature / per-model views without another round trip.
--
-- The day bucket is computed in the CALLER's timezone (`p_tz`, an IANA name) so
-- "today" on the chart matches the viewer's calendar day rather than UTC.
create or replace function llm_usage_summary(
  p_workspace_id text,
  p_from         timestamptz,
  p_to           timestamptz,
  p_tz           text default 'UTC'
)
returns table (
  day                         date,
  feature                     text,
  operation                   text,
  model                       text,
  key_mode                    text,
  calls                       bigint,
  failed_calls                bigint,
  input_tokens                bigint,
  output_tokens               bigint,
  cache_creation_input_tokens bigint,
  cache_read_input_tokens     bigint,
  est_cost_usd                numeric
)
language sql
stable
as $$
  select
    (e.created_at at time zone p_tz)::date            as day,
    e.feature,
    e.operation,
    e.model,
    e.key_mode,
    count(*)                                          as calls,
    count(*) filter (where e.status = 'failed')       as failed_calls,
    coalesce(sum(e.input_tokens), 0)                  as input_tokens,
    coalesce(sum(e.output_tokens), 0)                 as output_tokens,
    coalesce(sum(e.cache_creation_input_tokens), 0)   as cache_creation_input_tokens,
    coalesce(sum(e.cache_read_input_tokens), 0)       as cache_read_input_tokens,
    coalesce(sum(e.est_cost_usd), 0)                  as est_cost_usd
  from llm_usage_events e
  where e.workspace_id = p_workspace_id
    and e.created_at >= p_from
    and e.created_at <  p_to
  group by 1, 2, 3, 4, 5
  order by 1;
$$;
