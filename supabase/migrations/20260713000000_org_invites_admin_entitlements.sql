-- Staff admin panel: organization invites + per-company entitlements.
--
-- companies.seat_limit        — max members (company_members + pending
--                               workspace_invites); NULL = unlimited.
-- companies.prototype_enabled — per-company gate for the design-agent
--                               (prototype) feature. The global
--                               DESIGN_AGENT_ENABLED env var remains the
--                               master switch; this column gates per tenant
--                               underneath it. Existing companies are
--                               grandfathered to TRUE (they have the feature
--                               today); companies created after this default
--                               to FALSE and are enabled via the staff panel
--                               or their org invite.
ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS seat_limit integer;

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS prototype_enabled boolean NOT NULL DEFAULT false;

UPDATE companies SET prototype_enabled = true;

-- Organization invites, created by Sprntly staff from the admin panel. The
-- entitlement columns are a snapshot of the deal terms; they are applied to
-- the invitee's company when they finish onboarding (claim), and can be
-- edited later from the staff panel directly on the companies row.
CREATE TABLE IF NOT EXISTS org_invites (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email             text NOT NULL,
    company_name      text NOT NULL,
    invited_by        uuid,
    seat_limit        integer,
    prototype_enabled boolean NOT NULL DEFAULT false,
    use_platform_key  boolean NOT NULL DEFAULT false,
    feature_flags     jsonb NOT NULL DEFAULT '{}'::jsonb,
    status            text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'accepted', 'revoked')),
    company_id        uuid REFERENCES companies(id) ON DELETE SET NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    accepted_at       timestamptz
);

-- One live invite per email address (revoked/accepted rows don't block).
CREATE UNIQUE INDEX IF NOT EXISTS org_invites_pending_email_uq
    ON org_invites (lower(email))
    WHERE status = 'pending';

-- Service-role only: RLS on with no policies means the anon/authenticated
-- clients can't touch it — staff routes are the sole access path.
ALTER TABLE org_invites ENABLE ROW LEVEL SECURITY;
