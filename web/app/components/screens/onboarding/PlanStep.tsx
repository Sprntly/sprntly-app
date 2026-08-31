"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { ApiError, apiErrorMessage, billingApi } from "../../../lib/api"
import { useWorkspace } from "../../../context/WorkspaceContext"
import { ONBOARDING_STEP_SLUGS } from "../../../lib/onboarding/types"
import { SprntlyLockup } from "../../shared/SprntlyMark"
import {
  ONBOARDING_PLAN_PATH,
  SALES_CONTACT,
  SELF_SERVE_PLANS,
  TRIAL_CREDITS,
  TRIAL_DAYS,
} from "../../../lib/billingPlans"
import { companyHasPaid, subscriptionGrantsAccess } from "../../../lib/billingAccess"

/**
 * The PAYMENT GATE — "choose a plan" — between creating a company and the rest
 * of onboarding.
 *
 * UNNUMBERED, like `your-name` and `define-metrics`: it is NOT in
 * ONBOARDING_STEP_SLUGS, renders no progress dots, and touches none of the
 * 1-based `onboarding_step` index maths. That is not cosmetic. `onboarding_step`
 * is a persisted index into that array, so inserting a numbered step at
 * position two would silently shift every company already mid-flow — someone
 * sitting on step 5 (`product`) would resume on `workspace`.
 *
 * WHY HERE. The company row, the verified email and the profile all exist by
 * the time this renders, so an abandoned signup is still a lead we can reach.
 * And the alternative — letting someone finish ten steps and then refusing
 * their first generation with a 402 — spends all of a person's effort before
 * telling them the price.
 *
 * THE CARD IS TAKEN, THE MONEY IS NOT. Checkout runs with a trial (see
 * `plans.TRIAL_DAYS`), so a stranger is not charged before seeing a single
 * brief. Stripe still collects the card in subscription mode, so "card on
 * file" is not given up. The trial is granted server-side on `first_paid_at`
 * being null — nothing in this screen can ask for one.
 */

/** How long to keep asking whether the subscription landed, after Stripe sends
 *  the browser back. Checkout redirects the moment payment is accepted, but
 *  `subscription_status` is written by the WEBHOOK, which arrives on its own
 *  schedule. Trusting the redirect and forwarding immediately would bounce the
 *  user straight back here — the gate would re-read a company that has not been
 *  updated yet and conclude they had not paid. */
const CONFIRM_TIMEOUT_MS = 30_000
const CONFIRM_INTERVAL_MS = 1_500

type Phase =
  | { kind: "choosing" }
  | { kind: "redirecting" }
  /** Back from Stripe, waiting for the webhook to land. */
  | { kind: "confirming" }
  /** Paid, and the company row says so. */
  | { kind: "done" }

