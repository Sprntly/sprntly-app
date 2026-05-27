-- Onboarding workspace context, KPI tree, and progress tracking.
-- Aligns with Sprntly_Onboarding_Flow_Spec_v1.

alter table profiles
    add column if not exists first_name text,
    add column if not exists last_name text,
    add column if not exists role text,
    add column if not exists onboarding_step int not null default 0,
    add column if not exists onboarding_completed_at timestamptz,
    add column if not exists skipped_fields jsonb not null default '[]'::jsonb;

alter table companies
    add column if not exists product_description text,
    add column if not exists industry text,
    add column if not exists stage text,
    add column if not exists business_type text,
    add column if not exists team_size int,
    add column if not exists engineering_capacity int,
    add column if not exists pm_engineer_ratio text,
    add column if not exists competitors text[] not null default '{}',
    add column if not exists tech_stack text[] not null default '{}',
    add column if not exists okrs text,
    add column if not exists recent_decisions text,
    add column if not exists dead_ends text[] not null default '{}',
    add column if not exists biggest_risk text,
    add column if not exists kpi_tree jsonb not null default '{}'::jsonb,
    add column if not exists feature_flags jsonb not null default '{}'::jsonb,
    add column if not exists notification_settings jsonb not null default '{}'::jsonb,
    add column if not exists onboarding_step int not null default 1,
    add column if not exists onboarding_completed_at timestamptz;

create table if not exists workspace_invites (
    id         uuid primary key default gen_random_uuid(),
    company_id uuid not null references companies (id) on delete cascade,
    email      text not null,
    role       text not null default 'member'
                 check (role in ('admin', 'member')),
    invited_by uuid references auth.users (id) on delete set null,
    created_at timestamptz not null default now(),
    unique (company_id, email)
);

create index if not exists workspace_invites_company_id_idx on workspace_invites (company_id);

alter table workspace_invites enable row level security;

-- Members with owner/admin role can manage their company row.
create policy "companies_update_admin"
    on companies for update to authenticated
    using (
        exists (
            select 1 from company_members cm
            where cm.company_id = companies.id
              and cm.user_id = auth.uid()
              and cm.role in ('owner', 'admin')
        )
    )
    with check (
        exists (
            select 1 from company_members cm
            where cm.company_id = companies.id
              and cm.user_id = auth.uid()
              and cm.role in ('owner', 'admin')
        )
    );

create policy "workspace_invites_select_member"
    on workspace_invites for select to authenticated
    using (
        exists (
            select 1 from company_members cm
            where cm.company_id = workspace_invites.company_id
              and cm.user_id = auth.uid()
        )
    );

create policy "workspace_invites_insert_admin"
    on workspace_invites for insert to authenticated
    with check (
        exists (
            select 1 from company_members cm
            where cm.company_id = workspace_invites.company_id
              and cm.user_id = auth.uid()
              and cm.role in ('owner', 'admin')
        )
    );

-- Refresh profile trigger to capture first/last name from signup metadata.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.profiles (id, email, first_name, last_name, full_name, avatar_url)
    values (
        new.id,
        new.email,
        coalesce(new.raw_user_meta_data ->> 'first_name', ''),
        coalesce(new.raw_user_meta_data ->> 'last_name', ''),
        trim(both from concat_ws(' ',
            coalesce(new.raw_user_meta_data ->> 'first_name', ''),
            coalesce(new.raw_user_meta_data ->> 'last_name', '')
        )),
        coalesce(
            new.raw_user_meta_data ->> 'avatar_url',
            new.raw_user_meta_data ->> 'picture',
            ''
        )
    );
    return new;
end;
$$;
