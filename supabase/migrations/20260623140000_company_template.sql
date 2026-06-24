-- Company templates storage — the company's gold-standard PRD examples
-- ("what good looks like"). MANY rows per company (unlike roadmap_doc's one),
-- each individually listed and deletable. Holds the original file (base64) plus
-- the extracted text the prd-author skill reads as FORMAT/STYLE EXEMPLARS so
-- generated PRDs match the company's structure & voice.
create table if not exists company_template (
  id              uuid primary key default gen_random_uuid(),
  company_id      uuid not null references companies (id) on delete cascade,
  label           text,
  type            text not null default 'prd',
  filename        text not null,
  content_type    text,
  extracted_text  text not null default '',
  raw_b64         text,
  uploaded_at     timestamptz not null default now()
);
create index if not exists company_template_company_idx
  on company_template (company_id);
alter table company_template enable row level security;
-- Idempotent policy create: drop-if-exists first so this migration re-applies
-- cleanly on any environment where an earlier run already created the policy
-- (cf. roadmap_doc / #491).
drop policy if exists "srv_company_template" on company_template;
create policy "srv_company_template" on company_template for all using (true) with check (true);
