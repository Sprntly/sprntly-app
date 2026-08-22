-- Billing: subscription plans, an action-credit balance, referrals.
--
-- Sprntly bills a company (not a user, not a workspace): a Team plan's credits
-- are described as "pooled", and a company-level balance IS that pool with no
-- extra machinery. Every column here therefore lands on `companies`, next to
-- the entitlement snapshot (seat_limit / prototype_enabled / feature_flags)
-- that staff admin already writes.
--
-- A CREDIT IS AN ACTION, not a dollar and not a token (owner decision,
-- 2026-08-21). One chat turn costs 1, a PRD costs 25 — the table lives in
-- app/billing/plans.py::CREDIT_COSTS. Actual model spend stays where it
-- already is (`llm_usage_events`), which is analytics, is deliberately lossy,
-- and must never be summed against this ledger: they measure different things
-- and will never agree.
--
-- `companies.credit_balance` is the AUTHORITATIVE counter; `credit_ledger` is
-- the audit trail explaining how it got there. Two sources of truth is a real
-- risk, taken deliberately: summing a ledger on every generation start would
-- be a query per job on a path the user waits on. They are kept honest by
-- writing both in one compare-and-swap (app/billing/credits.py::spend) and by
-- `balance_after` on every row, which makes a divergence visible rather than
-- silent.
--
-- No free trial (owner decision): a plan is paid from day one, and the refund
-- window below replaces the trial. `first_paid_at` starts that clock.

-- ---------------------------------------------------------------------------
-- companies: plan + balance + the Stripe handles
-- ---------------------------------------------------------------------------

alter table companies
  -- 'starter' | 'product_builder' | 'team' | 'enterprise' | 'legacy'.
  -- Deliberately NOT a check constraint: plan names are product copy and will
  -- change faster than migrations. app/billing/plans.py is the allow-list, and
  -- it resolves an unknown value to the most restrictive plan.
  --
  -- The default is what every EXISTING company gets at launch (owner decision
  -- 2026-08-21, overriding a recommendation to grandfather them as unlimited).
  -- If that proves too aggressive, flip LAUNCH_DEFAULT_PLAN in plans.py and
  -- update the rows — no migration needed.
  add column if not exists plan text not null default 'starter',
  add column if not exists credit_balance integer not null default 0,
  -- Start of the billing period the current grant was issued for. The monthly
  -- top-up is idempotent on this: a replayed `invoice.paid` for a period we
  -- already granted is a no-op rather than free credits.
  add column if not exists credits_granted_for timestamptz,
  add column if not exists stripe_customer_id text,
  add column if not exists stripe_subscription_id text,
  -- Mirrors Stripe's own subscription status vocabulary verbatim (active,
  -- trialing, past_due, canceled, unpaid, incomplete, incomplete_expired,
  -- paused). Storing Stripe's word rather than our own interpretation means a
  -- status we have not thought about yet is still recorded faithfully, and
  -- plans.py decides what each one grants.
  add column if not exists subscription_status text,
  add column if not exists current_period_end timestamptz,
  -- When this company's FIRST invoice was paid. Anchors the 7-day
  -- cancel-and-refund window; null for a company that has never paid.
  add column if not exists first_paid_at timestamptz;

-- Webhooks arrive keyed by Stripe customer, never by our tenant id, so this
-- lookup is on the hot path of every billing event.
create unique index if not exists companies_stripe_customer_uidx
  on companies (stripe_customer_id)
  where stripe_customer_id is not null;

-- ---------------------------------------------------------------------------
-- credit_ledger — append-only audit of every balance change
-- ---------------------------------------------------------------------------

