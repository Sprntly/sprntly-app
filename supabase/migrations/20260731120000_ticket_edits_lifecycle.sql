-- Per-ticket lifecycle: whether a ticket is live, held back from the tracker,
-- or deleted.
--
--   active    the default — syncs to the bound tracker as it always has
--   excluded  stays in Sprntly, is NEVER pushed; if it was already pushed,
--             the tracker copy is removed (the point of excluding is that the
--             ticket does not exist in the PM tool)
--   deleted   gone from Sprntly too, and removed from the tracker
--
-- It lives on ticket_edits rather than in prd_tickets.stories because stories
-- is REGENERATED wholesale from the PRD — a flag written there would be erased
-- by the next regeneration, silently re-pushing a ticket the user deleted.
-- ticket_edits is the per-ticket override row every surface already reads, so
-- the state survives and the sync engine sees it with no extra query.
--
-- Nullable-with-default rather than a rewrite: existing rows read 'active',
-- which is exactly the behavior they have today. Additive + idempotent → safe
-- under migrate-on-deploy.
alter table ticket_edits
    add column if not exists lifecycle text not null default 'active';

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'ticket_edits_lifecycle_chk'
    ) then
        alter table ticket_edits
            add constraint ticket_edits_lifecycle_chk
            check (lifecycle in ('active', 'excluded', 'deleted'));
    end if;
end $$;

-- The sync pass and every ticket list filter on this per (company, prd), and
-- the non-active rows are the rare ones — a partial index keeps that lookup
-- cheap without carrying the overwhelmingly-'active' majority.
create index if not exists idx_ticket_edits_lifecycle
    on ticket_edits (company_id, ticket_key)
    where lifecycle <> 'active';
