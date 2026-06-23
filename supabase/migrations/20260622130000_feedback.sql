-- In-app feedback / feature-request submissions (June 20 #13 + #A).
--
-- v1 of the "feature request / feedback" surface: users open a lightweight
-- form from the left nav (next to sign-out), type a short message, optionally
-- pick a type (bug / feature_request / connector_request / other), and submit.
-- Each submission is stored here AND emailed to the team via Resend (see
-- app/db/feedback.py + app/routes/feedback.py).
--
-- We capture the submitting user + company for context so the team can follow
-- up. company_id/user_id may be NULL only in the degenerate case where the
-- session lacked an identity; the route requires a company, so in practice
-- both are always set.

create table if not exists feedback (
    id          uuid primary key default gen_random_uuid(),
    company_id  uuid references companies (id) on delete set null,
    user_id     uuid references auth.users (id) on delete set null,
    -- Denormalised email at submit time so the team can reply even if the
    -- profile/membership later changes.
    user_email  text,
    -- One of: 'bug', 'feature_request', 'connector_request', 'other'.
    type        text not null default 'other'
                  check (type in ('bug', 'feature_request', 'connector_request', 'other')),
    message     text not null,
    created_at  timestamptz not null default now()
);

create index if not exists feedback_company_idx on feedback (company_id, created_at desc);

alter table feedback enable row level security;

-- No client-facing policies: written + read exclusively by the backend via the
-- service-role key (the route runs server-side). RLS on with no policies denies
-- all anon/authenticated access by default, matching the other server-only
-- tables (drip_email_sends, etc.).
