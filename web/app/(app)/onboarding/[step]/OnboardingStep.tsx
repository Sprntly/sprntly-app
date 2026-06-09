"use client"

import {
  Onboarding1,
  Onboarding2,
  Onboarding3,
  Onboarding4,
  Onboarding5,
  Onboarding6,
  Onboarding7,
} from "../../../components/screens/onboarding"

// New flow order: Company → [analyzing interstitial] → Metrics → Optimizing →
// Business context → Connectors → Coworkers → First brief. The metrics page
// (Onboarding4) moved to route 2; the optimizing (Onboarding2) and business-
// context (Onboarding3) pages shifted to routes 3 and 4. The interstitial is an
// unnumbered route (/onboarding/analyzing) and is not in this map.
const STEPS: Record<string, React.ComponentType> = {
  "1": Onboarding1, // Company
  "2": Onboarding4, // Metrics (success metrics)
  "3": Onboarding2, // Optimizing-for (strategic context)
  "4": Onboarding3, // Business context
  "5": Onboarding5, // Connectors
  "6": Onboarding6, // Coworkers
  "7": Onboarding7, // First brief
}

export function OnboardingStep({ step }: { step: string }) {
  const Screen = STEPS[step]
  if (!Screen) {
    return null
  }
  return <Screen />
}
