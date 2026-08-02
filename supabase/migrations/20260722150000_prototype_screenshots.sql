-- Multi-screenshot design source: a join table replacing the exactly-one
-- `prototypes.screenshot_key` column for any prototype generated after this
-- migration. `prototypes.screenshot_key` itself is left completely untouched
-- -- pre-existing rows keep resolving through it via the legacy-fallback path
-- in db/prototype_screenshots.py's resolve_screenshot_keys().
--
-- Per-item ordering (`position`) is required because a single column can't
-- hold N items with a stable order -- upload/prompt order round-trips through
-- this column, not through row insertion order (which a DB is not obligated
-- to preserve on read).
--
-- Workspace isolation (Rule #20-#23): workspace_id text not null, NO default.
-- INSERTs populate from the route's already-ownership-checked request
-- (require_company's resolved company_id). User-facing reads filter by
-- workspace_id.
--
-- media_type is DERIVED from the stored key's extension at read time
-- (read_screenshot, storage.py) -- never stored here (no denormalised field,
-- per this engagement's standing "store inputs only" convention).

create table if not exists prototype_screenshots (
    id            bigint generated always as identity primary key,
    prototype_id  bigint not null references prototypes(id) on delete cascade,
    workspace_id  text   not null,                 -- NO default (Rule #20)
    storage_key   text   not null,                  -- uploads/{workspace_id}/{uuid}.{ext};
                                                     -- media_type is DERIVED from the
                                                     -- extension at read time (read_screenshot),
                                                     -- never stored here (no denormalised field).
    position      int    not null,                  -- 0-indexed upload/prompt order
    created_at    timestamptz not null default now()
);

create index if not exists prototype_screenshots_prototype_id_idx on prototype_screenshots (prototype_id);
create index if not exists prototype_screenshots_workspace_id_idx on prototype_screenshots (workspace_id);

alter table prototype_screenshots enable row level security;
-- No policies -- matches prototype_comments' pattern (service-role key bypasses RLS).
