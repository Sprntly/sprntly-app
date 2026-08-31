-- Billing email de-dup guard.
--
-- Every trigger for these emails fires more than once. Stripe redelivers a
-- webhook for days; the trial-ending reminder is a scheduler tick that runs
-- hourly against the same subscription; a spend that crosses the low-credit
-- threshold is followed by more spends still under it. Without a record of what
-- has been sent, a customer gets "your trial has started" eleven times.
--
-- Mirrors `drip_email_sends` (20260614100000): one row per delivered email,
-- UNIQUE on the thing that identifies the OCCASION rather than the moment.
--
-- `ref_id` is what makes an occasion distinct, and it differs per kind on
-- purpose:
--   trial_started / cancelled / trial_ending  -> the subscription id
--   plan_changed                              -> subscription + plan moved to
--   credits_low / credits_exhausted           -> the billing period, so it can
--                                                fire again next month
--   referral_converted                        -> the referral id
--   topup_purchased                           -> the checkout session id
--
-- So "once per occasion" is expressed in data rather than in whichever caller
-- remembered to check.

create table if not exists billing_email_sends (
  id          bigserial primary key,
  company_id  uuid not null references companies(id) on delete cascade,

  -- Which email. A stable slug, not a subject line — copy changes, the
  -- identity of the occasion does not.
  kind        text not null,
  ref_id      text not null,

  -- Who actually received it. Billing mail goes to owners and admins, so one
  -- occasion can produce several rows.
  email       text not null,

  -- 'sent' or 'skipped'. A skip is recorded too: without RESEND_API_KEY the
  -- send is a no-op, and recording it stops the whole backlog blasting out the
  -- day somebody sets the key.
  status      text not null default 'sent'
                check (status in ('sent', 'skipped')),

  sent_at     timestamptz not null default now(),

  unique (company_id, kind, ref_id, email)
);

-- READ PATTERN: "has this occasion been sent to this person", plus a company's
-- recent mail when support asks. Leads with company_id so it also serves the
-- delete cascade — Postgres does not index a foreign key for you, and an
-- unindexed one turns every parent delete into a full scan of this table.
create index if not exists billing_email_sends_company_kind_idx
  on billing_email_sends (company_id, kind, sent_at desc);

-- ---------------------------------------------------------------------------
-- RLS — service-role only. `to service_role` is explicit so the policy does not
-- silently sit open to `anon`.
-- ---------------------------------------------------------------------------

alter table billing_email_sends enable row level security;

drop policy if exists srv_billing_email_sends on billing_email_sends;
create policy srv_billing_email_sends on billing_email_sends
  for all to service_role using (true) with check (true);
