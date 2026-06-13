-- Tenant-scope the GitHub App installation + PR tables.
--
-- Connectors are company-scoped and shared among the company's invited
-- members (the `connections` table already uses company_id + a unique
-- (company_id, provider) constraint). The GitHub App's separate
-- installation / PR tables fell out of that pattern: github_installations
-- was keyed by installation_id ONLY (no company column), so every signed-in
-- user could read every company's installations and tracked PRs. This adds
-- the missing tenant column so reads can be scoped to the caller's company.
--
-- Backfill: NONE. Existing rows have no recoverable company association
-- (the installation_id → company link was never persisted), so they stay
-- company_id = NULL and are EXCLUDED from every scoped read. Those users
-- reconnect GitHub once post-deploy, which re-binds the installation to
-- their company via the OAuth callback. We deliberately do not guess a
-- company for legacy rows — a wrong guess would re-introduce the leak.

alter table github_installations
    add column if not exists company_id uuid
        references companies(id) on delete cascade;

create index if not exists github_installations_company_id_idx
    on github_installations (company_id);

-- github_pull_requests inherits its tenant via installation_id → the
-- installation's company_id (all PR reads join/filter through an
-- installation that already belongs to the caller's company). We also
-- carry company_id directly so PR reads can be scoped without a join and
-- so cross-tenant rows are filtered even before an installation lookup.
alter table github_pull_requests
    add column if not exists company_id uuid
        references companies(id) on delete cascade;

create index if not exists github_pull_requests_company_id_idx
    on github_pull_requests (company_id);
