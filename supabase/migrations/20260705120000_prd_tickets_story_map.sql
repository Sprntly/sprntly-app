-- Story map for a PRD's tickets: the Jeff Patton backbone (user activities) with
-- the same generated tickets sliced into releases. Built only when the sizing gate
-- fires (see app.stories.generate); null for flat/unsized ticket sets. Nullable +
-- idempotent so it is safe under migrate-on-deploy and older rows stay valid.
alter table prd_tickets add column if not exists story_map jsonb;
