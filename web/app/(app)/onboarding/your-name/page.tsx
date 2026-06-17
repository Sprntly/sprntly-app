import { YourName } from "../../../components/screens/onboarding/YourName"

/**
 * Pre-onboarding profile gate — "What should we call you?".
 *
 * Deliberately a SEPARATE, UNNUMBERED route (not under `[slug]`), modelled on
 * the `analyzing` interstitial: it is not in ONBOARDING_STEP_SLUGS, is not a
 * back-navigable numbered step, and is excluded from the progress-dot count.
 *
 * `postLoginPath` sends a NEW user (no workspace) here only when their profile
 * first_name is empty — primarily Google sign-ups, whose Supabase profile lands
 * with no name. Email/password users (who type their name at sign-up) skip it
 * and go straight to `/onboarding/business-info` (step 1).
 */
export default function OnboardingYourNamePage() {
  return <YourName />
}
