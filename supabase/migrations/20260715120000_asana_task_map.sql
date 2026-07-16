-- Idempotent Asana push: maps a ticket (company + destination project gid +
-- the content-derived ticket id) to the Asana task gid it created, so a
-- re-push resolves to the same task instead of creating a duplicate. Mirrors
-- clickup_task_map / jira_issue_map. Additive + idempotent → safe under
-- migrate-on-deploy.
create table if not exists asana_task_map (
  id              bigint generated always as identity primary key,
  company_id      uuid not null,
  project_gid     text not null,
  ticket_id       text not null,       -- Story.stable_id (hash of title + body)
  asana_task_gid  text not null,       -- Asana task gid
  updated_at      timestamptz not null default now(),
  unique (company_id, project_gid, ticket_id)
);
alter table asana_task_map enable row level security;
create policy "srv_asana_task_map" on asana_task_map for all using (true) with check (true);
