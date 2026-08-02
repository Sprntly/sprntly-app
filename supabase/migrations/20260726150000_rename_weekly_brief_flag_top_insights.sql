-- Weekly Brief → Top Insights rename: move the per-company module flag key.
--
-- companies.feature_flags is a JSONB dict of module booleans resolved by
-- backend/app/entitlements.py. The `weekly_brief` module was renamed to
-- `top_insights`; this migration rewrites stored rows so the modern key is
-- explicit. The backend keeps honoring `weekly_brief` as a read-time alias
-- (when the modern key is absent), so ordering against the code deploy is not
-- load-bearing in either direction.
--
-- Semantics preserved exactly: only rows that HAVE the legacy key are touched,
-- the boolean value is copied as-is, and the legacy key is removed. Rows with
-- both keys already present keep their `top_insights` value (the modern key
-- always wins in the resolver, so dropping the legacy key changes nothing).

UPDATE companies
SET feature_flags =
    (feature_flags - 'weekly_brief')
    || CASE
         WHEN feature_flags ? 'top_insights' THEN '{}'::jsonb
         ELSE jsonb_build_object('top_insights', feature_flags -> 'weekly_brief')
       END
WHERE feature_flags ? 'weekly_brief';
