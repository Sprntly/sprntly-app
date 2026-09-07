// @vitest-environment jsdom
//
// THE PAYMENT GATE IS THE LAST STEP NOW, not a guard on every step.
//
// This file used to assert the opposite. Payment sat at position two, enforced
// by an `OnboardingPaymentGuard` wrapped around every step this layout renders,
// because `OnboardingRequiredGuard` defers on `/onboarding/*` — it has to, or
// it would fight step navigation including going back — so without it a typed
// step URL walked straight past the plan screen and the whole flow could be
// completed without a card.
//
// Payment moved to the END of onboarding on 2026-09-07 and the guard was
// deleted with it. Two things replace it, and this file asserts both:
//
//   1. The layout lets an unpaid company walk every step. That is the point of
//      the move — a stranger picking between a $59 and a $99 plan at step one
//      has connected nothing and seen nothing.
//   2. Onboarding cannot be COMPLETED without paying, because
//      `finishOnboardingAndEnterApp` is called from exactly one place: the plan
//      step, on the far side of a `billingApi.summary()` that must report a
//      live subscription. That is a property of where the closer lives, which
//      is a much harder thing to walk around than a redirect.
import { readFileSync } from "node:fs"
import { join } from "node:path"
import * as React from "react"
import { cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const replace = vi.fn()
let pathname = "/onboarding/connectors"
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  usePathname: () => pathname,
}))

let auth: Record<string, unknown> = {
  kind: "authed",
  user: { email: "a@b.c" },
  isEmailVerified: () => true,
}
vi.mock("../../../lib/auth", () => ({ useAuth: () => auth }))

let onboarding: { loading: boolean; workspace: Record<string, unknown> | null } = {
  loading: false,
  workspace: null,
}
vi.mock("../../../context/OnboardingContext", () => ({
  OnboardingProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useOnboarding: () => onboarding,
}))

import OnboardingLayout from "../layout"
import { ONBOARDING_STEP_SLUGS } from "../../../lib/onboarding/types"

const paid = { id: "ws-1", plan: "starter", subscription_status: "active" }
const unpaid = { id: "ws-1", plan: "starter", subscription_status: null }

function mount() {
  return render(<OnboardingLayout>STEP_CONTENT</OnboardingLayout>)
}

beforeEach(() => {
  replace.mockReset()
  pathname = "/onboarding/connectors"
  auth = { kind: "authed", user: { email: "a@b.c" }, isEmailVerified: () => true }
  onboarding = { loading: false, workspace: null }
})
afterEach(() => cleanup())

describe("an unpaid company walks the whole flow", () => {
  it("paints every numbered step without being sent to the plan screen", () => {
    // The inverse of what this file asserted before 2026-09-07. Nothing here
    // asks for a card until the flow reaches the plan step on its own.
    for (const slug of ONBOARDING_STEP_SLUGS) {
      cleanup()
      replace.mockReset()
      pathname = `/onboarding/${slug}`
      onboarding = { loading: false, workspace: unpaid }
      const { getByText } = mount()
      expect(getByText("STEP_CONTENT"), slug).toBeTruthy()
      expect(replace, slug).not.toHaveBeenCalled()
    }
  })

  it("lets a paid company through just the same", () => {
    onboarding = { loading: false, workspace: paid }
    const { getByText } = mount()
    expect(getByText("STEP_CONTENT")).toBeTruthy()
    expect(replace).not.toHaveBeenCalled()
  })

  it("does not gate a brand-new user who has no company yet", () => {
    onboarding = { loading: false, workspace: null }
    const { getByText } = mount()
    expect(getByText("STEP_CONTENT")).toBeTruthy()
    expect(replace).not.toHaveBeenCalled()
  })
})

describe("what still gates", () => {
  it("still defers to the email gate for an unverified user", () => {
    auth = { kind: "authed", user: { email: "a@b.c" }, isEmailVerified: () => false }
    onboarding = { loading: false, workspace: unpaid }
    const { queryByText } = mount()
    expect(queryByText("STEP_CONTENT")).toBeNull()
    expect(replace).toHaveBeenCalledWith(expect.stringContaining("/verify-email"))
  })

  it("still keeps a COMPLETED user out of the flow", () => {
    onboarding = {
      loading: false,
      workspace: { ...paid, onboarding_completed_at: "2026-09-01T00:00:00Z" },
    }
    const { queryByText } = mount()
    expect(queryByText("STEP_CONTENT")).toBeNull()
    expect(replace).toHaveBeenCalledWith("/")
  })
})

describe("completion lives behind the payment step, and only there", () => {
  // A source-level assertion on purpose. The old guard could be walked around
  // by typing a URL; "there is exactly one caller of the closer, and it is the
  // plan step" cannot be. If a future screen starts completing onboarding
  // again, this fails and names the file.
  const screens = join(__dirname, "..", "..", "..", "components", "screens", "onboarding")

  it("only PlanStep calls finishOnboardingAndEnterApp", () => {
    const callers = ["PlanStep", "PersonalizeStep", "DefineMetrics"].filter((name) =>
      readFileSync(join(screens, `${name}.tsx`), "utf8").includes(
        "finishOnboardingAndEnterApp(",
      ),
    )
    expect(callers).toEqual(["PlanStep"])
  })
})
