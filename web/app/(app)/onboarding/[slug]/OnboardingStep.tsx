"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"
import {
  ApiKey,
  CompanyStep,
  Connectors,
  InviteStep,
  MetricsStep,
  PersonalizeStep,
  ProductStep,
  ReviewStep,
  WorkspaceStep,
} from "../../../components/screens/onboarding"
import {
  ONBOARDING_STEP_SLUGS,
  type OnboardingStepSlug,
} from "../../../lib/onboarding/types"

/**
 * The ordered onboarding step list — the single source of truth pairing each
 * semantic slug with its screen component, in flow order (2026-07-21
 * screenshot spec):
 *
 *   1. company     → CompanyStep     (name*; website/strategy, mission +
 *                                     portfolio + planning cycle behind
 *                                     "Add more")
 *   2. product     → ProductStep     (name* + surfaces*; monetization/users/
 *                                     competitors optional)
 *   3. metrics     → MetricsStep     (pick up to 5 metrics* + framework*)
 *   4. api-key     → ApiKey          (own Claude/Anthropic key — OPTIONAL,
 *                                     skippable; also in Settings → Admin)
 *   5. connectors  → Connectors      (connect your tools — all optional)
 *   6. workspace   → WorkspaceStep   (name* + scope*; strategy/roadmap; sizing
 *                                     + extras behind "Add more". Collapses the
 *                                     former team/strategy/decisions steps.)
 *   7. invite      → InviteStep      (email + job role + permission, bulk
 *                                     paste, CSV)
 *   8. review      → ReviewStep      (accept the AI business context)
 *   9. personalize → PersonalizeStep (what to surface + brief delivery; hands
 *                                     off to /onboarding/define-metrics when
 *                                     analytics is connected, otherwise
 *                                     completes onboarding itself)
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
  { slug: "workspace", Component: WorkspaceStep },
  { slug: "invite", Component: InviteStep },
  { slug: "review", Component: ReviewStep },
  { slug: "personalize", Component: PersonalizeStep },
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
