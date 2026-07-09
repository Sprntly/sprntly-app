-- Idempotent Jira push: maps a ticket (company + destination project + the
-- content-derived ticket id) to the Jira issue key it created, so a re-push
-- resolves to the same issue instead of creating a duplicate. Mirrors
-- clickup_task_map. Additive + idempotent → safe under migrate-on-deploy.
create table if not exists jira_issue_map (
  id             bigint generated always as identity primary key,
  company_id     uuid not null,
  project_key    text not null,
  ticket_id      text not null,       -- Story.stable_id (hash of title + body)
  jira_issue_key text not null,       -- e.g. "PROJ-123"
  updated_at     timestamptz not null default now(),
  unique (company_id, project_key, ticket_id)
);
alter table jira_issue_map enable row level security;
create policy "srv_jira_issue_map" on jira_issue_map for all using (true) with check (true);
