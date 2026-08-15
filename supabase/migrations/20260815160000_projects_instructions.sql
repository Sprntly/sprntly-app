-- Per-project free-text instructions for the Sprntly agent.
-- Nullable, no default: absent = no instructions. Tenancy is inherited from
-- the projects row (company_id/workspace_id FKs) — this table deliberately
-- uses the mainline uuid-FK convention, not the design-agent aud pattern
-- (see the header comment in 20260813130000_projects.sql).
alter table projects add column if not exists instructions text;
