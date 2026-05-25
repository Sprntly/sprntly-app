-- Briefs: one row per (dataset, generated_at). Only one row per dataset
-- carries is_current=true; older rows stay around for history but are
-- shadowed by the latest "current" one.
--
-- Source-of-truth port of the SQLite `briefs` table in
-- backend/app/db.py. SQLite stored the brief payload as a TEXT blob of
-- JSON; here we use jsonb so the API can query into it directly.

create table if not exists briefs (
    id           bigint generated always as identity primary key,
    dataset      text not null,
    generated_at timestamptz not null default now(),
    week_label   text,
    payload      jsonb not null,
    is_current   boolean not null default true
);

create index if not exists briefs_dataset_current_idx
    on briefs (dataset, is_current);

alter table briefs enable row level security;
-- No policies → only service_role (used by the backend) can read or write.
-- Browser-side keys (anon, authenticated) have no access by default.
