-- Subscription payments — one row per invoice actually paid.
--
-- `subscription_events` (20260829120000) records PLAN CHANGES: what the plan
-- was and what it became. That answers "when did I move to this tier", which is
-- not the question a customer opens billing to ask. They ask "what have I been
-- charged, when, and for what" — and nothing in this database could answer it.
-- `credit_ledger` deals only in credits; `companies` holds one current period.
--
-- So this is the money record: plan, amount in CURRENCY, the period it covers,
-- and the links to Stripe's own hosted invoice and PDF. A generated invoice
-- document later reads from here rather than re-deriving amounts from prices
-- that may since have changed.
--
-- Written from `invoice.paid`, which is the only event that means money moved.

create table if not exists subscription_invoices (
  id                     bigserial primary key,
  company_id             uuid not null references companies(id) on delete cascade,

  -- IDEMPOTENCY. Stripe delivers at least once and retries for days, so the
  -- invoice id — not the event id — is what stops one payment appearing twice.
  stripe_invoice_id      text not null unique,
  stripe_subscription_id text,

  -- The plan as WE resolved it at the time of payment. Kept rather than joined
  -- so a later plan change cannot rewrite what an old invoice says it was for.
  plan                   text,

  -- MINOR UNITS, exactly as Stripe reports them. Storing dollars as a float is
  -- how money quietly goes missing; the display layer divides.
  amount_paid_cents      integer not null default 0,
  currency               text not null default 'usd',
  status                 text,

  -- The service period this invoice covers, which is what a reader actually
  -- wants beside the amount — not the date we happened to receive the webhook.
  period_start           timestamptz,
  period_end             timestamptz,
  paid_at                timestamptz,

  -- Stripe already renders a PDF and hosts a receipt page. Keeping the links
  -- means "download invoice" needs no document generator of our own.
  invoice_number         text,
  hosted_invoice_url     text,
  invoice_pdf_url        text,

  created_at             timestamptz not null default now()
);

-- READ PATTERN: one company's payments, newest first. Leads with company_id so
-- it also serves the delete cascade — an unindexed FK turns every parent delete
-- into a full scan of this table (see kg_signal.source_id, 2026-08-28).
create index if not exists subscription_invoices_company_time_idx
  on subscription_invoices (company_id, paid_at desc);

-- ---------------------------------------------------------------------------
-- RLS — service-role only. `to service_role` is explicit so the policy does not
-- silently sit open to `anon`.
-- ---------------------------------------------------------------------------

alter table subscription_invoices enable row level security;

drop policy if exists srv_subscription_invoices on subscription_invoices;
create policy srv_subscription_invoices on subscription_invoices
  for all to service_role using (true) with check (true);
