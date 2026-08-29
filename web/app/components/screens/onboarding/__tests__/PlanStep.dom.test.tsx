// @vitest-environment jsdom
//
// The onboarding payment gate. Three things here are load-bearing and none of
// them are visual:
//
//  1. Stripe redirects to success_url the moment payment is ACCEPTED, but
//     `subscription_status` is written by the WEBHOOK. Forwarding on the
//     redirect alone bounces the user straight back to the gate, which would
//     read to them as "my payment didn't work" seconds after it did.
//  2. A company that already pays must never be shown a buy-it-again screen —
//     that is what makes an invited teammate free, since the gate is
//     company-level.
//  3. Only an owner or admin can buy. Everyone else gets told who can, not a
//     button that 403s.
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
vi.mock("../../../../lib/api", async () => {
  const actual = await vi.importActual<Record<string, unknown>>("../../../../lib/api")
  return {
    ...actual,
    billingApi: {
      checkout: (...a: unknown[]) => checkout(...a),
      summary: () => summary(),
    },
  }
})

const refresh = vi.fn().mockResolvedValue(undefined)
let workspace: Record<string, unknown> | null = null
let orgRole: string | null = "owner"

vi.mock("../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ workspace, orgRole, refresh }),
}))

import { PlanStep } from "../PlanStep"

beforeEach(() => {
  search = ""
  orgRole = "owner"
  workspace = { id: "ws-1", display_name: "Acme", plan: "starter", subscription_status: null }
  push.mockClear()
  refresh.mockClear()
  checkout.mockReset()
  summary.mockReset()
})
afterEach(() => cleanup())

describe("choosing a plan", () => {
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

  it("points Team and Enterprise at a conversation instead of a dead button", () => {
    render(<PlanStep />)
    const link = screen.getByText("Talk to us").closest("a")!
    expect(link.getAttribute("href")).toBe("mailto:sales@sprntly.ai")
  })

  it("promises the trial in the words the backend will honour", () => {
    render(<PlanStep />)
    expect(screen.getByText(/nothing is charged/i).textContent).toContain("7 days")
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

describe("a company that already pays", () => {
  it("is forwarded straight through rather than asked to buy again", async () => {
    workspace = { id: "ws-1", plan: "starter", subscription_status: "active" }
    render(<PlanStep />)
    await waitFor(() => expect(push).toHaveBeenCalledWith("/onboarding/import-context"))
  })

  it("includes a trialling one — the card is already on file", async () => {
    workspace = { id: "ws-1", plan: "starter", subscription_status: "trialing" }
    render(<PlanStep />)
    await waitFor(() => expect(push).toHaveBeenCalled())
  })

  it("includes a plan that was never sold through Stripe", async () => {
    workspace = { id: "ws-1", plan: "legacy", subscription_status: null }
    render(<PlanStep />)
    await waitFor(() => expect(push).toHaveBeenCalled())
  })
})

describe("coming back from a successful Checkout", () => {
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
    expect(push).not.toHaveBeenCalled()

    await waitFor(
      () => expect(push).toHaveBeenCalledWith("/onboarding/import-context"),
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
    await waitFor(() => expect(push).toHaveBeenCalled(), { timeout: 10_000 })
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

    expect(push).not.toHaveBeenCalled()
    // …and they are handed the picker back rather than a dead end.
    expect(screen.getByTestId("plan-continue")).toBeTruthy()
  }, 45_000)
})

describe("someone who cannot buy", () => {
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
