-- Delegation events: an immutable record of who handed what task to whom,
-- and where the assignee was told about it. Inputs/facts only — NO status
-- column. The delegation is a fact like a `project_memory_entries` row; the
-- future ledger (out of v1, a separate re-quote) appends its own append-only
-- `delegation_events` log off `project_delegations.id` and derives current
-- status at read time, never repainting this table.
--
-- No `workspace_id` column, deliberately diverging from the general
-- new-table convention: this ticket follows the Projects-era sibling
-- pattern already set by `project_memory_entries` / `project_chat_members`
-- (see `20260811120200_project_memory.sql`,
-- `20260811120100_conversations_project_columns.sql`) — scope via
-- `project_id -> projects(company_id, workspace_id)`, tenancy enforced at
-- the route layer, and a server-role RLS policy rather than a per-row
-- `workspace_id` column.
create table if not exists project_delegations (
  id                        bigint generated always as identity primary key,
  project_id                bigint not null references projects(id)         on delete cascade,
  assigner_user_id          uuid   not null references auth.users(id)        on delete cascade,
  assignee_user_id          uuid   not null references auth.users(id)        on delete cascade,
  task_summary              text   not null,
  source_conversation_id    bigint references conversations(id) on delete set null,
  source_turn_id            bigint,
  delivered_conversation_id bigint references conversations(id) on delete set null,
  delivered_turn_id         bigint,
  created_at                timestamptz not null default now()
  -- NO status column (AD-P17). The ledger (out of v1) adds an append-only
  -- delegation_events log later and DERIVES current status at read.
);
create index if not exists idx_project_delegations_project  on project_delegations(project_id, created_at desc);
create index if not exists idx_project_delegations_assignee on project_delegations(assignee_user_id, created_at desc);
create index if not exists idx_project_delegations_assigner on project_delegations(assigner_user_id, created_at desc);

alter table project_delegations enable row level security;
drop policy if exists "srv_project_delegations" on project_delegations;
create policy "srv_project_delegations" on project_delegations for all using (true) with check (true);
