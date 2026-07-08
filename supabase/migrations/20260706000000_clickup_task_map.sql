-- Idempotent ClickUp push: maps a ticket (company + destination list + the
-- content-derived ticket id) to the ClickUp task it created, so a re-push
-- UPDATEs that task instead of creating a duplicate. Additive + idempotent →
-- safe under migrate-on-deploy.
create table if not exists clickup_task_map (
  id              bigint generated always as identity primary key,
  company_id      uuid not null,
  list_id         text not null,
  ticket_id       text not null,      -- Story.stable_id (hash of title + body)
  clickup_task_id text not null,
  updated_at      timestamptz not null default now(),
  unique (company_id, list_id, ticket_id)
);
alter table clickup_task_map enable row level security;
create policy "srv_clickup_task_map" on clickup_task_map for all using (true) with check (true);
