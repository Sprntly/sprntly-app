-- Rename llm_usage_events.workspace_id -> company_id.
--
-- The column always held a COMPANY id (the tenant), never a `workspaces` row id
-- — the metering binds `app.llm_keys.current_company_id()` and the API filters
-- on `require_company`'s company_id. It was named `workspace_id` to match the
-- older `design_agent_usage_events` convention, which predates the real
-- multi-workspace layer (2026-07). Now that `workspaces` is an actual table
-- meaning something narrower, the old name reads as a promise the data does not
-- keep, and it has already caused confusion twice.
--
-- Scope is deliberately this table only. `design_agent_usage_events` keeps its
-- name: renaming it would be a second, unrelated change to a table with its own
-- callers, and it is not the one the usage dashboard reads.
--
-- Delivered as a follow-up rather than an edit to 20260725120000: that
-- migration is already recorded in `supabase_migrations.schema_migrations` on
-- the shared DB, so editing it in place would leave the file describing
-- something the database never ran. This way both a fresh database (create then
-- rename) and the already-migrated one (rename only) end up identical.

alter table llm_usage_events rename column workspace_id to company_id;

alter index llm_usage_events_workspace_created_idx
  rename to llm_usage_events_company_created_idx;
alter index llm_usage_events_workspace_feature_idx
  rename to llm_usage_events_company_feature_idx;

-- The rollup's parameter is renamed too, so the whole surface speaks one word.
-- DROP + CREATE rather than CREATE OR REPLACE: Postgres refuses to change the
-- name of an input parameter in a replace ("cannot change name of input
-- parameter"), so the old signature has to go first.
drop function if exists llm_usage_summary(text, timestamptz, timestamptz, text);

create function llm_usage_summary(
  p_company_id text,
  p_from       timestamptz,
  p_to         timestamptz,
  p_tz         text default 'UTC'
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
  where e.company_id = p_company_id
    and e.created_at >= p_from
    and e.created_at <  p_to
  group by 1, 2, 3, 4, 5
  order by 1;
$$;
