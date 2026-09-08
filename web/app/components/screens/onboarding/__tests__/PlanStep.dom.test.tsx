// @vitest-environment jsdom
//
// The onboarding payment step — THE LAST ONE, as of 2026-09-07. Four things
// here are load-bearing and none of them are visual:
//
//  1. Stripe redirects to success_url the moment payment is ACCEPTED, but
//     `subscription_status` is written by the WEBHOOK. Forwarding on the
//     redirect alone would enter the app before the company row agrees.
//  2. A company that already pays must never be shown a buy-it-again screen —
//     that is what makes an invited teammate free, since the check is
//     company-level.
//  3. Only an owner or admin can buy. Everyone else gets told who can, not a
//     button that 403s.
//  4. PAYING IS WHAT COMPLETES ONBOARDING. This screen owns the closer now (it
//     used to run from PersonalizeStep / DefineMetrics), so "paid" and
//     "onboarded" cannot come apart: `finishOnboardingAndEnterApp` runs on the
//     far side of a summary reporting a live subscription, and only then does
//     anyone reach the app.
//
// NO TRIAL IS PROMISED. `TRIALS_ENABLED` is false on both sides — the card is
// charged at checkout — so the copy says so. The trialling wording is still in
// the component, behind the same flag the server charges on.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const push = vi.fn()
let search = ""

// A STABLE router object. Next's own useRouter is stable across renders, and a
// mock that returns a fresh one each time makes any effect keyed on it re-run
// forever — which is how this file first OOM'd rather than failed.
const router = { push, replace: vi.fn(), refresh: vi.fn() }

vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(search),
}))

const checkout = vi.fn()
const summary = vi.fn()
const connectorsList = vi.fn()
vi.mock("../../../../lib/api", async () => {
  const actual = await vi.importActual<Record<string, unknown>>("../../../../lib/api")
  return {
    ...actual,
    billingApi: {
      checkout: (...a: unknown[]) => checkout(...a),
      summary: () => summary(),
    },
    // Read once by the closer, to decide whether a first brief is worth
    // kicking. See PlanStep's `advance`.
    connectorsApi: { list: () => connectorsList() },
  }
})

// The closer itself. That it runs from HERE, and only once payment is
// confirmed, is this file's fourth invariant; its innards have their own test.
const finishOnboarding = vi.fn()
vi.mock("../../../../lib/onboarding/finishOnboarding", () => ({
  finishOnboardingAndEnterApp: (...a: unknown[]) => finishOnboarding(...a),
  POST_ONBOARDING_PATH: "/?new=1",
}))

vi.mock("../../../../lib/auth", () => ({
  useAuth: () => ({ kind: "authed", user: { id: "u-1" } }),
}))
vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ setContent: vi.fn() }),
}))

const refresh = vi.fn().mockResolvedValue(undefined)
let orgRole: string | null = "owner"

// Deliberately its OWN state, not an alias of `onboardingWorkspace`. The bug
// this file now guards is the two contexts DISAGREEING, so a shared object
// would make it unreproducible.
let workspaceCtxWorkspace: Record<string, unknown> | null = null

vi.mock("../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ workspace: workspaceCtxWorkspace, orgRole, refresh }),
}))

// TWO CONTEXTS, ONE COMPANY. `OnboardingPaymentGuard` reads the onboarding
// context; this step must read the same one, or the two disagree about
// `companyHasPaid` and bounce the user between /onboarding/plan and the next
// slug forever. `refreshOnboarding` is tracked separately from `refresh` so a
// test can assert the guard's copy is the one that gets re-read after payment.
const refreshOnboarding = vi.fn().mockResolvedValue(undefined)
let onboardingWorkspace: Record<string, unknown> | null = null

vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => ({ workspace: onboardingWorkspace, refresh: refreshOnboarding }),
}))

import { TRIALS_ENABLED } from "../../../../lib/billingPlans"
import { PlanStep } from "../PlanStep"
import { BILLING_ENABLED } from "../../../../lib/billingAccess"

beforeEach(() => {
  search = ""
  orgRole = "owner"
  onboardingWorkspace = { id: "ws-1", display_name: "Acme", plan: "starter", subscription_status: null }
  workspaceCtxWorkspace = { ...onboardingWorkspace }
  push.mockClear()
  refresh.mockClear()
  refreshOnboarding.mockClear()
  checkout.mockReset()
  summary.mockReset()
  connectorsList.mockReset()
  connectorsList.mockResolvedValue({ connections: [] })
  finishOnboarding.mockReset()
  finishOnboarding.mockResolvedValue(undefined)
  router.replace.mockClear()
})
afterEach(() => cleanup())

