"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"
import {
  ApiKey,
  CompanyStep,
  Connectors,
  MetricsStep,
  ProductStep,
  Strategy,
  TeamStep,
  WorkspaceStep,
} from "../../../components/screens/onboarding"
import {
  ONBOARDING_STEP_SLUGS,
  type OnboardingStepSlug,
} from "../../../lib/onboarding/types"

/**
 * The ordered onboarding step list — the single source of truth pairing each
 * semantic slug with its screen component, in flow order (2026-07 registration
 * spec: Company / Product / Team sections; starred fields mandatory for
 * company accounts only):
 *
 *   1. company     → CompanyStep    (name* + website*; mission/strategy optional)
 *   2. product     → ProductStep    (URL* + surfaces*; personas/monetization optional)
 *   3. metrics     → MetricsStep    (pick-3 success metrics)
 *   4. api-key     → ApiKey         (company Claude key, BEFORE connectors)
 *   5. connectors  → Connectors     (connect your tools*)
 *   6. team        → TeamStep       (scope* + framework*; invites/brief day optional)
 *   7. strategy    → Strategy       (docs + roadmap upload)
 *   8. workspace   → WorkspaceStep  (name your workspace — completes onboarding)
 *
 * The slug order MUST stay aligned with ONBOARDING_STEP_SLUGS (the integer
 * `onboarding_step` is the 1-based index into both).
 */
export const ONBOARDING_STEPS: ReadonlyArray<{
  slug: OnboardingStepSlug
  Component: React.ComponentType
}> = [
  { slug: "company", Component: CompanyStep },
  { slug: "product", Component: ProductStep },
  { slug: "metrics", Component: MetricsStep },
  { slug: "api-key", Component: ApiKey },
  { slug: "connectors", Component: Connectors },
  { slug: "team", Component: TeamStep },
  { slug: "strategy", Component: Strategy },
  { slug: "workspace", Component: WorkspaceStep },
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
