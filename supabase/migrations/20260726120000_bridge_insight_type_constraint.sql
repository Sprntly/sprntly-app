-- BRIDGE (temporary): re-widen companies_brief_insight_types_check to accept
-- BOTH insight-slug vocabularies until the prod frontend cutover.
--
-- Why: staging and prod share this database, and migrations apply on every
-- main deploy. 20260723140000 renamed two slugs (drive_metric ->
-- build_priorities, emerging_complaints -> user_feedback) and tightened the
-- CHECK to the new 6-slug set — but the PROD frontend (pre-cutover) still
-- writes the OLD slugs, and its personalize step preselects drive_metric by
-- default. Result: every new prod onboarding failed at "Personalize your
-- workspace" with a constraint violation (2026-07-26).
--
-- This bridge accepts the union (6 new + 2 old) so old-frontend saves succeed.
-- Readers already treat unknown slugs as "no filter", so an old slug stored
-- during the bridge window degrades gracefully.
--
-- AFTER the prod cutover ships the new frontend, land a follow-up migration
-- that re-runs 20260723140000's remap (old slug -> new slug rewrite of
-- notification_settings->'brief_insight_types') and restores the strict
-- 6-slug constraint. Same formulation rules as before: containment (<@)
-- against a jsonb LITERAL — subqueries and STABLE builders are illegal in a
-- CHECK.
--
-- user_insight_prefs needs no bridge: only the post-rename frontend writes it.

alter table companies
    drop constraint if exists companies_brief_insight_types_check;

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
                    "wins",
                    "drive_metric",
                    "emerging_complaints"
                ]'::jsonb
            )
        );
