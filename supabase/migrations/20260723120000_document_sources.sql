-- Uploaded document sources — the user's own business documents as a connector.
--
-- A "document source" is a NAMED bundle of files the user uploaded, plus an
-- optional description of what those documents are. It is surfaced in Settings
-- alongside the catalogue connectors (provider `uploads`, see
-- backend/app/connectors/uploads.py) and pulled into the knowledge graph by the
-- uploads puller exactly like Fireflies or Jira records.
--
-- Shape follows company_document (20260626120000): the original bytes in
-- `raw_b64` for a future source-download affordance, the converted markdown in
-- `extracted_text` (produced by app.ingest.convert — the SAME converter the
-- dataset / roadmap / template upload paths use), and company_id + a nullable
-- workspace_id for scoping.
--
-- Scoping: COMPANY-scoped, matching the connector decision recorded in
-- 20260716124000_workspace_scope_columns.sql ("connectors are company-wide by
-- decision"). workspace_id is recorded on each source anyway so a later
-- per-workspace filter is a read change, not another migration.

create table if not exists document_source (
    id           uuid primary key default gen_random_uuid(),
    company_id   uuid not null references companies (id) on delete cascade,
    workspace_id uuid references workspaces (id) on delete cascade,
    name         text not null,
    description  text not null default '',
    created_at   timestamptz not null default now()
);

create index if not exists document_source_company_idx
    on document_source (company_id);
create index if not exists document_source_workspace_id_idx
    on document_source (workspace_id);

create table if not exists document_source_file (
    id             uuid primary key default gen_random_uuid(),
    source_id      uuid not null references document_source (id) on delete cascade,
    -- Denormalized from the parent so every read can be tenant-filtered in one
    -- query (the same belt-and-braces company_id every other table carries).
    company_id     uuid not null references companies (id) on delete cascade,
    filename       text not null,
    content_type   text,
    size_bytes     bigint not null default 0,
    extracted_text text not null default '',
    raw_b64        text,
    uploaded_at    timestamptz not null default now()
);

create index if not exists document_source_file_source_idx
    on document_source_file (source_id);
create index if not exists document_source_file_company_idx
    on document_source_file (company_id);
