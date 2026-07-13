-- Platform-key fallback flag (DB-only; no UI).
--
-- Policy: a company MUST use its own Claude key (collected during onboarding).
-- If a company has no key and this flag is false, Claude calls fail — EXCEPT
-- while the company is still onboarding (companies.onboarding_completed_at IS
-- NULL), during which platform fallback is always allowed.
--
-- Sprntly sets this flag to true directly in the DB for specific contracted
-- customers who should keep running on the platform key after onboarding.
ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS use_platform_key boolean NOT NULL DEFAULT false;
