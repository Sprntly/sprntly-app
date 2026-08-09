-- Add `provider` to the usage rollup so the dashboard can split spend by
-- provider, not just by model.
--
-- `llm_usage_events.provider` has existed since the ledger shipped
-- (20260725120000) and every row carries it — but `llm_usage_summary` neither
-- selected nor grouped by it, so the column was write-only. `routes/usage.py`
-- has always built a `by_provider` breakdown guarded on `"provider" in rows[0]`,
-- which was therefore always false: the API shipped the key as a permanent empty
-- list and the UI had nothing to render. That was invisible while every call was
-- Anthropic; with a company able to run on OpenAI, "which provider did this
-- spend go to" becomes the first question the page has to answer.
--
-- DROP + CREATE rather than CREATE OR REPLACE: Postgres refuses to change a
-- function's OUT-parameter list in a replace ("cannot change return type of
-- existing function"), and adding a column to the RETURNS TABLE is exactly that.
-- The signature (argument types) is unchanged, so `fetch_usage_summary`'s rpc
-- call is untouched — it simply receives one more column per row.
--
-- Adding `provider` to the GROUP BY can only SPLIT a bucket that previously
-- merged two providers, never merge two. No historical row changes value; a
-- pre-existing day/feature/model bucket keeps its totals because every row in it
-- carried provider='anthropic'.

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
  provider                    text,
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
    e.provider,
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
  group by 1, 2, 3, 4, 5, 6
  order by 1;
$$;