describe.skipIf(BILLING_ENABLED)("payments hidden", () => {
  it("finishes onboarding instead of asking anyone to pick a plan", async () => {
    // With payments hidden `companyHasPaid` answers true for everyone, so this
    // step must still be an EXIT and not a dead end — it runs the closer and
    // enters the app rather than parking someone on a picker whose only door is
    // a checkout.
    onboardingWorkspace = { id: "ws-1", display_name: "Acme", plan: "starter", subscription_status: null }
    workspaceCtxWorkspace = { ...onboardingWorkspace }
    render(<PlanStep />)
    await waitFor(() => expect(finishOnboarding).toHaveBeenCalled())
    expect(router.replace).toHaveBeenCalledWith("/?new=1")
    expect(checkout).not.toHaveBeenCalled()
  })
})

// DORMANT WHILE PAYMENTS ARE HIDDEN. `companyHasPaid` answers true for
// everyone, so this component advances the moment it mounts and never paints
// a picker to assert against. The expectations are the ones the plan step had
// and will have again — skipped rather than rewritten so flipping
// `BILLING_ENABLED` back to true restores the whole file's coverage.
describe.skipIf(!BILLING_ENABLED)("choosing a plan", () => {
  it("offers only the plans the backend will actually sell", () => {
    // `plans.SELF_SERVE_PLANS` excludes Team and Enterprise, and a checkout
    // naming either is rejected — so a button for one would be a 400 waiting
    // to happen.
    render(<PlanStep />)
    expect(screen.getByTestId("plan-starter")).toBeTruthy()
    expect(screen.getByTestId("plan-product_builder")).toBeTruthy()
    expect(screen.queryByTestId("plan-team")).toBeNull()
    expect(screen.queryByTestId("plan-enterprise")).toBeNull()
  })

  it("offers Custom as a third card, priceless and unbuyable", () => {
    // Team and Enterprise carry no self-serve price. They used to be a line of
    // text under the grid; at the end of onboarding that reads as "we do not
    // do what you need", so they are a card (owner decision 2026-09-08).
    render(<PlanStep />)
    const custom = screen.getByTestId("plan-custom")
    expect(custom).toBeTruthy()
    expect(custom.textContent).toMatch(/Let.s talk/)
    // No price on it, and it is still not a plan the backend sells.
    expect(custom.textContent).not.toMatch(/\$\d/)
    expect(screen.queryByTestId("plan-team")).toBeNull()
    expect(screen.queryByTestId("plan-enterprise")).toBeNull()
  })

  it("shows the sales address ON THE PAGE when Custom is picked", () => {
    // NOT a `mailto:`. A browser with no default mail client registered — a
    // webmail user on a fresh machine, which is most of them — swallows a
    // mailto click silently, so the reader presses the only button on screen
    // and nothing happens at all. The address being printed is what makes
    // this work for everyone.
    render(<PlanStep />)
    fireEvent.click(screen.getByTestId("plan-custom"))

    const panel = screen.getByTestId("plan-custom-panel")
    expect(panel.textContent).toContain("sales@sprntly.ai")
    // And it says what to put in the mail, rather than leaving the reader to
    // open the first message of a negotiation on a blank page.
    expect(panel.textContent).toMatch(/team size/i)
    expect(panel.textContent).toMatch(/business day/i)
  })

  it("replaces Continue rather than relabelling it — there is nothing to continue to", () => {
    // `custom` is not in plans.SELF_SERVE_PLANS, so a checkout naming it is
    // refused by the backend. A Continue that cannot continue is worse than
    // no Continue, so the panel takes its place entirely.
    render(<PlanStep />)
    fireEvent.click(screen.getByTestId("plan-custom"))

    expect(screen.queryByTestId("plan-continue")).toBeNull()
    expect(checkout).not.toHaveBeenCalled()
  })

  it("copies the address, and still shows it when the clipboard refuses", async () => {
    // The copy is a shortcut. A refused clipboard (insecure context, a
    // dismissed permission prompt) must cost the reader nothing, because the
    // address is selectable text either way.
    const writeText = vi.fn().mockRejectedValue(new Error("denied"))
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    })
    render(<PlanStep />)
    fireEvent.click(screen.getByTestId("plan-custom"))
    fireEvent.click(screen.getByTestId("plan-custom-copy"))

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("sales@sprntly.ai"))
    // No "Copied" claimed on a failure, and the address is still on screen.
    expect(screen.getByTestId("plan-custom-copy").textContent).toBe("Copy")
    expect(screen.getByTestId("plan-custom-panel").textContent).toContain(
      "sales@sprntly.ai",
    )
  })

  it("goes back to buying when a priced plan is picked again", () => {
    // Custom must not be a trap: selecting it and changing your mind has to
    // restore a Continue that actually buys something.
    render(<PlanStep />)
    fireEvent.click(screen.getByTestId("plan-custom"))
    expect(screen.queryByTestId("plan-continue")).toBeNull()

    fireEvent.click(screen.getByTestId("plan-starter"))
    expect(screen.getByTestId("plan-continue").textContent).toMatch(/Continue/)
    expect(screen.queryByTestId("plan-custom-panel")).toBeNull()
  })

  it("promises no trial, because the backend grants none", () => {
    // The mirror of `plans.TRIALS_ENABLED`. A screen promising a free week
    // against a checkout that charges today is the worst possible place to be
    // wrong about money, so the copy is keyed on the same flag the server is.
    expect(TRIALS_ENABLED).toBe(false)
    render(<PlanStep />)
    expect(screen.getByText(/charged today/i)).toBeTruthy()
    expect(screen.queryByText(/nothing is charged/i)).toBeNull()
    expect(screen.queryByText(/trial/i)).toBeNull()
  })

  it("sends the chosen plan, interval and its own return path to checkout", async () => {
    checkout.mockResolvedValue({ url: "https://checkout.stripe.test/x" })
    render(<PlanStep />)

    fireEvent.click(screen.getByTestId("plan-product_builder"))
    fireEvent.click(screen.getByText("Annual"))
    fireEvent.click(screen.getByTestId("plan-continue"))

    await waitFor(() =>
      expect(checkout).toHaveBeenCalledWith("product_builder", "annual", "/onboarding/plan"),
    )
  })

  it("recovers to the picker when checkout will not open", async () => {
    checkout.mockRejectedValue(new Error("stripe down"))
    render(<PlanStep />)
    fireEvent.click(screen.getByTestId("plan-continue"))

    await waitFor(() => expect(screen.getByText(/Couldn.t open checkout/i)).toBeTruthy())
    // Still usable — not a dead end.
    expect((screen.getByTestId("plan-continue") as HTMLButtonElement).disabled).toBe(false)
  })

  it("says nothing was charged when Checkout was cancelled", () => {
    search = "checkout=cancelled"
    render(<PlanStep />)
    expect(screen.getByText(/nothing was charged/i)).toBeTruthy()
  })
})

