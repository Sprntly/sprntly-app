-- Add the `viewer` role to the team enums.
--
-- Mockup (sprntly-pages/15-settings.html § Team & roles) introduces a
-- 4th role: Viewer — read-only access, can comment but not edit.
-- Backend validators (app/routes/team.py: InviteIn / MemberRolePatch)
-- already accept 'viewer' as of the team-settings-style slice; this
-- migration brings the DB CHECK constraints into alignment so the
-- inserts/updates don't fail at the database layer.
--
-- Two tables affected:
--   - company_members.role  ('owner' | 'admin' | 'member' → + 'viewer')
--   - workspace_invites.role ('admin' | 'member' → + 'viewer')
--     (owner is still reserved for the creator path and intentionally
--      absent from workspace_invites.)
--
-- Apply note: ALTER ... DROP CONSTRAINT ... ADD CONSTRAINT is the safe
-- shape for a Postgres CHECK swap. Idempotent re-run via `if exists`.
-- No data backfill required — the new value is additive; all existing
-- rows already satisfy the new (wider) constraint.

alter table company_members
    drop constraint if exists company_members_role_check;

alter table company_members
    add constraint company_members_role_check
        check (role in ('owner', 'admin', 'member', 'viewer'));

alter table workspace_invites
    drop constraint if exists workspace_invites_role_check;

alter table workspace_invites
    add constraint workspace_invites_role_check
        check (role in ('admin', 'member', 'viewer'));
