// @vitest-environment jsdom
//
// THE GATE COVERS EVERY STEP, not just the way in.
//
// The hole this closes: `OnboardingRequiredGuard` defers on `/onboarding/*` —
// it has to, or it would fight step navigation including going back a step —
// so the payment check only ran on ENTRY to the app. Once a browser was on any
// onboarding route, nothing re-checked, and typing `/onboarding/import-context`
// walked straight past the plan screen. From there the whole flow could be
// completed and `completeOnboarding()` called without a card ever being taken.
import * as React from "react"
import { cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const replace = vi.fn()
let pathname = "/onboarding/import-context"
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
import { BILLING_ENABLED } from "../../../lib/billingAccess"

const paid = { id: "ws-1", plan: "starter", subscription_status: "active" }
const unpaid = { id: "ws-1", plan: "starter", subscription_status: null }

function mount() {
  return render(<OnboardingLayout>STEP_CONTENT</OnboardingLayout>)
}

beforeEach(() => {
  replace.mockReset()
  pathname = "/onboarding/import-context"
  auth = { kind: "authed", user: { email: "a@b.c" }, isEmailVerified: () => true }
  onboarding = { loading: false, workspace: null }
})
afterEach(() => cleanup())

// DORMANT WHILE PAYMENTS ARE HIDDEN — the gate is open, so there is no bounce
// to assert. Every expectation here is the one the gate had and will have
// again; the block below covers what replaces it meanwhile.
describe.skipIf(!BILLING_ENABLED)("an unpaid company cannot walk the steps", () => {
  it("bounces a typed step URL back to the plan gate", async () => {
    onboarding = { loading: false, workspace: unpaid }
    mount()
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/onboarding/plan"))
  })

  it("does not paint the step it is about to move you off", () => {
    onboarding = { loading: false, workspace: unpaid }
    const { queryByText } = mount()
    expect(queryByText("STEP_CONTENT")).toBeNull()
  })

  it("covers EVERY step, not just the first", async () => {
    for (const step of [
      "/onboarding/import-context",
      "/onboarding/connectors",
      "/onboarding/product",
      "/onboarding/metrics",
      "/onboarding/review",
      "/onboarding/personalize",
      "/onboarding/define-metrics",
    ]) {
      cleanup()
      replace.mockReset()
      pathname = step
      onboarding = { loading: false, workspace: unpaid }
      mount()
      await waitFor(() => expect(replace, step).toHaveBeenCalledWith("/onboarding/plan"))
    }
  })
})

describe.runIf(!BILLING_ENABLED)("payments hidden: the gate is open", () => {
  it("walks every step without ever asking for a card", async () => {
    // The mirror of the dormant block above. `companyHasPaid` answers true, so
    // a company with no subscription at all paints the step it asked for and
    // is never sent to /onboarding/plan — which is the whole point: onboarding
    // runs company → … → finish with no payment screen in it.
    for (const step of [
      "/onboarding/import-context",
      "/onboarding/connectors",
      "/onboarding/product",
      "/onboarding/review",
      "/onboarding/define-metrics",
    ]) {
      cleanup()
      replace.mockReset()
      pathname = step
      onboarding = { loading: false, workspace: unpaid }
      const { getByText } = mount()
      expect(getByText("STEP_CONTENT"), step).toBeTruthy()
      expect(replace, step).not.toHaveBeenCalled()
    }
  })
})

describe("what the gate must NOT block", () => {
  it("never gates the plan route itself — that is the destination", () => {
    pathname = "/onboarding/plan"
    onboarding = { loading: false, workspace: unpaid }
    const { getByText } = mount()
    expect(getByText("STEP_CONTENT")).toBeTruthy()
    expect(replace).not.toHaveBeenCalled()
  })

  it("lets a paid company through", () => {
    onboarding = { loading: false, workspace: paid }
    const { getByText } = mount()
    expect(getByText("STEP_CONTENT")).toBeTruthy()
    expect(replace).not.toHaveBeenCalled()
  })

  it("lets a TRIALLING company through — the card is on file", () => {
    onboarding = {
      loading: false,
      workspace: { ...unpaid, subscription_status: "trialing" },
    }
    const { getByText } = mount()
    expect(getByText("STEP_CONTENT")).toBeTruthy()
  })

  it("lets a plan that was never sold through Stripe through", () => {
    onboarding = { loading: false, workspace: { id: "ws-1", plan: "legacy", subscription_status: null } }
    const { getByText } = mount()
    expect(getByText("STEP_CONTENT")).toBeTruthy()
  })

  it("does not gate a brand-new user who has no company yet", () => {
    // The company step comes BEFORE the gate. There is nothing to pay for.
    onboarding = { loading: false, workspace: null }
    const { getByText } = mount()
    expect(getByText("STEP_CONTENT")).toBeTruthy()
    expect(replace).not.toHaveBeenCalled()
  })

  it("waits for the workspace to load rather than bouncing on a slow read", () => {
    // Bouncing a mid-onboarding user because the read had not landed yet would
    // be worse than the hole this closes.
    onboarding = { loading: true, workspace: null }
    mount()
    expect(replace).not.toHaveBeenCalled()
  })

  it("still defers to the email gate for an unverified user", () => {
    auth = { kind: "authed", user: { email: "a@b.c" }, isEmailVerified: () => false }
    onboarding = { loading: false, workspace: unpaid }
    const { queryByText } = mount()
    expect(queryByText("STEP_CONTENT")).toBeNull()
    // Verify-email wins; the payment gate never gets a say.
    expect(replace).not.toHaveBeenCalledWith("/onboarding/plan")
  })
})
