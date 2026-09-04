"use client"

// Explicit, unlike the sibling panes: vitest transforms JSX with esbuild's
// CLASSIC runtime (tsconfig sets `jsx: "preserve"`), so a component file with
// no React binding throws "React is not defined" the moment a test renders it.
// Redundant in the Next.js build, which uses the automatic runtime.
import * as React from "react"
import { useEffect, useState } from "react"
import {
  ApiError,
  apiErrorMessage,
  billingApi,
  type BillingInterval,
  type BillingLedgerEntry,
  type BillingSummary,
} from "../../../../lib/api"
import {
  SALES_CONTACT,
  SELF_SERVE_PLANS,
  TEAM_MONTHLY_CREDITS,
  TRIAL_DAYS,
} from "../../../../lib/billingPlans"
import { trialDaysLeft, trialLabel } from "../../../../lib/billingAccess"
import {
  SettingsSection,
  SettingsMessage,
  SettingsPaneNav,
  type SettingsPaneNavItem,
} from "./SettingsLayout"

/**
 * Billing — plan, credits, top-ups, and referrals.
 *
 * Replaces the "Coming soon" stub that stood here since 2026-06-01.
 *
 * WHAT IS DELIBERATELY NOT HERE: payment method, invoice history, receipts,
 * and cancellation. All four live in Stripe's hosted customer portal, one
 * button away. Rebuilding them would mean putting card fields in a static
 * export with no server behind it, and would replace a PCI-compliant surface
 * Stripe maintains with one we would have to.
 *
 * A CREDIT IS AN ACTION, not a token and not a dollar. That decision is what
 * makes this screen legible: "2,340 credits" and "a PRD costs 25" are both
 * readable without knowing anything about models, and the cost table is
 * rendered from the backend's own price list so the two can never drift.
 *
 * The View is pure (props in, JSX out) so the states that matter — no
 * subscription, out of credits, unlimited, Stripe absent — are unit-testable
 * without the network. The default export wraps it with the API wiring, the
 * same split UsageSettings uses.
 */

/** Plans sold by conversation rather than by checkout.
 *
 *  Team is ANNUAL-ONLY. $20,000/yr is not a monthly product, and putting it
 *  beside $59 and $99 monthly prices invites a comparison that makes no sense
 *  — so it appears only once the reader has switched to annual and is already
 *  thinking in yearly figures. Enterprise has no interval at all and is always
 *  shown.
 *
 *  Neither has a checkout button: `plans.SELF_SERVE_PLANS` excludes both and
 *  the backend rejects a checkout naming either, so a button here would be a
 *  400 waiting to happen. */
const CONTACT_PLANS: {
  id: string
  label: string
  price: string
  per?: string
  blurb: string
  credits: string
  annualOnly?: boolean
}[] = [
  {
    id: "team",
    label: "Team",
    price: "$20,000",
    per: "/yr",
    blurb: "For a whole product org sharing one pool of credits.",
    credits: `${TEAM_MONTHLY_CREDITS.toLocaleString()} credits/mo, pooled`,
    annualOnly: true,
  },
  {
    id: "enterprise",
    label: "Enterprise",
    price: "Custom",
    blurb: "Custom volume, SSO, security review, and a contract.",
    credits: "Credits to fit your usage",
  },
]

/** The contact plans visible at a given interval. */
export function contactPlansFor(interval: BillingInterval) {
  return CONTACT_PLANS.filter((p) => !p.annualOnly || interval === "annual")
}

/** Human labels for the backend's feature slugs. An unknown slug falls through
 *  de-slugified rather than being dropped, so a newly priced surface reads
 *  sensibly before anyone updates this map. */
const FEATURE_LABELS: Record<string, string> = {
  chat: "Chat message",
  ask: "Ask / research",
  report: "Report",
  evidence: "Evidence brief",
  crucible: "Goal Analysis",
  prototype_iterate: "Prototype change",
  competitive_intel: "Competitive report",
  prd: "PRD",
  multi_agent: "Multi-agent PRD",
  prototype: "Prototype",
}

export function featureLabel(slug: string): string {
  return (
    FEATURE_LABELS[slug] ??
    slug.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase())
  )
}

/** Ledger reasons as a reader would describe them. */
const REASON_LABELS: Record<string, string> = {
  monthly_grant: "Monthly credits",
  spend: "Used",
  referral: "Referral reward",
  topup: "Credits purchased",
  refund: "Refund",
  adjustment: "Adjustment",
}

/** One plan change, said in a sentence.
 *
 *  The row has to read on its own: "Product Builder" alone does not tell you
 *  whether they arrived, upgraded or lapsed, and that is the whole question a
 *  history is opened to answer. */
