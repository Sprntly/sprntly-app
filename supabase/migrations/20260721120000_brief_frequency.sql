-- Brief cadence — make the schedule's frequency explicit on every company.
--
-- Settings → "Communications to you on the Top Product Insights" gained a
-- Frequency control alongside the existing Day / Time / Timezone dropdowns:
--   daily_weekdays — Monday..Friday, no weekends (the Day dropdown is hidden)
--   weekly         — the default, and the only behaviour that existed before
--   biweekly       — every other week on the chosen day (14-day cadence)
--   monthly        — the FIRST chosen weekday of each month
--
-- WHERE IT LIVES. The rest of the schedule (brief_weekday, brief_hour,
-- brief_minute, timezone) already lives inside the `companies`
-- .notification_settings JSONB, and app.brief_schedule resolves all of it from
-- that one object. Frequency is stored alongside them as `brief_frequency`
-- rather than promoted to its own column: splitting one schedule across a
-- JSONB blob and a scalar column would mean two write paths that can disagree,
-- and every existing reader/writer (settings pane, onboarding day picker, the
-- scheduler tick) already merges this object atomically.
--
-- BACKFILL, NOT A BEHAVIOUR CHANGE. app.brief_schedule.resolve_frequency
-- defaults a missing/unrecognised value to 'weekly', so this migration is not
-- required for correctness — every pre-existing company keeps its exact
-- current schedule either way. It is here so the stored state is explicit and
-- self-describing rather than relying on an absent key, which makes the value
-- greppable in the DB and the settings pane's Save a plain merge.
--
-- `brief_anchor_date` (the BIWEEKLY anchor, an ISO date string) is deliberately
-- NOT backfilled: it is only meaningful for biweekly, the settings pane stamps
-- it at save time, and the resolver falls back to the Unix-epoch Monday
-- (1970-01-05) when it is absent.

-- Only touch rows that don't already carry the key, so re-running is a no-op
-- and a company that has already saved a non-weekly cadence is never reset.
update companies
   set notification_settings =
         coalesce(notification_settings, '{}'::jsonb)
         || jsonb_build_object('brief_frequency', 'weekly')
 where coalesce(notification_settings, '{}'::jsonb) -> 'brief_frequency' is null;

-- WEEKEND SEND DAYS. The Day picker now offers Monday–Friday only (the brief
-- is a work artefact, so a weekend send has no audience). Rows written while
-- Saturday/Sunday were still selectable hold brief_weekday 5 or 6, which the
-- dropdown can no longer represent. Move them to Monday (0) — the product
-- default, and the next weekday after either weekend day.
--
-- app.brief_schedule.resolve_schedule and the settings pane apply the SAME
-- coercion at read time, so this is again a backfill rather than the mechanism:
-- a company that never opens settings is already moved off the weekend by the
-- resolver. Doing it in the data too keeps the stored value honest.
update companies
   set notification_settings =
         notification_settings || jsonb_build_object('brief_weekday', 0)
 where (notification_settings ->> 'brief_weekday') in ('5', '6');

-- Guard the enum at the DB level. Written to PASS when the key is absent
-- (resolve_frequency's default covers that) so the constraint can never block
-- an unrelated notification_settings write from an older client.
alter table companies drop constraint if exists companies_brief_frequency_check;

alter table companies
    add constraint companies_brief_frequency_check
    check (
      notification_settings -> 'brief_frequency' is null
      or notification_settings ->> 'brief_frequency'
         in ('daily_weekdays', 'weekly', 'biweekly', 'monthly')
    );
