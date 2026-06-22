-- Design Agent usage ledger: one row per user-action LLM run (full prototype
-- generation + comment-driven iteration), capturing token + cost from day one
-- for billing and observability. Token/cost backfill is impossible after the
-- fact, so the row is written at the user-action grain (the bg-runner terminals),
-- not at the telemetry primitive (which would double-count the internal repair).
--
-- Workspace isolation per the new-table convention: `workspace_id text not null`
-- with NO default — the runtime populates it from the caller's company id at
-- insert time. A baked-in default would ship rows under the wrong tenant.
--
-- FK action: `on delete set null` (NOT cascade) on both FKs. A billing ledger
-- must survive deletion of the prototype or PRD it refers to, so a deleted
-- prototype/PRD nulls the reference but keeps the usage row and its cost.

create table if not exists design_agent_usage_events (
  id                          bigint generated always as identity primary key,
  workspace_id                text not null,                 -- NO default; from the caller's company id at insert
  prd_id                      bigint references prds(id) on delete set null,
  prototype_id                bigint references prototypes(id) on delete set null,  -- set null, NOT cascade: ledger survives prototype deletion
  kind                        text not null check (kind in ('full_generation','iteration')),
  status                      text not null check (status in ('started','succeeded','failed')),
  trigger_comment_id          bigint,                        -- iterations only (the applied comment); null for generations
  model                       text,
  input_tokens                bigint,
  output_tokens               bigint,
  cache_creation_input_tokens bigint,
  cache_read_input_tokens     bigint,
  est_cost_usd                numeric,
  error_class                 text,
  created_at                  timestamptz not null default now(),
  completed_at                timestamptz
);

create index if not exists design_agent_usage_events_workspace_id_idx on design_agent_usage_events (workspace_id);
create index if not exists design_agent_usage_events_created_at_idx   on design_agent_usage_events (created_at);
create index if not exists design_agent_usage_events_kind_idx         on design_agent_usage_events (kind);
create index if not exists design_agent_usage_events_status_idx       on design_agent_usage_events (status);

alter table design_agent_usage_events enable row level security;
-- No policies (matches the existing pattern — the backend uses the service-role
-- key and bypasses RLS; the browser has no direct table access).
