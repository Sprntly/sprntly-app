-- Custom Skills (PRD 1854): workspace-scoped, user-uploaded skill definitions.
--
-- Built-in skills are vendored markdown directories baked into the backend
-- image (backend/skills/<id>/SKILL.md, loaded by app/skills/loader.py). User
-- uploads can't live on that filesystem (immutable image, multi-replica), so
-- custom skills store their PARSED markdown here — `method` is the SKILL.md
-- text injected into the prompt at invocation time, exactly like a built-in's
-- method block — while the ORIGINAL uploaded .md/.zip bytes go to Supabase
-- Storage under custom-skills/{company_id}/ (skills_storage.py) with the
-- object key recorded in `storage_key` for provenance/re-download.
--
-- Scoping is COMPANY-LEVEL for now (product decision 2026-07-28): every read
-- filters by company_id, so all workspaces in a company share one skill
-- library. workspace_id still records WHICH workspace uploaded the skill —
-- stamped not-null from the route's require_workspace context — so a future
-- move to workspace-level scoping is a query change, not a backfill. `slug`
-- is the invocation id + /trigger (kebab-case from the display name); unique
-- per company, and the upload route also rejects slugs that shadow a vendored
-- built-in id.
--
-- `modules` / `refs` are JSON-encoded {filename: markdown} maps (extra .md
-- files from a .zip, mirroring a skill dir's modules/ and references/
-- subdirs). TEXT rather than jsonb: they're opaque payloads — never queried
-- into, only round-tripped — and `refs` dodges the `references` keyword.
--
-- `content_hash` is the first 12 hex of sha256 over the parsed content,
-- mirroring loader._content_hash so prompt_version stays traceable in the
-- agent_decision_log (+<slug>@<hash>) for custom and built-in skills alike.

create table if not exists custom_skills (
    id            uuid primary key default gen_random_uuid(),
    company_id    uuid not null references companies (id) on delete cascade,
    workspace_id  uuid not null references workspaces (id) on delete cascade,
    slug          text not null,
    name          text not null,
    description   text not null,
    method        text not null,
    modules       text not null default '{}',
    refs          text not null default '{}',
    content_hash  text not null,
    storage_key   text,
    uploader_id   uuid not null,
    uploader_name text not null default '',
    created_at    timestamptz not null default now(),
    constraint custom_skills_company_slug_uniq unique (company_id, slug)
);

create index if not exists custom_skills_workspace_id_idx on custom_skills (workspace_id);
create index if not exists custom_skills_company_id_idx on custom_skills (company_id);

alter table custom_skills enable row level security;
-- Idempotent policy create: drop-if-exists first so this migration re-applies
-- cleanly on any environment where an earlier run already created the policy.
drop policy if exists "srv_custom_skills" on custom_skills;
create policy "srv_custom_skills" on custom_skills for all using (true) with check (true);
