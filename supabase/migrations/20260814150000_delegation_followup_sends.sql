-- Idempotent per-company send-ledger for the autonomous task follow-up
-- scheduler sweep (app/delegation_followup.py). One row per instant a
-- channel actually fired (or was best-effort attempted) for a delegation,
-- so a racing/duplicate sweep tick is a no-op rather than a double DM.
--
-- Citation split (deliberate — the two halves of this table follow two
-- different precedents):
--   - COLUMN set + the `unique(...)` idempotency guard + the
--     `status='skipped'`-still-counts-as-delivered rule are copied from
--     `invite_reminder_sends` (20260720120000_invite_reminder_sends.sql).
--   - The RLS SHAPE is the `project_delegations` / `delegation_events`
--     pattern instead: RLS enabled WITH an explicit permissive `srv_*`
--     `for all using (true) with check (true)` service-role policy —
--     NOT `invite_reminder_sends`'s RLS-on-with-zero-policies (deny-all).
--     Both shapes are functionally equivalent for this table's only
--     writer (the service-role scheduler bypasses RLS either way); the
--     citation matches what each migration's SQL actually does.
--
-- No `workspace_id` column — the same Projects-era divergence as
-- `project_delegations` / `delegation_followups`: tenancy flows
-- `delegation_id -> project_delegations -> projects(company_id,
-- workspace_id)`. `company_id` is carried directly (rather than requiring
-- a join on every cap/isolation read) and is resolved once per task per
-- cycle via `db.projects.get_project(project_id)["company_id"]`.
create table if not exists delegation_followup_sends (
  id               uuid primary key default gen_random_uuid(),
  delegation_id    bigint not null references project_delegations(id) on delete cascade,
  company_id       uuid   not null,
  assignee_user_id uuid   not null,
  -- The ISO instant of the `next_check_in` being serviced — the
  -- idempotency guard: two sweeps racing the same due task compute the
  -- same check_key, and the UNIQUE below turns the second insert into a
  -- caught conflict rather than a second send.
  check_key        text   not null,
  channel           text  not null,           -- 'dm' | 'email' | 'escalation'
  status            text  not null default 'sent',   -- 'sent' | 'skipped'
  sent_at           timestamptz not null default now(),
  unique (delegation_id, check_key, channel)
);
create index if not exists idx_delegation_followup_sends_person
  on delegation_followup_sends (assignee_user_id, sent_at desc);
create index if not exists idx_delegation_followup_sends_deleg
  on delegation_followup_sends (delegation_id);

alter table delegation_followup_sends enable row level security;
drop policy if exists "srv_delegation_followup_sends" on delegation_followup_sends;
create policy "srv_delegation_followup_sends" on delegation_followup_sends for all using (true) with check (true);
