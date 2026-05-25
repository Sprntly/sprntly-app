-- Dataset registry. One row per dataset slug ("asurion", "wordpress",
-- etc.) — these are the keys used everywhere else (briefs.dataset,
-- cached_asks.dataset, the corpus loader on disk).
--
-- Memory note: the user-facing term is "company"; "dataset" is the
-- internal/DB name. We keep the DB column as `dataset` to match the
-- existing schema; the API/UI layer translates at the boundary.

create table if not exists datasets (
    slug         text primary key,
    display_name text not null,
    created_at   timestamptz not null default now()
);

alter table datasets enable row level security;
