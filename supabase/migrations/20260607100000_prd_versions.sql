-- PRD version history table
-- Stores snapshots of PRD content for version control
create table if not exists prd_versions (
  id          bigint generated always as identity primary key,
  prd_id      bigint not null references prds(id) on delete cascade,
  version_number int not null default 1,
  title       text not null default '',
  payload_md  text not null default '',
  saved_by    text not null default 'user',
  saved_at    timestamptz not null default now()
);

-- Index for fast lookup by prd_id
create index if not exists idx_prd_versions_prd_id on prd_versions(prd_id);

-- RLS: allow all for service role (backend uses service-role key)
alter table prd_versions enable row level security;
create policy "Service role full access" on prd_versions
  for all using (true) with check (true);
