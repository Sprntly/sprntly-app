-- Per-user insight-type preferences + the merged 6-type taxonomy.
--
-- The weekly brief ("Top Insights") is generated ONCE per company/dataset, but
-- each member filters it down to the insight types they personally care about
-- (see backend/app/insight_types.py + synthesis/agent.py, which now composes a
-- POOL_SIZE-wide pool and classifies every finding into these types). This
-- migration does three things:
--
--   1. Remaps the two renamed slugs in the existing company-wide default
--      (companies.notification_settings->'brief_insight_types'):
--        drive_metric        -> build_priorities
--        emerging_complaints -> user_feedback
--      The client-requested "report" types (weekly feedback summary, monthly
--      competitive report, most-important-to-build) were duplicates of existing
--      chips, so the merged set is still SIX — no new slugs, just two renames.
--   2. Widens the CHECK constraint to the merged 6-slug vocabulary.
--   3. Adds user_insight_prefs — the per-user selection. Absent row / empty
--      array = "surface everything" (the reader default), so there is no
--      backfill: existing members keep today's unfiltered view until they pick.

-- ─────────────── 1 + 2. company-wide default: remap + widen ───────────────
--
-- Drop the old constraint FIRST: it lists the old slugs, so it would both
-- reject the remapped values and block the rewrite. Re-added below with the new
-- vocabulary once the data is migrated.
alter table companies
    drop constraint if exists companies_brief_insight_types_check;

-- Rewrite each stored array, renaming the two changed slugs and leaving the
-- other four untouched. distinct guards against a (degenerate) array that
-- already held both a slug and its new name; empty arrays collapse to '[]'.
update companies c
set notification_settings = jsonb_set(
        c.notification_settings,
        '{brief_insight_types}',
        (
            select coalesce(jsonb_agg(distinct
                case elem
                    when 'drive_metric'        then 'build_priorities'
                    when 'emerging_complaints' then 'user_feedback'
                    else elem
                end
            ), '[]'::jsonb)
            from jsonb_array_elements_text(
                     c.notification_settings->'brief_insight_types') as elem
        )
    )
where jsonb_typeof(c.notification_settings->'brief_insight_types') = 'array';

-- Re-add the constraint against the merged 6-slug vocabulary. Same formulation
-- as 20260721140000 (containment `<@` against a jsonb LITERAL — subqueries and
-- STABLE builders like jsonb_build_array are both illegal in a CHECK). Passes
-- when the key is absent; requires a known-slug array when present.
alter table companies
    add constraint companies_brief_insight_types_check
        check (
            notification_settings->'brief_insight_types' is null
            or (
                jsonb_typeof(notification_settings->'brief_insight_types') = 'array'
                and notification_settings->'brief_insight_types' <@ '[
                    "top_problems",
                    "build_priorities",
                    "user_feedback",
                    "competitor_moves",
                    "reliability_signals",
                    "wins"
                ]'::jsonb
            )
        );

-- ─────────────────────── 3. per-user preferences ───────────────────────
--
-- Keyed on (company_id, user_id): the brief is company/dataset-scoped, so a
-- member's filter is too. insight_types is the selection ([] = everything);
-- note mirrors the company-wide brief_insight_note free-text override.
create table if not exists user_insight_prefs (
    id            uuid primary key default gen_random_uuid(),
    company_id    uuid not null references companies (id) on delete cascade,
    user_id       uuid not null references auth.users (id) on delete cascade,
    insight_types jsonb not null default '[]'::jsonb,
    note          text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    unique (company_id, user_id),
    constraint user_insight_prefs_types_check check (
        jsonb_typeof(insight_types) = 'array'
        and insight_types <@ '[
            "top_problems",
            "build_priorities",
            "user_feedback",
            "competitor_moves",
            "reliability_signals",
            "wins"
        ]'::jsonb
    )
);

create index if not exists user_insight_prefs_user_id_idx
    on user_insight_prefs (user_id);

alter table user_insight_prefs enable row level security;

-- A member reads/writes only their OWN row, and only within a company they
-- belong to. Unlike company_members/workspace_members (service-role-only
-- writes), these are written straight from the browser through PostgREST — the
-- same path onboarding uses for companies.notification_settings — so the
-- authenticated INSERT/UPDATE policies are intentional.
create policy user_insight_prefs_select_own on user_insight_prefs
    for select to authenticated
    using (user_id = auth.uid());

create policy user_insight_prefs_insert_own on user_insight_prefs
    for insert to authenticated
    with check (
        user_id = auth.uid()
        and exists (
            select 1 from company_members cm
            where cm.company_id = user_insight_prefs.company_id
              and cm.user_id = auth.uid()
        )
    );

create policy user_insight_prefs_update_own on user_insight_prefs
    for update to authenticated
    using (user_id = auth.uid())
    with check (user_id = auth.uid());
