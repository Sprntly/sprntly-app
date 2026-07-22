-- Workspace-owned fields (2026-07-22): move the six "Your workspace" fields off
-- the companies row and onto the workspaces row, so there is a SINGLE source of
-- truth for what a workspace is.
--
-- The fields: the workspace NAME (companies.team_name → the existing
-- workspaces.name column, which the left-sidebar switcher displays) plus the
-- five typed blocks — team_scope, team_strategy, team_roadmap,
-- sizing_methodology, additional_context — which get NEW columns here.
--
-- These fields previously lived on companies (see 20260717120000_onboarding_v6.sql
-- + 20260716124000_workspace_scope_columns.sql). Onboarding's "Your workspace"
-- step and Settings → Process both now read/write the workspaces row instead.
--
-- The matching companies columns (team_name, team_scope, team_strategy,
-- team_roadmap, sizing_methodology, additional_context) are DELIBERATELY LEFT IN
-- PLACE and are now DORMANT — nothing reads or writes them after this change.
-- A follow-up migration can drop them once this migration is verified in prod.

-- ─────────────────────────── workspaces ─────────────────────────

alter table workspaces
    add column if not exists team_scope text,
    add column if not exists team_strategy text,
    add column if not exists team_roadmap text,
    add column if not exists sizing_methodology text,
    add column if not exists additional_context text;

-- Backfill ONLY the default workspace of each company from its (soon dormant)
-- companies columns. The name is copied only when the company carried a
-- non-blank team_name — otherwise the existing workspaces.name ("Default" or a
-- prior rename) is kept, respecting the workspaces_name_nonempty check.
update workspaces w set
    name                = coalesce(nullif(trim(c.team_name), ''), w.name),
    team_scope          = c.team_scope,
    team_strategy       = c.team_strategy,
    team_roadmap        = c.team_roadmap,
    sizing_methodology  = c.sizing_methodology,
    additional_context  = c.additional_context
from companies c
where w.company_id = c.id
  and w.is_default = true;
