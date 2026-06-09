import { OnboardingAnalyzing } from "../../../components/screens/onboarding/OnboardingAnalyzing"

/**
 * Blocking "Gathering information about your business" interstitial.
 *
 * Deliberately a SEPARATE, unnumbered route (not under `[step]`), so it is not
 * a back-navigable numbered step and is excluded from the progress-dot count.
 * It sits between the Company page (step 1) and the Metrics page (step 2).
 */
export default function OnboardingAnalyzingPage() {
  return <OnboardingAnalyzing />
}
