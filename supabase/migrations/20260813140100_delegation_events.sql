-- The accountability ledger's spine: an append-only log of lifecycle
-- events for a delegation (AD-P26 — inputs/facts only, no cached status
-- column) plus a derive-at-read view that folds the latest event per
-- delegation into a current status. `project_delegations` itself is
-- unchanged (AD-P17 held) — this is a net-new sibling, not a schema
-- change to the fact table.
--
-- No `workspace_id` column, same divergence as `project_delegations`
-- (see `20260813130300_project_delegations.sql`): tenancy is scoped via
-- `delegation_id -> project_delegations -> projects(company_id,
-- workspace_id)` and enforced at the route layer, with a server-role
-- RLS policy here rather than a per-row `workspace_id` column.
create table if not exists delegation_events (
  id             bigint generated always as identity primary key,
  delegation_id  bigint not null references project_delegations(id) on delete cascade,
  event          text   not null check (event in
                   ('assigned','accepted','in_progress','completed','declined','cancelled','reopened')),
  actor_user_id  uuid   not null references auth.users(id) on delete cascade,
  note           text,                                   -- optional decline reason / comment (an input, not derived)
  created_at     timestamptz not null default now()
);
-- The one index the derivation needs: latest event per delegation is
-- `... order by id desc limit 1` per delegation_id (AD-P26 derive-at-read cost model).
create index if not exists idx_delegation_events_delegation on delegation_events(delegation_id, id desc);

alter table delegation_events enable row level security;
drop policy if exists "srv_delegation_events" on delegation_events;
create policy "srv_delegation_events" on delegation_events for all using (true) with check (true);

-- Derive-at-read current status (NOT materialized, AD-P26): the latest
-- event per delegation, left-joined so a delegation with zero events
-- still derives a status rather than nothing at all (belt-and-braces
-- zero-events fallback — the one flagged design choice made here).
create or replace view v_delegation_status as
select
  d.id                                          as delegation_id,
  d.project_id,
  d.assigner_user_id,
  d.assignee_user_id,
  d.task_summary,
  d.delivered_conversation_id,
  d.delivered_turn_id,
  d.created_at                                  as delegated_at,
  coalesce(e.event, 'assigned')                 as status,
  coalesce(e.created_at, d.created_at)          as status_at,
  coalesce(e.actor_user_id, d.assigner_user_id) as status_actor
from project_delegations d
left join lateral (
  select event, created_at, actor_user_id
  from delegation_events ev
  where ev.delegation_id = d.id
  order by ev.id desc
  limit 1
) e on true;
