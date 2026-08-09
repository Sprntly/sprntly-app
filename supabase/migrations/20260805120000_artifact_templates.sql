-- Artifact format templates: a company's own PRD / ticket / engineering-spec
-- FORMS, uploaded once and adopted by the generators.
--
-- The structural twin of custom_skills (20260728180000_custom_skills.sql), and
-- deliberately so — a skill is the METHOD ("how to reason"), a template here is
-- the FORM ("what the finished document looks like"). Built-in forms are
-- vendored files baked into the backend image
-- (backend/skills/prd-author/templates/prd-template.html and the
-- implementation-spec markdown template). Customer uploads can't live on that
-- filesystem (immutable image, multi-replica), so the uploaded markdown is
-- stored here in `source_md`, and the canonical-vocabulary skeleton compiled
-- from it lands in `compiled` + `section_map`.
--
-- Scoping is COMPANY-LEVEL (matching custom_skills' 2026-07-28 decision): every
-- read filters by company_id, so all workspaces in a company share one library
-- and one active form per artifact type. workspace_id still records WHICH
-- workspace uploaded it — stamped not-null from the route's require_workspace
-- context, NEVER used as a query filter — so a future move to workspace-level
-- scoping is a query change, not a backfill.
--
-- `section_map` and `compile_notes` are TEXT holding JSON, not jsonb: they are
-- opaque payloads, round-tripped whole and never queried into, and TEXT is what
-- the SQLite test mirror can represent faithfully (same reasoning as
-- custom_skills.modules / .refs).
--
-- `is_active` + the partial unique index below are the whole activation model:
-- AT MOST ONE active template per (company, artifact_type), enforced by the
-- database rather than by application care. Activation therefore has to
-- deactivate the outgoing sibling BEFORE activating the incoming one — the
-- other order trips 23505 (app/db/artifact_templates.py::activate_template maps
-- that to a 409).
--
-- `content_hash` is the first 12 hex of sha256 over the uploaded source,
-- mirroring skills.custom.content_hash_for so the exact form behind a generated
-- artifact stays traceable in prompt_version / agent_decision_log.
--
-- Purely additive. No column, table, or policy is dropped or narrowed.

create table if not exists artifact_templates (
    id             uuid primary key default gen_random_uuid(),
    company_id     uuid not null references companies (id) on delete cascade,
    workspace_id   uuid not null references workspaces (id) on delete cascade,
    -- Which generator this form governs. 'impl_spec' is the implementation-spec
    -- skill's Part B (the markdown the ticket generator consumes), not a
    -- separate viewer surface.
    artifact_type  text not null
                     check (artifact_type in ('prd', 'tickets', 'impl_spec')),
    -- Free text, NOT a slug. Templates are never invoked by trigger, so the
    -- custom-skills `<slug>` / `<slug>-2` deconfliction has no analogue here
    -- and two templates in one company may share a name.
    name           text not null,
    -- The markdown the customer uploaded or pasted, verbatim. Untrusted input:
    -- it reaches a prompt, so every reader tags it as company-supplied data.
    source_md      text not null,
    -- len(source_md), denormalised so the library list can show "4,210
    -- characters" without selecting every row's full source. Same idiom as
    -- company_documents' extracted_chars; maintained on every write.
    source_chars   integer not null default 0,
    -- The compiled canonical-vocabulary skeleton. Empty until a compiler runs.
    -- Deliberately NOT cleared when a row goes back to 'compiling': the last
    -- good compile keeps serving generation while a re-upload is being checked,
    -- so one careless source edit cannot silently reformat every document
    -- produced in the next minute (set_compile_result writes it on success only).
    compiled       text not null default '',
    -- JSON: {sections: [{id, house, customer, order, form}], unmapped_house,
    -- extra_sections}. `id` is stable so the preview's mapping table can key
    -- rows; `form` is a closed set (prose | bullets | table | stories) so one
    -- concept never gets two labels across compiles.
    section_map    text not null default '{}',
    compile_status text not null default 'pending'
                     check (compile_status in
                            ('pending', 'compiling', 'ready', 'needs_review', 'failed')),
    -- JSON array of {code, message} objects — NEVER free text. `code` comes
    -- from a closed set (app/artifact_templates/store.py::COMPILE_NOTE_CODES)
    -- because the web keys a translation table on exactly those strings, so a
    -- raw validator note never reaches a screen. `message` is already
    -- plain-language, and is what the activation refusal quotes back.
    compile_notes  text not null default '[]',
    content_hash   text not null default '',
    is_active      boolean not null default false,
    uploader_id    uuid not null,
    uploader_name  text not null default '',
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

create index if not exists artifact_templates_company_id_idx
    on artifact_templates (company_id);
-- The library read and the active-template resolution both key on this pair.
create index if not exists artifact_templates_company_type_idx
    on artifact_templates (company_id, artifact_type);
-- Stamped-only, but indexed for the same reason custom_skills indexes it: a
-- future narrowing to workspace scope should not need a second migration.
create index if not exists artifact_templates_workspace_id_idx
    on artifact_templates (workspace_id);

-- At most one active template per company per artifact type. Partial, so the
-- unlimited inactive rows in a library never collide with each other.
create unique index if not exists artifact_templates_active_uniq
    on artifact_templates (company_id, artifact_type)
    where is_active;

alter table artifact_templates enable row level security;
-- Idempotent policy create: drop-if-exists first so this migration re-applies
-- cleanly on any environment where an earlier run already created the policy.
-- The backend connects with the service-role key, so RLS is bypassed and every
-- tenancy check is application-layer (routes/artifact_templates.py).
drop policy if exists "srv_artifact_templates" on artifact_templates;
create policy "srv_artifact_templates" on artifact_templates for all using (true) with check (true);
