-- RECONSTRUCTED 2026-07-30: this migration was applied directly to the shared
-- Supabase project (version 20260730120000 recorded in
-- supabase_migrations.schema_migrations) but its file was never committed,
-- which blocked every deploy with "Remote migration versions not found in
-- local migrations directory". Statements below are verbatim from
-- schema_migrations.statements for that version; committing the file is a
-- no-op against the database (the version is already recorded) and exists
-- purely so `supabase db push` recognizes the remote history.
--
-- Reports — the durable home for skill-generated HTML report documents
-- (voice-of-customer-report, competitive-intelligence-review,
-- public-feedback-report, …).
--
-- Until now a report existed ONLY as `ask_jobs.response.answer`: a
-- self-contained HTML document that the chat thread renders in a sandboxed
-- iframe (web/app/components/shared/HtmlReportView.tsx) and then effectively
-- loses. It was never listed on /artifacts, could not be downloaded, and could
-- not be shared. This table is the record that makes a report a first-class
-- artifact alongside PRDs, prototypes and evidence.
--
-- Additive only: no existing table, index, or policy is touched. Nothing reads
-- this table until the artifacts surface lands (a later slice) — the capture
-- write is best-effort, so an empty table changes no behaviour.
--
-- Scoping mirrors custom_skills (product decision 2026-07-28): reads filter by
-- company_id, so every workspace in a company shares one report library.
-- workspace_id records WHICH workspace generated it. Unlike custom_skills it is
-- NULLABLE: capture runs in the ask background worker, and a report must be
-- saved even on a path that carries no workspace context rather than lost to a
-- not-null violation.
--
-- ATTACHMENT (provenance). A report is generated *somewhere*, and that somewhere
-- is part of what it is: `conversation_id` is the chat room it was produced in,
-- `prd_id` the PRD whose context it was produced against. Both are copied from
-- the originating `ask_jobs` row, so each is set exactly when the ask carried it
-- and NULL when it did not — presence of the id IS the attachment. They are
-- links, not ownership: deleting the conversation or PRD nulls the link
-- (`on delete set null`) and the report survives in the artifacts library.
--
--   skill        — routed skill id (`ask_jobs.response._skill`), e.g.
--                  'voice-of-customer-report'. What the report IS; the artifacts
--                  row derives its label/badge from this.
--   title        — from the document's <title>/<h1>, falling back to the skill's
--                  humanised label. Denormalised so listing never parses HTML.
--   html         — the rendered, fence-stripped document. Source of truth for the
--                  viewer, the PDF render and (later) the share link.
--   question     — the prompt that produced it, which carries the run's scope
--                  ("VoC for last quarter, enterprise only").
--   ask_id       — the originating ask job, for audit and re-capture dedupe.

create table if not exists reports (
    id              bigint generated always as identity primary key,
    company_id      uuid        not null references companies (id) on delete cascade,
    workspace_id    uuid        references workspaces (id) on delete set null,
    skill           text        not null,
    title           text        not null default '',
    html            text        not null default '',
    question        text        not null default '',
    ask_id          bigint,
    conversation_id bigint      references conversations (id) on delete set null,
    prd_id          bigint      references prds (id) on delete set null,
    created_at      timestamptz not null default now()
);

-- The artifacts listing: newest-first per company.
create index if not exists reports_company_idx on reports (company_id, id desc);

-- The attachment lookups: "which reports hang off this chat / this PRD".
create index if not exists reports_conversation_idx on reports (conversation_id);

create index if not exists reports_prd_idx on reports (prd_id);

-- Re-capture dedupe: one report per originating ask job.
create unique index if not exists reports_ask_id_uniq on reports (ask_id)
    where ask_id is not null;

alter table reports enable row level security;

-- Idempotent policy create: drop-if-exists first so this migration re-applies
-- cleanly on an environment where an earlier run already created the policy.
drop policy if exists "srv_reports" on reports;

create policy "srv_reports" on reports for all using (true) with check (true);
