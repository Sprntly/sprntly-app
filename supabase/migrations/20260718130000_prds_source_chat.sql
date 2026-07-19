-- Chat-task PRDs: allow prds.source = 'chat'.
--
-- "Generate a PRD for <specific need>" typed in chat builds a PRD from the
-- user's own words — no brief insight, no KG theme. Those rows are a distinct
-- origin from 'brief' / 'ideation' / 'upload', so widen the CHECK. Dedup keys
-- on a synthetic theme_id ('chat:<hash-of-task>'), same precedent as ideation's
-- 'manual:%' ids.
do $$
begin
  if exists (
    select 1 from pg_constraint where conname = 'prds_source_check'
  ) then
    alter table prds drop constraint prds_source_check;
  end if;
  alter table prds
    add constraint prds_source_check
    check (source in ('brief', 'ideation', 'backlog', 'upload', 'chat'));
end $$;
