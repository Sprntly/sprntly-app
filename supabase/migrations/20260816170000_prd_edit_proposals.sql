-- prd_edit_proposals: the transient, single-use store behind the
-- confirmation gate on project PRD edits from chat. When a project chat
-- agent is asked to edit a PRD it now PROPOSES the change instead of
-- writing it: the already-computed patch (proposed_html/proposed_title)
-- plus the original edit instruction are stored here keyed by an opaque
-- token, and nothing touches `prds` until the user explicitly confirms.
-- Confirm re-reads this row and commits exactly the stored patch, so the
-- applied content is byte-identical to what was proposed (no second edit
-- pass, no drift). Rows are single-use (deleted on apply) and short-lived
-- (`expires_at`), so this is a queue of transient inputs, not durable state.
--
-- Tenancy is stored DIRECTLY on the row (company_id + workspace_id columns),
-- unlike sibling project tables that derive tenancy through project_id. The
-- token lookup is keyed on an untrusted, caller-supplied value BEFORE any
-- project_id is known or trusted, so tenancy must be a column on the row for
-- the very first lookup to be tenant-scoped rather than derived through a
-- project_id an attacker could influence. The FK cascades keep a proposal
-- from outliving its PRD, project, or conversation.
--
-- Tenancy follows the Projects-domain uuid-FK convention (company_id /
-- workspace_id uuid not null references ..., matching 20260813130000), NOT
-- the design-agent `workspace_id text` + JWT-`aud` pattern.

create table if not exists prd_edit_proposals (
  token             text primary key,
  prd_id            bigint not null references prds(id) on delete cascade,
  project_id        bigint not null references projects(id) on delete cascade,
  conversation_id   bigint references conversations(id) on delete cascade,
  surface           text not null,
  company_id        uuid not null references companies(id) on delete cascade,
  workspace_id      uuid not null references workspaces(id) on delete cascade,
  instruction       text not null,
  base_html         text not null,
  proposed_title    text,
  proposed_html     text not null,
  summary           text,
  sections_changed  jsonb,
  client_message_id text,
  created_at        timestamptz not null default now(),
  expires_at        timestamptz not null
);

create index if not exists idx_prd_edit_proposals_tenant
  on prd_edit_proposals(company_id, workspace_id);

alter table prd_edit_proposals enable row level security;
drop policy if exists "srv_prd_edit_proposals" on prd_edit_proposals;
create policy "srv_prd_edit_proposals" on prd_edit_proposals for all using (true) with check (true);
