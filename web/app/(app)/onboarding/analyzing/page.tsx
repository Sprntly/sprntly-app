import { Analyzing } from "../../../components/screens/onboarding/Analyzing"

/**
 * Blocking "Gathering information about your business" interstitial.
 *
 * Deliberately a SEPARATE, unnumbered route (not under `[slug]`), so it is not
 * a back-navigable numbered step and is excluded from the progress-dot count.
 * It sits between the business-info page (step 1) and the metrics page (step 2).
 */
export default function OnboardingAnalyzingPage() {
  return <Analyzing />
}
