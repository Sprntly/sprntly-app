import { DefineMetrics } from "../../../components/screens/onboarding/DefineMetrics"

/**
 * Post-wizard define-metrics sub-flow — "Define <metric>." + review.
 *
 * Deliberately a SEPARATE, UNNUMBERED route (not under `[slug]`), modelled on
 * the `your-name` gate: it is not in ONBOARDING_STEP_SLUGS, renders no
 * progress dots, and is excluded from the step-index math. The closing review
 * step (step 9) hands off here; confirming the definitions COMPLETES
 * onboarding and kicks the first brief.
 */
export default function OnboardingDefineMetricsPage() {
  return <DefineMetrics />
}
