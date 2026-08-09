-- Synced skill folders: a GitHub folder a company imported skills from, kept
-- live instead of read once.
--
-- Importing skills from a repo was a one-shot pull. The modal asked for a repo,
-- a branch and a folder, discovered the skills in that folder, the user ticked
-- the ones they wanted, and then those three values were thrown away — nothing
-- on `custom_skills` recorded where a skill came from. Adding a skill to that
-- folder next week meant reopening the modal and retyping all three, so a team
-- whose methods live in `skills/` had to re-import by hand every time the
-- folder changed. This table is the memory that was missing.
--
-- The synced unit is the FOLDER (company + repo + ref + path), not the repo and
-- not the individual skills the user ticked. That is deliberate and it is the
-- product decision behind the whole feature: discovery treats every .md file
-- under the folder as a skill, so a synced folder means "every markdown file in
-- here is one of our skills, now and later". Pointing a source at a folder that
-- also holds a README will import that README as a skill — the folder choice is
-- the only thing scoping it, which is why the UI asks for a folder and the
-- 30-minute sweep never widens beyond one.
--
-- `ref` is stored exactly as the user typed it, EMPTY MEANING "the repo's
-- default branch" rather than resolved to a branch name at import time: a team
-- that renames its default branch should keep syncing, which a resolved-once
-- value would silently stop doing. `path` empty means the repo root, which the
-- import UI declines to sync for the README reason above but which the column
-- still represents honestly.
--
-- `last_commit_sha` is the sweep's short-circuit, not provenance: the sync
-- resolves the folder's current head SHA first and does nothing when it matches,
-- so a folder nobody has touched costs one API call per 30 minutes instead of a
-- tree walk plus a file read per skill. An installation's REST budget is 5,000
-- requests/hour shared with the Design Agent's codebase map, so this cheap path
-- is what makes a half-hourly sweep affordable across every tenant at once.
--
-- `installation_id` is stored rather than re-resolved per sweep because the
-- sweep has no request and therefore no caller company to resolve it from.
-- It is written from `find_github_installation_for_repo(repo, company_id)` at
-- import time, which is company-filtered, so the id on this row was proven to
-- belong to this company by an authenticated request — the sweep must never
-- accept one from anywhere else.
--
-- No company/workspace foreign keys, matching custom_skills: the tenant tables
-- are already the cascade root and route tests fabricate tenant ids.

create table if not exists skill_sources (
    id               uuid primary key default gen_random_uuid(),
    company_id       uuid not null references companies (id) on delete cascade,
    -- Which workspace set the sync up. Recorded, never a query filter — custom
    -- skills are company-scoped, so a synced folder is too.
    workspace_id     uuid,
    installation_id  bigint not null,
    repo             text not null,
    ref              text not null default '',
    path             text not null default '',
    last_commit_sha  text not null default '',
    last_synced_at   timestamptz,
    -- Last failure, user-readable and verbatim from the sync. Empty after a
    -- clean run: a source that recovered must not keep showing a stale error.
    last_error       text not null default '',
    -- False = the folder was imported with "keep synced" off, or turned off
    -- later. The row survives so re-enabling it does not lose last_commit_sha.
    active           boolean not null default true,
    created_by       uuid,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    -- One row per folder per company: re-importing the same repo+branch+folder
    -- updates this source rather than creating a second one that would sync the
    -- identical files twice and fight over the same skill names.
    constraint skill_sources_folder_uniq unique (company_id, repo, ref, path)
);

create index if not exists skill_sources_company_id_idx on skill_sources (company_id);
-- The sweep's own read: every active source across every tenant, oldest sync
-- first, so a backlog drains fairly instead of starving one company's folder.
create index if not exists skill_sources_active_idx
    on skill_sources (last_synced_at) where active;

alter table skill_sources enable row level security;
drop policy if exists "srv_skill_sources" on skill_sources;
create policy "srv_skill_sources" on skill_sources for all using (true) with check (true);

-- Which source produced a skill, NULL for every skill uploaded by hand.
--
-- This is what makes a synced skill identifiable after the fact, and it carries
-- one behavioural consequence: a skill with a source is not editable in place.
-- The repo is its source of truth, so the Skills screen disables the pencil and
-- the PATCH route refuses with a 409 — the alternative was letting someone edit
-- a method and watch the next sweep silently overwrite it half an hour later.
--
-- ON DELETE SET NULL, never cascade: removing a source must not delete the
-- skills it produced. Skills that came from a folder stay in the library on
-- their own terms — they simply stop updating and become editable again.
alter table custom_skills
    add column if not exists source_id uuid references skill_sources (id) on delete set null;

create index if not exists custom_skills_source_id_idx on custom_skills (source_id);
