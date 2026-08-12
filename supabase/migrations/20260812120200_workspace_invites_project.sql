-- Project association for a workspace invite (AD-TNM3, Extension B).
--
-- A tag on a not-yet-in-workspace teammate (t_company) or a brand-new
-- invitee at the company domain (t_newuser) creates a workspace_invites row
-- that carries the project it was raised from, so the accepter lands in
-- project_members automatically at accept time. NULL project_id = a plain
-- company/workspace invite (every existing invite, and every WJ team-invite),
-- which auto-adds nothing.
--
-- projects.id is `bigint generated always as identity` (see
-- 20260811120000_projects.sql) — the FK type matches, NOT uuid. RLS is
-- already enabled on workspace_invites (20260525150000_onboarding_workspace.sql);
-- a nullable additive column needs no new policy. Idempotent add — safe to
-- double-apply.
alter table workspace_invites
    add column if not exists project_id bigint references projects (id) on delete cascade;
