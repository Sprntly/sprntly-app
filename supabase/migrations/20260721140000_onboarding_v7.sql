-- Onboarding v7 (2026-07-21 screenshot spec).
--
-- The flow collapsed team/strategy/decisions into a single "Your workspace"
-- step and gained a "Personalize your workspace" step. Almost nothing new is
-- needed on the schema side, deliberately:
--
--   * The workspace step's sizing prompt reuses companies.sizing_methodology
--     (added 20260716120000, already owned by Settings → Process) rather than
--     introducing a parallel column that would drift out of sync.
--   * The personalize step's delivery cadence reuses the existing
--     companies.notification_settings JSONB — same keys Settings → Comms &
--     Brief already writes (brief_frequency / brief_weekday / brief_hour /
--     brief_minute / timezone). See 20260721120000_brief_frequency.sql, whose
--     header argues against promoting schedule fields to scalar columns; that
--     argument applies to the new insight-types key below too.
--
-- So this migration only does two things: allow the sizing attachment as a
-- document type, and constrain the new insight-types key.

-- ──────────────────────── company_document ──────────────────────

-- The workspace step's "Attach a previous sizing doc" affordance uploads
-- through the same companyDocsApi path as the strategy/roadmap cards, so its
-- doc_type has to be in the allow-list. Re-stated in full (the constraint is
-- replaced, not appended to).
alter table company_document
    drop constraint if exists company_document_doc_type_check;

alter table company_document
    add constraint company_document_doc_type_check
        check (doc_type in (
            'ceo_memo', 'team_priorities', 'research', 'company_strategy',
            'team_strategy', 'team_roadmap', 'decision_process',
            'additional_context', 'sizing_doc'
        ));

-- ─────────────────── notification_settings.brief_insight_types ───────────
--
-- What the workspace should surface — the personalize step's chips. Stored in
-- the existing JSONB blob rather than a new column/table, matching how every
-- other brief-delivery preference is held.
--
-- Readers default to "everything" when the key is absent, so there is no
-- backfill: an existing company with no key keeps today's behaviour. The check
-- PASSES when the key is missing, and requires an array of known slugs when
-- present.
--
-- Membership is expressed with the JSONB containment operator (`<@`, "is
-- contained by") against a jsonb LITERAL. Two constraints on the formulation:
--   * Postgres forbids subqueries in CHECK, so the obvious `not exists (select
--     … from jsonb_array_elements_text(…))` fails at DDL time (SQLSTATE 0A000).
--   * The allow-list has to be a literal cast, not jsonb_build_array(), which
--     is STABLE rather than IMMUTABLE and so is rejected in a CHECK.
-- Containment on two JSONB arrays ignores order and duplicates, which is
-- exactly the semantics we want.
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
                    "drive_metric",
                    "emerging_complaints",
                    "competitor_moves",
                    "reliability_signals",
                    "wins"
                ]'::jsonb
            )
        );
