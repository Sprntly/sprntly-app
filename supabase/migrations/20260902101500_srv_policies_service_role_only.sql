-- Restrict every `srv_*` policy to the service_role.
--
-- THE HOLE. `20260812170000_enable_rls_service_role_only.sql` turned RLS on
-- across the schema, which is why the Supabase advisor reports no
-- `rls_disabled_in_public` tables. But twelve of the policies it relies on were
-- written without a TO clause, and a policy with no TO applies to `{public}` —
-- every role, `anon` included. Combined with `FOR ALL USING (true) WITH CHECK
-- (true)` and the table-level grants Supabase gives `anon` by default, RLS was
-- enabled and doing nothing:
--
--   projects, project_members, project_artifacts, project_delegations,
--   project_memory_entries, project_memory_summary, project_chat_members,
--   prd_edit_proposals, delegation_events, delegation_followups,
--   delegation_followup_sends, conversation_read_cursors
--
-- Anyone holding the anon key could SELECT, INSERT, UPDATE and DELETE every row
-- in all twelve, across every tenant. The anon key is not a secret — it is
-- inlined into the static web bundle at build time and served publicly — so the
-- only thing standing between a stranger and this data was not knowing the
-- table names, which the repository publishes.
--
-- MATCHED BY NAME, NOT BY SHAPE. Selecting on "roles = {public} and qual is
-- true" would be more thorough and would also catch a policy somebody
-- deliberately makes public later — a genuinely open table would be silently
-- locked, and the breakage would look like an unrelated bug. The `srv_` prefix
-- is this repo's stated convention for "the backend reaches this with the
-- service-role key"; a policy meant to be reachable by a browser is never
-- named that way. So the prefix is the intent, and intent is what we act on.
--
-- Idempotent: after the ALTER the policy's roles are `{service_role}`, so a
-- second run matches nothing. Self-healing too — a future `srv_` policy that
-- forgets its TO clause is corrected the next time this runs, though it should
-- not need to be. New policies must carry `to service_role` themselves.
--
-- NOT A BEHAVIOUR CHANGE FOR THE APP. The backend connects with the
-- service-role key, so every path it uses is unaffected. Checked before
-- writing this: the frontend never queries these tables through supabase-js
-- (everything goes via the backend API), each affected table carries exactly
-- ONE policy so there is no auth-scoped policy left stranded, and the projects
-- realtime feature authorises through `is_project_channel_member` /
-- `is_individual_channel_member`, which are `security definer` and therefore
-- read `project_members` without consulting RLS at all.
do $$
declare
  r record;
begin
  for r in
    select tablename, policyname
    from pg_policies
    where schemaname = 'public'
      and policyname like 'srv\_%'
      and roles = '{public}'
  loop
    execute format(
      'alter policy %I on public.%I to service_role',
      r.policyname, r.tablename
    );
    raise notice 'restricted %.% to service_role', r.tablename, r.policyname;
  end loop;
end $$;
