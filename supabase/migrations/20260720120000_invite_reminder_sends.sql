-- Invite reminder drip tracking.
--
-- Records each reminder email sent for a pending workspace_invites row so the
-- scheduler sweep (app/invite_reminders.py) never double-sends a step and can
-- compute Day-3 relative to when Day-1 actually went out.
--
-- The whole "not yet accepted" state IS the workspace_invites row (accept and
-- revoke both DELETE it). The FK below is ON DELETE CASCADE, so accepting or
-- revoking an invite auto-clears its reminder rows — the drip's stop-on-accept
-- falls out of that for free.
create table if not exists invite_reminder_sends (
    id         uuid primary key default gen_random_uuid(),
    invite_id  uuid not null references workspace_invites (id) on delete cascade,
    company_id uuid not null,
    email      text not null,
    -- Which step: 'day_1' | 'day_3'. Widen the copy in code, not the schema.
    step_key   text not null,
    -- 'sent' (Resend accepted) or 'skipped' (no key / send failed). A skipped
    -- row still counts as delivered so flipping RESEND on later never
    -- retro-blasts a historical step (mirrors drip_email_sends).
    status     text not null default 'sent',
    sent_at    timestamptz not null default now(),
    -- One row per (invite, step): the idempotency guard for the sweep.
    unique (invite_id, step_key)
);

create index if not exists invite_reminder_sends_invite_idx
    on invite_reminder_sends (invite_id);

alter table invite_reminder_sends enable row level security;
-- No client-facing policies: written + read exclusively by the backend via the
-- service-role key (the scheduler runs server-side). RLS on with no policies
-- denies any anon/authenticated access by default — matches drip_email_sends.
