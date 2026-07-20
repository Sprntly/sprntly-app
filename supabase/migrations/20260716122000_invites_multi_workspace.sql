-- Invites target one or more workspaces.
--
-- BUG FIX first: routes/team.py's InviteIn has accepted 'viewer' since
-- 20260607000002_role_viewer.sql, but this table's CHECK still rejected it —
-- a viewer invite violated the constraint at insert time.

alter table workspace_invites
    drop constraint if exists workspace_invites_role_check;
alter table workspace_invites
    add constraint workspace_invites_role_check
        check (role in ('admin', 'member', 'viewer'));

-- uuid[] over a junction table: invites are short-lived rows deleted on
-- accept, so FK integrity buys nothing — the accept path validates each id
-- still exists and belongs to the invite's company, falling back to the
-- default workspace when the filtered set is empty. Empty array means "let
-- accept resolve the default workspace at accept time".
alter table workspace_invites
    add column if not exists workspace_ids uuid[] not null default '{}';
