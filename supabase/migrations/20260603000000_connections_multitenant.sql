-- Make `connections` multitenant.
--
-- Previously the table had a global `unique (provider)` constraint, so
-- only one Figma/GitHub/Google Drive/etc. could exist across the entire
-- installation. Any signed-in user saw — and could disconnect — the
-- connector tokens of whichever workspace had connected last. (Solo-demo
-- days; pre-existing bug.)
--
-- This migration:
--   1. Truncates connections. No customer data has been onboarded; the
--      existing rows are internal team test connections. Wipe + force
--      reconnect through the soon-to-be-tenant-safe flow is cleaner than
--      backfilling to a guessed company.
--   2. Adds company_id (FK to companies(id), cascade on delete).
--      Tenancy term aligned with require_company / CompanyContext —
--      one-user-one-company is the product invariant.
--   3. Replaces the global `unique (provider)` with composite
--      `unique (company_id, provider)` — each company gets its own slot
--      per provider.
--   4. Adds a defense-in-depth RLS policy: a user-context Supabase client
--      can only read its company's connection rows. The backend uses
--      the service-role key (which bypasses RLS), so route-level
--      membership checks (require_company) remain the primary defense;
--      the policy is a safety net for any future direct-frontend access.

truncate table connections;

alter table connections
    drop constraint if exists connections_provider_key;

alter table connections
    add column company_id uuid not null
        references companies (id) on delete cascade;

alter table connections
    add constraint connections_company_provider_key
        unique (company_id, provider);

create index if not exists connections_company_id_idx
    on connections (company_id);

drop policy if exists connections_member_select on connections;
create policy connections_member_select on connections
    for select
    using (
        exists (
            select 1
              from company_members
             where company_members.company_id = connections.company_id
               and company_members.user_id    = auth.uid()
        )
    );
