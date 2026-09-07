/**
 * The plans a customer can buy, and who to talk to about the ones they can't.
 *
 * Lifted out of BillingSettings when the onboarding payment gate started
 * needing the same list. Two copies of a price table is one wrong price
 * waiting to happen — and the wrong one would be on the signup screen, which
 * is the worst place to be wrong about money.
 *
 * `plans.SELF_SERVE_PLANS` on the backend is the authority on what may be
 * bought: a checkout naming anything outside it is rejected rather than
 * quietly downgraded. Keep the ids here matching those.
 */

export type SelfServePlan = {
  id: string
  label: string
  /** Dollars per month, billed monthly. */
  monthly: number
  /** Dollars per YEAR, billed once. Ten months' money for twelve months. */
  annual: number
  credits: number
  blurb: string
  featured?: boolean
}

/**
 * THE ALLOWANCE IS DERIVED, not typed in. A MIRROR of
 * `backend/app/billing/plans.py` — see PLATFORM_FEE_USD and the `_allowance`
 * formula there, which is the authority.
 *
 *     credits = (monthly list price - PLATFORM_FEE_USD) x CREDITS_PER_TOPUP_USD
 *
 * The previous version of this file carried the numbers as literals, copied
 * from the old table. When the backend allowances were re-derived from price,
 * these did not move — so the plan cards went on advertising 500 and 2,500
 * while a customer who bought one was granted 756 and 1,316. Two sources for
 * one number is one wrong number waiting, and the wrong one is on the screen
 * where somebody decides what to pay.
 *
 * Deriving it here means a price change moves the card with it, and there is
 * no literal left to go stale.
 */
export const PLATFORM_FEE_USD = 5
export const CREDITS_PER_TOPUP_USD = 14

export function monthlyCreditsFor(monthlyPriceUsd: number): number {
  // Floored, never rounded up — matching the backend, where the formula is a
  // ceiling on what we owe and a fractional credit is ours.
  return Math.floor((monthlyPriceUsd - PLATFORM_FEE_USD) * CREDITS_PER_TOPUP_USD)
}

export const SELF_SERVE_PLANS: SelfServePlan[] = [
  {
    id: "starter",
    label: "Starter",
    monthly: 59,
    annual: 590,
    credits: monthlyCreditsFor(59),
    blurb: "For one person shipping their first products.",
  },
  {
    id: "product_builder",
    label: "Product Builder",
    monthly: 99,
    annual: 990,
    credits: monthlyCreditsFor(99),
    blurb: "For a product manager running a full portfolio.",
    featured: true,
  },
]

/** Team is sold annually at $20,000; the formula wants a monthly figure. */
export const TEAM_ANNUAL_USD = 20_000
export const TEAM_MONTHLY_CREDITS = monthlyCreditsFor(TEAM_ANNUAL_USD / 12)

export const SALES_CONTACT = "sales@sprntly.ai"

/** Where Stripe returns the browser after an onboarding checkout. A PATH, not
 *  a URL — the backend validates it as an open-redirect boundary. */
export const ONBOARDING_PLAN_PATH = "/onboarding/plan"

/**
 * NO FREE TRIAL — the first invoice is charged at checkout (owner decision
 * 2026-09-07). The mirror of `plans.TRIALS_ENABLED`, which is the authority:
 * the SERVER decides what Stripe is told, and this constant only decides what
 * the plan step promises. Keep the two in step, or the screen offers a free
 * week the checkout does not grant.
 *
 * NOTHING WAS REMOVED. `TRIAL_DAYS`, `TRIAL_CREDITS`, `trialDaysLeft`, the
 * sidebar countdown, the trial-ending email and the `trialing` status that
 * `subscriptionGrantsAccess` honours are all exactly as they were — this is a
 * switch, in the same shape as `BILLING_ENABLED`, not a deletion. A company
 * that is mid-trial today keeps it and reads correctly everywhere.
 *
 * TO SELL TRIALS AGAIN: set this true and `plans.TRIALS_ENABLED` to True.
 */
export const TRIALS_ENABLED = false

/** Days before the first charge on a company's FIRST subscription, when
 *  trials are on. Mirrors `plans.TRIAL_DAYS`; the backend decides whether a
 *  given checkout actually gets one (on `TRIALS_ENABLED` and `first_paid_at`),
 *  this is only what we promise on screen. */
export const TRIAL_DAYS = 7

/**
 * What the trial itself grants — NOT the plan's monthly allowance.
 *
 * Mirrors `plans.TRIAL_CREDITS`, which is the authority and carries the full
 * reasoning. A trial is a flat figure whichever plan is being trialled: enough
 * for one pass through the DOCUMENT loop (a few chats, an ask, an evidence
 * brief, a PRD ≈ 48) and not a free month of whatever tier was picked.
 *
 * A prototype costs 50 on its own and is deliberately out of reach on a trial
 * — that is the intended shape, so copy near this number must not promise one.
 *
 * The plan cards go on quoting their own monthly credits, because that is what
 * the customer gets from day eight — but a card promising "756 credits a month"
 * with no mention of this reads as a promise about the free week, which it is
 * not.
 */
export const TRIAL_CREDITS = 50
