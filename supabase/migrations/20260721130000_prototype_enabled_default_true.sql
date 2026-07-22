-- Prototype generation is a core module available to every organization by
-- default. The admin-panel entitlements migration
-- (20260713000000_org_invites_admin_entitlements.sql) added
-- companies.prototype_enabled DEFAULT false and only backfilled rows that
-- existed at migration time — so every company created since then (self-serve
-- signup omits the column; org invites default the flag off) landed on false
-- and got a silent 404 from /v1/design-agent/generate. Flip the default to
-- true and backfill, keeping the staff panel as a per-company opt-OUT switch.

alter table companies
    alter column prototype_enabled set default true;

update companies set prototype_enabled = true where prototype_enabled = false;

-- Same flip for pending org invites: a staff invite created without touching
-- the Prototype toggle should grant it. Settled (accepted/revoked) rows are
-- history — leave them as recorded.
alter table org_invites
    alter column prototype_enabled set default true;

update org_invites
    set prototype_enabled = true
    where status = 'pending' and prototype_enabled = false;
