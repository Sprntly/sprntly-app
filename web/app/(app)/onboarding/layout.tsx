"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../lib/auth"
import {
  OnboardingProvider,
  useOnboarding,
} from "../../context/OnboardingContext"

function OnboardingEmailGuard({ children }: { children: React.ReactNode }) {
  const auth = useAuth()
  const router = useRouter()

  useEffect(() => {
    if (auth.kind === "authed" && !auth.isEmailVerified()) {
      router.replace(
        `/verify-email?email=${encodeURIComponent(auth.user.email ?? "")}`,
      )
    }
  }, [auth, router])

  if (auth.kind !== "authed" || !auth.isEmailVerified()) {
    return <div className="ob-shell">Loading…</div>
  }

  return <>{children}</>
}

// Keeps a user who has already finished onboarding out of every `/onboarding/*`
// page. We wait for the workspace to load before deciding so a slow load never
// bounces a mid-onboarding user, and we redirect only from an effect so there
// is no update-during-render. Rendered inside `OnboardingProvider` so it can
// read the workspace from the onboarding context.
function OnboardingCompletedGuard({
  children,
}: {
  children: React.ReactNode
}) {
  const { loading, workspace } = useOnboarding()
  const router = useRouter()

  const isCompleted = !loading && workspace?.onboarding_completed_at != null

  useEffect(() => {
    if (isCompleted) {
      router.replace("/")
    }
  }, [isCompleted, router])

  // While the workspace is still loading we can't tell mid-onboarding from
  // completed, so show the shell rather than risk bouncing a mid-onboarding
  // user. Once completed, keep showing the shell until the redirect lands.
  if (loading || isCompleted) {
    return <div className="ob-shell">Loading…</div>
  }

  return <>{children}</>
}

export default function OnboardingLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <OnboardingProvider>
      <OnboardingEmailGuard>
        <OnboardingCompletedGuard>{children}</OnboardingCompletedGuard>
      </OnboardingEmailGuard>
    </OnboardingProvider>
  )
}
