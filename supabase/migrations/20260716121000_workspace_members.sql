-- Workspace membership — activates the dormant workspaces table.
--
-- Two-level role model:
--   * company_members.role (owner/admin/member/viewer) stays the ORG role;
--     org owners/admins implicitly access every workspace (enforced in the
--     backend's require_workspace, no rows needed here).
--   * workspace_members.role (admin/member/viewer) governs plain members'
--     access inside each workspace.
--
-- Mutations happen through the backend (service role) only, mirroring how
-- company_members are managed today; browsers get read access so member
-- lists can render.

-- Repair: companies created after 20260606120000 never got a default
-- workspace (the frontend's createWorkspace inserts companies/products only).
-- Identical INSERT to that migration's backfill; idempotent.
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

create table if not exists workspace_members (
    id           uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references workspaces (id) on delete cascade,
    user_id      uuid not null references auth.users (id) on delete cascade,
    role         text not null default 'member'
                 check (role in ('admin', 'member', 'viewer')),
    created_at   timestamptz not null default now(),
    unique (workspace_id, user_id)
);

create index if not exists workspace_members_user_id_idx
    on workspace_members (user_id);
create index if not exists workspace_members_workspace_id_idx
    on workspace_members (workspace_id);

-- Backfill: every company member joins their company's default workspace.
-- Org owner/admin map to workspace admin, viewer stays viewer, else member.
insert into workspace_members (workspace_id, user_id, role)
select
    w.id,
    cm.user_id,
    case cm.role
        when 'owner'  then 'admin'
        when 'admin'  then 'admin'
        when 'viewer' then 'viewer'
        else 'member'
    end
from company_members cm
join workspaces w on w.company_id = cm.company_id and w.is_default
on conflict (workspace_id, user_id) do nothing;

alter table workspace_members enable row level security;

-- Read: any member of the owning company (matches workspaces_select_member).
create policy workspace_members_select_company on workspace_members
    for select to authenticated
    using (
        exists (
            select 1 from workspaces w
            join company_members cm on cm.company_id = w.company_id
            where w.id = workspace_members.workspace_id
              and cm.user_id = auth.uid()
        )
    );

-- No authenticated INSERT/UPDATE/DELETE policies on purpose: membership
-- writes go through the backend service role (invite accept, member
-- management endpoints), same posture as company_members mutations.
