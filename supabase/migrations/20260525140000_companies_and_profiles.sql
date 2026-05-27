-- User profiles, companies (workspaces), and membership mapping.
--
-- auth.users is managed by Supabase Auth. profiles extends it with app
-- metadata. companies is the user-facing tenant; company_members links
-- users to companies with a role. Dataset slugs can align with
-- companies.slug during onboarding.

create table if not exists profiles (
    id         uuid primary key references auth.users (id) on delete cascade,
    email      text,
    full_name  text,
    avatar_url text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists companies (
    id           uuid primary key default gen_random_uuid(),
    slug         text not null unique,
    display_name text not null,
    created_at   timestamptz not null default now(),
    constraint companies_slug_format check (slug ~ '^[a-z0-9][a-z0-9_-]{1,62}$')
);

create table if not exists company_members (
    id         uuid primary key default gen_random_uuid(),
    company_id uuid not null references companies (id) on delete cascade,
    user_id    uuid not null references auth.users (id) on delete cascade,
    role       text not null default 'member'
                 check (role in ('owner', 'admin', 'member')),
    created_at timestamptz not null default now(),
    unique (company_id, user_id)
);

create index if not exists company_members_user_id_idx on company_members (user_id);
create index if not exists company_members_company_id_idx on company_members (company_id);

-- Auto-create a profile row when a user signs up via Supabase Auth.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.profiles (id, email, full_name, avatar_url)
    values (
        new.id,
        new.email,
        coalesce(
            new.raw_user_meta_data ->> 'full_name',
            new.raw_user_meta_data ->> 'name',
            ''
        ),
        coalesce(
            new.raw_user_meta_data ->> 'avatar_url',
            new.raw_user_meta_data ->> 'picture',
            ''
        )
    );
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();

alter table profiles enable row level security;
alter table companies enable row level security;
alter table company_members enable row level security;

-- profiles
create policy "profiles_select_own"
    on profiles for select to authenticated
    using (id = auth.uid());

create policy "profiles_update_own"
    on profiles for update to authenticated
    using (id = auth.uid())
    with check (id = auth.uid());

-- company_members: users see their own memberships
create policy "company_members_select_own"
    on company_members for select to authenticated
    using (user_id = auth.uid());

create policy "company_members_insert_self"
    on company_members for insert to authenticated
    with check (user_id = auth.uid());

-- companies: members can read; any signed-in user can create (onboarding)
create policy "companies_select_member"
    on companies for select to authenticated
    using (
        exists (
            select 1
            from company_members cm
            where cm.company_id = companies.id
              and cm.user_id = auth.uid()
        )
    );

create policy "companies_insert_authenticated"
    on companies for insert to authenticated
    with check (true);
