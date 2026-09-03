/**
 * Does this company get to use the product right now?
 *
 * A DELIBERATE MIRROR of `backend/app/billing/plans.py`
 * (`ACTIVE_SUBSCRIPTION_STATUSES` / `subscription_grants_access`). The backend
 * is the authority — `enforce.bill` is what actually refuses work, and nothing
 * here can grant access the server will not honour. This copy exists so the
 * onboarding gate can decide where to send someone from the company row it has
 * already fetched, without a round trip on every sign-in.
 *
 * Keep the two in step. The rule is small on purpose: if it ever needs more
 * than these few lines, it belongs on the server behind one endpoint instead of
 * being copied at all.
 */

/**
 * PAYMENTS ARE HIDDEN. The one switch, and the whole of it.
 *
 * Nothing below is deleted and no billing file was removed — the plan screen,
 * the Billing pane, the trial pill, the Stripe client and the credit ledger
 * are all still here, exactly as they were. While this is false every gate in
 * this module simply answers "yes, carry on": nothing is ever refused, no
 * subscription can lapse, and no balance can run out.
 *
 * WHAT IS HIDDEN IS THE COMPULSION, NOT THE SCREENS (owner decision). Settings
 * still shows Billing and a curious user can read it and even start a checkout
 * — it is only that nothing they see there stands between them and the
 * product. The two places that DID stand in the way are gone with the gates:
 * the onboarding plan step, which is reached only by a guard redirect, and the
 * sidebar trial countdown, which checks this flag at its call site along with
 * the product-tour step anchored to it. `trialDaysLeft` itself stays honest —
 * the Billing pane still has to report a trial Stripe really did record.
 *
 * TO BRING PAYMENTS BACK: set this to true, and its backend mirror
 * `BILLING_ENABLED` in `backend/app/billing/enforce.py` to True. That is the
 * entire revert — two constants, no other edit, no behaviour lost in between.
 *
 * A constant rather than a `NEXT_PUBLIC_*` env var on purpose: web is a static
 * export, so an env var is inlined at build time anyway and buys nothing but a
 * second place for the two sides to disagree.
 */
export const BILLING_ENABLED = false

/** Stripe's vocabulary, stored verbatim. `past_due` still grants access —
 *  cutting a paying customer off while Smart Retries work the card is how a
 *  bounced card becomes churn. */
const ACTIVE_SUBSCRIPTION_STATUSES = new Set(["active", "trialing", "past_due"])

/** Plans that were never sold through Stripe, so they carry a null status and
 *  must not be gated on one. */
const BYPASS_PLANS = new Set(["legacy", "enterprise"])

export function subscriptionGrantsAccess(
  plan: string | null | undefined,
  status: string | null | undefined,
): boolean {
  if (BYPASS_PLANS.has((plan ?? "").trim().toLowerCase())) return true
  return ACTIVE_SUBSCRIPTION_STATUSES.has((status ?? "").trim().toLowerCase())
}

/** The onboarding payment gate's question, named for what it is at the call
 *  site: has this company put a card down yet? */
export function companyHasPaid(
  company: { plan?: string | null; subscription_status?: string | null } | null,
): boolean {
  // Hidden: nobody is ever asked for a card, so everybody has already paid.
  if (!BILLING_ENABLED) return true
  if (!company) return false
  return subscriptionGrantsAccess(company.plan, company.subscription_status)
}

/**
 * Days left in a trial, or null when the company is not trialling.
 *
 * `current_period_end` IS the trial end while Stripe reports `trialing` — a
 * trialling subscription's first period runs to the first charge, which is
 * exactly the date we want to count down to.
 *
 * ROUNDS UP. At twenty-three hours remaining a reader has one more day, not
 * zero, and "0 days left" beside a subscription that has not charged anyone
 * yet reads like a fault. The floor is 1 for the same reason: while the status
 * still says `trialing` the trial is still running, whatever the clock says
 * about the boundary and whatever skew sits between us and Stripe.
 */
export function trialDaysLeft(
  company: { subscription_status?: string | null; current_period_end?: string | null } | null,
  now: Date = new Date(),
): number | null {
  if (!company) return null
  if ((company.subscription_status ?? "").trim().toLowerCase() !== "trialing") return null
  const end = company.current_period_end
  if (!end) return null
  const at = new Date(end)
  if (Number.isNaN(at.getTime())) return null
  return Math.max(1, Math.ceil((at.getTime() - now.getTime()) / 86_400_000))
}

/** "1 day left" / "6 days left" — the countdown, in the one place that decides
 *  how it is worded, so the billing screen and the rail can never disagree. */
export function trialLabel(days: number): string {
  return `${days} ${days === 1 ? "day" : "days"} left`
}

/**
 * Is this company locked out, and how hard?
 *
 * `canceled` and `unpaid` only. NOT `past_due` — Stripe is still working the
 * card there and the customer may not know yet; locking someone out mid-retry
 * is how a bounced card becomes a cancellation. `companyHasPaid` already draws
 * that line, so this is the mode on top of it.
 *
 * A ROUTING decision, not a security boundary. `enforce.bill` on the backend is
 * what actually refuses work, and nothing here can grant access the server will
 * not honour — this only stops us rendering a working-looking app that isn't.
 */
export type LockMode = "off" | "read_only" | "hard"

export function lockModeFor(
  company: { plan?: string | null; subscription_status?: string | null } | null,
  configured: string | null | undefined,
): LockMode {
  const mode = (configured ?? "off").trim().toLowerCase()
  if (mode !== "read_only" && mode !== "hard") return "off"
  // Nothing to lock: a paying, trialling, retrying, legacy or enterprise
  // company all read as paid.
  if (companyHasPaid(company)) return "off"
  return mode
}
