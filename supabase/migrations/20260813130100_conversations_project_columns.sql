-- Additive group-chat columns on `conversations`. This does NOT retrofit
-- the existing single-owner model (`20260713120000_conversations_per_user.sql`,
-- whose whole point was to stop company-wide chat visibility) — every
-- pre-existing row, and every future per-user chat, keeps `project_id NULL`
-- and `kind='individual'` by column default, so the untouched per-user
-- ownership path (`user_id` scoping) is unaffected.
--
-- A project's group chat is a NEW, additive kind: `project_id=<id>,
-- kind='group'`. Its `user_id` is whoever created it; membership/authz for
-- posting is NOT `user_id` (that stays singular) but the new
-- `project_chat_members` roster below, read only on the group path.
-- Exactly one group-chat row per project is enforced by a partial unique
-- index rather than application logic, so it holds even under a race.
alter table conversations add column if not exists project_id bigint references projects(id) on delete set null;
alter table conversations add column if not exists kind text not null default 'individual'
      check (kind in ('individual', 'group'));

create index if not exists idx_conversations_project
  on conversations(project_id, kind, updated_at desc);

create unique index if not exists uq_one_group_chat_per_project
  on conversations(project_id) where kind = 'group';

-- Group-chat roster. Used only when `conversations.kind='group'` — v1
-- opens the group chat to every project member, so this roster is
-- effectively `project_members`, but keeping it as its own join table
-- makes the group authorization path explicit today and leaves room for a
-- narrower roster (e.g. topic chats) later without another migration.
create table if not exists project_chat_members (
  conversation_id bigint not null references conversations(id) on delete cascade,
  user_id         uuid not null references auth.users(id) on delete cascade,
  joined_at       timestamptz not null default now(),
  primary key (conversation_id, user_id)
);

alter table project_chat_members enable row level security;
drop policy if exists "srv_project_chat_members" on project_chat_members;
create policy "srv_project_chat_members" on project_chat_members for all using (true) with check (true);

-- Which human posted a given turn. Single-owner individual chats never
-- needed this (the owner is implied by the conversation); a multi-author
-- group chat does. Nullable: assistant turns (role='assistant') leave it
-- NULL, and pre-existing turns are unaffected.
alter table conversation_turns add column if not exists author_user_id uuid references auth.users(id);
