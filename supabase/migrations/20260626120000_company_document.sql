-- Company documents storage — the strategy/context files a PM uploads during
-- the onboarding strategy step (design scene onbstrat). The GENERALIZED sibling
-- of roadmap_doc / company_template: a SINGLE table with a `doc_type`
-- discriminator (ceo_memo | team_priorities | research | company_strategy)
-- instead of one table per kind. MANY rows per company (like company_template):
-- each upload is its own row, scoped to the company. Holds the original file
-- (base64) plus the extracted text (the same converter the roadmap/template
-- paths use) for a future agent-context follow-up — STORED only for now.
--
-- Timestamp 20260626120000 chosen to be strictly LATEST + unique (last existing
-- migration is 20260625120000) so `db push` records it cleanly on auto-apply —
-- avoiding the duplicate-version collision roadmap_doc hit (#491).
create table if not exists company_document (
  id              uuid primary key default gen_random_uuid(),
  company_id      uuid not null references companies (id) on delete cascade,
  doc_type        text not null
                    check (doc_type in (
                      'ceo_memo', 'team_priorities', 'research', 'company_strategy'
                    )),
  filename        text not null,
  content_type    text,
  extracted_text  text not null default '',
  raw_b64         text,
  uploaded_at     timestamptz not null default now()
);
create index if not exists company_document_company_idx
  on company_document (company_id);
create index if not exists company_document_company_type_idx
  on company_document (company_id, doc_type);
alter table company_document enable row level security;
-- Idempotent policy create: drop-if-exists first so this migration re-applies
-- cleanly on any environment where an earlier run already created the policy
-- (cf. roadmap_doc / company_template).
drop policy if exists "srv_company_document" on company_document;
create policy "srv_company_document" on company_document for all using (true) with check (true);
