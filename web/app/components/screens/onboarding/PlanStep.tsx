"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import {
  ApiError,
  apiErrorMessage,
  billingApi,
  connectorsApi,
  type ConnectionSummary,
} from "../../../lib/api"
import { useAuth } from "../../../lib/auth"
import { useWorkspace } from "../../../context/WorkspaceContext"
import { useOnboarding } from "../../../context/OnboardingContext"
import { useContent } from "../../../context/ContentContext"
import { hasDataSourceConnection } from "../../../lib/connectorsCatalog"
import {
  POST_ONBOARDING_PATH,
  finishOnboardingAndEnterApp,
} from "../../../lib/onboarding/finishOnboarding"
import { SprntlyLockup } from "../../shared/SprntlyMark"
import { ArrowLeft } from "../../auth/icons"
import {
  ONBOARDING_PLAN_PATH,
  SALES_CONTACT,
  SELF_SERVE_PLANS,
  TRIALS_ENABLED,
  TRIAL_CREDITS,
  TRIAL_DAYS,
} from "../../../lib/billingPlans"
import { companyHasPaid, subscriptionGrantsAccess } from "../../../lib/billingAccess"

/**
 * "Choose a plan" — THE LAST STEP of onboarding, and the one that completes it.
 *
 * MOVED HERE FROM POSITION TWO (owner decision 2026-09-07). It used to be an
 * unnumbered gate sitting between creating the company row and the rest of the
 * flow, enforced by a guard on every step. The case for that placement was
 * reachability: a company row, a verified email and a profile all existed by
 * then, so an abandoned signup was still a lead. The case against it is that
 * it asked a stranger to pick between a $59 and a $99 plan having connected
 * nothing, read nothing we drafted for them, and seen nothing the product
 * does. Now the workspace is built first and the plan is bought for something
 * visible.
 *
 * IT IS A NUMBERED STEP NOW, appended to ONBOARDING_STEP_SLUGS rather than
 * inserted — an append shifts no stored `onboarding_step`, which is exactly
 * what made it safe to do without a rebase migration. Being numbered is what
 * makes an abandoned checkout resume by itself: `slugForStep` routes here like
 * any other step, with no payment-specific branch anywhere in the resume path.
 *
 * IT ALSO OWNS THE CLOSER. `finishOnboardingAndEnterApp` used to run from
 * PersonalizeStep / DefineMetrics; both now hand on here instead, and it runs
 * only after `billingApi.summary()` reports a live subscription. So "you
 * cannot finish onboarding without paying" is a property of where completion
 * lives rather than of a guard that has to out-argue every URL a user can
 * type — the old `OnboardingPaymentGuard` was deleted with this move.
 *
 * THE MONEY IS TAKEN TODAY. No trial (owner decision 2026-09-07 — see
 * `plans.TRIALS_ENABLED`): the first invoice is charged at checkout. The trial
 * machinery is intact and switched off, not removed, so the copy below reads
 * off the same flag the server decides on.
 */

