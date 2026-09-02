-- Corrected re-application of the Projects realtime private-channel
-- authorization policies. `20260813140000_projects_realtime_channel_auth.sql`
-- created `public.is_project_channel_member` / `public.is_individual_channel_member`
-- but never actually created the 4 policies on hosted: that migration also ran
-- `alter table realtime.messages enable row level security`, which requires
-- table ownership (or superuser) in Postgres. `realtime.messages` is owned by
-- `supabase_realtime_admin`, not `postgres` (the role the hosted deploy
-- pipeline connects as), so that `ALTER TABLE` raised `insufficient_privilege`
-- (SQLSTATE 42501). The migration wrapped everything in one `do $$ ...
-- exception when insufficient_privilege ... end $$` block, so that single
-- failure aborted the whole block -- including the four `create policy`
-- statements that follow it in the same block -- while the migration itself
-- still recorded as applied (the exception was caught, not re-raised).
--
-- Per Supabase's current documentation and permissions whitelist for
-- `realtime.messages`: `CREATE POLICY` on that table IS allowed for the
-- `postgres` role, and row level security is already enabled on it by
-- default. The `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` statement was
-- therefore both unnecessary and the specific statement that broke the
-- block. This migration re-applies only what's actually needed: no `ALTER
-- TABLE`, no `ENABLE ROW LEVEL SECURITY`, and no exception guard, so that if
-- policy creation ever fails again for a real reason, the migration fails
-- loudly instead of silently recording as applied with nothing done.
--
-- The two `SECURITY DEFINER` functions are re-affirmed here verbatim from the
-- original migration (harmless no-op if unchanged) so this migration is
-- self-contained and independently re-runnable.

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
-- only the per-user regex -- regex-disjoint by construction, so the
-- per-user policy cannot widen the group grant. SELECT gates *receiving*
-- broadcasts; INSERT gates a client *sending* (Presence track + typing,
-- added later) so both surfaces need zero new authz work when they land.
-- The backend publishes over the service-role REST path and is unaffected
-- by these policies.

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
