-- PRD import: allow prds.source = 'upload'.
--
-- prds.source discriminates how a PRD originated. It was introduced in
-- 20260702000000_prds_backlog_source.sql constrained to ('brief','backlog').
-- Uploaded PRDs (the customer uploads an existing PDF/PPT, we convert it into
-- our format) are a third origin. Widen the CHECK to include 'upload'.
--
-- Idempotent: drop the old constraint if present, then add the widened one.
-- The default stays 'brief' (every legacy row), so existing rows are unaffected.

do $$
begin
  if exists (
    select 1 from pg_constraint where conname = 'prds_source_check'
  ) then
    alter table prds drop constraint prds_source_check;
  end if;

  alter table prds
    add constraint prds_source_check
    check (source in ('brief', 'backlog', 'upload'));
end $$;
