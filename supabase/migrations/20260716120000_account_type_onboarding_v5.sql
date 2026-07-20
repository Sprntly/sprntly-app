-- Onboarding v5 (registration spec 2026-07): explicit company-vs-personal
-- account types + the new Company/Product/Team profile fields.
--
-- Field placement follows the spec's marks: starred fields are
-- onboarding-mandatory FOR COMPANY ACCOUNTS ONLY (enforced client-side —
-- personal accounts may skip everything), blue fields never appear in
-- onboarding and are edited in Settings. Simple scalars are first-class
-- columns (matching industry/stage/okrs precedent); grouped fields (ICP,
-- tone & voice) are small jsonb objects.
--
-- account_type lives on BOTH profiles (chosen at signup, before any company
-- exists) and companies (denormalized at workspace creation so company-scoped
-- reads never need a join). NULL on profiles means "not chosen yet" — only
-- possible for new Google SSO users, who are routed through the your-name
-- gate to pick one; all existing rows are backfilled to 'company' because
-- they signed up under the company-shaped flow.

-- ─────────────────────────── profiles ───────────────────────────

alter table profiles
    add column if not exists account_type text
        check (account_type in ('company', 'personal'));

update profiles set account_type = 'company' where account_type is null;

-- ─────────────────────────── companies ──────────────────────────

alter table companies
    add column if not exists account_type text
        check (account_type in ('company', 'personal')),
    add column if not exists mission text,
    add column if not exists strategy text,
    -- Blue/settings-only fields below.
    add column if not exists portfolio text,
    add column if not exists icp jsonb not null default '{}'::jsonb,
        -- keys: segment text, buyer_persona text, buyer text
    add column if not exists tone_voice jsonb not null default '{}'::jsonb,
        -- keys: brand text, tone text, colors text[]
    add column if not exists planning_cycle text
        check (planning_cycle in ('half', 'quarterly', 'monthly')),
    -- Team section.
    add column if not exists team_scope text,
    add column if not exists prioritization_framework text
        check (prioritization_framework in
               ('goal-based', 'rice', 'wsjf', 'moscow', 'kano', 'volume-severity')),
    add column if not exists sizing_methodology text;

update companies set account_type = 'company' where account_type is null;

-- ─────────────────────────── products ───────────────────────────

alter table products
    add column if not exists surfaces text[] not null default '{}',
        -- canonical values: 'web','mobile','api','hardware' (client-validated)
    add column if not exists personas text[] not null default '{}',
    add column if not exists positioning text,
    add column if not exists monetization text[] not null default '{}',
        -- 'subscription','seat','usage','transaction-fee','advertising'
    -- "State" in the spec; named maturity to avoid colliding with the
    -- existing companies.stage (Seed/Growth/Scale).
    add column if not exists maturity text
        check (maturity in ('enterprise', 'mid-market', 'startup', 'early-stage'));

-- ─────────────────────── handle_new_user ────────────────────────
-- Recreate with the FULL latest body (20260625120000_user_timezone.sql —
-- first/last/full name, avatar, timezone) plus two new metadata reads:
--   * role — sent by auth.signUpWithPassword since the v4 signup but never
--     persisted by any trigger version (fixes that gap), and
--   * account_type — the signup choice, validated against the two values.

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
    v_account_type text := case
        when new.raw_user_meta_data ->> 'account_type' in ('company', 'personal')
            then new.raw_user_meta_data ->> 'account_type'
        else null
    end;
begin
    insert into public.profiles
        (id, email, first_name, last_name, full_name, avatar_url, timezone,
         role, account_type)
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
        v_account_type
    );
    return new;
end;
$$;
