import { notFound } from "next/navigation"
import { OnboardingStep } from "./OnboardingStep"

export function generateStaticParams() {
  return ["1", "2", "3", "4", "5", "6", "7", "8"].map((step) => ({ step }))
}

export default async function OnboardingStepPage({
  params,
}: {
  params: Promise<{ step: string }>
}) {
  const { step } = await params
  if (!["1", "2", "3", "4", "5", "6", "7", "8"].includes(step)) {
    notFound()
  }
  return <OnboardingStep step={step} />
}
