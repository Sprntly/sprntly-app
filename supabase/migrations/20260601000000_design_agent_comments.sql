-- Design Agent anchored comments (P3-01).
-- F8: viewers right-click a prototype element and leave a comment anchored to
-- that element's data-anchor-id (AD4 stable JSX ID). AD12: comments orphan +
-- re-attach on regeneration — status flips to 'orphaned' when the anchor_id no
-- longer exists in a new checkpoint's bundle (P3-04 does the walk).
--
-- Workspace isolation (Rule #20-#23): workspace_id text not null, NO default.
-- INSERTs populate from the route's session.aud (internal) or the resolved
-- prototype's workspace_id (public-route writes, P3-02). User-facing reads
-- filter by workspace_id.
--
-- Identity punt: author is a free-text string ('demo' for now). No users FK --
-- a real users table can land later without migrating this column.

create table if not exists prototype_comments (
    id            bigint generated always as identity primary key,
    prototype_id  bigint not null references prototypes(id) on delete cascade,
    workspace_id  text   not null,                          -- NO default (Rule #20)
    anchor_id     text   not null,                          -- the data-anchor-id (AD4 8-hex)
    body          text   not null,
    author        text   not null default 'demo',           -- identity punt; free-text not FK
    status        text   not null default 'open',            -- 'open' | 'resolved' | 'orphaned'
    created_at    timestamptz not null default now(),
    resolved_at   timestamptz
);

create index if not exists prototype_comments_prototype_id_idx on prototype_comments (prototype_id);
create index if not exists prototype_comments_workspace_id_idx on prototype_comments (workspace_id);
create index if not exists prototype_comments_anchor_id_idx    on prototype_comments (anchor_id);

alter table prototype_comments enable row level security;
-- No policies -- matches Sprntly's pattern (backend uses the service-role key
-- and bypasses RLS; the browser has no direct table access).

-- status CHECK (constrained to the three legal values) -- defence-in-depth;
-- helpers also validate. Idempotent drop+add (matches the share_mode CHECK in
-- 20260530000000_design_agent_sharing.sql).
alter table prototype_comments drop constraint if exists prototype_comments_status_check;
alter table prototype_comments
    add constraint prototype_comments_status_check
    check (status in ('open', 'resolved', 'orphaned'));
