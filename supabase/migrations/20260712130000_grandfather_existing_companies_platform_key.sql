-- Rollout safety: grandfather companies that finished onboarding BEFORE the
-- per-company Claude key feature. They never saw the onboarding key step, so
-- they have no key yet — without this they'd immediately fail every Claude call
-- (onboarding complete + no key + use_platform_key=false => CompanyKeyRequiredError).
--
-- Set use_platform_key=true for them so they keep running on the platform key
-- until they add their own in Settings → Admin (the resolver still prefers a
-- company key the moment one is set). NEW companies onboard through the required
-- key step and are unaffected by this backfill.
UPDATE companies
SET use_platform_key = true
WHERE onboarding_completed_at IS NOT NULL
  AND llm_api_key_encrypted IS NULL;
