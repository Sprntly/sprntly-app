-- Artifact share-grant primitive: a persistent, non-expiring share token
-- minted per artifact (currently only 'prd') that lets an unauthenticated
-- visitor see minimal metadata, lets a domain-matched signup or a
-- same-company-different-workspace member read the artifact as a read-only
-- guest, and lets that guest join the owning company/workspace with the
-- join attributed to the sharer.
--
-- Sibling tables only — no ALTER on companies / workspaces / company_members
-- / prds. `owner_company_id` / `owner_workspace_id` are plain TEXT (no FK),
-- matching this codebase's existing tenant-id convention on sibling tables
-- that fabricate ids in tests (see workspaces table comment in
-- supabase/migrations/20260606120000_workspaces_and_connection_scope.sql).
--
-- Idempotent (`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`),
-- matching every existing migration in this directory.

CREATE TABLE IF NOT EXISTS artifact_shares (
  id BIGSERIAL PRIMARY KEY,
  token TEXT NOT NULL UNIQUE,                 -- opaque uuid4, the URL-facing secret (not derivable from artifact_id)
  artifact_type TEXT NOT NULL DEFAULT 'prd',  -- only 'prd' populated by this ticket; extensible for evidence/ticket shares later
  artifact_id BIGINT NOT NULL,
  owner_company_id TEXT NOT NULL,
  owner_workspace_id TEXT NOT NULL,
  created_by_user_id TEXT NOT NULL,           -- the sharer, for attribution copy ("joined via {sharer}'s link")
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ                      -- schema-shaped for FUTURE revocability (deferred).
                                               -- No revoke endpoint in this ticket; column exists so a later ticket
                                               -- doesn't need a migration. NEVER set by any code path in this ticket.
);
CREATE INDEX IF NOT EXISTS artifact_shares_artifact_idx
  ON artifact_shares (artifact_type, artifact_id);

CREATE TABLE IF NOT EXISTS artifact_share_joins (
  id BIGSERIAL PRIMARY KEY,
  share_id BIGINT NOT NULL REFERENCES artifact_shares(id),
  joined_user_id TEXT NOT NULL,
  joined_company_id TEXT NOT NULL,
  joined_workspace_id TEXT NOT NULL,
  joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(share_id, joined_user_id)            -- idempotent: re-POSTing /join for an already-joined user is a no-op-success
);