/** How long to keep asking whether the subscription landed, after Stripe sends
 *  the browser back. Checkout redirects the moment payment is accepted, but
 *  `subscription_status` is written by the WEBHOOK, which arrives on its own
 *  schedule. Trusting the redirect and completing immediately would stamp
 *  onboarding done off a company row that has not been updated yet — and the
 *  app's own guard, re-reading it, would send them straight back into signup. */
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
  // PAID-STATE READS COME FROM THE ONBOARDING CONTEXT, NOT THE WORKSPACE ONE.
  // That began as a fix for an infinite redirect loop against the since-deleted
  // `OnboardingPaymentGuard`, which read this context while the step read the
  // other: the two held separate copies of the same company, so the step saw
  // paid and moved on while the guard saw unpaid and replaced back here,
  // forever.
  //
  // The guard is gone; the rule stays, and now matters more. This step
  // COMPLETES onboarding, so acting on a copy that disagrees with the one the
  // flow reads means finishing signup off a stale read.
  const { workspace, refresh: refreshOnboarding } = useOnboarding()
  // orgRole only lives on the workspace context; its refresh is still called
  // after payment so the OUTER OnboardingRequiredGuard is not left stale when
  // onboarding later completes.
  const { orgRole, refresh: refreshWorkspace } = useWorkspace()
  const auth = useAuth()
  const { setContent } = useContent()

  const [interval, setInterval] = useState<"monthly" | "annual">("monthly")
  const [plan, setPlan] = useState<string>(SELF_SERVE_PLANS[0]!.id)
  const [phase, setPhase] = useState<Phase>({ kind: "choosing" })
  const [error, setError] = useState<string | null>(null)
  const [slow, setSlow] = useState(false)
  const [copied, setCopied] = useState(false)

  const checkout = params.get("checkout")
  const cancelled = checkout === "cancelled"

  /**
   * BACK, because this is a step now and every other one has it.
   *
   * The screen predates being numbered: it was an unnumbered gate you were
   * redirected to, so it rendered its own shell with no footer, and moving it
   * to the end of the flow left it the one step with no way out but forwards.
   *
   * It goes to `personalize`, the numbered step before it, for someone who
   * arrived through the define-metrics sub-flow too — that sub-flow is reached
   * FROM personalize, so this lands them one screen further back rather than
   * somewhere they never chose to be.
   *
   * Rendered in the choosing phases ONLY. Once Stripe has taken the money there
   * is nothing to go back to, and offering it while the subscription is being
   * confirmed invites someone to walk away mid-write.
   */
  const back = (
    <div className="onb-footer">
      <div className="meta" />
      <button
        type="button"
        className="btn btn-ghost"
        data-testid="plan-back"
        onClick={() => router.push("/onboarding/personalize")}
      >
        <ArrowLeft style={{ width: 13, height: 13 }} aria-hidden /> Back
      </button>
    </div>
  )

  // PAID — so onboarding is done. This runs the shared closer (register the
  // dataset, kick the first brief when a real data source is connected, stamp
  // completion) and enters the app. It used to live on the two screens before
  // this one; it moved here with payment, because completing anywhere earlier
  // would hand someone a finished workspace without a card.
  //
  // `hasDataSourceConnection` needs the connector list, which this screen has
  // no other reason to hold — one read, and a failure to read it counts as no
  // data source. That is the same fail-open the personalize step used: the
  // brief is regenerable from Settings, and stranding someone on a spinner at
  // the very last step, having just been charged, is the worse failure.
  //
  // ITS INPUTS RIDE A REF, and that is not a style choice. `advance` is in the
  // dependency array of the confirm effect below, whose cleanup STOPS the poll
  // — so an `advance` that changed identity on every render would tear down and
  // restart that poll on every render, forever. The workspace object, the auth
  // object and `setContent` are all new references on most renders; reading
  // them off a ref keeps `advance` stable while still seeing the latest values.
  // (The same class of bug as the stable-router note in this file's test.)
  const closerRef = useRef({ workspace, auth, setContent })
  closerRef.current = { workspace, auth, setContent }

  const advance = useCallback(async () => {
    const { workspace: ws, auth: a, setContent: setC } = closerRef.current
    if (!ws || a.kind !== "authed") return
    setPhase({ kind: "done" })
    setError(null)
    try {
      const connections = await connectorsApi.list().then(
        (r) => r.connections,
        () => [] as ConnectionSummary[],
      )
      await finishOnboardingAndEnterApp(
        ws,
        a.user.id,
        setC,
        hasDataSourceConnection(connections),
      )
      router.replace(POST_ONBOARDING_PATH)
    } catch {
      // The money moved and the completion stamp did not. Stay on this screen
      // with a retry rather than dropping back to the plan cards — a customer
      // who has just been charged must never be shown "choose a plan" again.
      setError(
        "You're all paid up, but we couldn't finish setting up your workspace. "
          + "Try again — you will not be charged twice.",
      )
    }
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
      void advance()
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
            // BOTH caches, and the onboarding one is the load-bearing half:
            // advancing while the guard's copy still says unpaid is exactly
            // the redirect loop this ordering exists to prevent.
            await Promise.all([refreshOnboarding(), refreshWorkspace()])
            if (!stopped) await advance()
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
  }, [checkout, refreshOnboarding, refreshWorkspace, advance])

  /**
   * CUSTOM IS A PLAN CARD NOW, not a footnote (owner decision 2026-09-08).
   *
   * Team and Enterprise carry no self-serve price — `plans.SELF_SERVE_PLANS`
   * on the backend is the authority on what may be bought, and a checkout
   * naming either is refused rather than quietly downgraded — so they used to
   * be a line of text under the grid: "offering a plan nobody can buy here is
   * worse than saying who to talk to". That reasoning held while payment was
   * step two of a flow nobody had committed to. At the END of onboarding, a
   * team that has just built a workspace and needs invoicing or SSO reads two
   * priced cards and concludes we are not for them, having skimmed past the
   * sentence that said otherwise.
   *
   * So it is the third card, and it is selectable — but it never reaches
   * Stripe. This id is not in SELF_SERVE_PLANS; selecting it replaces the
   * Continue button with the sales panel rather than relabelling it, because a
   * Continue that cannot continue is worse than no Continue. The backend would
   * refuse the id anyway if a stale client ever posted it.
   */
  const CUSTOM_PLAN_ID = "custom"

  /**
   * Copy the sales address. A CONVENIENCE, never the only way to get it — the
   * address is rendered as plain selectable text beside this, so a refused or
   * missing clipboard (insecure context, an older browser, a permission
   * prompt someone dismisses) costs the reader nothing but a manual select.
   */
  async function copySalesEmail() {
    try {
      await navigator.clipboard.writeText(SALES_CONTACT)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2_000)
    } catch {
      /* the address is on screen regardless */
    }
  }

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
        {back}
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
          {/* Only reachable when the closer failed AFTER a successful payment
              — see `advance`. Retry in place; the plan cards must not come
              back for someone who has already been charged. */}
          {error ? (
            <>
              <div className="onb-form-error">{error}</div>
              <button
                type="button"
                className="btn primary onb-plan-continue"
                data-testid="plan-finish-retry"
                onClick={() => void advance()}
              >
                Try again
              </button>
            </>
          ) : (
            <div className="onb-plan-spinner" aria-live="polite" role="status">
              Confirming your plan…
            </div>
          )}
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
        {/* NO TRIAL, and the copy says so plainly. Payment is the last step,
            so by the time anyone reads this their workspace is built and the
            plan buys something they have already seen — which is the whole
            reason the trial came off. The trialling wording is kept behind the
            same flag the server charges on, so flipping `TRIALS_ENABLED` back
            on restores the promise and the behaviour together rather than
            leaving the screen promising a free week nobody gets. */}
        <div className="onb-sub">
          {workspace?.display_name ? `${workspace.display_name} is set up. ` : ""}
          {TRIALS_ENABLED ? (
            <>
              Pick a plan to carry on. Your card is saved now, but nothing is
              charged for {TRIAL_DAYS} days — cancel any time before then and
              you pay nothing. Your trial comes with{" "}
              {TRIAL_CREDITS.toLocaleString()} credits; the plan's own monthly
              credits start when the trial ends.
            </>
          ) : (
            <>
              Last step — pick a plan and you're in. Your card is charged today,
              and your plan's credits land straight away. Change or cancel any
              time from Settings → Billing.
            </>
          )}
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

          {/* The one card with no price. It carries the reassurance the old
              footnote did — that picking a priced plan today is not a wrong
              turn — because that is what stops someone stalling here waiting
              for a reply. */}
          <button
            type="button"
            className={`onb-plan-card${plan === CUSTOM_PLAN_ID ? " active" : ""}`}
            aria-pressed={plan === CUSTOM_PLAN_ID}
            data-testid={`plan-${CUSTOM_PLAN_ID}`}
            onClick={() => setPlan(CUSTOM_PLAN_ID)}
          >
            <span className="onb-plan-name">Custom</span>
            <span className="onb-plan-price">Let&apos;s talk</span>
            <span className="onb-plan-credits">Team &amp; Enterprise</span>
            <span className="onb-plan-blurb">
              Invoiced, with the seats and credits your team actually needs.
              Start on a plan above meanwhile and we&apos;ll move you across —
              no double billing.
            </span>
          </button>
        </div>

        {/* PICKING CUSTOM SHOWS THE ADDRESS, it does not fire a `mailto:`.
            A mailto is silent when it fails, and it fails often — a browser
            with no default mail client registered (a webmail user on a fresh
            Windows box is the common case) swallows the click entirely, so the
            reader clicks the one button on the screen and nothing whatsoever
            happens. Printing the address means the answer is always on screen
            and copying it is a convenience rather than the only route.

            It also says what to put IN the mail, and when to expect a reply.
            "Talk to sales" with no brief makes the reader compose the first
            message of a negotiation from nothing, which is how a warm lead
            turns into a tab they close. Deliberately no list of examples —
            naming seats, SSO and the rest reads as a menu to pick from, and
            a reader who wants none of them concludes the question is not for
            them. */}
        {plan === CUSTOM_PLAN_ID ? (
          <div className="onb-plan-custom" data-testid="plan-custom-panel">
            <div className="onb-plan-custom-h">Email us and we'll size it with you</div>
            <div className="onb-plan-custom-mail">
              {/* Selectable text FIRST, button second: the address is the
                  content, the copy is the shortcut. */}
              <span className="onb-plan-custom-addr">{SALES_CONTACT}</span>
              <button
                type="button"
                className="btn btn-secondary"
                data-testid="plan-custom-copy"
                onClick={() => void copySalesEmail()}
              >
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
            <p className="onb-plan-custom-body">
              Tell us your team size and what you need us to cover. We answer
              within one business day.
            </p>
            <p className="onb-plan-custom-body">
              <strong>You don't have to wait to get started.</strong> Pick
              Starter or Product Builder above and we'll move you across when we
              talk — no double billing, and nothing you set up today is lost.
            </p>
          </div>
        ) : (
          <button
            type="button"
            className="btn primary onb-plan-continue"
            disabled={phase.kind === "redirecting"}
            data-testid="plan-continue"
            onClick={() => void startCheckout()}
          >
            {phase.kind === "redirecting" ? "Opening checkout…" : "Continue"}
          </button>
        )}
      </div>
      {back}
    </div>
  )
}