describe.skipIf(!BILLING_ENABLED)("going back", () => {
  // The screen predates being a step: it was an unnumbered gate you were
  // redirected to, so it rendered its own shell with no footer. Moving it to
  // the end of the flow left it the only step with no way out but forwards.
  it("offers Back to the step before it", () => {
    render(<PlanStep />)
    fireEvent.click(screen.getByTestId("plan-back"))
    expect(push).toHaveBeenCalledWith("/onboarding/personalize")
  })

  it("offers it to a member who cannot buy — they are the most stuck of all", () => {
    // They cannot act on this screen at all, so leaving them here with no exit
    // is worse than for anyone else.
    orgRole = "member"
    render(<PlanStep />)
    expect(screen.queryByTestId("plan-continue")).toBeNull()
    expect(screen.getByTestId("plan-back")).toBeTruthy()
  })

  it("withdraws it once the money has moved", async () => {
    // Back while the subscription is being confirmed invites someone to walk
    // away mid-write, and there is nothing behind them to go back TO — they
    // have paid.
    search = "checkout=success"
    summary.mockResolvedValue({ plan: "starter", subscription_status: null })
    render(<PlanStep />)
    expect(screen.getByRole("status")).toBeTruthy()
    expect(screen.queryByTestId("plan-back")).toBeNull()
  })
})

describe.skipIf(!BILLING_ENABLED)("a company that already pays", () => {
  it("is finished through rather than asked to buy again", async () => {
    // This is the last step, so "forwarded" now means completed and let into
    // the app — an invited teammate joining a company that already pays walks
    // out of onboarding here without a second charge.
    onboardingWorkspace = { id: "ws-1", plan: "starter", subscription_status: "active" }
    render(<PlanStep />)
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/?new=1"))
    expect(finishOnboarding).toHaveBeenCalledTimes(1)
  })

  it("includes a trialling one — a trial in flight is still a live subscription", async () => {
    // No NEW trials are sold, but a company mid-trial from before the switch
    // reads as paid and must not be charged again to finish signing up.
    onboardingWorkspace = { id: "ws-1", plan: "starter", subscription_status: "trialing" }
    render(<PlanStep />)
    await waitFor(() => expect(finishOnboarding).toHaveBeenCalled())
  })

  it("includes a plan that was never sold through Stripe", async () => {
    onboardingWorkspace = { id: "ws-1", plan: "legacy", subscription_status: null }
    render(<PlanStep />)
    await waitFor(() => expect(finishOnboarding).toHaveBeenCalled())
  })
})

