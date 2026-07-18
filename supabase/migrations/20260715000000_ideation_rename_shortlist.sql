-- Backlog → Ideation rename + weekly prioritization shortlist.
--
-- The backlog grew without bound (every non-brief converged theme persists, no
-- cap — see 20260608120000_backlog_items.sql and PR #374), so the page drowns
-- in 100+ ideas. The feature is renamed to "Ideation" end-to-end, and a weekly
-- LLM prioritization pass now picks the 25–30 ideas worth showing: they get
-- `shortlisted = true`; the rest stay persisted (audit trail + they can climb
-- back in on a later run) but hidden from every read path.
--
-- Compat window (⚠ shared prod DB): this migration applies on merge-to-main
-- (staging migrate-on-deploy) while the `production` branch still runs the old
-- code. A pass-through view keeps old-prod reads/updates/inserts working:
--   * old prod SELECT/UPDATE/DELETE/INSERT via `backlog_items` → auto-updatable
--     simple view over ideation_items.
--   * the legacy status value 'backlog' and prds.source 'backlog' stay ALLOWED
--     (old prod still writes them); new code treats them as synonyms of
--     'proposed' / 'ideation'.
--   * known gap: old prod's sequencer upsert (ON CONFLICT) cannot resolve the
--     unique index through the view, so it no-ops inside its existing
--     best-effort try/except — prod's backlog goes stale (not broken) until
--     prod cutover.
-- A follow-up cleanup migration (at/after prod cutover) drops the view,
-- rewrites any legacy 'backlog' values, and tightens both CHECKs.
--
-- Idempotent: the rename + one-time backfills are guarded on the base table
-- still carrying its old name; everything else is IF (NOT) EXISTS / re-runnable.

do $$
begin
  if exists (
    select 1 from information_schema.tables
    where table_schema = 'public'
      and table_name = 'backlog_items'
      and table_type = 'BASE TABLE'
  ) then
    alter table backlog_items rename to ideation_items;
    alter index if exists backlog_items_rank_idx rename to ideation_items_rank_idx;

    -- Rename the auto-named constraints for hygiene (guarded: names come from
    -- the original inline definitions).
    if exists (select 1 from pg_constraint where conname = 'backlog_items_pkey') then
      alter table ideation_items rename constraint backlog_items_pkey to ideation_items_pkey;
    end if;
    if exists (select 1 from pg_constraint where conname = 'backlog_items_enterprise_id_theme_id_key') then
      alter table ideation_items rename constraint backlog_items_enterprise_id_theme_id_key
        to ideation_items_enterprise_id_theme_id_key;
    end if;
    if exists (select 1 from pg_constraint where conname = 'backlog_items_enterprise_id_fkey') then
      alter table ideation_items rename constraint backlog_items_enterprise_id_fkey
        to ideation_items_enterprise_id_fkey;
    end if;
  end if;
end $$;

-- Shortlist flag: set weekly by the prioritization pass (sequence_ideation).
alter table ideation_items
  add column if not exists shortlisted boolean not null default false;

create index if not exists ideation_items_shortlist_idx
  on ideation_items (enterprise_id) where shortlisted;

-- Status: 'backlog' → 'proposed'. The legacy value stays ALLOWED until the
-- post-cutover cleanup because old prod still inserts manual ideas with
-- status='backlog' through the compat view.
do $$
begin
  if exists (select 1 from pg_constraint where conname = 'backlog_items_status_check') then
    alter table ideation_items drop constraint backlog_items_status_check;
  end if;
  if exists (select 1 from pg_constraint where conname = 'ideation_items_status_check') then
    alter table ideation_items drop constraint ideation_items_status_check;
  end if;
  alter table ideation_items
    add constraint ideation_items_status_check
    check (status in ('proposed', 'backlog', 'in_progress', 'done', 'dismissed'));
end $$;

alter table ideation_items alter column status set default 'proposed';

-- One-time backfills (guarded so a re-run never clobbers the LLM's shortlist):
-- run only while no row has ever been shortlisted, i.e. before the first
-- prioritization pass. Seeds the page so it isn't empty until the next weekly
-- run: top 28 by rank per enterprise + every manual idea.
do $$
begin
  if not exists (select 1 from ideation_items where shortlisted) then
    update ideation_items set status = 'proposed' where status = 'backlog';
    update ideation_items
      set shortlisted = true
      where rank <= 28 or theme_id like 'manual:%';
  end if;
end $$;

-- prds.source: 'backlog' → 'ideation'. Widen the CHECK first (the old one
-- would reject 'ideation'), then rewrite. Same legacy tolerance: old prod
-- keeps writing source='backlog' for ideation-sourced PRDs until cutover.
do $$
begin
  if exists (select 1 from pg_constraint where conname = 'prds_source_check') then
    alter table prds drop constraint prds_source_check;
  end if;
  alter table prds
    add constraint prds_source_check
    check (source in ('brief', 'ideation', 'backlog', 'upload'));
end $$;

update prds set source = 'ideation' where source = 'backlog';

-- Compat pass-through for the old prod code (dropped at prod cutover). A
-- simple single-table view is auto-updatable, so old-prod SELECT / UPDATE /
-- DELETE / plain INSERT all keep working.
create or replace view backlog_items as select * from ideation_items;
