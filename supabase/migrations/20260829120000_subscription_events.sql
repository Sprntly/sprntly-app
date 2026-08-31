-- Subscription history.
--
-- `companies` carries only the CURRENT subscription state — plan, status,
-- period end — and every webhook overwrites it. So the database can say a
-- company is on Starter and active today, and nothing more: not that it was on
-- Product Builder last month, not that it cancelled and came back, not when
-- any of it happened. Stripe's dashboard has that history; we did not.
--
-- That is fine until the first chargeback dispute, the first "why was I billed
-- twice", or the first question about churn and upgrade patterns that has to be
-- answered from our own data rather than by reading somebody else's UI.
--
-- APPEND-ONLY, and written only when something actually CHANGES. A single
-- purchase produces three webhooks (invoice.paid, customer.subscription.created,
-- checkout.session.completed) and each one re-syncs the subscription; logging
-- every sync would bury one real transition under two identical rows.

create table if not exists subscription_events (
  id                bigserial primary key,
  company_id        uuid not null references companies(id) on delete cascade,

  -- What it became.
  plan              text not null,
  status            text,
  -- What it was. Null on the first row for a company, which is itself the
  -- signal that this is where their billing history begins.
  previous_plan     text,
  previous_status   text,

  stripe_subscription_id text,
  current_period_end     timestamptz,

  -- Which door this arrived through: a webhook type ('invoice.paid',
  -- 'customer.subscription.updated', …), 'reconcile' for the pull path, or
  -- 'change_plan' for an in-app switch. Answers "how did we learn this?",
  -- which is the first question asked when two records disagree.
  source            text,

  created_at        timestamptz not null default now()
);

-- READ PATTERN: one company's history, newest first. Composite rather than a
-- bare company_id index so the ordering comes free.
create index if not exists subscription_events_company_time_idx
  on subscription_events (company_id, created_at desc);

-- INDEX THE FOREIGN KEY. Postgres does not create one for a FK, and an
-- unindexed FK turns every parent delete into a full scan of this table — the
-- exact cost that made deleting one test tenant take three attempts and knock
-- the backend over (kg_signal.source_id, 2026-08-28). The composite above
-- already leads with company_id and serves the cascade, so this note exists to
-- stop anyone "tidying" it into something that does not.

-- ---------------------------------------------------------------------------
-- RLS — service-role only, matching every other table in this schema.
-- `to service_role` is explicit so the policy does not silently sit open to
-- `anon`.
-- ---------------------------------------------------------------------------

alter table subscription_events enable row level security;

drop policy if exists srv_subscription_events on subscription_events;
create policy srv_subscription_events on subscription_events
  for all to service_role using (true) with check (true);
