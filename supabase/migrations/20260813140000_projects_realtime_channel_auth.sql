-- Realtime channel-join authorization: the ONLY new authz surface added by
-- the realtime transport (AD-P23). Gates who may join two Broadcast topic
-- shapes on Supabase Realtime's private `realtime.messages` table — the
-- shared group channel `project:{project_id}` and the private per-user
-- channel `project:{project_id}:user:{user_id}`. No product table's RLS is
-- touched here; `conversations`/`conversation_turns`/`project_members`/etc.
-- keep their existing `srv_*` server-role `using(true)` policies untouched
-- (§2.3), and no Projects table is added to the `supabase_realtime` CDC
-- publication (Broadcast carries backend-authored payloads only — adding
-- tables to the publication is the rejected Option-A shape).
--
-- Both functions mirror `is_project_member` (`backend/app/db/projects.py`)
-- in SQL: a `project_members` row exists for `(project_id, auth.uid())`.
-- SECURITY DEFINER so each can read `public.project_members` regardless of
-- the calling role's own grants; STABLE; never raises (a malformed or
-- unexpected topic returns false rather than erroring the policy check).

create or replace function public.is_project_channel_member(topic text)
returns boolean language plpgsql security definer set search_path = public stable as $$
declare pid bigint;
begin
  if topic is null or topic !~ '^project:[0-9]+$' then
    return false;                      -- not a group project topic -> don't claim it
  end if;
  pid := split_part(topic, ':', 2)::bigint;
  return exists (select 1 from public.project_members pm
                 where pm.project_id = pid and pm.user_id = auth.uid());
exception when others then return false;
end; $$;

-- Per-user channel: you ARE that user AND a member. Membership alone is NOT
-- enough (every group member is a project member, but only the owner may
-- read their own individual thread).
create or replace function public.is_individual_channel_member(topic text)
returns boolean language plpgsql security definer set search_path = public stable as $$
declare pid bigint; uid uuid;
begin
  if topic is null or topic !~ '^project:[0-9]+:user:[0-9a-fA-F-]{36}$' then
    return false;
  end if;
  pid := split_part(topic, ':', 2)::bigint;
  uid := split_part(topic, ':', 4)::uuid;
  if uid <> auth.uid() then return false; end if;   -- only your OWN thread channel
  return exists (select 1 from public.project_members pm
                 where pm.project_id = pid and pm.user_id = auth.uid());
exception when others then return false;
end; $$;

-- Four permissive policies on `realtime.messages` (Supabase's private-
-- channel authorization table). Permissive policies OR-compose, so the
-- group and per-user policies coexist without conflict: a `project:{id}`
-- topic matches only the group regex, `project:{id}:user:{uid}` matches
-- only the per-user regex — regex-disjoint by construction, so the
-- per-user policy cannot widen the group grant. SELECT gates *receiving*
-- broadcasts; INSERT gates a client *sending* (Presence track + typing,
-- added later) so both surfaces need zero new authz work when they land.
-- The backend publishes over the service-role REST path and is unaffected
-- by these policies.
--
-- NOTE (local-rig / deploy-pipeline landmark, flagged for the planner):
-- `realtime.messages` is owned by `supabase_realtime_admin` on this stack,
-- not by `postgres` — the default role the CLI's local DB_URL and
-- `SUPABASE_DB_URL` connect as. CREATE POLICY / ALTER TABLE ... ENABLE ROW
-- LEVEL SECURITY require table ownership (or superuser) in Postgres; a
-- plain GRANT does not suffice. Applying this migration therefore needs a
-- connection role with superuser (locally: `supabase_admin`) or explicit
-- ownership of `realtime.messages` — verified empirically against this
-- local rig; NOT fixable from inside a migration file run as `postgres`
-- (granting `supabase_realtime_admin` membership is itself
-- superuser-only). Whether the deploy pipeline's `SUPABASE_DB_URL` role has
-- the needed privilege in staging/prod is unverified and out of this
-- ticket's scope — flag to `sprntly-infra` before promotion.
alter table realtime.messages enable row level security;  -- no-op if already enabled

drop policy if exists "project_group_channel_receive" on realtime.messages;
create policy "project_group_channel_receive" on realtime.messages
  for select to authenticated using (public.is_project_channel_member(realtime.topic()));
drop policy if exists "project_group_channel_send" on realtime.messages;
create policy "project_group_channel_send" on realtime.messages
  for insert to authenticated with check (public.is_project_channel_member(realtime.topic()));

drop policy if exists "project_individual_channel_receive" on realtime.messages;
create policy "project_individual_channel_receive" on realtime.messages
  for select to authenticated using (public.is_individual_channel_member(realtime.topic()));
drop policy if exists "project_individual_channel_send" on realtime.messages;
create policy "project_individual_channel_send" on realtime.messages
  for insert to authenticated with check (public.is_individual_channel_member(realtime.topic()));
