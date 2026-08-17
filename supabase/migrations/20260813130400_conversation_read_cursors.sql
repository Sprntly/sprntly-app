-- Per-(conversation, user) last-read cursor — an INPUT, not a derived
-- state. Unread is derived at read time (latest_individual_turn_id >
-- last_read_turn_id), never stored as a boolean/count column here
-- (AD-P3/AD-P20 inputs-only + derive-at-read;
-- [[feedback_prefer-inference-over-stored-derived-state]]).
--
-- No `workspace_id` column, deliberately diverging from the general
-- new-table convention — same Projects-era sibling pattern already set by
-- `project_delegations`/`project_memory_entries`/`project_chat_members`
-- (see `20260813130300_project_delegations.sql`,
-- `20260813130100_conversations_project_columns.sql`): scoped via
-- `conversation_id -> conversations -> project`, tenancy enforced at the
-- route layer (`_require_project_member`), server-role RLS rather than a
-- per-row `workspace_id` column.
create table if not exists conversation_read_cursors (
  conversation_id   bigint not null references conversations(id) on delete cascade,
  user_id           uuid   not null references auth.users(id)     on delete cascade,
  last_read_turn_id bigint not null default 0,
  updated_at        timestamptz not null default now(),
  primary key (conversation_id, user_id)
);

alter table conversation_read_cursors enable row level security;
drop policy if exists "srv_conversation_read_cursors" on conversation_read_cursors;
create policy "srv_conversation_read_cursors" on conversation_read_cursors for all using (true) with check (true);
