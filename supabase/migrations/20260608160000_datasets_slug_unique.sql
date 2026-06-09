-- Enforce a UNIQUE constraint on datasets.slug.
--
-- Background / bug
-- ----------------
-- `insert_dataset(slug, display_name)` did a check-then-insert (SELECT the
-- slug, INSERT if absent) with no DB-level uniqueness backing it. Two
-- concurrent creates of the same slug can both pass the SELECT and both
-- INSERT, duplicating the slug — and slug is the tenant/dataset key used
-- everywhere else (briefs.dataset, cached_asks.dataset, the corpus loader),
-- so a dupe is corrupting.
--
-- The original table (20260525120400_datasets.sql) declares slug as
-- `primary key`, which is already unique in a clean DB. This migration makes
-- that guarantee explicit and idempotent so the application can rely on a
-- 23505 unique-violation to close the race (insert_dataset catches it and
-- treats it as "already exists").
--
-- Dedup first: if any duplicate slug rows snuck in before the constraint
-- existed, keep the earliest-created row per slug and drop the rest, so the
-- unique index can be created without error.
delete from datasets a
using datasets b
where a.slug = b.slug
  and a.created_at > b.created_at;

-- Tie-break for rows with identical created_at: keep one arbitrary row
-- (by ctid) per slug.
delete from datasets a
using datasets b
where a.slug = b.slug
  and a.created_at = b.created_at
  and a.ctid > b.ctid;

create unique index if not exists datasets_slug_unique on datasets (slug);
