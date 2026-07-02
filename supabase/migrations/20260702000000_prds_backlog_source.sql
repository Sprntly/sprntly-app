-- Backlog-sourced PRDs: let a PRD be generated from a backlog item (a theme
-- ranked ≥ 4 that never made the weekly brief's top-3) instead of only from a
-- brief insight.
--
-- Brief PRDs are keyed by (brief_id, insight_index) — the insight lives at that
-- index in the brief payload. Backlog themes are NOT in brief.insights, so they
-- have no natural insight_index. Rather than fork a parallel table, we attach a
-- backlog PRD to the company's CURRENT brief (for tenant/dataset grounding) and
-- discriminate it with two new columns:
--
--   source   — 'brief' (default, every existing row) | 'backlog'
--   theme_id — the KG theme the PRD was generated from (NULL for brief PRDs,
--              which resolve their theme via brief.insights[insight_index])
--
-- Dedup and version-history grouping for backlog PRDs key on (brief_id,
-- theme_id) in code; brief PRDs keep keying on (brief_id, insight_index). The
-- index below backs the theme dedup lookup.

alter table prds add column if not exists source text not null default 'brief';
alter table prds add column if not exists theme_id text;

-- Constrain source to the two known discriminators (idempotent add).
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'prds_source_check'
  ) then
    alter table prds
      add constraint prds_source_check check (source in ('brief', 'backlog'));
  end if;
end $$;

create index if not exists prds_brief_theme_idx
    on prds (brief_id, theme_id);
