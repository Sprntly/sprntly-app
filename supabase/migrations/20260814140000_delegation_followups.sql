-- Delegation follow-up cadence — the durable per-task scheduler memory the
-- autonomous follow-up engine needs (spec §4: "persist in a table, not
-- scheduler memory"). One upserted row per `project_delegations.id`,
-- INPUTS/facts only, never a derived status column:
--   expected_completion — input: a human commitment / agent estimate
--   next_check_in       — input: the agent's next-ping instant; read by the
--                          (separately shipped) scheduler follow-up sweep
--   last_checked_in      — fact: when the loop last acted on this task
--   muted                — input: per-task "stop reminding me"
--   pending_done_since   — input: soft-done marker, set when an INFERRED
--                          completion confirmation was posted
--
-- Current *status* is still derived from `delegation_events`/
-- `v_delegation_status` and is NOT copied here (AD-P17) — this table only
-- ever holds cadence scheduling state.
--
-- No `workspace_id` column — deliberate, matches the Projects-era sibling
-- precedent set by `20260813130300_project_delegations.sql`: tenancy flows
-- `delegation_id -> project_delegations -> projects(company_id,
-- workspace_id)`, enforced at the route/reader layer, with a server-role
-- RLS policy instead of a per-row `workspace_id`.
create table if not exists delegation_followups (
  delegation_id       bigint primary key
                        references project_delegations(id) on delete cascade,
  expected_completion timestamptz,
  next_check_in       timestamptz,
  last_checked_in     timestamptz,
  muted               boolean not null default false,
  pending_done_since  timestamptz,
  updated_at          timestamptz not null default now()
);
create index if not exists idx_delegation_followups_due
  on delegation_followups(next_check_in) where muted = false;
alter table delegation_followups enable row level security;
drop policy if exists "srv_delegation_followups" on delegation_followups;
create policy "srv_delegation_followups" on delegation_followups for all using (true) with check (true);
