import { redirect } from "next/navigation"
import { ONBOARDING_STEP_SLUGS, isOnboardingStepSlug } from "../../../lib/onboarding/types"
import { OnboardingStep } from "./OnboardingStep"

/**
 * Prerender one static page per numbered onboarding slug. The `analyzing`
 * interstitial is NOT listed here — it has its own (sibling) route folder.
 */
export function generateStaticParams() {
  return ONBOARDING_STEP_SLUGS.map((slug) => ({ slug }))
}

export default async function OnboardingStepPage({
  params,
}: {
  params: Promise<{ slug: string }>
}) {
  const { slug } = await params
  // Unknown slug → send the user to the first step rather than a hard 404, so a
  // stale/typo'd onboarding URL still lands them somewhere they can continue.
  if (!isOnboardingStepSlug(slug)) {
    redirect(`/onboarding/${ONBOARDING_STEP_SLUGS[0]}`)
  }
  return <OnboardingStep slug={slug} />
}