export function PlanStep() {
  const router = useRouter()
  const params = useSearchParams()
  const { workspace, orgRole, refresh } = useWorkspace()

  const [interval, setInterval] = useState<"monthly" | "annual">("monthly")
  const [plan, setPlan] = useState<string>(SELF_SERVE_PLANS[0]!.id)
  const [phase, setPhase] = useState<Phase>({ kind: "choosing" })
  const [error, setError] = useState<string | null>(null)
  const [slow, setSlow] = useState(false)

  const checkout = params.get("checkout")
  const cancelled = checkout === "cancelled"

  const advance = useCallback(() => {
    setPhase({ kind: "done" })
    router.push(`/onboarding/${ONBOARDING_STEP_SLUGS[1]}`)
  }, [router])

  // A company that already has a live subscription must never be shown a
  // buy-it-again screen: an admin who reloads this URL, an invited teammate
  // whose company already pays, or someone who simply came back. The gate is
  // company-level, which is exactly what makes the teammate case free.
  const alreadyPaid = useRef(false)
  useEffect(() => {
    if (alreadyPaid.current) return
    if (!workspace) return
    if (checkout === "success") return   // the confirm effect owns this case
    if (companyHasPaid(workspace)) {
      alreadyPaid.current = true
      advance()
    }
  }, [workspace, checkout, advance])

  // Back from a successful Checkout: poll until the webhook has written the
  // subscription onto the company, then move on.
  // Guarded against re-entry rather than trusting the dependency array. A
  // second poll loop would double the request rate and, if `advance` ever
  // changed identity on a re-render, never stop starting new ones.
  const confirming = useRef(false)
  useEffect(() => {
    if (checkout !== "success") return
    if (confirming.current) return
    confirming.current = true
    let stopped = false
    setPhase({ kind: "confirming" })

    const started = Date.now()
    const slowTimer = window.setTimeout(() => !stopped && setSlow(true), 6_000)

    async function poll() {
      while (!stopped && Date.now() - started < CONFIRM_TIMEOUT_MS) {
        try {
          const summary = await billingApi.summary()
          if (subscriptionGrantsAccess(summary.plan, summary.subscription_status)) {
            await refresh()
            if (!stopped) advance()
            return
          }
        } catch {
          /* A transient failure here is not a payment failure. Keep asking. */
        }
        await new Promise((r) => window.setTimeout(r, CONFIRM_INTERVAL_MS))
      }
      // Timed out, and THE GATE DOES NOT OPEN. It used to: the reasoning was
      // that Stripe only redirects to success_url after a payment is accepted,
      // so letting them through was kinder than asking anyone to pay twice.
      //
      // That reasoning was wrong. `?checkout=success` is a string in a URL —
      // anyone can type this route with it and wait out the timer, which made
      // a hard gate openable by hand. And in the case it was built for, it
      // dropped a real customer into a workspace with no credits and no
      // explanation, which is not kindness.
      //
      // The backend no longer needs the webhook to answer this: `summary`
      // reconciles against Stripe directly. So reaching here means Stripe
      // itself has no subscription for this company — which is the one case
      // where stopping is right.
      if (!stopped) {
        confirming.current = false
        setPhase({ kind: "choosing" })
        setError(
          "We couldn't confirm your subscription. If you completed payment, " +
            "give it a moment and try again — you will not be charged twice.",
        )
      }
    }
    void poll()

    return () => {
      stopped = true
      confirming.current = false
      window.clearTimeout(slowTimer)
    }
  }, [checkout, refresh, advance])

  async function startCheckout() {
    setError(null)
    setPhase({ kind: "redirecting" })
    try {
      const { url } = await billingApi.checkout(plan, interval, ONBOARDING_PLAN_PATH)
      // A full navigation, not router.push: Checkout is Stripe's own origin.
      window.location.href = url
    } catch (e) {
      setError(
        e instanceof ApiError
          ? apiErrorMessage(e.status, e.body)
          : "Couldn’t open checkout. Try again.",
      )
      setPhase({ kind: "choosing" })
    }
  }

  // ONLY AN OWNER OR ADMIN CAN BUY — `/v1/billing/checkout` refuses anyone
  // else, so showing a plain member a Continue button would hand them a 403.
  // They reach this screen the same way an admin does (the gate is
  // company-level, which is what stops us charging a company twice for an
  // invited teammate), so the honest answer is to name who can act, not to
  // pretend they can.
  const canBuy = ["owner", "admin"].includes((orgRole ?? "").toLowerCase())

  if (phase.kind === "choosing" && orgRole !== null && !canBuy) {
    return (
      <div className="onb-shell">
        <div className="onb-head">
          <span className="onb-brand">
            <SprntlyLockup height={18} />
          </span>
        </div>
        <div className="onb-card">
          <div className="onb-h">
            Waiting on <em>your admin</em>
          </div>
          <div className="onb-sub">
            {workspace?.display_name ?? "This workspace"} needs a plan before
            anyone can carry on. Ask an owner or admin to choose one in
            Settings → Account, and you'll pick up right where you left off.
          </div>
        </div>
      </div>
    )
  }

  if (phase.kind === "confirming" || phase.kind === "done") {
    return (
      <div className="onb-shell">
        <div className="onb-head">
          <span className="onb-brand">
            <SprntlyLockup height={18} />
          </span>
        </div>
        <div className="onb-card onb-plan-confirm">
          <div className="onb-h">
            Setting up <em>your workspace</em>
          </div>
          <div className="onb-sub">
            {slow
              ? "Payment went through — we're just waiting on the confirmation. This won't take much longer."
              : "One moment while we confirm your plan."}
          </div>
          <div className="onb-plan-spinner" aria-live="polite" role="status">
            Confirming your plan…
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="onb-shell">
      <div className="onb-head">
        <span className="onb-brand">
          <SprntlyLockup height={18} />
        </span>
      </div>

      <div className="onb-card">
        <div className="onb-h">
          Choose <em>your plan</em>
        </div>
        <div className="onb-sub">
          {workspace?.display_name ? `${workspace.display_name} is set up. ` : ""}
          Pick a plan to carry on. Your card is saved now, but nothing is charged
          for {TRIAL_DAYS} days — cancel any time before then and
          you pay nothing. Your trial comes with{" "}
          {TRIAL_CREDITS.toLocaleString()} credits; the plan's own monthly
          credits start when the trial ends.
        </div>

        {cancelled && (
          <div className="onb-form-notice">
            Checkout was cancelled — nothing was charged. Pick a plan when you're
            ready.
          </div>
        )}
        {error && <div className="onb-form-error">{error}</div>}

        <div className="onb-plan-toggle" role="group" aria-label="Billing interval">
          {(["monthly", "annual"] as const).map((v) => (
            <button
              key={v}
              type="button"
              className={`onb-plan-toggle-btn${interval === v ? " active" : ""}`}
              aria-pressed={interval === v}
              onClick={() => setInterval(v)}
            >
              {v === "monthly" ? "Monthly" : "Annual"}
              {v === "annual" && <span className="onb-plan-save">2 months free</span>}
            </button>
          ))}
        </div>

        <div className="onb-plan-grid">
          {SELF_SERVE_PLANS.map((choice) => {
            const on = plan === choice.id
            return (
              <button
                key={choice.id}
                type="button"
                className={`onb-plan-card${on ? " active" : ""}`}
                aria-pressed={on}
                data-testid={`plan-${choice.id}`}
                onClick={() => setPlan(choice.id)}
              >
                <span className="onb-plan-name">{choice.label}</span>
                <span className="onb-plan-price">
                  ${interval === "annual" ? choice.annual : choice.monthly}
                  <span className="onb-plan-per">
                    /{interval === "annual" ? "yr" : "mo"}
                  </span>
                </span>
                <span className="onb-plan-credits">
                  {choice.credits.toLocaleString()} credits a month
                </span>
                <span className="onb-plan-blurb">{choice.blurb}</span>
              </button>
            )
          })}
        </div>

        <button
          type="button"
          className="btn primary onb-plan-continue"
          disabled={phase.kind === "redirecting"}
          data-testid="plan-continue"
          onClick={startCheckout}
        >
          {phase.kind === "redirecting" ? "Opening checkout…" : "Continue"}
        </button>

        {/* Team and Enterprise carry no self-serve price — a checkout naming
            either is refused by the backend rather than quietly downgraded. So
            they are a conversation, not a card. Deliberately a link and not a
            fourth card: offering a plan nobody can buy here is worse than
            saying who to talk to. */}
        <p className="onb-plan-sales">
          Need Team or Enterprise?{" "}
          <a href={`mailto:${SALES_CONTACT}`}>Talk to us</a> — start on a plan
          above and we'll move you across, no double billing.
        </p>
      </div>
    </div>
  )
}
