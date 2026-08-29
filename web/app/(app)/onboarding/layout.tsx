"use client"

import { useEffect } from "react"
import { usePathname, useRouter } from "next/navigation"
import { useAuth } from "../../lib/auth"
import {
  OnboardingProvider,
  useOnboarding,
} from "../../context/OnboardingContext"
import { companyHasPaid } from "../../lib/billingAccess"
import { ONBOARDING_PLAN_PATH } from "../../lib/billingPlans"

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

// THE PAYMENT GATE, on every numbered step rather than only on the way in.
//
// `OnboardingRequiredGuard` deliberately defers on `/onboarding/*` — it must,
// or it would fight step navigation, including going back a step. That left
// the gate covering only ENTRY to the app: once a browser was on any step,
// nothing re-checked payment, so typing `/onboarding/import-context` skipped
// straight past the plan screen and the whole flow could be walked to
// `completeOnboarding()` without a card. A gate that only guards the front
// door is not a gate.
//
// So it lives here, wrapping every step this layout renders. Three things it
// deliberately does NOT do:
//
//   - It never gates the plan route itself. That is the destination.
//   - It waits for the workspace to load. Bouncing a mid-onboarding user on a
//     slow read would be worse than the hole it closes.
//   - It never gates a company with no workspace yet — a brand-new user is on
//     the company step, which comes BEFORE the gate, and has nothing to pay
//     for yet.
//
// This is still the routing half. `enforce.bill` on the backend is what
// actually refuses work; nothing here can grant access the server won't honour.
function OnboardingPaymentGuard({ children }: { children: React.ReactNode }) {
  const { loading, workspace } = useOnboarding()
  const router = useRouter()
  const pathname = usePathname()

  const onGate = pathname?.startsWith(ONBOARDING_PLAN_PATH) ?? false
  const blocked = !loading && workspace != null && !onGate && !companyHasPaid(workspace)

  useEffect(() => {
    if (blocked) router.replace(ONBOARDING_PLAN_PATH)
  }, [blocked, router])

  // Hold the shell rather than paint a step the user is about to be moved off.
  if (blocked) return <div className="ob-shell">Loading…</div>

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
        <OnboardingCompletedGuard>
          <OnboardingPaymentGuard>{children}</OnboardingPaymentGuard>
        </OnboardingCompletedGuard>
      </OnboardingEmailGuard>
    </OnboardingProvider>
  )
}
