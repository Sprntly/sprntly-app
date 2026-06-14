-- Onboarding drip / nudge email tracking (v0 checklist 2.1).
--
-- Recurring onboarding emails are sent on a schedule (e.g. day-1 / day-3 /
-- day-7 after a member joins a company). This table records EVERY drip step
-- that has been sent to a given (company member × step) so the scheduler
-- never double-sends a step. One row per delivered step; the UNIQUE
-- constraint is the idempotency guard (a concurrent or retried cycle that
-- races to insert the same step hits the constraint and is treated as
-- already-sent).
--
-- Per-company cadence + opt-out lives in companies.notification_settings
-- (JSONB, already present from 20260525150000_onboarding_workspace.sql); see
-- app/drip_email.py:resolve_cadence. This table is delivery bookkeeping only.

create table if not exists drip_email_sends (
    id          uuid primary key default gen_random_uuid(),
    company_id  uuid not null references companies (id) on delete cascade,
    user_id     uuid not null references auth.users (id) on delete cascade,
    -- Stable identifier of the cadence step (e.g. "day_1", "day_3", "day_7").
    -- Matches the `key` of an entry in the resolved cadence.
    step_key    text not null,
    email       text not null,
    -- "sent" when Resend accepted the message; "skipped" when sending was
    -- not configured (no RESEND_API_KEY) but we still record the step so a
    -- later config change doesn't retroactively blast old steps.
    status      text not null default 'sent'
                  check (status in ('sent', 'skipped')),
    sent_at     timestamptz not null default now(),
    unique (company_id, user_id, step_key)
);

create index if not exists drip_email_sends_company_user_idx
    on drip_email_sends (company_id, user_id);

alter table drip_email_sends enable row level security;

-- No client-facing policies: this table is written + read exclusively by the
-- backend via the service-role key (the scheduler runs server-side). RLS is
-- enabled with no policies so any anon/authenticated access is denied by
-- default, matching the other server-only tables.
