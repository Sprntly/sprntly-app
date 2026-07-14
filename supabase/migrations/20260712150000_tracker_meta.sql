-- Cached per-destination tracker vocabulary (TrackerMeta): the statuses,
-- priorities, issue types, and custom-field definitions of ONE tracker
-- destination (a ClickUp list / Jira project), normalized to the
-- provider-agnostic shape in app/connectors/tracker_meta.py. One row per
-- (company, provider, destination) — metadata is a property of the
-- destination, shared by every PRD syncing to it, which is why it does NOT
-- live on prd_ticket_sync (per-PRD) or connections.config (read on every
-- token resolution; createmeta blobs run to tens of KB).
--
-- Written on destination bind and re-fetched when `fetched_at` exceeds the
-- TTL (app/db/tracker_meta.py). Additive + idempotent → safe under
-- migrate-on-deploy.
create table if not exists tracker_meta (
  id             bigint generated always as identity primary key,
  company_id     uuid not null,
  provider       text not null,                      -- 'clickup' | 'jira'
  destination_id text not null,                      -- ClickUp list id / Jira project key
  meta           jsonb not null default '{}'::jsonb, -- normalized TrackerMeta payload
  fetched_at     timestamptz not null default now(),
  created_at     timestamptz not null default now(),
  unique (company_id, provider, destination_id)
);
alter table tracker_meta enable row level security;
drop policy if exists "srv_tracker_meta" on tracker_meta;
create policy "srv_tracker_meta" on tracker_meta for all using (true) with check (true);
