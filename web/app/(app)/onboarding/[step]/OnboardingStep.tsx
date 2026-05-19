"use client"

import {
  Onboarding1,
  Onboarding2,
  Onboarding3,
  Onboarding4,
  Onboarding5,
  Onboarding6,
  Onboarding7,
  Onboarding8,
} from "../../../components/screens/onboarding"

const STEPS: Record<string, React.ComponentType> = {
  "1": Onboarding1,
  "2": Onboarding2,
  "3": Onboarding3,
  "4": Onboarding4,
  "5": Onboarding5,
  "6": Onboarding6,
  "7": Onboarding7,
  "8": Onboarding8,
}

export function OnboardingStep({ step }: { step: string }) {
  const Screen = STEPS[step]
  if (!Screen) {
    return null
  }
  return <Screen />
}
