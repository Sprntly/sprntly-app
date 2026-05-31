-- Design Agent PRD patch model (P3-09, F11). The agent proposes PRD edits as
-- SIBLING rows; prds.payload_md is NEVER altered. PRD is rendered by applying
-- outstanding applied=true patches on read (apply_patches_to_prd_md).
--
-- Workspace isolation (Rule #20-#23): workspace_id text not null, NO default.

create table if not exists prd_patches (
    id            bigint generated always as identity primary key,
    prd_id        bigint not null references prds(id) on delete cascade,
    prototype_id  bigint not null references prototypes(id) on delete cascade,
    workspace_id  text   not null,                          -- NO default (Rule #20)
    rationale     text   not null,                          -- agent's reason for the edit
    patch_md      text   not null,                          -- the proposed markdown delta
    status        text   not null default 'pending',         -- 'pending' | 'applied' | 'rejected'
    created_at    timestamptz not null default now(),
    resolved_at   timestamptz
);

create index if not exists prd_patches_prd_id_idx        on prd_patches (prd_id);
create index if not exists prd_patches_prototype_id_idx  on prd_patches (prototype_id);
create index if not exists prd_patches_workspace_id_idx  on prd_patches (workspace_id);
create index if not exists prd_patches_status_idx        on prd_patches (status);

alter table prd_patches enable row level security;
-- No policies -- matches Sprntly's pattern (backend uses the service-role key and
-- bypasses RLS; the browser has no direct table access).

-- status CHECK (constrained to the three legal values) -- defence-in-depth; the
-- helpers also validate. Idempotent drop+add (matches the status CHECK in
-- 20260601000000_design_agent_comments.sql).
alter table prd_patches drop constraint if exists prd_patches_status_check;
alter table prd_patches
    add constraint prd_patches_status_check
    check (status in ('pending', 'applied', 'rejected'));
