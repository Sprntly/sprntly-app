-- Design source — persists the user's chosen design source per workspace.
-- Nullable jsonb; no default. Shape: { design_source, figma_file_key?, github_repo?, website_url? }
alter table companies add column if not exists design_source jsonb;
