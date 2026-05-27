-- Let workspace creators read/update their company row before company_members
-- exists (PostgREST INSERT ... RETURNING requires SELECT policy).

alter table companies
    add column if not exists created_by uuid references auth.users (id) on delete set null;

create index if not exists companies_created_by_idx on companies (created_by);

drop policy if exists "companies_select_member" on companies;
create policy "companies_select_member"
    on companies for select to authenticated
    using (
        exists (
            select 1
            from company_members cm
            where cm.company_id = companies.id
              and cm.user_id = auth.uid()
        )
        or created_by = auth.uid()
    );

drop policy if exists "companies_insert_authenticated" on companies;
create policy "companies_insert_authenticated"
    on companies for insert to authenticated
    with check (created_by = auth.uid());

drop policy if exists "companies_update_admin" on companies;
create policy "companies_update_admin"
    on companies for update to authenticated
    using (
        exists (
            select 1 from company_members cm
            where cm.company_id = companies.id
              and cm.user_id = auth.uid()
              and cm.role in ('owner', 'admin')
        )
        or created_by = auth.uid()
    )
    with check (
        exists (
            select 1 from company_members cm
            where cm.company_id = companies.id
              and cm.user_id = auth.uid()
              and cm.role in ('owner', 'admin')
        )
        or created_by = auth.uid()
    );
