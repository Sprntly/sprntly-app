-- Projects: a shared container that gathers a topic's artifacts (PRDs,
-- evidence, prototypes, reports, ticket sets) plus the collaboration layer
-- that sits on top of them (group chat, per-user chats, project memory —
-- added in later migrations in this set).
--
-- Tenancy follows the mainline `conversations` / `ticket_sets` convention:
-- bigint identity PK, `company_id uuid` + `workspace_id uuid` FKs, `on
-- delete cascade`. This is deliberately NOT the Design-Agent
-- `workspace_id TEXT NOT NULL` + JWT-`aud` pattern — that convention is
-- scoped to the design-agent surface only.
--
-- No `status` column: `origin` is provenance (how the project came to
-- exist — manual create, auto-forked from a PRD, or spun up from an
-- existing artifact), not a lifecycle stage. A manual stage field goes
-- stale; if a status concept is wanted later it must be derived at read
-- time, not stored here.
--
-- Artifacts are attached via a join table rather than an owning FK on each
-- of the five artifact tables (prds / evidences / prototypes / reports /
-- ticket_sets): those tables already carry inconsistent tenancy keys
-- (dataset slug, workspace_id, company_id) and a project must be able to
-- hold any of them, and an artifact must be able to move between
-- projects — an owning FK on the artifact side forbids both. No FK from
-- `project_artifacts` to the five artifact tables themselves, for the same
-- reason; referential integrity to the artifact is validated at write time
-- by the caller and tolerated-stale on read, matching how the existing
-- cross-artifact fan-out already behaves.

create table if not exists projects (
  id           bigint generated always as identity primary key,
  company_id   uuid not null references companies(id) on delete cascade,
  workspace_id uuid not null references workspaces(id) on delete cascade,
  name         text not null,
  origin       text not null default 'manual'
                 check (origin in ('manual', 'prd_auto', 'artifact')),
  created_by   uuid not null references auth.users(id),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index if not exists idx_projects_company_ws
  on projects(company_id, workspace_id, updated_at desc);

alter table projects enable row level security;
drop policy if exists "srv_projects" on projects;
create policy "srv_projects" on projects for all using (true) with check (true);

-- Membership = access: being a project member is sufficient to read the
-- project's chats, memory, and artifact list. No role column in v1 — all
-- members have equal access; a role column is the extension seam if
-- view-vs-edit tiers are wanted later.
create table if not exists project_members (
  project_id bigint not null references projects(id) on delete cascade,
  user_id    uuid not null references auth.users(id) on delete cascade,
  added_at   timestamptz not null default now(),
  primary key (project_id, user_id)
);

alter table project_members enable row level security;
drop policy if exists "srv_project_members" on project_members;
create policy "srv_project_members" on project_members for all using (true) with check (true);

-- Join table binding a project to the artifacts it contains. `artifact_id`
-- is deliberately untyped-by-FK (see module comment above) — `artifact_type`
-- selects which of the five artifact tables it points into.
create table if not exists project_artifacts (
  project_id    bigint not null references projects(id) on delete cascade,
  artifact_type text not null
                  check (artifact_type in ('prd', 'evidence', 'prototype', 'report', 'ticket_set')),
  artifact_id   bigint not null,
  added_at      timestamptz not null default now(),
  primary key (project_id, artifact_type, artifact_id)
);
create index if not exists idx_project_artifacts_lookup
  on project_artifacts(project_id, added_at desc);

alter table project_artifacts enable row level security;
drop policy if exists "srv_project_artifacts" on project_artifacts;
create policy "srv_project_artifacts" on project_artifacts for all using (true) with check (true);
