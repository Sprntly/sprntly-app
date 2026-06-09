"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"
import {
  BusinessInfo,
  Metrics,
  Connectors,
  Coworkers,
  FirstBrief,
} from "../../../components/screens/onboarding"
import {
  ONBOARDING_STEP_SLUGS,
  type OnboardingStepSlug,
} from "../../../lib/onboarding/types"

/**
 * The ordered onboarding step list — the single source of truth pairing each
 * semantic slug with its screen component, in flow order:
 *
 *   1. business-info → BusinessInfo  (company + product + website)
 *      [analyzing]   → Analyzing     (its own /onboarding/analyzing route, NOT here)
 *   2. metrics       → Metrics       (the metrics-tree page)
 *   3. connectors    → Connectors
 *   4. coworkers     → Coworkers
 *   5. first-brief   → FirstBrief
 *
 * The slug order MUST stay aligned with ONBOARDING_STEP_SLUGS (the integer
 * `onboarding_step` is the 1-based index into both). The analyzing interstitial
 * is deliberately absent — it is an unnumbered route with its own folder.
 */
export const ONBOARDING_STEPS: ReadonlyArray<{
  slug: OnboardingStepSlug
  Component: React.ComponentType
}> = [
  { slug: "business-info", Component: BusinessInfo },
  { slug: "metrics", Component: Metrics },
  { slug: "connectors", Component: Connectors },
  { slug: "coworkers", Component: Coworkers },
  { slug: "first-brief", Component: FirstBrief },
]

// Dev-time guard: the route map and the slug source of truth must agree in
// both membership and order (the index↔slug mapping for `onboarding_step`
// depends on it). A mismatch is a programming error, surfaced loudly here.
if (
  ONBOARDING_STEPS.length !== ONBOARDING_STEP_SLUGS.length ||
  ONBOARDING_STEPS.some((s, i) => s.slug !== ONBOARDING_STEP_SLUGS[i])
) {
  throw new Error(
    "ONBOARDING_STEPS is out of sync with ONBOARDING_STEP_SLUGS (order/membership).",
  )
}

const BY_SLUG: Record<string, React.ComponentType> = Object.fromEntries(
  ONBOARDING_STEPS.map((s) => [s.slug, s.Component]),
)

export function OnboardingStep({ slug }: { slug: string }) {
  const router = useRouter()
  const Screen = BY_SLUG[slug]

  // Unknown slug → bounce to the first step. Done in an effect (never as a
  // render side-effect) so navigation doesn't fire during render. This is the
  // client-side safety net that complements the server redirect in page.tsx
  // (which the static export can't run for non-prerendered params).
  useEffect(() => {
    if (!Screen) router.replace(`/onboarding/${ONBOARDING_STEP_SLUGS[0]}`)
  }, [Screen, router])

  if (!Screen) {
    return null
  }
  return <Screen />
}
