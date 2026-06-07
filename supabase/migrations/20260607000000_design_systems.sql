-- Cache of the normalized design system per (company, source provider,
-- source instance).
--
-- A company can connect several design sources — a Figma file, a code
-- repository, a website. Each one is extracted and reduced to the common
-- normalized DesignSystem object, which is stored here in `data` so it can be
-- reused without re-running the (expensive) extraction. The unique key
-- (company_id, source_provider, source_ref) is the cache key: one cached design
-- system per company per specific source instance.
--
-- Provenance columns (source_category / source_provider / source_ref /
-- source_version / extracted_at) record where each cached system came from and
-- how fresh it is, so staleness can be judged against the source's own cheap
-- version marker.
--
-- This table is knowledge-graph-ready by construction — a stable uuid primary
-- key plus full provenance columns — but no knowledge-graph projection is wired
-- to it here.

create table if not exists design_systems (
    id                  uuid primary key default gen_random_uuid(),
    company_id          uuid not null references companies (id) on delete cascade,
    source_category     text not null,          -- design_tool | codebase | website
    source_provider     text not null,          -- figma | github | gitlab | web | …
    source_ref          text not null,          -- file_key | "owner/repo@branch" | normalized_url
    source_version      text,                   -- lastModified | commit SHA | etag/TTL marker
    data                jsonb not null default '{}',  -- the normalized DesignSystem object
    has_explicit_system boolean,
    confidence          text,                   -- high | medium | low
    status              text not null default 'active',
    extracted_at        timestamptz,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    unique (company_id, source_provider, source_ref)
);

create index if not exists design_systems_company_id_idx
    on design_systems (company_id);

alter table design_systems enable row level security;

-- Membership-scoped policy covering BOTH reads and writes: a user-context
-- Supabase client may only touch rows for a company it belongs to. The backend
-- uses the service-role key (which bypasses RLS), so route-level membership
-- checks (require_company) remain the primary defense; this policy is a
-- defense-in-depth safety net for any future direct-frontend access.
drop policy if exists design_systems_member_all on design_systems;
create policy design_systems_member_all on design_systems
    for all
    using (
        exists (
            select 1
              from company_members
             where company_members.company_id = design_systems.company_id
               and company_members.user_id    = auth.uid()
        )
    );
