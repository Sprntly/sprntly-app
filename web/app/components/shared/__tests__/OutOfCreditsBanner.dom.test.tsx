// @vitest-environment jsdom
//
// The standing "you're out of credits" banner.
//
// `PaymentRequiredPrompt` already catches the 402 — but it is a modal that
// fires at the moment a generation is REFUSED. Someone at zero opens the app,
// writes a prompt, waits, and only then learns it was never going to run. This
// is the same fact said in advance, at the bottom of the shell.
//
// Two things here are load-bearing and neither is visual:
//
//  1. WHEN IT SHOWS. The predicate is narrow on purpose — payments off, an
//     unknown balance, a lapsed subscription and an unmetered plan are all
//     "say nothing", each for a different reason. A banner that fires on a
//     null balance would tell someone they have nothing on the strength of a
//     column we never read.
//  2. THAT IT OFFERS A REFERRAL. Buying is owner/admin only, so for a plain
//     member "Buy credits" leads to a screen that will not let them act. The
//     referral is the one route open to everyone.
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const push = vi.fn()
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }))

let workspace: Record<string, unknown> | null = null
vi.mock("../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ workspace }),
}))

import { OutOfCreditsBanner } from "../OutOfCreditsBanner"
import { BILLING_ENABLED, isOutOfCredits } from "../../../lib/billingAccess"

const BROKE = {
  id: "ws-1",
  plan: "starter",
  subscription_status: "active",
  credit_balance: 0,
}

afterEach(() => {
  cleanup()
  push.mockReset()
  workspace = null
})

describe.skipIf(!BILLING_ENABLED)("when it shows", () => {
  it("shows at zero on a live subscription", () => {
    workspace = BROKE
    render(<OutOfCreditsBanner />)
    expect(screen.getByTestId("out-of-credits-banner")).toBeTruthy()
  })

  it("shows on a NEGATIVE balance too", () => {
    // The ledger can go under on a concurrent charge. "Not exactly zero" is
    // not "fine".
    workspace = { ...BROKE, credit_balance: -12 }
    render(<OutOfCreditsBanner />)
    expect(screen.getByTestId("out-of-credits-banner")).toBeTruthy()
  })

  it("says nothing while credits remain", () => {
    workspace = { ...BROKE, credit_balance: 1 }
    render(<OutOfCreditsBanner />)
    expect(screen.queryByTestId("out-of-credits-banner")).toBeNull()
  })

  it("says nothing when the balance is UNKNOWN", () => {
    // A null is a column we never read — a row predating billing, or a failed
    // load. Telling someone they have nothing on that basis is the one
    // mistake this banner must not make.
    workspace = { ...BROKE, credit_balance: null }
    render(<OutOfCreditsBanner />)
    expect(screen.queryByTestId("out-of-credits-banner")).toBeNull()
  })

  it("leaves a LAPSED company to its own lock", () => {
    // Cancelled is a bigger problem with its own screen (see lockModeFor).
    // Two alarms for two problems means the reader fixes the wrong one.
    workspace = { ...BROKE, subscription_status: "canceled" }
    render(<OutOfCreditsBanner />)
    expect(screen.queryByTestId("out-of-credits-banner")).toBeNull()
  })

  it("says nothing on a plan this counter does not meter", () => {
    for (const plan of ["legacy", "enterprise"]) {
      cleanup()
      workspace = { ...BROKE, plan, subscription_status: null }
      render(<OutOfCreditsBanner />)
      expect(screen.queryByTestId("out-of-credits-banner"), plan).toBeNull()
    }
  })

  it("says nothing with no workspace loaded", () => {
    workspace = null
    render(<OutOfCreditsBanner />)
    expect(screen.queryByTestId("out-of-credits-banner")).toBeNull()
  })
})

describe.runIf(!BILLING_ENABLED)("payments hidden", () => {
  it("never appears — nothing is charged, so no balance means anything", () => {
    workspace = BROKE
    render(<OutOfCreditsBanner />)
    expect(screen.queryByTestId("out-of-credits-banner")).toBeNull()
  })
})

describe.skipIf(!BILLING_ENABLED)("what it offers", () => {
  it("offers a REFERRAL beside the purchase, because buying is admin-only", () => {
    // `/v1/billing/checkout` refuses anyone below admin. A banner whose only
    // action is one most readers cannot take is a dead end for exactly the
    // people most likely to hit it.
    workspace = BROKE
    render(<OutOfCreditsBanner />)
    expect(screen.getByTestId("out-of-credits-refer")).toBeTruthy()
    expect(screen.getByTestId("out-of-credits-buy")).toBeTruthy()
  })

  it("routes both actions to Billing", () => {
    workspace = BROKE
    render(<OutOfCreditsBanner />)
    fireEvent.click(screen.getByTestId("out-of-credits-buy"))
    expect(push).toHaveBeenCalledWith("/settings?section=billing")

    push.mockReset()
    fireEvent.click(screen.getByTestId("out-of-credits-refer"))
    expect(push).toHaveBeenCalledWith("/settings?section=billing")
  })

  it("can be put away", () => {
    // Nothing is mid-failure, so someone reading a document should be able to
    // dismiss it. Session-only: the workspace is still stuck, and a banner
    // that never returns has stopped telling the truth.
    workspace = BROKE
    render(<OutOfCreditsBanner />)
    fireEvent.click(screen.getByTestId("out-of-credits-dismiss"))
    expect(screen.queryByTestId("out-of-credits-banner")).toBeNull()
  })

  it("is announced politely, not as an alert", () => {
    // role="status" so a screen reader hears it at the next pause. An alert
    // interrupts, and this is not urgent — nothing is failing right now.
    workspace = BROKE
    render(<OutOfCreditsBanner />)
    expect(screen.getByTestId("out-of-credits-banner").getAttribute("role")).toBe("status")
  })
})

describe("isOutOfCredits — the predicate on its own", () => {
  it("answers false for every 'say nothing' case", () => {
    expect(isOutOfCredits(null)).toBe(false)
    expect(isOutOfCredits({ ...BROKE, credit_balance: null })).toBe(false)
    expect(isOutOfCredits({ ...BROKE, credit_balance: undefined })).toBe(false)
    expect(isOutOfCredits({ ...BROKE, subscription_status: "canceled" })).toBe(false)
    expect(isOutOfCredits({ ...BROKE, plan: "enterprise", subscription_status: null })).toBe(false)
  })

  it("counts past_due as live — Stripe is still working the card", () => {
    // Mirrors ACTIVE_SUBSCRIPTION_STATUSES: cutting someone off mid-retry is
    // how a bounced card becomes churn. They are out of credits, not lapsed.
    const answer = isOutOfCredits({ ...BROKE, subscription_status: "past_due" })
    expect(answer).toBe(BILLING_ENABLED)
  })
})
