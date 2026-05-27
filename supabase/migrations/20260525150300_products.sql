-- Products belong to a company; onboarding creates the first (primary) product.

create table if not exists products (
    id          uuid primary key default gen_random_uuid(),
    company_id  uuid not null references companies (id) on delete cascade,
    name        text not null,
    website     text,
    description text,
    is_primary  boolean not null default false,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    constraint products_name_nonempty check (char_length(trim(name)) > 0)
);

create index if not exists products_company_id_idx on products (company_id);
create unique index if not exists products_one_primary_per_company
    on products (company_id)
    where is_primary;

-- Backfill a primary product from legacy company.product_description / display_name.
insert into products (company_id, name, website, description, is_primary)
select
    c.id,
    c.display_name,
    null,
    c.product_description,
    true
from companies c
where not exists (
    select 1 from products p where p.company_id = c.id and p.is_primary
);

alter table products enable row level security;

create policy "products_select_member"
    on products for select to authenticated
    using (
        exists (
            select 1 from company_members cm
            where cm.company_id = products.company_id
              and cm.user_id = auth.uid()
        )
        or exists (
            select 1 from companies c
            where c.id = products.company_id
              and c.created_by = auth.uid()
        )
    );

create policy "products_insert_member"
    on products for insert to authenticated
    with check (
        exists (
            select 1 from company_members cm
            where cm.company_id = products.company_id
              and cm.user_id = auth.uid()
              and cm.role in ('owner', 'admin')
        )
        or exists (
            select 1 from companies c
            where c.id = products.company_id
              and c.created_by = auth.uid()
        )
    );

create policy "products_update_member"
    on products for update to authenticated
    using (
        exists (
            select 1 from company_members cm
            where cm.company_id = products.company_id
              and cm.user_id = auth.uid()
              and cm.role in ('owner', 'admin')
        )
        or exists (
            select 1 from companies c
            where c.id = products.company_id
              and c.created_by = auth.uid()
        )
    )
    with check (
        exists (
            select 1 from company_members cm
            where cm.company_id = products.company_id
              and cm.user_id = auth.uid()
              and cm.role in ('owner', 'admin')
        )
        or exists (
            select 1 from companies c
            where c.id = products.company_id
              and c.created_by = auth.uid()
        )
    );
