-- Workspaces (1 company → many products → many workspaces) + denormalised
-- connection-scope columns (CEO 2026-06-06).
--
-- The product mental model is now:
--   1 user      ↔ 1 company         (already enforced by
--                                    20260604_one_company_per_user.sql)
--   1 company   ↔ N products        (already enforced by 20260525_products.sql)
--   1 company   ↔ N workspaces      (this migration introduces workspaces)
--   1 product   ↔ N workspaces      (workspaces.product_id FK)
--
-- This migration is intentionally schema-only. The backend connector
-- routes still scope by company_id today; switching them to a workspace-
-- scoped lookup (which provider+workspace row to read/write) is a later
-- slice once the UX for "active workspace" is decided. Until then:
--
--   - The new `workspaces` table is populated with one "Default"
--     workspace per (company, primary product) so foreign-key references
--     have a valid row to point at.
--   - The new `connections.workspace_id` / `connections.product_id`
--     columns are backfilled from each connection's company → primary
--     product → default workspace, then made NOT NULL.
--   - `connections.company_name` and `connections.product_name` are
--     additive, denormalised columns kept in sync at write-time by the
--     application (no trigger yet — added when the application starts
--     writing them).
--   - `unique (company_id, provider)` STAYS in place. A composite
--     `unique (workspace_id, provider)` is added alongside it; once the
--     routes have moved over to workspace_id, the old constraint can be
--     dropped in a follow-up migration.
--
-- Apply note: this migration is non-destructive. It does not TRUNCATE,
-- DROP, or rename anything. Re-runs are idempotent thanks to
-- "if not exists" / "if exists" guards.

-- ─────────────────────── workspaces table ───────────────────────

create table if not exists workspaces (
    id          uuid primary key default gen_random_uuid(),
    company_id  uuid not null references companies (id) on delete cascade,
    product_id  uuid references products (id) on delete set null,
    name        text not null,
    slug        text not null,
    is_default  boolean not null default false,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    constraint workspaces_name_nonempty check (char_length(trim(name)) > 0),
    constraint workspaces_slug_format check (slug ~ '^[a-z0-9][a-z0-9_-]{0,62}$')
);

create unique index if not exists workspaces_company_slug_key
    on workspaces (company_id, slug);
create index if not exists workspaces_company_id_idx on workspaces (company_id);
create index if not exists workspaces_product_id_idx on workspaces (product_id);
create unique index if not exists workspaces_one_default_per_company
    on workspaces (company_id)
    where is_default;

-- Touch updated_at on every update.
create or replace function workspaces_touch_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists workspaces_set_updated_at on workspaces;
create trigger workspaces_set_updated_at
    before update on workspaces
    for each row execute function workspaces_touch_updated_at();

-- Backfill: one Default workspace per (company, primary product).
-- Companies without a primary product (shouldn't happen — products
-- migration always backfills one) get a Default workspace with
-- product_id = NULL; the application's onboarding flow will populate
-- the product reference when the user picks one.
insert into workspaces (company_id, product_id, name, slug, is_default)
select
    c.id,
    (select p.id from products p where p.company_id = c.id and p.is_primary limit 1),
    'Default',
    'default',
    true
from companies c
where not exists (
    select 1 from workspaces w where w.company_id = c.id and w.is_default
);

alter table workspaces enable row level security;

create policy workspaces_select_member on workspaces
    for select to authenticated
    using (
        exists (
            select 1 from company_members cm
            where cm.company_id = workspaces.company_id
              and cm.user_id    = auth.uid()
        )
    );

create policy workspaces_insert_admin on workspaces
    for insert to authenticated
    with check (
        exists (
            select 1 from company_members cm
            where cm.company_id = workspaces.company_id
              and cm.user_id    = auth.uid()
              and cm.role in ('owner', 'admin')
        )
    );

create policy workspaces_update_admin on workspaces
    for update to authenticated
    using (
        exists (
            select 1 from company_members cm
            where cm.company_id = workspaces.company_id
              and cm.user_id    = auth.uid()
              and cm.role in ('owner', 'admin')
        )
    )
    with check (
        exists (
            select 1 from company_members cm
            where cm.company_id = workspaces.company_id
              and cm.user_id    = auth.uid()
              and cm.role in ('owner', 'admin')
        )
    );

-- ─────────────────── connection scoping columns ───────────────────

alter table connections
    add column if not exists workspace_id  uuid
        references workspaces (id) on delete cascade;

alter table connections
    add column if not exists product_id    uuid
        references products (id) on delete set null;

alter table connections
    add column if not exists company_name  text;

alter table connections
    add column if not exists product_name  text;

-- Backfill: derive workspace_id / product_id / names from each
-- connection's company + that company's primary product + the company's
-- default workspace. The 2026-06-03 multitenant migration truncated
-- connections, so in practice this updates 0 rows in prod (it's here
-- for safety + future-self).
update connections
   set workspace_id  = (
            select w.id from workspaces w
             where w.company_id = connections.company_id
               and w.is_default
             limit 1
       ),
       product_id    = (
            select p.id from products p
             where p.company_id = connections.company_id
               and p.is_primary
             limit 1
       ),
       company_name  = (
            select c.display_name from companies c where c.id = connections.company_id
       ),
       product_name  = (
            select p.name from products p
             where p.company_id = connections.company_id
               and p.is_primary
             limit 1
       )
 where connections.workspace_id is null;

create index if not exists connections_workspace_id_idx
    on connections (workspace_id);
create index if not exists connections_product_id_idx
    on connections (product_id);

-- New tenancy uniqueness: each workspace gets its own slot per provider.
-- The legacy `unique (company_id, provider)` constraint stays in place
-- until the route layer is moved over to workspace_id; both constraints
-- coexist safely as long as the backfill kept everything consistent
-- (default workspace per company == one workspace per company today, so
-- the two constraints are equivalent until additional workspaces appear).
create unique index if not exists connections_workspace_provider_key
    on connections (workspace_id, provider)
    where workspace_id is not null;
