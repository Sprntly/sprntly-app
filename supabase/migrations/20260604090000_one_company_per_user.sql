-- One company per user (product decision 2026-06-04).
--
-- A user belongs to exactly ONE company; a company has many users. The
-- original schema only had unique (company_id, user_id) — which permitted a
-- user in multiple companies. Enforce the stronger invariant with a unique
-- index on user_id alone.
--
-- ⚠ Apply note: if existing rows violate this (a user in 2+ companies — e.g.
-- internal test accounts), the index creation FAILS. Dedupe first; keep the
-- oldest membership:
--
--   delete from company_members cm
--    using company_members keep
--    where cm.user_id = keep.user_id
--      and cm.created_at > keep.created_at;

create unique index if not exists company_members_one_company_per_user
    on company_members (user_id);
