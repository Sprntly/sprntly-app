-- Persisted user-story tickets generated from a PRD.
-- One row per PRD (unique prd_id), upserted on (re)generation. Stores the
-- generated stories as JSON plus a `content_hash` of the rendered PRD they were
-- produced from, so the Tickets tab serves the cached set when the PRD is
-- unchanged and only regenerates when the PRD content actually changed.
create table if not exists prd_tickets (
  id              bigint generated always as identity primary key,
  company_id      uuid not null,
  prd_id          bigint not null references prds(id) on delete cascade,
  content_hash    text not null,
  stories         jsonb not null default '[]'::jsonb,
  status          text not null default 'ready',  -- ready | failed
  error           text,
  generated_at    timestamptz not null default now(),
  unique (prd_id)
);
create index if not exists idx_prd_tickets_company on prd_tickets(company_id);
alter table prd_tickets enable row level security;
create policy "srv_prd_tickets" on prd_tickets for all using (true) with check (true);