describe.skipIf(!BILLING_ENABLED)("the two contexts disagreeing", () => {
  it("does not advance on the workspace context alone", async () => {
    // THE INFINITE REDIRECT LOOP, which the since-deleted
    // `OnboardingPaymentGuard` was one half of: it read the ONBOARDING context
    // while this step read the workspace one, so after checkout the step saw
    // paid and moved on while the guard still saw unpaid and replaced back to
    // /onboarding/plan, for as long as the tab stayed open.
    //
    // The guard is gone, but reading the same context is still the rule, and
    // the stakes went UP with the move: this step COMPLETES onboarding now, so
    // acting on a copy that disagrees with the one the app's own guard reads
    // means finishing signup for a company the app will bounce straight back
    // into onboarding.
    workspaceCtxWorkspace = { id: "ws-1", plan: "starter", subscription_status: "active" }
    onboardingWorkspace = { id: "ws-1", plan: "starter", subscription_status: null }

    render(<PlanStep />)

    // Completing here on the workspace copy alone is the bug.
    await new Promise((r) => setTimeout(r, 50))
    expect(finishOnboarding).not.toHaveBeenCalled()
  })

  it("advances on the onboarding context — the one the flow reads", async () => {
    workspaceCtxWorkspace = { id: "ws-1", plan: "starter", subscription_status: null }
    onboardingWorkspace = { id: "ws-1", plan: "starter", subscription_status: "active" }

    render(<PlanStep />)
    await waitFor(() => expect(finishOnboarding).toHaveBeenCalled())
  })
})

describe.skipIf(!BILLING_ENABLED)("coming back from a successful Checkout", () => {
  it("waits for the webhook rather than trusting the redirect", async () => {
    // THE BUG THIS PREVENTS: Stripe redirects on payment acceptance, but the
    // company row is written by the webhook. Forwarding immediately would let
    // the gate re-read an unpaid company and bounce the user back here.
    search = "checkout=success"
    summary
      .mockResolvedValueOnce({ plan: "starter", subscription_status: null })
      .mockResolvedValueOnce({ plan: "starter", subscription_status: null })
      .mockResolvedValue({ plan: "starter", subscription_status: "trialing" })

    render(<PlanStep />)

    // Reads as progress, never as a failure — the money has already moved.
    expect(screen.getByRole("status").textContent).toMatch(/Confirming/i)
    expect(finishOnboarding).not.toHaveBeenCalled()

    await waitFor(
      () => expect(router.replace).toHaveBeenCalledWith("/?new=1"),
      { timeout: 10_000 },
    )
    expect(summary.mock.calls.length).toBeGreaterThan(1)
    // The workspace context is refreshed BEFORE forwarding, so the next screen
    // does not re-read a stale unpaid company and bounce them back.
    expect(refresh).toHaveBeenCalled()
  }, 15_000)

  it("keeps waiting through a transient summary failure", async () => {
    search = "checkout=success"
    summary
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValue({ plan: "starter", subscription_status: "active" })

    render(<PlanStep />)
    await waitFor(() => expect(finishOnboarding).toHaveBeenCalled(), { timeout: 10_000 })
  }, 15_000)

  it("does not show the buy screen while confirming", () => {
    search = "checkout=success"
    summary.mockResolvedValue({ plan: "starter", subscription_status: null })
    render(<PlanStep />)
    expect(screen.queryByTestId("plan-continue")).toBeNull()
  })

  it("NEVER opens the gate on a timeout — `?checkout=success` is not proof", async () => {
    // The bypass this closes: `?checkout=success` is a string in a URL. Anyone
    // could type this route with it, wait out the timer, and walk into the
    // product. The backend reconciles against Stripe directly now, so
    // exhausting the wait means Stripe itself has no subscription for this
    // company — the one case where stopping is right.
    search = "checkout=success"
    summary.mockResolvedValue({ plan: "starter", subscription_status: null })

    render(<PlanStep />)
    await waitFor(
      () => expect(screen.getByText(/couldn.t confirm your subscription/i)).toBeTruthy(),
      { timeout: 40_000 },
    )

    expect(finishOnboarding).not.toHaveBeenCalled()
    // …and they are handed the picker back rather than a dead end.
    expect(screen.getByTestId("plan-continue")).toBeTruthy()
  }, 45_000)
})

describe.skipIf(!BILLING_ENABLED)("someone who cannot buy", () => {
  it("is told who can, instead of given a button that 403s", () => {
    // /v1/billing/checkout is owner-or-admin only. A plain member reaches this
    // screen the same way an admin does, because the gate is company-level.
    orgRole = "member"
    render(<PlanStep />)
    expect(screen.getByText(/Waiting on/i)).toBeTruthy()
    expect(screen.queryByTestId("plan-continue")).toBeNull()
  })

  it("still lets an admin buy", () => {
    orgRole = "admin"
    render(<PlanStep />)
    expect(screen.getByTestId("plan-continue")).toBeTruthy()
  })
})
