-- Export staging (P2-09). Snapshot of the markdown brief at Mark Complete.
-- Per Apurva 2026-05-29: export is markdown, not JSON envelope.
-- Per BUILD.md §AD7: Mark Complete promotes current_checkpoint_id → complete_checkpoint_id;
-- this table snapshots the markdown at that promotion.
--
-- Workspace isolation (Rule #20-#23): workspace_id text not null, NO default.
-- INSERTs populate from the route's session.aud at insert time.
-- User-facing reads filter by workspace_id (the GET /export route enforces it).

create table if not exists prototype_exports (
    id                bigint generated always as identity primary key,
    prototype_id      bigint not null references prototypes(id) on delete cascade,
    checkpoint_id     bigint not null references prototype_checkpoints(id) on delete cascade,
    workspace_id      text   not null,                              -- NO default (Rule #20)
    markdown_content  text   not null,
    generated_at      timestamptz not null default now(),
    is_stale          boolean not null default false,
    constraint prototype_exports_prototype_checkpoint_uq
        unique (prototype_id, checkpoint_id)
);

create index if not exists prototype_exports_prototype_id_idx on prototype_exports (prototype_id);
create index if not exists prototype_exports_workspace_id_idx on prototype_exports (workspace_id);

alter table prototype_exports enable row level security;
-- Matches the existing pattern: no RLS policies; the backend uses the service-
-- role key and bypasses RLS; the browser has no direct table access.