create table if not exists credit_ledger (
  id             bigint generated always as identity primary key,
  company_id     text not null,
  -- Negative = spend, positive = grant. Never zero.
  delta          integer not null,
  -- 'monthly_grant' | 'spend' | 'referral' | 'topup' | 'refund' | 'adjustment'
  reason         text not null,
  -- For a spend: the surface it was spent on ('prd', 'chat', …), so the
  -- Billing screen can answer "what did my credits go to" without joining
  -- anything. Null on grants.
  feature        text,
  -- Idempotency handle: the job id, Stripe object id, or referral id that
  -- caused this row. Two writes with the same (company_id, reason, ref_id)
  -- are the same event replayed — the partial unique index below rejects the
  -- second. Null is allowed (manual staff adjustments have no natural key)
  -- and, per SQL null semantics, never collides.
  ref_id         text,
  -- Balance immediately after applying `delta`. Makes a drift between this
  -- ledger and companies.credit_balance detectable by inspection.
  balance_after  integer not null,
  -- Who/what triggered it: a user id for a spend, null for webhook grants.
  actor_user_id  text,
  created_at     timestamptz not null default now()
);

create index if not exists credit_ledger_company_created_idx
  on credit_ledger (company_id, created_at desc);

-- The idempotency guarantee. Partial so that ref_id-less adjustments stay
-- unconstrained.
create unique index if not exists credit_ledger_idempotency_uidx
  on credit_ledger (company_id, reason, ref_id)
  where ref_id is not null;

-- ---------------------------------------------------------------------------
-- referrals — invite a friend, get credit when they actually pay
-- ---------------------------------------------------------------------------
--
-- Distinct from `workspace_invites`, which adds a teammate INSIDE your company
-- and consumes a seat. A referral brings a whole new company onto Sprntly.
-- Conflating the two would let a user farm referral credit by inviting their
-- own colleagues.

create table if not exists referrals (
  id                    text primary key,
  -- The company that gets paid when this converts.
  referrer_company_id   text not null,
  referrer_user_id      text,
  -- Lowercased at write time; the invite is addressed to a person, and the
  -- same address must not be invitable twice by the same referrer.
  invitee_email         text not null,
  -- Opaque, unguessable, carried in the invite link.
  code                  text not null unique,
  -- 'pending'  — sent, nothing has happened
  -- 'signed_up'— they created a company (NOT yet rewarded)
  -- 'rewarded' — their first invoice paid and the credit was granted
  -- 'void'     — self-referral or otherwise rejected
  status                text not null default 'pending',
  -- Set at signup; lets us reward on that company's first invoice.
  invitee_company_id    text,
  reward_credits        integer,
  created_at            timestamptz not null default now(),
  signed_up_at          timestamptz,
  rewarded_at           timestamptz
);

create index if not exists referrals_referrer_idx
  on referrals (referrer_company_id, created_at desc);
create index if not exists referrals_invitee_company_idx
  on referrals (invitee_company_id)
  where invitee_company_id is not null;
-- One live invite per (referrer, email). Re-inviting an address you already
-- invited is a no-op, not a second shot at the reward.
create unique index if not exists referrals_referrer_email_uidx
  on referrals (referrer_company_id, lower(invitee_email));

-- ---------------------------------------------------------------------------
-- stripe_events — webhook replay guard
-- ---------------------------------------------------------------------------
--
-- Stripe retries a webhook for up to 3 days until it gets a 2xx, and delivery
-- is explicitly at-least-once and NOT ordered. Every handler here is written
-- to be idempotent on its own, but this table is the cheap outer guard: an
-- event id we have already processed is acknowledged and dropped.

create table if not exists stripe_events (
  id            text primary key,        -- Stripe's `evt_…` id
  type          text not null,
  processed_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- RLS — service-role only, matching every other table in this schema.
-- The backend connects with the service-role key and bypasses RLS; the browser
-- has no direct table access. `to service_role` is explicit so the policy does
-- not silently sit open to `anon`.
-- ---------------------------------------------------------------------------

alter table credit_ledger enable row level security;
alter table referrals enable row level security;
alter table stripe_events enable row level security;

drop policy if exists srv_credit_ledger on credit_ledger;
create policy srv_credit_ledger on credit_ledger
  for all to service_role using (true) with check (true);

drop policy if exists srv_referrals on referrals;
create policy srv_referrals on referrals
  for all to service_role using (true) with check (true);

drop policy if exists srv_stripe_events on stripe_events;
create policy srv_stripe_events on stripe_events
  for all to service_role using (true) with check (true);
