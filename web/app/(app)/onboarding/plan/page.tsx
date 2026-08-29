import { PlanStep } from "../../../components/screens/onboarding/PlanStep"

/**
 * The onboarding payment gate — "choose a plan".
 *
 * Deliberately a SEPARATE, UNNUMBERED route (not under `[slug]`), the same
 * shape `your-name` uses: it is not in ONBOARDING_STEP_SLUGS, is not a
 * back-navigable numbered step, and is excluded from the progress-dot count.
 * `onboarding_step` is a persisted 1-based index into that array, so a numbered
 * step inserted at position two would shift every company already mid-flow.
 *
 * `postLoginPath` sends anyone here whose company has no live subscription and
 * has not finished onboarding, which is what makes an abandoned checkout
 * resumable: they come back to the gate, not to a half-built workspace.
 */
export default function OnboardingPlanPage() {
  return <PlanStep />
}
