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
import { SettingsSection, SettingsMessage } from "./SettingsLayout"

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

/** Plans a customer can buy here. Team is invoiced and Enterprise goes through
 *  sales, so both are shown as a contact card rather than a checkout button —
 *  the backend rejects a checkout naming either. */
const SELF_SERVE = [
  {
    id: "starter",
    label: "Starter",
    monthly: 59,
    annual: 590,
    credits: 500,
    blurb: "For one person shipping their first products.",
  },
  {
    id: "product_builder",
    label: "Product Builder",
    monthly: 99,
    annual: 990,
    credits: 2500,
    blurb: "For a product manager running a full portfolio.",
    featured: true,
  },
]

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
): { kind: "error" | "success"; text: string } | null {
  switch (status) {
    case "past_due":
      return {
        kind: "error",
        text:
          "Your last payment did not go through. We are retrying — update your card to avoid interruption.",
      }
    case "canceled":
      return {
        kind: "error",
        text: "Your subscription has ended. Choose a plan to keep generating.",
      }
    case "unpaid":
      return {
        kind: "error",
        text: "Your subscription is unpaid. Settle the outstanding invoice to restore access.",
      }
    case "incomplete":
      return {
        kind: "error",
        text: "Your payment needs confirming. Reopen checkout to finish it.",
      }
    default:
      return null
  }
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
  const notice = statusNotice(d.subscription_status)
  const refundDays = refundWindowRemaining(d.first_paid_at, d.refund_window_days)
  const used =
    d.monthly_credits && d.credit_balance !== null
      ? Math.max(0, d.monthly_credits - d.credit_balance)
      : 0
  const pct =
    d.monthly_credits && d.credit_balance !== null
      ? Math.max(0, Math.min(100, (d.credit_balance / d.monthly_credits) * 100))
      : 100

  return (
    <>
      <SettingsSection title="Billing" sub="Plan, credits, and invoices.">
        {p.error && <SettingsMessage kind="error">{p.error}</SettingsMessage>}
        {p.notice && <SettingsMessage kind="success">{p.notice}</SettingsMessage>}
        {notice && <SettingsMessage kind={notice.kind}>{notice.text}</SettingsMessage>}

        {!d.billing_configured && (
          <p className="settings-placeholder bill-inert">
            Payments are not enabled in this environment, so your plan is shown
            read-only.
          </p>
        )}

        <div className="bill-hero">
          <div className="bill-hero-plan">
            <span className="bill-hero-label">Current plan</span>
            <strong className="bill-hero-name">{d.plan_label}</strong>
            {d.current_period_end && (
              <span className="bill-hero-meta">
                Renews {formatDate(d.current_period_end)}
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
              Update your card, download invoices, or cancel.
            </span>
          </div>
        )}

        {refundDays !== null && (
          <p className="bill-refund">
            You are within your {d.refund_window_days}-day refund window —{" "}
            {refundDays} {refundDays === 1 ? "day" : "days"} left. Cancel in the
            portal above and contact us and we will refund your first payment.
          </p>
        )}
      </SettingsSection>

      {/* ── Plans ─────────────────────────────────────────────────── */}
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
          {SELF_SERVE.map((plan) => {
            const current = d.plan === plan.id
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
                      : d.has_subscription
                        ? "Switch"
                        : "Choose"}
                </button>
              </div>
            )
          })}

          <div className="bill-plan bill-plan-contact">
            <h4 className="bill-plan-name">Team &amp; Enterprise</h4>
            <div className="bill-plan-price">
              <span className="amt">Custom</span>
            </div>
            <p className="bill-plan-blurb">
              Pooled credits across your whole team, SSO, and a contract.
            </p>
            <p className="bill-plan-credits">15,000+ credits/mo, pooled</p>
            <a className="btn btn-secondary" href="mailto:sales@sprntly.ai">
              Talk to sales
            </a>
          </div>
        </div>

        {p.error && <SettingsMessage kind="error">{p.error}</SettingsMessage>}

        <p className="bill-code-hint">
          Have a discount code? Enter it at checkout.
        </p>
      </SettingsSection>

      {/* ── Top up ────────────────────────────────────────────────── */}
      {!d.unlimited && (
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

      {/* ── Referrals ─────────────────────────────────────────────── */}
      <SettingsSection
        title="Invite a friend"
        sub={`Earn ${d.referral_reward_credits.toLocaleString()} credits when they subscribe.`}
      >
        <p className="bill-referral-copy">
          Invite up to {d.referrals.length + d.referral_invites_remaining} people.
          Each invite gives you a link to send them yourself. When one of them
          starts a paid plan, we add{" "}
          {d.referral_reward_credits.toLocaleString()} credits to your balance —
          once their first payment goes through.
        </p>

        {d.referral_invites_remaining > 0 ? (
          <div className="bill-invite">
            <input
              type="email"
              placeholder="friend@company.com"
              aria-label="Friend's email address"
              value={p.inviteEmail}
              onChange={(e) => p.onInviteEmail(e.target.value)}
            />
            <button
              type="button"
              className="btn btn-primary"
              disabled={p.busy !== null || !p.inviteEmail.trim()}
              onClick={p.onInvite}
            >
              {p.busy === "invite" ? "Creating…" : "Create invite link"}
            </button>
            <span className="bill-invite-left">
              {d.referral_invites_remaining} left
            </span>
          </div>
        ) : (
          <p className="settings-placeholder">
            You have used all your invites.
          </p>
        )}

        {d.referrals.length > 0 && (
          <ul className="bill-referrals">
            {d.referrals.map((r) => (
              <li key={r.id} className="bill-referral">
                <span className="bill-referral-email">{r.invitee_email}</span>
                {r.status === "pending" && (
                  <input
                    className="bill-referral-link"
                    readOnly
                    value={referralLink(r.code)}
                    aria-label={`Invite link for ${r.invitee_email}`}
                    onFocus={(e) => e.currentTarget.select()}
                  />
                )}
                <span className={`bill-referral-status s-${r.status}`}>
                  {r.status === "rewarded"
                    ? `+${(r.reward_credits ?? 0).toLocaleString()} credits`
                    : r.status === "signed_up"
                      ? "Signed up"
                      : r.status === "void"
                        ? "Not eligible"
                        : "Invited"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </SettingsSection>

      {/* ── Costs + history ───────────────────────────────────────── */}
      <SettingsSection title="What things cost" sub="In credits, per action.">
        <ul className="bill-costs">
          {Object.entries(d.action_costs)
            .sort((a, b) => a[1] - b[1])
            .map(([slug, cost]) => (
              <li key={slug} className="bill-cost">
                <span>{featureLabel(slug)}</span>
                <span className="bill-cost-n">
                  {cost} {cost === 1 ? "credit" : "credits"}
                </span>
              </li>
            ))}
        </ul>
        <p className="bill-costs-note">
          Scheduled work — your Top Insights brief, connector syncs, and keeping
          the knowledge graph current — does not use credits.
        </p>
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

  const onInvite = async () => {
    const email = inviteEmail.trim()
    if (!email) return
    setBusy("invite")
    setError(null)
    try {
      const created = await billingApi.invite(email)
      setInviteEmail("")
      setNotice(`Invite link ready for ${created.invitee_email} — copy it below.`)
      setData((prev) =>
        prev
          ? {
              ...prev,
              referrals: [
                {
                  id: created.id,
                  invitee_email: created.invitee_email,
                  status: created.status,
                  code: created.code,
                  reward_credits: created.reward_credits,
                  created_at: new Date().toISOString(),
                },
                ...prev.referrals,
              ],
              referral_invites_remaining: created.invites_remaining,
            }
          : prev,
      )
    } catch (e) {
      setError(
        e instanceof ApiError
          ? apiErrorMessage(e.status, e.body)
          : "Could not send that invite.",
      )
    } finally {
      setBusy(null)
    }
  }

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
      onInterval={setInterval}
      onSubscribe={(plan) => go(plan, () => billingApi.checkout(plan, interval))}
      onPortal={() => go("portal", () => billingApi.portal())}
      onTopup={(amount) => go("topup", () => billingApi.topup(amount))}
      onInviteEmail={setInviteEmail}
      onInvite={onInvite}
      onCustomTopup={setCustomTopup}
    />
  )
}