/** Minor units to something a person reads. Stripe reports cents; dividing
 *  here rather than storing dollars keeps the stored figure exact. */
/** A rewarded row should carry its own credit figure; fall back to the
 *  current reward so an older row never renders a blank. */
function plans_reward(d: { referral_reward_credits: number }): number {
  return d.referral_reward_credits
}

export function formatMoney(cents: number, currency = "usd"): string {
  const amount = (Number(cents) || 0) / 100
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: (currency || "usd").toUpperCase(),
    }).format(amount)
  } catch {
    // An unknown currency code must not blank out an invoice row.
    return `${amount.toFixed(2)} ${(currency || "").toUpperCase()}`.trim()
  }
}

export function planChangeLabel(e: {
  plan: string
  status: string | null
  previous_plan: string | null
  previous_status: string | null
}): string {
  const to = PLAN_LABELS[e.plan] ?? e.plan
  const from = e.previous_plan ? (PLAN_LABELS[e.previous_plan] ?? e.previous_plan) : null

  // Status moved, plan did not — a lapse, a recovery, a trial converting.
  if (from && from === to) {
    if (e.status === "canceled") return `${to} ended`
    if (e.status === "past_due") return `${to} payment failed`
    if (e.status === "unpaid") return `${to} unpaid`
    if (e.previous_status === "trialing" && e.status === "active") {
      return `${to} trial converted`
    }
    return `${to} — ${e.status ?? "updated"}`
  }
  // No previous status at all: this is where their billing history begins.
  if (!e.previous_status) {
    return e.status === "trialing" ? `Started ${to} trial` : `Subscribed to ${to}`
  }
  return `${from} → ${to}`
}

/** Plan keys are stored, not labels — so the history reads in the same words
 *  the plan cards use rather than in database values. */
const PLAN_LABELS: Record<string, string> = {
  starter: "Starter",
  product_builder: "Product Builder",
  team: "Team",
  enterprise: "Enterprise",
  legacy: "Legacy",
}

export function entryLabel(entry: BillingLedgerEntry): string {
  if (entry.reason === "spend" && entry.feature) return featureLabel(entry.feature)
  return REASON_LABELS[entry.reason] ?? entry.reason
}

/** The link a referrer shares. Built client-side from the current origin so it
 *  is correct in dev, staging and prod without another config value.
 *
 *  A LINK, NOT AN EMAIL, deliberately. The obvious move was to reuse
 *  `send_invite_email`, and that would have been wrong: it creates a Supabase
 *  user and lands them as a MEMBER of the referrer's company, which is the
 *  precise confusion a referral must avoid — the friend is signing up for their
 *  own workspace. People share referral links in DMs anyway, and this way the
 *  pane does not claim to send mail it never sends. */
export function referralLink(code: string, origin?: string): string {
  const base =
    origin ?? (typeof window === "undefined" ? "" : window.location.origin)
  return `${base}/sign-up?ref=${encodeURIComponent(code)}`
}

function formatDate(iso: string | null): string {
  if (!iso) return "—"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return "—"
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}

/** Whether the 7-day cancel-and-refund window is still open.
 *
 *  Shown as information, never as a button: cancelling is self-serve in the
 *  Stripe portal, but the refund itself is approved by a person after seeing
 *  how many credits were consumed. Promising an instant refund here and then
 *  not delivering one is worse than describing the real process. */
export function refundWindowRemaining(
  firstPaidAt: string | null,
  windowDays: number,
  now: Date = new Date(),
): number | null {
  if (!firstPaidAt) return null
  const paid = new Date(firstPaidAt)
  if (Number.isNaN(paid.getTime())) return null
  const elapsedDays = (now.getTime() - paid.getTime()) / 86_400_000
  const left = Math.ceil(windowDays - elapsedDays)
  return left > 0 ? left : null
}

/** The subscription states worth saying something about. `active` needs no
 *  banner; the rest each need a different next action from the user. */
