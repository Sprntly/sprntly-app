-- Per-PRD ticket-tracker sync state: which tracker (ClickUp list / Jira
-- project) a PRD's tickets sync to, plus the last sync outcome and the pulled
-- per-ticket tracker state. One row per (company, prd): a PRD syncs with ONE
-- tool at a time — switching tools replaces the row's provider/destination.
--
-- Created by the first manual push (the user picks the destination); from then
-- on the scheduler's ticket_sync job two-way syncs every row with
-- auto_sync=true. `statuses` persists the pulled tracker state (status /
-- assignee / url per ticket id) so the UI and MCP read it without a live
-- tracker call. Additive + idempotent → safe under migrate-on-deploy.
create table if not exists prd_ticket_sync (
  id               bigint generated always as identity primary key,
  company_id       uuid not null,
  prd_id           bigint not null,
  provider         text not null,                    -- 'clickup' | 'jira'
  destination_id   text not null,                    -- ClickUp list id / Jira project key
  destination_name text,
  auto_sync        boolean not null default true,
  sync_status      text not null default 'idle',     -- 'idle' | 'syncing'
  sync_started_at  timestamptz,
  last_synced_at   timestamptz,
  last_error       text,
  statuses         jsonb not null default '{}'::jsonb, -- ticket_id -> {status, assignee, url}
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (company_id, prd_id)
);
alter table prd_ticket_sync enable row level security;
create policy "srv_prd_ticket_sync" on prd_ticket_sync for all using (true) with check (true);
