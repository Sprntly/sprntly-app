-- Opt-in worker queue for Design Agent generation isolation (Tier 2)
--
-- Tier 0 (graceful drain) + Tier 1 (a per-process concurrency semaphore) keep a
-- single API process from running two heavy generations at once, but the heavy
-- work (LLM recreate loop + vite build + headless Chromium screenshot) still
-- runs INSIDE the API request process. On the prod t3.micro that pins both
-- cores and starves a concurrent /locate (the 504s). Tier 2 is the true
-- isolation: a SEPARATE `python -m app.worker` process drains this table, so the
-- heavy work leaves the API process entirely.
--
-- This requires a 2nd systemd unit (the client's deploy action), so it is
-- OPT-IN behind DESIGN_AGENT_WORKER_ENABLED with a test-proven fallback: when
-- the flag is off, no worker heartbeat is fresh, or this table is missing,
-- POST /generate degrades to exactly today's in-process asyncio.create_task
-- path. A box that has not deployed the worker unit behaves identically to
-- today.
--
-- Convention: mirrors 20260615120000_design_agent_map_cache.sql — identity PK,
-- create-if-not-exists, RLS enabled with a single srv_* all-access policy, and
-- new SIBLING tables only (never ALTER an existing table).

create table if not exists design_agent_jobs (
  id           bigint generated always as identity primary key,
  prototype_id bigint not null,
  workspace_id uuid   not null,
  payload      jsonb  not null,
  -- queued  -> claimable by a worker
  -- claimed -> a worker owns it and is running it (claimed_by/claimed_at set)
  -- done    -> generation finished (the prototype row is already 'ready')
  -- error   -> generation failed (the prototype row is already 'failed')
  status       text   not null default 'queued',
  claimed_by   text,
  claimed_at   timestamptz,
  attempts     int    not null default 0,
  error        text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

-- One job per prototype — dedupe parity with find_existing_prototype: a
-- double-click on Generate that re-enters the enqueue branch must not fan out a
-- second job for the same prototype. enqueue_job upserts on this key.
create unique index if not exists uq_design_agent_jobs_prototype
  on design_agent_jobs (prototype_id);

-- The claimable set: a partial index on (status, created_at) where status =
-- 'queued' keeps claim_next_job's "oldest queued first" scan cheap as done/error
-- rows accumulate.
create index if not exists idx_design_agent_jobs_queued
  on design_agent_jobs (status, created_at)
  where status = 'queued';

alter table design_agent_jobs enable row level security;
create policy "srv_design_agent_jobs" on design_agent_jobs
  for all using (true) with check (true);

-- Worker liveness heartbeat (single row, id = 1). /generate consults it before
-- enqueuing: only when a heartbeat is FRESH (updated within N seconds) does the
-- enqueue branch arm — otherwise it falls back to the in-process path, so a box
-- without a running worker unit never strands a job in 'queued' forever. A
-- dedicated single-row table (not a sentinel row in design_agent_jobs) keeps the
-- heartbeat write off the queue's hot path and out of its unique/partial
-- indexes.
create table if not exists design_agent_worker_heartbeat (
  id         int primary key default 1,
  worker_id  text,
  updated_at timestamptz not null default now()
);

alter table design_agent_worker_heartbeat enable row level security;
create policy "srv_design_agent_worker_heartbeat" on design_agent_worker_heartbeat
  for all using (true) with check (true);
