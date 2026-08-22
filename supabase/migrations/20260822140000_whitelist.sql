-- Whitelist / early-access signups from the public marketing site.
--
-- One row per email. This is the only table the product writes to with no
-- tenant on it: the person filling in the form has no account yet, so there is
-- no company_id to attribute the row to and no session to authenticate.
-- `source` is whatever the front end says sent them (a landing-page slug, a
-- campaign tag) — untrusted free text, length-capped in the route, and used for
-- reporting only.
--
-- Email is stored ALREADY LOWERCASED by app/db/whitelist.py, so a plain unique
-- constraint is the whole dedupe story — no `lower(email)` functional index and
-- no citext extension. The db layer upserts with on_conflict=email / do nothing,
-- so re-submitting the same address is a silent success rather than a 409: a
-- signup form that says "you are already on the list" tells an anonymous
-- stranger whether a given address signed up, and saying nothing costs nothing.

create table if not exists whitelist (
    id         uuid primary key default gen_random_uuid(),
    email      text not null unique,
    source     text,
    created_at timestamptz not null default now()
);

create index if not exists whitelist_created_idx on whitelist (created_at desc);

alter table whitelist enable row level security;

-- No client-facing policies, deliberately. The backend writes with the
-- service-role key (which bypasses RLS); RLS on with zero policies denies every
-- anon/authenticated read, which is what stops the public anon key being used to
-- scrape the list. Mirrors `feedback` (20260622130000_feedback.sql).
