-- Non-guessable external identifier for a PRD. `prds.id` is a sequential
-- BIGSERIAL — fine as an internal key, but exposed in a URL (internal
-- `?prd=` deep-link, or the bare-link guest-access primitive) it lets anyone
-- who can pass the company-domain gate blind-enumerate every PRD the tenant
-- has ever generated. `public_id` is the opaque, unguessable identifier used
-- in every URL/external-facing API path going forward; `prds.id` stays the
-- real primary key for every internal join/FK — nothing else changes.
--
-- `DEFAULT gen_random_uuid()` on a NOT NULL column backfills every existing
-- row as part of this single ALTER TABLE — no separate backfill migration
-- needed (gen_random_uuid() is volatile, so Postgres can't use the
-- constant-default fast path, but it still fills every row inline here).
--
-- Idempotent guards, matching every existing migration in this directory.

ALTER TABLE prds ADD COLUMN IF NOT EXISTS public_id UUID NOT NULL DEFAULT gen_random_uuid();

CREATE UNIQUE INDEX IF NOT EXISTS prds_public_id_idx ON prds (public_id);
