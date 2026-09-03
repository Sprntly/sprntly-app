"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"
import {
  CompanyStep,
  Connectors,
  PersonalizeStep,
  ReviewStep,
} from "../../../components/screens/onboarding"
import {
  ONBOARDING_STEP_SLUGS,
  type OnboardingStepSlug,
} from "../../../lib/onboarding/types"

/**
 * The ordered onboarding step list — the single source of truth pairing each
 * semantic slug with its screen component, in flow order.
 *
 *   1. company     → CompanyStep     (company name* + website, product name +
 *                                     website. Creates the company row and its
 *                                     default "Main workspace", and kicks the
 *                                     website analysis in the background.)
 *   2. connectors  → Connectors      (connect your tools — all optional)
 *   3. review      → ReviewStep      (accept the AI business context)
 *   4. personalize → PersonalizeStep (what to surface + brief delivery; hands
 *                                     off to /onboarding/define-metrics when
 *                                     analytics is connected AND metrics have
 *                                     been picked, otherwise completes
 *                                     onboarding itself)
 *
 * CUT FROM TEN TO FOUR on 2026-09-03 (via five). import-context, api-key,
 * product, workspace, metrics and invite were removed and their screen
 * components deleted; everything they collected is edited in Settings
 * instead — bulk teammate invite moved into Settings → Team & roles rather
 * than being dropped. The flow was asking for OKRs, success metrics, a
 * prioritization framework and a teammate list from someone who had not yet
 * seen the product. See lib/onboarding/types.ts for the full map of what moved
 * where.
 *
 * The slug order MUST stay aligned with ONBOARDING_STEP_SLUGS (the integer
 * `onboarding_step` is the 1-based index into both). Markers written by the
 * ten-step flow were rebased in migration 20260903160000.
 */
export const ONBOARDING_STEPS: ReadonlyArray<{
  slug: OnboardingStepSlug
  Component: React.ComponentType
}> = [
  { slug: "company", Component: CompanyStep },
  { slug: "connectors", Component: Connectors },
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
