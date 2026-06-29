-- Brief-nudge delivery: idempotency ledger + brief-open state.
--
-- The brief-nudge feature (app/brief_nudge.py) sends a Day-0 announcement plus
-- Day 1/2/3 reminders that drive a user to OPEN their weekly brief. Two small,
-- additive tables back it. Both are idempotent (create if not exists) so a
-- re-run / out-of-order apply never blocks the deploy.

-- One row per cadence step actually sent, so a scheduler re-tick never
-- double-sends the same (company, user, brief, day, channel).
create table if not exists brief_nudge_sends (
  id          uuid primary key default gen_random_uuid(),
  company_id  uuid not null,
  user_id     uuid not null,
  brief_id    bigint not null,
  day_offset  int  not null,            -- 0 = announce, 1/2/3 = reminders
  channel     text not null,            -- 'slack' | 'email'
  status      text not null default 'sent',
  sent_at     timestamptz not null default now(),
  created_at  timestamptz not null default now(),
  unique (company_id, user_id, brief_id, day_offset, channel)
);

create index if not exists brief_nudge_sends_company_brief_idx
  on brief_nudge_sends (company_id, brief_id, day_offset);

-- One row per (company, user, brief) the recipient opened. Presence of a row
-- means "opened" → the Day 1/2/3 reminders stop for that recipient.
create table if not exists brief_opens (
  id          uuid primary key default gen_random_uuid(),
  company_id  uuid not null,
  user_id     uuid not null,
  brief_id    bigint not null,
  opened_at   timestamptz not null default now(),
  unique (company_id, user_id, brief_id)
);

create index if not exists brief_opens_company_brief_idx
  on brief_opens (company_id, brief_id);
