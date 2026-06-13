-- Conversation turns — each message in a conversation thread
create table if not exists conversation_turns (
  id              bigint generated always as identity primary key,
  conversation_id bigint not null references conversations(id) on delete cascade,
  role            text not null default 'user',  -- 'user' or 'assistant'
  content         text not null default '',
  created_at      timestamptz not null default now()
);
create index if not exists idx_conv_turns_conv on conversation_turns(conversation_id, created_at);
alter table conversation_turns enable row level security;
create policy "srv_conv_turns" on conversation_turns for all using (true) with check (true);
