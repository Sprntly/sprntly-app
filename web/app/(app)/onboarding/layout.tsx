"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../lib/auth"
import { OnboardingProvider } from "../../context/OnboardingContext"

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

export default function OnboardingLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <OnboardingProvider>
      <OnboardingEmailGuard>{children}</OnboardingEmailGuard>
    </OnboardingProvider>
  )
}
