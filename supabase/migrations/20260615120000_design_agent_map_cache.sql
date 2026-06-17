-- Durable L2 for the codebase-map cache
--
-- An L2 behind the process-local LRU in
-- backend/app/design_agent/codebase_map/service.py, keyed by
-- (installation_id, repo, commit_sha). A deploy/restart wipes the in-process
-- LRU, so today EVERY post-deploy locate re-pays the full cold map build. This
-- durable layer means a deploy no longer throws away a still-valid map: the
-- first post-deploy locate is warm.
--
-- The map is keyed on commit_sha, so a new commit naturally produces a fresh
-- key (the natural invalidation). The row stores the serialized MapResult
-- (a Pydantic v2 model -> model_dump(mode="json")) as jsonb; it round-trips
-- losslessly via MapResult.model_validate(...). created_at drives the TTL
-- filter on read + an opportunistic sweep.
--
-- Pre-warming on connect + webhook-driven invalidation are handled separately;
-- this migration is the durable cache layer only.

create table if not exists design_agent_map_cache (
  id              bigint generated always as identity primary key,
  installation_id bigint not null,
  repo            text   not null,
  commit_sha      text   not null,
  payload         jsonb  not null,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

-- The cache key: one row per (installation_id, repo, commit_sha). The UPSERT in
-- db/design_agent_map_cache.py conflicts on this unique to refresh in place.
create unique index if not exists uq_design_agent_map_cache_key
  on design_agent_map_cache (installation_id, repo, commit_sha);

-- Supports the TTL read filter + the opportunistic expiry sweep (range scan on
-- created_at).
create index if not exists idx_design_agent_map_cache_created_at
  on design_agent_map_cache (created_at);

alter table design_agent_map_cache enable row level security;
create policy "srv_design_agent_map_cache" on design_agent_map_cache
  for all using (true) with check (true);