export function statusNotice(
  status: string | null,
  hasSubscription = true,
): { kind: "error" | "success"; text: string; subscribe?: boolean } | null {
  switch (status) {
    case "past_due":
      // Retries are still running and the plan is already chosen — the fix is
      // a working card, not a different plan. So no Subscribe link here; the
      // portal button above it is the right door.
      return {
        kind: "error",
        text:
          "Your last payment did not go through. We are retrying — update your card to avoid interruption.",
      }
    case "canceled":
      return {
        kind: "error",
        text: "Your subscription has ended.",
        subscribe: true,
      }
    case "unpaid":
      // Retries are exhausted. Stripe will not resurrect this subscription, so
      // "settle the invoice" was advice with nowhere to act on it — buying
      // again is what actually restores access.
      return {
        kind: "error",
        text: "Your subscription is unpaid and access has stopped.",
        subscribe: true,
      }
    case "incomplete":
      return {
        kind: "error",
        text: "Your payment needs confirming. Reopen checkout to finish it.",
        subscribe: true,
      }
    default:
      // NEVER SUBSCRIBED — the case that showed nothing at all. A company that
      // finished onboarding without a plan (or had one cleared) landed on a
      // billing screen with no banner, no explanation, and a credits meter
      // reading zero. Silence is the worst answer here: it is the one state
      // where the user has to do something and no other screen will tell them.
      if (!hasSubscription) {
        return {
          kind: "error",
          text: "You have not subscribed yet.",
          subscribe: true,
        }
      }
      return null
  }
}

/** The pane's sections, in nav order. "billing" is the landing view because
 *  it answers the two questions people actually open this screen for: what am
 *  I on, and how much is left. */
export type BillingTab =
  | "billing"
  | "plans"
  | "referrals"
  | "history"

export const BILLING_TAB_LABELS: Record<BillingTab, string> = {
  billing: "Billing",
  plans: "Plans",
  referrals: "Invite a friend",
  history: "History",
}

/** Nav items for a given account state.
 *
 *  Built from the data rather than hardcoded so the list never offers a view
 *  that cannot exist: an unlimited plan has no balance to top up, so the
 *  top-up entry is absent rather than present-and-empty. The hints carry the
 *  one number each section is about, which is most of the value of the nav —
 *  you can read your balance and invites left without opening anything. */
export function billingTabs(d: BillingSummary): SettingsPaneNavItem[] {
  const items: SettingsPaneNavItem[] = [
    {
      id: "billing",
      label: BILLING_TAB_LABELS.billing,
      hint: d.unlimited ? "Unlimited" : (d.credit_balance ?? 0).toLocaleString(),
    },
    { id: "plans", label: BILLING_TAB_LABELS.plans, hint: d.plan_label },
  ]
  items.push(
    {
      id: "referrals",
      label: BILLING_TAB_LABELS.referrals,
      // Uncapped shows no hint at all — "∞ left" is noise on a nav pill.
      hint: d.referral_invites_remaining
        ? `${d.referral_invites_remaining} left`
        : undefined,
    },
    {
      id: "history",
      label: BILLING_TAB_LABELS.history,
      hint: d.history.length ? String(d.history.length) : undefined,
    },
  )
  return items
}

/** Resolve the tab actually shown.
 *
 *  Guards the case where the selected tab stops existing under the current
 *  account state — switching to an Enterprise plan removes "Buy more credits"
 *  while you are standing on it, which would otherwise render a blank pane. */
export function resolveBillingTab(
  requested: BillingTab,
  items: SettingsPaneNavItem[],
): BillingTab {
  return items.some((i) => i.id === requested) ? requested : "billing"
}

export type BillingView = {
  data: BillingSummary | null
  loading: boolean
  restricted: boolean
  error: string | null
  notice: string | null
  interval: BillingInterval
  busy: string | null
  inviteEmail: string
  customTopup: string
  tab: BillingTab
  onTab: (t: BillingTab) => void
  /** Two-step confirm: the first click arms, the second cancels. A
   *  destructive action should not fire on a single click, and an inline
   *  confirm beats a modal for one button. */
  confirmingCancel: boolean
  onConfirmCancel: (v: boolean) => void
  onCancelSubscription: () => void
  onResumeSubscription: () => void
  onInterval: (i: BillingInterval) => void
  onSubscribe: (plan: string) => void
  onPortal: () => void
  onTopup: (amountUsd: number) => void
  onInviteEmail: (v: string) => void
  onInvite: () => void
  onCustomTopup: (v: string) => void
}

