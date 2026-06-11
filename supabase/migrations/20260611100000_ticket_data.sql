-- Ticket data storage — edits, attachments, comments
-- Keyed by a stable ticket_key (e.g. "MER-481") + company_id

-- Ticket edits (description, acceptance criteria overrides)
create table if not exists ticket_edits (
  id              bigint generated always as identity primary key,
  company_id      uuid not null,
  ticket_key      text not null,
  description     text not null default '',
  acceptance_criteria jsonb not null default '[]'::jsonb,
  updated_at      timestamptz not null default now(),
  unique (company_id, ticket_key)
);
alter table ticket_edits enable row level security;
create policy "srv_ticket_edits" on ticket_edits for all using (true) with check (true);

-- Ticket attachments
create table if not exists ticket_attachments (
  id              bigint generated always as identity primary key,
  company_id      uuid not null,
  ticket_key      text not null,
  label           text not null,
  sub             text not null default '',
  created_at      timestamptz not null default now()
);
create index if not exists idx_ticket_attachments_key on ticket_attachments(company_id, ticket_key);
alter table ticket_attachments enable row level security;
create policy "srv_ticket_attachments" on ticket_attachments for all using (true) with check (true);

-- Ticket comments
create table if not exists ticket_comments (
  id              bigint generated always as identity primary key,
  company_id      uuid not null,
  ticket_key      text not null,
  author          text not null default 'user',
  body            text not null,
  created_at      timestamptz not null default now()
);
create index if not exists idx_ticket_comments_key on ticket_comments(company_id, ticket_key);
alter table ticket_comments enable row level security;
create policy "srv_ticket_comments" on ticket_comments for all using (true) with check (true);
