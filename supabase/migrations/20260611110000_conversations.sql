-- Chat conversations table — persists user chat history
create table if not exists conversations (
  id              bigint generated always as identity primary key,
  company_id      uuid not null,
  user_id         uuid,
  title           text not null default '',
  preview         text not null default '',
  agent_type      text not null default 'ask',
  query           text not null default '',
  reply           text not null default '',
  pinned          boolean not null default false,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index if not exists idx_conversations_company on conversations(company_id, created_at desc);
alter table conversations enable row level security;
create policy "srv_conversations" on conversations for all using (true) with check (true);
