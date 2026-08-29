// The rule the onboarding payment gate routes on. It mirrors
// backend/app/billing/plans.py — the backend is the authority, and nothing
// here can grant access the server will not honour. These tests exist because
// a drift between the two is silent: the UI would wave someone through and
// every generation would 402 at them.
import { describe, expect, it } from "vitest"

import {
  companyHasPaid,
  subscriptionGrantsAccess,
  trialDaysLeft,
  trialLabel,
} from "../billingAccess"
import {
  CREDITS_PER_TOPUP_USD,
  PLATFORM_FEE_USD,
  SELF_SERVE_PLANS,
  TEAM_MONTHLY_CREDITS,
  monthlyCreditsFor,
} from "../billingPlans"

describe("subscriptionGrantsAccess", () => {
  it("admits the live statuses Stripe emits", () => {
    for (const status of ["active", "trialing"]) {
      expect(subscriptionGrantsAccess("starter", status), status).toBe(true)
    }
  })

  it("admits past_due — a retry in flight is not a cancellation", () => {
    // Cutting a paying customer off while Smart Retries work the card is how a
    // bounced card becomes churn. Matches ACTIVE_SUBSCRIPTION_STATUSES.
    expect(subscriptionGrantsAccess("starter", "past_due")).toBe(true)
  })

  it("refuses the dead ones", () => {
    for (const status of ["canceled", "unpaid", "incomplete", "incomplete_expired"]) {
      expect(subscriptionGrantsAccess("starter", status), status).toBe(false)
    }
  })

  it("refuses a company with no subscription at all", () => {
    expect(subscriptionGrantsAccess("starter", null)).toBe(false)
    expect(subscriptionGrantsAccess(null, null)).toBe(false)
    expect(subscriptionGrantsAccess("starter", "")).toBe(false)
  })

  it("lets legacy and enterprise straight through, status or no status", () => {
    // Neither was sold through Stripe, so both carry a null status. Gating
    // them on one would lock out every pre-billing tenant the day this ships.
    for (const plan of ["legacy", "enterprise", "LEGACY", " Enterprise "]) {
      expect(subscriptionGrantsAccess(plan, null), plan).toBe(true)
      expect(subscriptionGrantsAccess(plan, "canceled"), plan).toBe(true)
    }
  })

  it("does not extend that bypass to a paid plan", () => {
    expect(subscriptionGrantsAccess("team", null)).toBe(false)
    expect(subscriptionGrantsAccess("product_builder", null)).toBe(false)
  })
})

describe("companyHasPaid", () => {
  it("is false for no company at all", () => {
    expect(companyHasPaid(null)).toBe(false)
  })

  it("reads the plan and status off a company row", () => {
    expect(companyHasPaid({ plan: "starter", subscription_status: "trialing" })).toBe(true)
    expect(companyHasPaid({ plan: "starter", subscription_status: "canceled" })).toBe(false)
  })

  it("is false for a company row that predates billing", () => {
    // Columns added by the billing migration; a row written before it has
    // neither. Falling closed is right — the gate sends them to pick a plan.
    expect(companyHasPaid({})).toBe(false)
  })
})

describe("trialDaysLeft", () => {
  const NOW = new Date("2026-09-01T12:00:00Z")
  const trialing = (end: string) => ({
    subscription_status: "trialing",
    current_period_end: end,
  })

  it("counts the days to the first charge", () => {
    expect(trialDaysLeft(trialing("2026-09-07T12:00:00Z"), NOW)).toBe(6)
  })

  it("rounds UP — a part-day is still a day the reader has", () => {
    // 23 hours left is one more day, not zero. "0 days left" beside a
    // subscription that has charged nobody reads as a fault.
    expect(trialDaysLeft(trialing("2026-09-02T11:00:00Z"), NOW)).toBe(1)
    expect(trialDaysLeft(trialing("2026-09-02T13:00:00Z"), NOW)).toBe(2)
  })

  it("never goes below 1 while Stripe still says trialing", () => {
    // The status is the authority, not our clock — skew between us and Stripe
    // must not produce "-1 days left".
    expect(trialDaysLeft(trialing("2026-08-30T12:00:00Z"), NOW)).toBe(1)
  })

  it("is null for anything that is not a trial", () => {
    for (const status of ["active", "past_due", "canceled", null]) {
      expect(
        trialDaysLeft({ subscription_status: status, current_period_end: "2026-09-07T12:00:00Z" }, NOW),
        String(status),
      ).toBeNull()
    }
  })

  it("is null when there is no date to count to, or it is unusable", () => {
    expect(trialDaysLeft({ subscription_status: "trialing", current_period_end: null }, NOW)).toBeNull()
    expect(trialDaysLeft({ subscription_status: "trialing", current_period_end: "not a date" }, NOW)).toBeNull()
    expect(trialDaysLeft(null, NOW)).toBeNull()
  })
})

describe("trialLabel", () => {
  it("says day, not days, at one", () => {
    expect(trialLabel(1)).toBe("1 day left")
    expect(trialLabel(6)).toBe("6 days left")
  })
})

describe("the plan cards quote the allowance a buyer actually gets", () => {
  // THE DRIFT THIS CLOSES: these numbers were literals, copied from the old
  // table. When the backend re-derived allowances from price they did not move,
  // so the cards advertised 500 and 2,500 while a customer who bought one was
  // granted 756 and 1,316 — a wrong number on the screen where somebody decides
  // what to pay.
  it("matches the backend formula, plan for plan", () => {
    for (const p of SELF_SERVE_PLANS) {
      expect(p.credits, p.id).toBe(monthlyCreditsFor(p.monthly))
    }
  })

  it("withholds the platform fee before converting to credits", () => {
    for (const p of SELF_SERVE_PLANS) {
      const granted = p.credits / CREDITS_PER_TOPUP_USD
      expect(p.monthly - granted, p.id).toBeGreaterThanOrEqual(PLATFORM_FEE_USD)
    }
  })

  it("floors rather than rounds up — a fractional credit is ours", () => {
    // Team is the case that proves it: $1,661.67 x 14 is 23,263.33.
    const exact = (20_000 / 12 - PLATFORM_FEE_USD) * CREDITS_PER_TOPUP_USD
    expect(TEAM_MONTHLY_CREDITS).toBeLessThanOrEqual(exact)
    expect(exact - TEAM_MONTHLY_CREDITS).toBeLessThan(1)
  })

  it("carries no stale literal from the old table", () => {
    const quoted = SELF_SERVE_PLANS.map((p) => p.credits)
    expect(quoted).not.toContain(500)
    expect(quoted).not.toContain(2500)
    expect(TEAM_MONTHLY_CREDITS).not.toBe(15000)
  })
})
