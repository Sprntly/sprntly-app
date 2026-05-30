-- Design Agent message queue (P3-06, AD11). Up to 5 iterate prompts stack per
-- prototype; they run serially. DB-backed so a process restart recovers the
-- queue (orphan-clear pattern). Position is DERIVED at read time, not stored.
--
-- Workspace isolation (Rule #20-#23): workspace_id text not null, NO default.

create table if not exists prototype_pending_iterations (
    id                 bigint generated always as identity primary key,
    prototype_id       bigint not null references prototypes(id) on delete cascade,
    workspace_id       text   not null,                     -- NO default (Rule #20)
    prompt             text   not null,
    applied_comment_id bigint references prototype_comments(id) on delete set null,
    mode               text   not null default 'execute',    -- 'plan' | 'execute' (P3-07)
    status             text   not null default 'pending',    -- 'pending' | 'running' | 'done' | 'failed'
    error              text,
    created_at         timestamptz not null default now(),
    started_at         timestamptz,
    finished_at        timestamptz
);

create index if not exists pending_iterations_prototype_id_idx on prototype_pending_iterations (prototype_id);
create index if not exists pending_iterations_workspace_id_idx on prototype_pending_iterations (workspace_id);
create index if not exists pending_iterations_status_idx       on prototype_pending_iterations (status);

alter table prototype_pending_iterations enable row level security;

alter table prototype_pending_iterations drop constraint if exists pending_iterations_status_check;
alter table prototype_pending_iterations
    add constraint pending_iterations_status_check
    check (status in ('pending', 'running', 'done', 'failed'));
