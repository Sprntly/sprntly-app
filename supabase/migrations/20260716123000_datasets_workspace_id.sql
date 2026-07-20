-- Bind datasets to workspaces.
--
-- The dataset text slug is the key threaded through the whole brief/ask/KG
-- pipeline (and the corpus directory on disk), so per-workspace data uses
-- per-workspace dataset SLUGS rather than adding workspace_id columns across
-- that world:
--   * a company's DEFAULT workspace keeps the bare company slug — zero data
--     migration for every existing briefs/cached_asks/knowledge_*/
--     pipeline_runs/enterprise_input_sources/ask_jobs row and corpus dir;
--   * additional workspaces get "{company_slug}--{workspace_slug}" datasets
--     created by the backend at workspace creation.
-- datasets.workspace_id is the source of truth for the mapping — code must
-- never parse slugs.

alter table datasets
    add column if not exists workspace_id uuid
        references workspaces (id) on delete cascade;

-- Bind each company's existing dataset (slug == companies.slug, 1:1) to its
-- default workspace. Legacy demo datasets (no companies row) stay NULL.
update datasets d
   set workspace_id = w.id
  from companies c
  join workspaces w on w.company_id = c.id and w.is_default
 where c.slug = d.slug
   and d.workspace_id is null;

create unique index if not exists datasets_workspace_id_key
    on datasets (workspace_id)
    where workspace_id is not null;
