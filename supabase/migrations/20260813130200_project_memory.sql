-- Project memory, layered: discrete attributable entries (source of
-- truth) plus a cached, derived synthesized summary on top.
--
-- `project_memory_entries` is discrete rows rather than one editable blob
-- specifically to preserve provenance (who said it vs. what the agent
-- promoted) and to allow per-entry edit/removal without blending away a
-- user's verbatim guardrail. Provenance is a stored fact, not derivable,
-- and exactly one of the two provenance columns is set — enforced by the
-- XOR check below (`<>` between two booleans is XOR in Postgres).
create table if not exists project_memory_entries (
  id                     bigint generated always as identity primary key,
  project_id             bigint not null references projects(id) on delete cascade,
  body                   text not null,
  author_user_id         uuid references auth.users(id),
  promoted_by            text check (promoted_by in ('agent')),
  source_conversation_id bigint references conversations(id),
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  constraint pme_one_provenance check ((author_user_id is not null) <> (promoted_by is not null))
);
create index if not exists idx_pme_project
  on project_memory_entries(project_id, updated_at desc);

alter table project_memory_entries enable row level security;
drop policy if exists "srv_project_memory_entries" on project_memory_entries;
create policy "srv_project_memory_entries" on project_memory_entries for all using (true) with check (true);

-- One cached row per project: "what this project knows", synthesized from
-- the entries above by a bounded LLM call. This is the one place in the
-- project data model that stores derived state rather than deriving it at
-- read time — justified because regenerating it is an LLM call, and
-- re-running that on every project open would add latency and spend on
-- every read for no benefit. `stale` is flipped true on entry mutation and
-- cleared by the (async, debounced) regeneration; the summary itself is
-- read-only to the user, never hand-edited.
create table if not exists project_memory_summary (
  project_id   bigint primary key references projects(id) on delete cascade,
  summary_md   text not null,
  entry_count  int not null,
  generated_at timestamptz not null default now(),
  stale        boolean not null default false
);

alter table project_memory_summary enable row level security;
drop policy if exists "srv_project_memory_summary" on project_memory_summary;
create policy "srv_project_memory_summary" on project_memory_summary for all using (true) with check (true);
