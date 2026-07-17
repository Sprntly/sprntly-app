-- Onboarding v6 (screenshot spec 2026-07-17): the 9-step wizard + metric
-- definition sub-flow.
--
-- Flow changes this migration backs:
--   * sign-up "About you" gains a free-text priorities field (profiles),
--   * step 1 Company surfaces mission/strategy/portfolio/planning-cycle
--     (planning_cycle gains 'annual'),
--   * step 2 Product gains a free-text "tell us about your users" field,
--   * step 5 Team collects a team NAME alongside the scope (a company field —
--     deliberately NOT the workspaces row, which stays "Default" until renamed
--     in Settings → Workspaces),
--   * steps 6-7 (Strategy & roadmap / How your team decides) are upload-OR-type
--     blocks: typed text lands on companies columns, uploads reuse
--     company_document with four new doc_types,
--   * step 8 invites carry the teammate's JOB role (Data Science, Engineer…)
--     next to the permission role,
--   * step 9 stores the accepted AI-drafted business-context prose, and the
--     metric sub-flow stores per-metric definitions/mappings (jsonb).
--
-- Account types: the company/personal split is RETIRED from the UI (every new
-- signup is 'company'). Columns and checks stay for existing rows; nothing to
-- migrate.

-- ─────────────────────────── profiles ───────────────────────────

alter table profiles
    add column if not exists priorities text;

-- ─────────────────────────── companies ──────────────────────────

alter table companies
    add column if not exists team_name text,
    -- Typed (not uploaded) step-6/7 blocks.
    add column if not exists team_strategy text,
    add column if not exists team_roadmap text,
    add column if not exists decision_process text,
    add column if not exists additional_context text,
    -- Step 9 "Here's what we learned" — the accepted, fully-editable prose
    -- every agent reasons through (distinct from the structured 8-layer
    -- companies.business_context lens, which the agents also maintain).
    add column if not exists business_context_summary text,
    add column if not exists business_context_accepted_at timestamptz,
    -- Metric definition sub-flow: [{metric, definition, mapping, baseline}].
    add column if not exists metric_definitions jsonb not null default '[]'::jsonb;

-- planning_cycle gains 'annual' (screenshot chips: Every half / Quarterly /
-- Annual / Monthly). Postgres auto-named the inline check <table>_<column>_check.
alter table companies
    drop constraint if exists companies_planning_cycle_check;
alter table companies
    add constraint companies_planning_cycle_check
        check (planning_cycle in ('half', 'quarterly', 'annual', 'monthly'));

-- ─────────────────────────── products ───────────────────────────

alter table products
    -- Step 2 "Tell us about your users" — free prose (the old personas chips
    -- remain as a column but are no longer collected).
    add column if not exists users_description text;
    -- monetization values are client-validated (no CHECK) — the new
    -- 'partner-rev-share' / 'free' / 'one-time' options need no DDL.

-- ──────────────────────── workspace_invites ─────────────────────

alter table workspace_invites
    -- The teammate's JOB role (Data Science, Engineer…), distinct from the
    -- permission role. Free text; display-only.
    add column if not exists job_role text;

-- ──────────────────────── company_document ──────────────────────

-- Steps 6-7 upload cards: team strategy / team roadmap / decision process /
-- additional context join the four v5 doc types.
alter table company_document
    drop constraint if exists company_document_doc_type_check;
alter table company_document
    add constraint company_document_doc_type_check
        check (doc_type in (
            'ceo_memo', 'team_priorities', 'research', 'company_strategy',
            'team_strategy', 'team_roadmap', 'decision_process',
            'additional_context'
        ));

-- ─────────────────────── handle_new_user ────────────────────────
-- Recreate with the FULL latest body (20260716120000_account_type_onboarding_v5)
-- plus one new metadata read: priorities (the sign-up "About you" free-text,
-- sent by auth.signUpWithPassword). account_type metadata is still honored for
-- in-flight signups but the UI no longer sends 'personal'.

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    v_first text := coalesce(
        nullif(new.raw_user_meta_data ->> 'first_name', ''),
        new.raw_user_meta_data ->> 'given_name',
        ''
    );
    v_last text := coalesce(
        nullif(new.raw_user_meta_data ->> 'last_name', ''),
        new.raw_user_meta_data ->> 'family_name',
        ''
    );
    v_timezone text := nullif(new.raw_user_meta_data ->> 'timezone', '');
    v_role text := nullif(new.raw_user_meta_data ->> 'role', '');
    v_priorities text := nullif(new.raw_user_meta_data ->> 'priorities', '');
    v_account_type text := case
        when new.raw_user_meta_data ->> 'account_type' in ('company', 'personal')
            then new.raw_user_meta_data ->> 'account_type'
        else null
    end;
begin
    insert into public.profiles
        (id, email, first_name, last_name, full_name, avatar_url, timezone,
         role, priorities, account_type)
    values (
        new.id,
        new.email,
        v_first,
        v_last,
        coalesce(
            nullif(new.raw_user_meta_data ->> 'full_name', ''),
            new.raw_user_meta_data ->> 'name',
            nullif(trim(both from concat_ws(' ', nullif(v_first, ''), nullif(v_last, ''))), ''),
            ''
        ),
        coalesce(
            new.raw_user_meta_data ->> 'avatar_url',
            new.raw_user_meta_data ->> 'picture',
            ''
        ),
        v_timezone,
        v_role,
        v_priorities,
        v_account_type
    );
    return new;
end;
$$;