export function BillingSettingsView(p: BillingView) {
  if (p.restricted) {
    return (
      <SettingsSection title="Billing" sub="Plan, credits, and invoices.">
        <p className="settings-placeholder">
          Billing is managed by your workspace owner or an admin.
        </p>
      </SettingsSection>
    )
  }

  if (p.loading && !p.data) {
    return (
      <SettingsSection title="Billing" sub="Plan, credits, and invoices.">
        <p className="settings-placeholder">Loading…</p>
      </SettingsSection>
    )
  }

  if (!p.data) {
    return (
      <SettingsSection title="Billing" sub="Plan, credits, and invoices.">
        {p.error && <SettingsMessage kind="error">{p.error}</SettingsMessage>}
      </SettingsSection>
    )
  }

  const d = p.data
  const notice = statusNotice(d.subscription_status, d.has_subscription)
  const used =
    d.monthly_credits && d.credit_balance !== null
      ? Math.max(0, d.monthly_credits - d.credit_balance)
      : 0
  const pct =
    d.monthly_credits && d.credit_balance !== null
      ? Math.max(0, Math.min(100, (d.credit_balance / d.monthly_credits) * 100))
      : 100

  const trialDays = trialDaysLeft(d)
  const items = billingTabs(d)
  // Guarded rather than trusted: switching to an unlimited plan removes the
  // top-up view while you are standing on it.
  const tab = resolveBillingTab(p.tab, items)

  return (
    <SettingsPaneNav
      items={items}
      active={tab}
      onSelect={(id) => p.onTab(id as BillingTab)}
      label="Billing sections"
    >
      {tab === "billing" && (
      <SettingsSection title="Billing" sub="Plan, credits, and invoices.">
        {p.error && <SettingsMessage kind="error">{p.error}</SettingsMessage>}
        {p.notice && <SettingsMessage kind="success">{p.notice}</SettingsMessage>}
        {notice && (
          <SettingsMessage kind={notice.kind}>
            {notice.text}
            {notice.subscribe && (
              <>
                {" "}
                {/* The fix, one click from the sentence that describes the
                    problem. It opens the Plans view rather than starting a
                    checkout: which plan is still the user's choice, and a
                    banner that silently began buying something would be
                    worse than no banner at all. */}
                <button
                  type="button"
                  className="bill-subscribe-link"
                  data-testid="billing-subscribe-now"
                  onClick={() => p.onTab("plans")}
                >
                  Pay now
                </button>
              </>
            )}
          </SettingsMessage>
        )}

        {!d.billing_configured && (
          <p className="settings-placeholder bill-inert">
            Payments are not enabled in this environment, so your plan is shown
            read-only.
          </p>
        )}

        {/* ON TRIAL. The plan name alone is a half-truth during a trial — it
            says Starter and says nothing about the fact that nobody has been
            charged yet, or when that changes. Both facts belong together, and
            the DATE matters as much as the countdown: "6 days left" tells you
            how long, "then $59 on 7 Sep" tells you what actually happens. */}
        {trialDays != null && (
          <div className="bill-trial" data-testid="billing-trial">
            <span className="bill-trial-chip">Free trial</span>
            <span className="bill-trial-copy">
              <strong>{trialLabel(trialDays)}</strong> on {d.plan_label}. Your
              card is saved and nothing has been charged
              {d.current_period_end
                ? ` — the first payment is on ${formatDate(d.current_period_end)}.`
                : "."}{" "}
              Cancel before then and you pay nothing.
            </span>
          </div>
        )}

        <div className="bill-hero">
          <div className="bill-hero-plan">
            <span className="bill-hero-label">Current plan</span>
            <strong className="bill-hero-name">{d.plan_label}</strong>
            {d.current_period_end && (
              <span className="bill-hero-meta">
                {d.cancel_at_period_end
                  ? `Ends ${formatDate(d.cancels_at ?? d.current_period_end)}`
                  : trialDays != null
                    ? `First payment ${formatDate(d.current_period_end)}`
                    : `Renews ${formatDate(d.current_period_end)}`}
              </span>
            )}
          </div>

          <div className="bill-hero-credits">
            <span className="bill-hero-label">Credits</span>
            {d.unlimited ? (
              <strong className="bill-hero-name">Unlimited</strong>
            ) : (
              <>
                <strong className="bill-hero-name">
                  {(d.credit_balance ?? 0).toLocaleString()}
                  {d.monthly_credits !== null && (
                    <span className="bill-hero-of">
                      {" "}
                      / {d.monthly_credits.toLocaleString()}
                    </span>
                  )}
                </strong>
                {d.monthly_credits !== null && (
                  <>
                    <div
                      className="bill-meter"
                      role="progressbar"
                      aria-valuemin={0}
                      aria-valuemax={d.monthly_credits}
                      aria-valuenow={d.credit_balance ?? 0}
                      aria-label="Credits remaining this period"
                    >
                      <div className="bill-meter-fill" style={{ width: `${pct}%` }} />
                    </div>
                    <span className="bill-hero-meta">
                      {used.toLocaleString()} used this period
                    </span>
                  </>
                )}
              </>
            )}
          </div>
        </div>

        {d.has_subscription && d.billing_configured && (
          <>
            <div className="bill-actions">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={p.onPortal}
                disabled={p.busy !== null}
              >
                {p.busy === "portal" ? "Opening…" : "Manage payment & invoices"}
              </button>
              <span className="bill-actions-hint">
                Update your card or download invoices.
              </span>
            </div>

            {d.cancel_at_period_end ? (
              // Pending cancellation. The message leads with what they KEEP —
              // they paid for this period and none of it is being taken away.
              <div className="bill-ending">
                <div className="bill-ending-copy">
                  <strong>Your plan ends {formatDate(d.cancels_at)}.</strong>{" "}
                  You keep {d.unlimited ? "full access" : "your credits"} and
                  everything on your plan until then. Nothing more will be
                  charged.
                </div>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={p.onResumeSubscription}
                  disabled={p.busy !== null}
                >
                  {p.busy === "resume" ? "Resuming…" : "Keep my plan"}
                </button>
              </div>
            ) : p.confirmingCancel ? (
              <div className="bill-ending">
                <div className="bill-ending-copy">
                  <strong>Cancel your subscription?</strong> You will keep your
                  plan and{" "}
                  {d.unlimited
                    ? "access"
                    : `${(d.credit_balance ?? 0).toLocaleString()} credits`}{" "}
                  until {formatDate(d.current_period_end)}, then it ends. You
                  can undo this any time before then.
                </div>
                <div className="bill-ending-actions">
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => p.onConfirmCancel(false)}
                    disabled={p.busy !== null}
                  >
                    Keep my plan
                  </button>
                  <button
                    type="button"
                    className="bill-cancel-confirm"
                    onClick={p.onCancelSubscription}
                    disabled={p.busy !== null}
                  >
                    {p.busy === "cancel" ? "Cancelling…" : "Cancel subscription"}
                  </button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                className="bill-cancel-link"
                onClick={() => p.onConfirmCancel(true)}
                disabled={p.busy !== null}
              >
                Cancel subscription
              </button>
            )}
          </>
        )}

        {/* THE SEVEN DAYS ARE THE TRIAL, and nothing else.
            This used to say "you are within your 7-day refund window — cancel
            and contact us and we will refund your first payment". That was the
            PRE-TRIAL design (owner decision 2026-08-21: no free trial, pay and
            we refund inside a week). A trial replaced it, and during a trial
            nothing is deducted — so there is no payment to undo, and offering
            to refund one reads as though we are holding money we never took.

            The refund WINDOW is therefore gone from this screen entirely, not
            merely hidden while trialling: a customer who has converted has
            paid for a month they are using, and dangling a refund at them is
            an offer we did not intend to make. Staff can still issue one by
            hand from the admin panel — that path is unchanged; we just no
            longer advertise it as a window. */}
        {trialDays !== null && (
          <p className="bill-refund">
            You are within your {TRIAL_DAYS}-day trial — {trialDays}{" "}
            {trialDays === 1 ? "day" : "days"} left. Cancel any time before it
            ends and you pay nothing.
          </p>
        )}
      </SettingsSection>
      )}

      {tab === "billing" && !d.unlimited && (
        <SettingsSection
          title="Buy more credits"
          sub="Purchased credits are added on top of your monthly allowance."
        >
          <div className="bill-topups">
            {d.topup_presets.map((amount) => (
              <button
                key={amount}
                type="button"
                className="bill-topup"
                disabled={p.busy !== null || !d.billing_configured}
                onClick={() => p.onTopup(amount)}
              >
                <span className="bill-topup-amt">${amount}</span>
                <span className="bill-topup-credits">
                  {(amount * d.credits_per_topup_usd).toLocaleString()} credits
                </span>
              </button>
            ))}
          </div>

          {p.error && <SettingsMessage kind="error">{p.error}</SettingsMessage>}

          <div className="bill-topup-custom">
            <label htmlFor="bill-custom-amt">Custom amount (USD)</label>
            <input
              id="bill-custom-amt"
              type="number"
              inputMode="numeric"
              min={d.topup_min_usd}
              max={d.topup_max_usd}
              value={p.customTopup}
              placeholder={String(d.topup_min_usd)}
              onChange={(e) => p.onCustomTopup(e.target.value)}
            />
            <button
              type="button"
              className="btn btn-secondary"
              disabled={p.busy !== null || !d.billing_configured || !p.customTopup}
              onClick={() => p.onTopup(Number(p.customTopup))}
            >
              Buy
            </button>
          </div>
        </SettingsSection>
      )}

      {tab === "plans" && (
      <SettingsSection
        title="Plans"
        sub="Change plan at any time — Stripe prorates the difference."
      >
        <div className="bill-interval" role="group" aria-label="Billing interval">
          {(["monthly", "annual"] as BillingInterval[]).map((i) => (
            <button
              key={i}
              type="button"
              className={`bill-interval-tab${p.interval === i ? " on" : ""}`}
              aria-pressed={p.interval === i}
              onClick={() => p.onInterval(i)}
            >
              {i === "monthly" ? "Monthly" : "Annual"}
              {i === "annual" && <span className="bill-save">2 months free</span>}
            </button>
          ))}
        </div>

        <div className="bill-plans">
          {SELF_SERVE_PLANS.map((plan) => {
            // A LIVE SUBSCRIPTION ON THIS PLAN, not merely the plan column.
            //
            // `companies.plan` defaults to 'starter' for every company,
            // subscribed or not — it is a column default, not evidence of
            // anything. Comparing against it alone told a never-subscribed
            // company that Starter was its "current plan" and disabled the
            // button, so the one plan they wanted was the one plan they could
            // not buy. The banner sent them to Plans and Plans refused them.
            const current =
              d.plan === plan.id && d.has_subscription && d.has_access
            // THE PLAN YOU ARE ON, paid or not. `companies.plan` names it even
            // before a subscription exists, so an unpaid company on Starter
            // sees "Pay now" against Starter — settle the plan you already
            // have — and a move to anything else reads as a change.
            const onThisPlan = d.plan === plan.id
            const currentMonthly =
              SELF_SERVE_PLANS.find((x) => x.id === d.plan)?.monthly ?? 0
            // Labelled by PRICE, not by position. With two self-serve tiers the
            // "other" plan is an upgrade from Starter and a downgrade from
            // Product Builder, and calling both "Upgrade" would be plainly
            // wrong on screen half the time.
            const dearerThanCurrent = plan.monthly > currentMonthly
            const price = p.interval === "annual" ? plan.annual : plan.monthly
            return (
              <div
                key={plan.id}
                className={`bill-plan${plan.featured ? " featured" : ""}${
                  current ? " current" : ""
                }`}
              >
                {plan.featured && <span className="bill-plan-tag">Most popular</span>}
                <h4 className="bill-plan-name">{plan.label}</h4>
                <div className="bill-plan-price">
                  <span className="amt">${price}</span>
                  <span className="per">/{p.interval === "annual" ? "yr" : "mo"}</span>
                </div>
                <p className="bill-plan-blurb">{plan.blurb}</p>
                <p className="bill-plan-credits">
                  {plan.credits.toLocaleString()} credits/mo
                </p>
                <button
                  type="button"
                  className={`btn ${plan.featured ? "btn-primary" : "btn-secondary"}`}
                  disabled={current || p.busy !== null || !d.billing_configured}
                  onClick={() => p.onSubscribe(plan.id)}
                >
                  {current
                    ? "Current plan"
                    : p.busy === plan.id
                      ? "Opening…"
                      : onThisPlan
                        ? "Pay now"
                        : dearerThanCurrent
                          ? "Upgrade"
                          : "Downgrade"}
                </button>
              </div>
            )
          })}

          {contactPlansFor(p.interval).map((plan) => (
            <div
              key={plan.id}
              className={`bill-plan bill-plan-contact${
                d.plan === plan.id ? " current" : ""
              }`}
            >
              <h4 className="bill-plan-name">{plan.label}</h4>
              <div className="bill-plan-price">
                <span className="amt">{plan.price}</span>
                {plan.per && <span className="per">{plan.per}</span>}
              </div>
              <p className="bill-plan-blurb">{plan.blurb}</p>
              <p className="bill-plan-credits">{plan.credits}</p>
              <a
                className="btn btn-secondary"
                href={`mailto:${SALES_CONTACT}?subject=${encodeURIComponent(
                  `${plan.label} plan enquiry`,
                )}`}
              >
                {d.plan === plan.id ? "Contact us" : "Talk to sales"}
              </a>
            </div>
          ))}
        </div>

        {p.error && <SettingsMessage kind="error">{p.error}</SettingsMessage>}

        <p className="bill-code-hint">
          Have a discount code? Enter it at checkout.
        </p>
      </SettingsSection>
      )}

      {tab === "referrals" && (
      <SettingsSection
        title="Invite a friend"
        sub={`Earn ${d.referral_reward_credits.toLocaleString()} credits when they subscribe.`}
      >
        {/* ONE LINK, no form. It used to take an address and mint a code for
            that one person, which put a form between someone and sharing a
            link and capped how many people they could tell. The link is
            permanent: share it anywhere, and whoever signs up through it is
            attributed.

            The reward lands when they SUBSCRIBE, not when their first payment
            clears — with a trial in between, the old copy promised a referrer a
            week of silence. */}
        <p className="bill-referral-copy">
          Share this link with anyone. When someone signs up through it and
          starts a subscription, we add{" "}
          {d.referral_reward_credits.toLocaleString()} credits to your balance
          straight away — they do not have to wait out their trial first.
        </p>

        <div className="bill-reflink">
          <input
            className="bill-reflink-input"
            readOnly
            value={d.referral_url ?? ""}
            aria-label="Your referral link"
            data-testid="referral-link"
            /* Select-on-focus so it is copyable without the button too —
               clipboard access is blocked in some browsers and contexts. */
            onFocus={(e) => e.currentTarget.select()}
          />
          <button
            type="button"
            className="btn btn-secondary"
            data-testid="referral-copy"
            disabled={!d.referral_url}
            onClick={() => {
              if (!d.referral_url) return
              void navigator.clipboard?.writeText(d.referral_url).catch(() => {
                /* Blocked clipboard: the input is still selectable, so this is
                   a convenience failing, not the feature failing. */
              })
            }}
          >
            Copy
          </button>
        </div>

        {/* WHO HAS ARRIVED. No email column: nobody types an address any more,
            so a referral has no address to show — what it has is a date and
            whether it converted. */}
        {d.referrals.length > 0 ? (
          <ul className="bill-referrals">
            {d.referrals.map((r) => (
              <li key={r.id} className="bill-referral" data-testid="referral-row">
                <span className="bill-referral-when">
                  {formatDate(r.signed_up_at ?? r.created_at)}
                </span>
                <span className={`bill-referral-status s-${r.status}`}>
                  {r.status === "rewarded"
                    ? `+${(r.reward_credits ?? plans_reward(d)).toLocaleString()} credits`
                    : r.status === "signed_up"
                      ? "Signed up — no subscription yet"
                      : r.status === "void"
                        ? "Not eligible"
                        : "Signed up"}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="settings-placeholder">
            Nobody has signed up through your link yet.
          </p>
        )}
      </SettingsSection>
      )}

      {tab === "history" && (
      <>
      {/* SUBSCRIPTIONS first, then credits. What a customer opens billing to
          see is what they were charged, when, and for what — the credit ledger
          answers a different, finer question underneath it.

          Plan history (`subscription_history`) is deliberately NOT rendered.
          It is still served, because "when did they move tier" is a real
          support question, but it is not what a customer came here to read. */}
      <SettingsSection title="Subscriptions" sub="Every payment, most recent first.">
        {!d.invoices?.length ? (
          <p className="settings-placeholder">
            No payments yet. Each month's charge will appear here.
          </p>
        ) : (
          <ul className="bill-invoices">
            {d.invoices.map((inv) => (
              <li key={inv.id} className="bill-invoice" data-testid="invoice-row">
                <span className="bill-invoice-plan">
                  {PLAN_LABELS[inv.plan ?? ""] ?? inv.plan ?? "Subscription"}
                  {inv.invoice_number && (
                    <span className="bill-invoice-num">{inv.invoice_number}</span>
                  )}
                </span>
                <span className="bill-invoice-period">
                  {inv.period_start && inv.period_end
                    ? `${formatDate(inv.period_start)} — ${formatDate(inv.period_end)}`
                    : formatDate(inv.paid_at)}
                </span>
                <span className="bill-invoice-amount">
                  {formatMoney(inv.amount_paid_cents, inv.currency)}
                </span>
                {/* Stripe already renders the PDF, so "download invoice" needs
                    no document generator of ours. */}
                {inv.invoice_pdf_url ? (
                  <a
                    className="bill-invoice-pdf"
                    href={inv.invoice_pdf_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    PDF
                  </a>
                ) : (
                  <span className="bill-invoice-pdf" aria-hidden />
                )}
              </li>
            ))}
          </ul>
        )}
      </SettingsSection>

      <SettingsSection title="Credit history" sub="Most recent first.">
        {d.history.length === 0 ? (
          <p className="settings-placeholder">Nothing yet.</p>
        ) : (
          <ul className="bill-history">
            {d.history.map((entry) => (
              <li key={entry.id} className="bill-history-row">
                <span className="bill-history-what">{entryLabel(entry)}</span>
                <span className="bill-history-when">
                  {formatDate(entry.created_at)}
                </span>
                <span
                  className={`bill-history-delta ${entry.delta < 0 ? "out" : "in"}`}
                >
                  {entry.delta > 0 ? "+" : ""}
                  {entry.delta.toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </SettingsSection>
      </>
      )}
    </SettingsPaneNav>
  )
}

export function BillingSettings() {
  const [data, setData] = useState<BillingSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [restricted, setRestricted] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [interval, setInterval] = useState<BillingInterval>("monthly")
  const [busy, setBusy] = useState<string | null>(null)
  const [inviteEmail, setInviteEmail] = useState("")
  const [customTopup, setCustomTopup] = useState("")
  // Local, not a query param, on purpose: Stripe returns the browser here
  // after checkout and the overview — where the new plan and balance are — is
  // the right place to land, not wherever you were standing before.
  const [tab, setTab] = useState<BillingTab>("billing")
  const [confirmingCancel, setConfirmingCancel] = useState(false)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const s = await billingApi.summary()
        if (!cancelled) {
          setData(s)
          setError(null)
        }
      } catch (e) {
        if (cancelled) return
        if (e instanceof ApiError && e.status === 403) {
          setRestricted(true)
        } else {
          setError(
            e instanceof ApiError
              ? apiErrorMessage(e.status, e.body)
              : "Could not load billing.",
          )
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  // Checkout and the portal are HOSTED, so every action here ends in a
  // full-page redirect rather than a modal. `web/` is a static export with no
  // server, so there is no callback route to return to — Stripe comes back to
  // `?checkout=success` on this same settings page, which the effect above
  // then re-reads.
  const go = async (key: string, run: () => Promise<{ url: string }>) => {
    setBusy(key)
    setError(null)
    try {
      const { url } = await run()
      window.location.assign(url)
    } catch (e) {
      setError(
        e instanceof ApiError
          ? apiErrorMessage(e.status, e.body)
          : "Could not reach Stripe. Try again.",
      )
      setBusy(null)
    }
  }

  /** Cancel and resume both refresh from the server rather than guessing:
   *  the end date comes from Stripe's own period boundary, and a stale local
   *  guess on a billing screen is worse than a second of latency. */
  const mutate = async (
    key: string,
    run: () => Promise<unknown>,
    done: (s: BillingSummary) => BillingSummary,
  ) => {
    setBusy(key)
    setError(null)
    try {
      await run()
      setConfirmingCancel(false)
      setData((prev) => (prev ? done(prev) : prev))
      // Re-read so `cancels_at` is Stripe's date, not one we inferred.
      try {
        setData(await billingApi.summary())
      } catch {
        /* the optimistic shape above is close enough to render */
      }
    } catch (e) {
      setError(
        e instanceof ApiError
          ? apiErrorMessage(e.status, e.body)
          : "Could not update your subscription.",
      )
    } finally {
      setBusy(null)
    }
  }

  // The email invite handler is gone with its endpoint. A referral row is
  // created when someone ARRIVES through the link, so there is nothing for
  // this screen to POST — the link already exists.
  const onInvite = () => {}

  return (
    <BillingSettingsView
      data={data}
      loading={loading}
      restricted={restricted}
      error={error}
      notice={notice}
      interval={interval}
      busy={busy}
      inviteEmail={inviteEmail}
      customTopup={customTopup}
      tab={tab}
      onTab={setTab}
      confirmingCancel={confirmingCancel}
      onConfirmCancel={setConfirmingCancel}
      onCancelSubscription={() =>
        mutate("cancel", () => billingApi.cancel(), (s) => ({
          ...s,
          cancel_at_period_end: true,
          cancels_at: s.current_period_end,
        }))
      }
      onResumeSubscription={() =>
        mutate("resume", () => billingApi.resume(), (s) => ({
          ...s,
          cancel_at_period_end: false,
          cancels_at: null,
        }))
      }
      onInterval={setInterval}
      // TWO DIFFERENT OPERATIONS BEHIND ONE BUTTON, and they have to stay
      // different. Checkout always creates a NEW subscription — it cannot
      // replace one — so an existing customer sent through it ends up paying
      // for both plans. A switch modifies the subscription they already have:
      // the old price comes off, the new one goes on, and there is only ever
      // one subscription on the customer.
      //
      // The backend refuses the wrong one either way (409), so this decides
      // which door to knock on, not whether the door is locked.
      onSubscribe={(plan) => {
        const live = Boolean(data?.has_subscription && data?.has_access)
        if (!live) return go(plan, () => billingApi.checkout(plan, interval))
        return mutate(
          plan,
          () => billingApi.changePlan(plan, interval),
          (s) => ({ ...s, plan, plan_label: s.plan_label }),
        )
      }}
      onPortal={() => go("portal", () => billingApi.portal())}
      onTopup={(amount) => go("topup", () => billingApi.topup(amount))}
      onInviteEmail={setInviteEmail}
      onInvite={onInvite}
      onCustomTopup={setCustomTopup}
    />
  )
}
