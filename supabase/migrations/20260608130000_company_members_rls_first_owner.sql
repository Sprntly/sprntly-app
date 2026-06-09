-- Close a tenant-seizure hole in the company_members INSERT RLS policy.
--
-- The previous policy (company_members_insert_self) only checked that a
-- signed-in user inserted THEMSELVES:
--
--     with check (user_id = auth.uid())
--
-- It did NOT constrain WHICH company the row pointed at, so any authenticated
-- user could insert {company_id: <any victim company>, user_id: self,
-- role: 'owner'} directly from the browser Supabase client and seize an
-- existing org. Only a non-security unique index (company_members_one_company_
-- per_user) accidentally limited the blast radius.
--
-- Founder model: anyone can sign up and create their own org, but joining an
-- EXISTING org requires an admin invite (Slack/Cursor-style). Same company
-- name from a different user → a different company row / UID; "E can only join
-- A's org if A invites E."
--
-- The only legitimate CLIENT-SIDE membership insert is onboarding making the
-- creator the FIRST owner of a company THEY just created. That flow
-- (web/app/lib/onboarding/store.ts createWorkspace) inserts the companies row
-- (created_by = self, enforced by companies_insert_authenticated's
-- `with check (created_by = auth.uid())`) BEFORE the company_members row, so at
-- member-insert time the company already exists with created_by = self and has
-- 0 members → the new policy passes.
--
-- A malicious user E inserting into victim company A fails the created_by check
-- (A.created_by != E). The `not exists (... any member ...)` clause is
-- belt-and-suspenders: once a company has its first owner, all further members
-- are added only via the SERVICE-ROLE backend invite-accept path
-- (backend/app/db/team.py accept_invite_for_user, which uses require_client()
-- and therefore BYPASSES RLS — so legitimate invite acceptance is unaffected
-- by this policy).

drop policy if exists "company_members_insert_self" on company_members;

create policy "company_members_insert_first_owner"
    on company_members for insert to authenticated
    with check (
        user_id = auth.uid()
        and exists (
            select 1 from companies c
            where c.id = company_id
              and c.created_by = auth.uid()
        )
        and not exists (
            select 1 from company_members m
            where m.company_id = company_members.company_id
        )
    );
