-- Roadmap doc storage — the company's uploaded roadmap (priorities anchor).
-- ONE row per company (UNIQUE company_id): the latest upload replaces the prior
-- one via upsert on_conflict=company_id. Holds the original file (base64) plus
-- the extracted text the weekly brief reads + the roadmapdoc artifact renders.
create table if not exists roadmap_doc (
  id              bigint generated always as identity primary key,
  company_id      uuid not null references companies (id) on delete cascade,
  filename        text not null,
  content_type    text,
  extracted_text  text not null default '',
  raw_b64         text,
  version         integer not null default 1,
  uploaded_at     timestamptz not null default now(),
  unique (company_id)
);
alter table roadmap_doc enable row level security;
create policy "srv_roadmap_doc" on roadmap_doc for all using (true) with check (true);
