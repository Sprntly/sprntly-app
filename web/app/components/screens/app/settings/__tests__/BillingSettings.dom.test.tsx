// @vitest-environment jsdom
import * as React from "react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
// The shared SettingsLayout chrome cannot render under vitest today — its JSX
// compiles to the classic `React.createElement` and the module has no React
// import, so any test that renders it dies with "React is not defined". That is
// pre-existing and unrelated to billing (a bare `render(<SettingsSection/>)`
// fails on main), so it is stubbed here rather than fixed in this PR. The card
// chrome is not what these tests are about.
vi.mock("../SettingsLayout", () => ({
  SettingsSection: ({
    title,
    children,
  }: {
    title: string
    sub?: string
    children: React.ReactNode
  }) => (
    <section>
      <h3>{title}</h3>
      {children}
    </section>
  ),
  SettingsMessage: ({ children }: { kind: string; children: React.ReactNode }) => (
    <div role="alert">{children}</div>
  ),
  // Rendered for real (it is the thing under test in the nav cases), just
  // without the card chrome around it.
  SettingsPaneNav: ({
    items,
    active,
    onSelect,
    label,
    children,
  }: {
    items: { id: string; label: string; hint?: string }[]
    active: string
    onSelect: (id: string) => void
    label: string
    children: React.ReactNode
  }) => (
    <div>
      <div>{children}</div>
      <nav aria-label={label}>
        {items.map((i) => (
          <button
            key={i.id}
            type="button"
            aria-current={i.id === active ? "page" : undefined}
            onClick={() => onSelect(i.id)}
          >
            {i.label}
            {i.hint ? ` ${i.hint}` : ""}
          </button>
        ))}
      </nav>
    </div>
  ),
}))

import {
  BillingSettingsView,
  entryLabel,
  featureLabel,
  billingTabs,
  contactPlansFor,
  referralLink,
  refundWindowRemaining,
  resolveBillingTab,
  statusNotice,
  type BillingView,
} from "../BillingSettings"
import type { BillingSummary } from "../../../../../lib/api"
// Derived from price, not typed in — see lib/billingPlans.
import { TEAM_MONTHLY_CREDITS } from "../../../../../lib/billingPlans"

/**
 * The Billing pane's states.
 *
 * The View is pure, so the states that actually matter — no subscription, out
 * of credits, unlimited plan, Stripe absent — are reachable without a network
 * or a redirect. The redirect itself (`window.location.assign`) lives in the
 * wrapper and is deliberately not exercised here.
 */

const SUMMARY: BillingSummary = {
  plan: "starter",
  plan_label: "Starter",
  unlimited: false,
  credit_balance: 320,
  monthly_credits: 500,
  subscription_status: "active",
  has_access: true,
  current_period_end: "2026-09-21T00:00:00Z",
  cancel_at_period_end: false,
  cancels_at: null,
  first_paid_at: "2026-08-20T00:00:00Z",
  refund_window_days: 7,
  billing_configured: true,
  has_subscription: true,
  topup_presets: [20, 50, 100],
  topup_min_usd: 5,
  topup_max_usd: 2000,
  credits_per_topup_usd: 14,
  history: [
    {
      id: 2,
      delta: -25,
      reason: "spend",
      feature: "prd",
      balance_after: 320,
      actor_user_id: "u1",
      created_at: "2026-08-21T10:00:00Z",
    },
    {
      id: 1,
      delta: 500,
      reason: "monthly_grant",
      feature: null,
      balance_after: 500,
      actor_user_id: null,
      created_at: "2026-08-20T10:00:00Z",
    },
  ],
  referrals: [],
  referral_invites_remaining: 3,
  referral_reward_credits: 140,
}

afterEach(cleanup)

function view(over: Partial<BillingView> = {}, data: Partial<BillingSummary> = {}) {
  const props: BillingView = {
    data: { ...SUMMARY, ...data },
    loading: false,
    restricted: false,
    error: null,
    notice: null,
    interval: "monthly",
    busy: null,
    inviteEmail: "",
    customTopup: "",
    tab: "billing",
    onTab: vi.fn(),
    confirmingCancel: false,
    onConfirmCancel: vi.fn(),
    onCancelSubscription: vi.fn(),
    onResumeSubscription: vi.fn(),
    onInterval: vi.fn(),
    onSubscribe: vi.fn(),
    onPortal: vi.fn(),
    onTopup: vi.fn(),
    onInviteEmail: vi.fn(),
    onInvite: vi.fn(),
    onCustomTopup: vi.fn(),
    ...over,
  }
  return { props, ...render(<BillingSettingsView {...props} />) }
}

describe("BillingSettings — balance", () => {
  it("shows the balance against the allowance", () => {
    view()
    expect(screen.getByText("320")).toBeTruthy()
    expect(screen.getByText(/\/ 500/)).toBeTruthy()
    expect(screen.getByText(/180 used this period/)).toBeTruthy()
  })

  it("exposes the meter to assistive tech with real numbers", () => {
    view()
    const meter = screen.getByRole("progressbar")
    expect(meter.getAttribute("aria-valuenow")).toBe("320")
    expect(meter.getAttribute("aria-valuemax")).toBe("500")
  })

  it("says Unlimited rather than printing the -1 sentinel", () => {
    view({}, { unlimited: true, credit_balance: null, monthly_credits: null })
    expect(screen.getByText("Unlimited")).toBeTruthy()
    expect(screen.queryByText("-1")).toBeNull()
  })

  it("hides the top-up section on an unlimited plan", () => {
    view({}, { unlimited: true, credit_balance: null, monthly_credits: null })
    expect(screen.queryByText("Buy more credits")).toBeNull()
  })

  it("does not render a negative used figure if the balance exceeds the allowance", () => {
    // Top-ups stack on top of the monthly grant, so this is reachable.
    view({}, { credit_balance: 700, monthly_credits: 500 })
    expect(screen.getByText(/0 used this period/)).toBeTruthy()
  })
})

describe("BillingSettings — subscription states", () => {
  it("warns on past_due without implying access is already gone", () => {
    const notice = statusNotice("past_due")
    expect(notice?.kind).toBe("error")
    expect(notice?.text).toMatch(/retrying/i)
  })

  it("tells a cancelled customer what to do next", () => {
    // The instruction used to be prose ("Choose a plan to keep generating").
    // It is a real Subscribe now link now, so the notice carries the flag that
    // renders it rather than the sentence describing it.
    expect(statusNotice("canceled")?.text).toMatch(/subscription has ended/i)
    expect(statusNotice("canceled")?.subscribe).toBe(true)
  })

  it("says nothing when the subscription is healthy", () => {
    expect(statusNotice("active")).toBeNull()
    expect(statusNotice(null)).toBeNull()
  })

  it("renders the banner for a failing card", () => {
    view({}, { subscription_status: "past_due" })
    expect(screen.getByText(/did not go through/i)).toBeTruthy()
  })
})

describe("BillingSettings — Stripe absent", () => {
  it("explains itself and disables every purchase button", () => {
    // No Stripe means no subscription either — that is the real combination.
    view({ tab: "plans" }, { billing_configured: false, has_subscription: false })
    // "Choose" became "Pay now" / "Upgrade" when the two operations were split.
    const buy = screen.getAllByRole("button", { name: /pay now|upgrade|downgrade/i })
    expect(buy.length).toBeGreaterThan(0)
    expect(buy.every((b) => (b as HTMLButtonElement).disabled)).toBe(true)
  })

  it("hides the portal button when there is nothing to manage", () => {
    view({}, { billing_configured: false, has_subscription: false })
    expect(screen.queryByRole("button", { name: /manage payment/i })).toBeNull()
  })
})

describe("BillingSettings — plans", () => {
  it("marks the current plan and does not let you rebuy it", () => {
    view({ tab: "plans" }, { has_subscription: true, has_access: true })
    const current = screen.getByRole("button", { name: "Current plan" })
    expect((current as HTMLButtonElement).disabled).toBe(true)
  })

  it("offers a move to the plan you are not on", async () => {
    // Labelled by price now: from Starter, Product Builder is an Upgrade.
    const { props } = view({ tab: "plans" })
    await userEvent.click(screen.getByRole("button", { name: "Upgrade" }))
    expect(props.onSubscribe).toHaveBeenCalledWith("product_builder")
  })

  it("shows annual prices when the annual tab is active", () => {
    view({ interval: "annual", tab: "plans" })
    expect(screen.getByText("$590")).toBeTruthy()
    expect(screen.getByText("$990")).toBeTruthy()
  })

  it("routes the contact plans to sales rather than checkout", () => {
    // Neither is in SELF_SERVE_PLANS and the backend rejects a checkout naming
    // either, so a Choose button here would be a 400 waiting to happen.
    view({ tab: "plans" })
    const link = screen.getByRole("link", { name: /talk to sales/i })
    expect(link.getAttribute("href")).toContain("mailto:sales@sprntly.ai")
  })

  it("hides Team on monthly — $20,000/yr is not a monthly product", () => {
    view({ tab: "plans", interval: "monthly" })
    expect(screen.queryByRole("heading", { name: "Team" })).toBeNull()
    // Enterprise has no interval, so it is always there.
    expect(screen.getByRole("heading", { name: "Enterprise" })).toBeTruthy()
  })

  it("shows Team with its real price once you switch to annual", () => {
    view({ tab: "plans", interval: "annual" })
    expect(screen.getByRole("heading", { name: "Team" })).toBeTruthy()
    expect(screen.getByText("$20,000")).toBeTruthy()
    expect(screen.getByText(new RegExp(`${TEAM_MONTHLY_CREDITS.toLocaleString()} credits/mo, pooled`))).toBeTruthy()
  })

  it("contactPlansFor is the single rule for that", () => {
    expect(contactPlansFor("monthly").map((c) => c.id)).toEqual(["enterprise"])
    expect(contactPlansFor("annual").map((c) => c.id)).toEqual([
      "team",
      "enterprise",
    ])
  })

  it("marks a company already on Team without offering to sell it again", () => {
    view({ tab: "plans", interval: "annual" }, { plan: "team" })
    expect(screen.getByRole("link", { name: /contact us/i })).toBeTruthy()
  })
})

describe("BillingSettings — top-ups", () => {
  it("quotes the credits each preset buys", () => {
    view({ tab: "billing" })
    expect(screen.getByText("280 credits")).toBeTruthy() // $20 x 14
    expect(screen.getByText("1,400 credits")).toBeTruthy() // $100 x 14
  })

  it("passes the chosen amount up", async () => {
    const { props } = view({ tab: "billing" })
    await userEvent.click(screen.getByRole("button", { name: /\$50/ }))
    expect(props.onTopup).toHaveBeenCalledWith(50)
  })

  it("bounds the custom amount input to the server's own limits", () => {
    view({ tab: "billing" })
    const input = screen.getByLabelText(/custom amount/i) as HTMLInputElement
    expect(input.min).toBe("5")
    expect(input.max).toBe("2000")
  })
})

describe("BillingSettings — referrals", () => {
  // ONE PERMANENT LINK, no email form. The old model took an address and minted
  // a code for that one person, which put a form between someone and sharing a
  // link and capped how many people they could tell.
  const withLink = {
    referral_code: "K7MPQ2XR9T",
    referral_url: "https://app.sprntly.test/sign-up?ref=K7MPQ2XR9T",
    referral_invites_remaining: null,
  }

  it("shows the link, ready to copy", () => {
    view({ tab: "referrals" }, withLink)
    expect((screen.getByTestId("referral-link") as HTMLInputElement).value).toBe(
      "https://app.sprntly.test/sign-up?ref=K7MPQ2XR9T",
    )
  })

  it("offers no email form — there is nothing to submit", () => {
    view({ tab: "referrals" }, withLink)
    expect(screen.queryByLabelText(/friend.s email/i)).toBeNull()
    expect(screen.queryByRole("button", { name: /create invite/i })).toBeNull()
  })

  it("promises the reward on SUBSCRIBING, not on their first payment", () => {
    // With a trial in between, the old copy promised a referrer a week of
    // silence after they had already done everything right.
    view({ tab: "referrals" }, withLink)
    const copy = screen.getByText(/share this link/i)
    expect(copy.textContent).toMatch(/straight away/i)
    expect(copy.textContent).not.toMatch(/first payment clears/i)
  })

  it("caps nothing — no 'N left', no 'you have used all your invites'", () => {
    view({ tab: "referrals" }, withLink)
    expect(screen.queryByText(/left$/)).toBeNull()
    expect(screen.queryByText(/used all your invites/i)).toBeNull()
  })

  it("lists who has arrived, and whether it converted", () => {
    view(
      { tab: "referrals" },
      {
        ...withLink,
        referrals: [
          {
            id: "r1", status: "rewarded", reward_credits: 140,
            created_at: "2026-08-01T00:00:00Z", signed_up_at: "2026-08-02T00:00:00Z",
          },
          {
            id: "r2", status: "signed_up", reward_credits: null,
            created_at: "2026-08-10T00:00:00Z", signed_up_at: "2026-08-10T00:00:00Z",
          },
        ],
      },
    )
    const rows = screen.getAllByTestId("referral-row")
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toContain("+140 credits")
    expect(rows[1].textContent).toMatch(/no subscription yet/i)
  })

  it("says so plainly when nobody has used the link", () => {
    view({ tab: "referrals" }, { ...withLink, referrals: [] })
    expect(screen.getByText(/nobody has signed up through your link/i)).toBeTruthy()
  })

  it("does not break before the link has loaded", () => {
    view({ tab: "referrals" }, { referral_url: null, referral_code: null, referrals: [] })
    expect((screen.getByTestId("referral-link") as HTMLInputElement).value).toBe("")
    expect((screen.getByTestId("referral-copy") as HTMLButtonElement).disabled).toBe(true)
  })
})

describe("BillingSettings — refund window", () => {
  it("counts down from the first payment", () => {
    const left = refundWindowRemaining(
      "2026-08-20T00:00:00Z",
      7,
      new Date("2026-08-23T00:00:00Z"),
    )
    expect(left).toBe(4)
  })

  it("closes once the window has passed", () => {
    expect(
      refundWindowRemaining("2026-08-01T00:00:00Z", 7, new Date("2026-08-21T00:00:00Z")),
    ).toBeNull()
  })

  it("is absent for a company that has never paid", () => {
    expect(refundWindowRemaining(null, 7)).toBeNull()
  })

  it("is never offered to a customer — the seven days are the TRIAL", () => {
    // The refund window was the PRE-TRIAL design: no free trial, pay, and we
    // refund inside a week. A trial replaced it, and during a trial nothing is
    // deducted — so there is no payment to undo. The helper survives because
    // the staff admin panel still computes the same window; the SCREEN never
    // mentions it.
    view({}, { first_paid_at: new Date().toISOString() })
    expect(screen.queryByText(/refund/i)).toBeNull()
    expect(screen.queryByText(/contact us/i)).toBeNull()
  })
})

describe("BillingSettings — history", () => {
  // The "What things cost" view was removed (owner decision, 2026-08-28). A
  // customer is shown what they HAVE used, here in Credit history, and never
  // what an action would cost before they take it. `action_costs` went with it
  // — a price list sitting unrendered in the network tab is still a price list
  // we published.
  it("no longer offers a costs view at all", () => {
    view({})
    expect(screen.queryByText("What things cost")).toBeNull()
  })

  it("still shows what HAS been spent, which is the part that stays", () => {
    view({ tab: "history" })
    expect(screen.getByText("Credit history")).toBeTruthy()
  })

  it("labels a spend by the surface it went to", () => {
    expect(
      entryLabel({
        id: 1,
        delta: -25,
        reason: "spend",
        feature: "prd",
        balance_after: 0,
        actor_user_id: null,
        created_at: "",
      }),
    ).toBe("PRD")
  })

  it("labels a grant by its reason", () => {
    expect(
      entryLabel({
        id: 1,
        delta: 500,
        reason: "monthly_grant",
        feature: null,
        balance_after: 500,
        actor_user_id: null,
        created_at: "",
      }),
    ).toBe("Monthly credits")
  })

  it("de-slugifies a feature nobody has labelled yet", () => {
    expect(featureLabel("brand_new_surface")).toBe("Brand new surface")
  })

  it("signs the history entries", () => {
    view({ tab: "history" })
    expect(screen.getByText("+500")).toBeTruthy()
    expect(screen.getByText("-25")).toBeTruthy()
  })
})

describe("BillingSettings — access", () => {
  it("tells a viewer who owns billing instead of showing an error", () => {
    view({ restricted: true })
    expect(screen.getByText(/managed by your workspace owner/i)).toBeTruthy()
  })

  it("shows a load failure", () => {
    view({ data: null, error: "Could not load billing." })
    expect(screen.getByText("Could not load billing.")).toBeTruthy()
  })
})

describe("BillingSettings — one card at a time", () => {
  it("lands on Billing, not on a wall of cards", () => {
    view()
    expect(screen.getByRole("heading", { name: "Billing" })).toBeTruthy()
    // The other sections exist as nav entries, not as stacked content.
    expect(screen.queryByRole("heading", { name: "Plans" })).toBeNull()
    expect(screen.queryByRole("heading", { name: "Credit history" })).toBeNull()
  })

  it("lists every section in the nav", () => {
    view()
    const nav = screen.getByRole("navigation", { name: /billing sections/i })
    for (const label of [
      "Billing",
      "Plans",
      "Invite a friend",
      "History",
    ]) {
      expect(nav.textContent).toContain(label)
    }
  })

  it("marks the current section for assistive tech", () => {
    view({ tab: "plans" })
    const current = screen
      .getAllByRole("button")
      .find((b) => b.getAttribute("aria-current") === "page")
    expect(current?.textContent).toContain("Plans")
  })

  it("asks the container to switch when a nav entry is clicked", async () => {
    const { props } = view()
    const nav = screen.getByRole("navigation", { name: /billing sections/i })
    await userEvent.click(
      within(nav).getByRole("button", { name: /history/i }),
    )
    expect(props.onTab).toHaveBeenCalledWith("history")
  })

  it("shows the numbers that matter without opening anything", () => {
    view()
    const nav = screen.getByRole("navigation", { name: /billing sections/i })
    expect(nav.textContent).toContain("320") // balance
    expect(nav.textContent).toContain("Starter") // current plan
    expect(nav.textContent).toContain("3 left") // invites
  })

  it("omits Buy more credits entirely on an unlimited plan", () => {
    // Absent rather than present-and-empty: there is no balance to top up.
    view({}, { unlimited: true, credit_balance: null, monthly_credits: null })
    const nav = screen.getByRole("navigation", { name: /billing sections/i })
    expect(nav.textContent).not.toContain("Buy more credits")
  })

  it("falls back to Billing when the selected section stops existing", () => {
    // Reachable: switching to an unlimited plan removes the top-up view while
    // you are standing on it, which would otherwise render a blank pane.
    view({ tab: "billing" }, { unlimited: true, credit_balance: null, monthly_credits: null })
    expect(screen.getByRole("heading", { name: "Billing" })).toBeTruthy()
  })

  it("resolveBillingTab keeps a valid selection", () => {
    const items = billingTabs(SUMMARY)
    expect(resolveBillingTab("history", items)).toBe("history")
    expect(resolveBillingTab("referrals", items)).toBe("referrals")
  })

  it("no longer carries a separate top-up tab — it lives on the billing card", () => {
    // Moved (owner decision, 2026-08-28): someone notices they are short while
    // looking at their balance, so the way to fix it belongs on that same view
    // rather than one click away.
    expect(billingTabs(SUMMARY).map((i) => i.id)).not.toContain("topup")
  })
})

describe("BillingSettings — cancelling", () => {
  it("does not cancel on a single click", async () => {
    const { props } = view()
    await userEvent.click(
      screen.getByRole("button", { name: /cancel subscription/i }),
    )
    expect(props.onCancelSubscription).not.toHaveBeenCalled()
    expect(props.onConfirmCancel).toHaveBeenCalledWith(true)
  })

  it("leads the confirm with what the customer KEEPS", () => {
    // They paid for this period. The copy must not read as though cancelling
    // takes it away, or every cancellation becomes a refund request.
    view({ confirmingCancel: true })
    expect(screen.getByText(/you will keep your plan/i)).toBeTruthy()
    expect(screen.getByText(/320 credits/)).toBeTruthy()
    expect(screen.getByText(/undo this any time/i)).toBeTruthy()
  })

  it("cancels on the second click", async () => {
    const { props } = view({ confirmingCancel: true })
    const buttons = screen.getAllByRole("button", { name: /cancel subscription/i })
    await userEvent.click(buttons[buttons.length - 1])
    expect(props.onCancelSubscription).toHaveBeenCalled()
  })

  it("backs out without cancelling", async () => {
    const { props } = view({ confirmingCancel: true })
    await userEvent.click(screen.getByRole("button", { name: /keep my plan/i }))
    expect(props.onConfirmCancel).toHaveBeenCalledWith(false)
    expect(props.onCancelSubscription).not.toHaveBeenCalled()
  })

  it("says when the plan ends, and that nothing more is charged", () => {
    view({}, { cancel_at_period_end: true, cancels_at: "2026-09-21T00:00:00Z" })
    expect(screen.getByText(/your plan ends/i)).toBeTruthy()
    expect(screen.getByText(/nothing more will be charged/i)).toBeTruthy()
  })

  it("stops promising a renewal that is not coming", () => {
    view({}, { cancel_at_period_end: true, cancels_at: "2026-09-21T00:00:00Z" })
    expect(screen.queryByText(/^Renews/)).toBeNull()
    expect(screen.getByText(/^Ends /)).toBeTruthy()
  })

  it("offers to undo while the cancellation is still pending", async () => {
    const { props } = view(
      {},
      { cancel_at_period_end: true, cancels_at: "2026-09-21T00:00:00Z" },
    )
    await userEvent.click(screen.getByRole("button", { name: /keep my plan/i }))
    expect(props.onResumeSubscription).toHaveBeenCalled()
  })

  it("shows no cancel affordance without a subscription", () => {
    view({}, { has_subscription: false })
    expect(
      screen.queryByRole("button", { name: /cancel subscription/i }),
    ).toBeNull()
  })

  it("still shows the balance while a cancellation is pending", () => {
    // Access continues until the period ends — the screen must not look as
    // though the credits are already gone.
    view({}, { cancel_at_period_end: true, cancels_at: "2026-09-21T00:00:00Z" })
    expect(screen.getByText("320")).toBeTruthy()
  })
})

describe("on trial", () => {
  // The plan name alone is a half-truth during a trial: it says Starter and
  // says nothing about nobody having been charged yet, or when that changes.
  const inDays = (n: number) =>
    new Date(Date.now() + n * 86_400_000).toISOString()

  const onTrial = (days = 6) => ({
    subscription_status: "trialing",
    current_period_end: inDays(days),
  })

  it("says it is a trial, and how long is left", () => {
    view({}, onTrial(6))
    const banner = screen.getByTestId("billing-trial")
    expect(banner.textContent).toContain("Free trial")
    expect(banner.textContent).toContain("6 days left")
  })

  it("names the plan the trial is on", () => {
    view({}, onTrial(6))
    expect(screen.getByTestId("billing-trial").textContent).toContain("Starter")
  })

  it("says nothing has been charged, and when the first payment lands", () => {
    // The countdown says how long; the DATE says what actually happens. Both,
    // or the reader is left to work out the second from the first.
    view({}, onTrial(6))
    const banner = screen.getByTestId("billing-trial")
    expect(banner.textContent).toMatch(/nothing has been charged/i)
    expect(banner.textContent).toMatch(/first payment is on/i)
    expect(banner.textContent).toMatch(/cancel before then and you pay nothing/i)
  })

  it("does not promise a RENEWAL during a trial — the first charge is not a renewal", () => {
    view({}, onTrial(6))
    expect(screen.queryByText(/^Renews/)).toBeNull()
    expect(screen.getByText(/^First payment/)).toBeTruthy()
  })

  it("says day, not days, on the last one", () => {
    view({}, onTrial(0.5))
    expect(screen.getByTestId("billing-trial").textContent).toContain("1 day left")
  })

  it("is absent once the subscription is really paying", () => {
    view({}, { subscription_status: "active", current_period_end: inDays(20) })
    expect(screen.queryByTestId("billing-trial")).toBeNull()
    expect(screen.getByText(/^Renews/)).toBeTruthy()
  })

  it("is absent on a plan that was never sold through Stripe", () => {
    view({}, { subscription_status: null, current_period_end: null })
    expect(screen.queryByTestId("billing-trial")).toBeNull()
  })
})

describe("Buy more credits, on the billing card", () => {
  // It used to be its own tab. Someone notices they are short while looking at
  // their balance, so the way to fix it belongs on that same view.
  it("renders under the balance, on the billing view", () => {
    view({ tab: "billing" })
    expect(screen.getByText("Buy more credits")).toBeTruthy()
    // …alongside the balance it is answering, not instead of it.
    expect(screen.getByText(/\/ 500/)).toBeTruthy()
  })

  it("stays hidden on an unlimited plan, where there is nothing to top up", () => {
    view({ tab: "billing" }, { unlimited: true, credit_balance: null, monthly_credits: null })
    expect(screen.queryByText("Buy more credits")).toBeNull()
  })

  it("is gone from the nav, so it is offered once rather than twice", () => {
    view({ tab: "billing" })
    const nav = screen.getByRole("navigation", { name: /billing sections/i })
    expect(nav.textContent).not.toContain("Buy more credits")
  })
})

describe("the seven days are the trial, and the only window shown", () => {
  // They were told as one sentence, written before the trial existed. A
  // trialling customer has been charged NOTHING, so offering to "refund your
  // first payment" describes a payment that has not happened — and reads as
  // though we are holding money we never took.
  const inDays = (n: number) => new Date(Date.now() + n * 86_400_000).toISOString()
  const agoDays = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString()

  it("on trial: says trial, names the days left, and stops there", () => {
    view({}, { subscription_status: "trialing", current_period_end: inDays(2) })
    const line = screen.getByText(/within your/i)
    expect(line.textContent).toMatch(/7-day trial/)
    expect(line.textContent).toMatch(/2 days left/)
  })

  it("on trial: never offers to refund a payment that has not happened", () => {
    // The trial banner DOES name the date the first charge lands, which is the
    // useful half. What must not appear is the undo-a-payment language.
    view({}, { subscription_status: "trialing", current_period_end: inDays(2) })
    expect(screen.queryByText(/refund/i)).toBeNull()
    expect(screen.getByText(/within your/i).textContent).not.toMatch(/first payment/i)
  })

  it("on trial: says day, not days, on the last one", () => {
    view({}, { subscription_status: "trialing", current_period_end: inDays(0.5) })
    expect(screen.getByText(/within your/i).textContent).toMatch(/1 day left/)
  })

  it("paying and past the trial: says nothing — no window is being offered", () => {
    view({}, { subscription_status: "active", first_paid_at: agoDays(5) })
    expect(screen.queryByText(/within your/i)).toBeNull()
    expect(screen.queryByText(/refund/i)).toBeNull()
  })

  it("keeps Cancel subscription either way — it is needed after the trial too", () => {
    for (const data of [
      { subscription_status: "trialing", current_period_end: inDays(2) },
      { subscription_status: "active", first_paid_at: agoDays(5) },
    ]) {
      cleanup()
      view({}, data)
      expect(
        screen.getAllByRole("button", { name: /cancel subscription/i }).length,
      ).toBeGreaterThan(0)
    }
  })

  it("says nothing at all once both windows have closed", () => {
    view({}, { subscription_status: "active", first_paid_at: agoDays(60) })
    expect(screen.queryByText(/within your/i)).toBeNull()
  })
})

describe("a trial is never described as refundable", () => {
  // Nothing is deducted during a trial, so there is nothing to refund. The
  // trial branch wins UNCONDITIONALLY — not merely because `first_paid_at`
  // happens to be null while trialling. If a stray first_paid_at ever appears
  // on a trialling row (a webhook ordering quirk, a staff edit, a resubscribe),
  // the screen must still not offer to refund a payment the customer has not
  // made.
  const inDays = (n: number) => new Date(Date.now() + n * 86_400_000).toISOString()
  const agoDays = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString()

  it("trial wins even when a first_paid_at is also present", () => {
    view(
      {},
      {
        subscription_status: "trialing",
        current_period_end: inDays(3),
        first_paid_at: agoDays(2), // would otherwise open the refund window
      },
    )
    const line = screen.getByText(/within your/i)
    expect(line.textContent).toMatch(/7-day trial/)
    expect(line.textContent).not.toMatch(/refund/i)
    expect(screen.queryByText(/we will refund/i)).toBeNull()
  })

  it("the word refund appears nowhere on a trialling account", () => {
    view({}, { subscription_status: "trialing", current_period_end: inDays(3) })
    expect(screen.queryByText(/refund/i)).toBeNull()
  })
})

describe("when you cannot generate, the screen says so and offers the fix", () => {
  // The gap: `statusNotice(null)` returned nothing, so a company that finished
  // onboarding without a plan landed on a billing screen with NO banner, no
  // explanation, and a credits meter reading zero. Silence is the worst answer
  // in the one state where the user must act and no other screen will tell them.

  it("never subscribed: says so, and offers to subscribe", () => {
    const { props } = view({}, { subscription_status: null, has_subscription: false })
    expect(screen.getByText(/have not subscribed yet/i)).toBeTruthy()
    fireEvent.click(screen.getByTestId("billing-subscribe-now"))
    expect(props.onTab).toHaveBeenCalledWith("plans")
  })

  it("cancelled: same treatment — buying again is what restores access", () => {
    const { props } = view({}, { subscription_status: "canceled" })
    expect(screen.getByText(/subscription has ended/i)).toBeTruthy()
    fireEvent.click(screen.getByTestId("billing-subscribe-now"))
    expect(props.onTab).toHaveBeenCalledWith("plans")
  })

  it("unpaid: same, because Stripe will not resurrect that subscription", () => {
    // The old copy said "settle the outstanding invoice", which was advice with
    // nowhere to act on it once retries are exhausted.
    view({}, { subscription_status: "unpaid" })
    expect(screen.getByText(/access has stopped/i)).toBeTruthy()
    expect(screen.getByTestId("billing-subscribe-now")).toBeTruthy()
  })

  it("opens the plans view rather than starting a checkout", () => {
    // Which plan is still the user's choice. A banner that silently began
    // buying something would be worse than no banner.
    const { props } = view({}, { subscription_status: "canceled" })
    fireEvent.click(screen.getByTestId("billing-subscribe-now"))
    expect(props.onSubscribe).not.toHaveBeenCalled()
  })

  it("past_due gets NO subscribe link — the plan is fine, the card is not", () => {
    view({}, { subscription_status: "past_due" })
    expect(screen.getByText(/did not go through/i)).toBeTruthy()
    expect(screen.queryByTestId("billing-subscribe-now")).toBeNull()
  })

  it("a healthy subscription shows no banner at all", () => {
    view({}, { subscription_status: "active", has_subscription: true })
    expect(screen.queryByTestId("billing-subscribe-now")).toBeNull()
  })

  it("a trialling company is not told it has not subscribed", () => {
    view({}, { subscription_status: "trialing", has_subscription: true })
    expect(screen.queryByText(/have not subscribed/i)).toBeNull()
  })
})

describe("Pay now, and what 'current plan' actually means", () => {
  // THE BUG: `companies.plan` defaults to 'starter' for every company,
  // subscribed or not. Comparing against it alone told a never-subscribed
  // company that Starter was its current plan and disabled the button — so the
  // banner sent them to Plans and Plans refused them the one plan they wanted.
  const unsubscribed = {
    plan: "starter",
    subscription_status: null,
    has_subscription: false,
    has_access: false,
  }

  it("a never-subscribed company CAN buy the plan its column happens to name", () => {
    view({ tab: "plans" }, unsubscribed)
    const pay = screen.getByRole("button", { name: "Pay now" })
    expect((pay as HTMLButtonElement).disabled).toBe(false)
    expect(screen.queryByRole("button", { name: "Current plan" })).toBeNull()
  })

  it("Pay now appears ONCE — on the plan you are on, not on every card", () => {
    view({ tab: "plans" }, unsubscribed)
    expect(screen.getAllByRole("button", { name: "Pay now" })).toHaveLength(1)
    expect(screen.queryByRole("button", { name: "Choose" })).toBeNull()
  })

  it("a dearer plan says Upgrade", () => {
    view({ tab: "plans" }, unsubscribed)   // on starter
    expect(screen.getByRole("button", { name: "Upgrade" })).toBeTruthy()
  })

  it("a cheaper plan says Downgrade, because calling it Upgrade would be a lie", () => {
    view(
      { tab: "plans" },
      { ...unsubscribed, plan: "product_builder" },
    )
    expect(screen.getByRole("button", { name: "Downgrade" })).toBeTruthy()
    // …and Pay now still sits on the plan they are actually on.
    expect(screen.getAllByRole("button", { name: "Pay now" })).toHaveLength(1)
  })

  it("a cancelled company can pay again — a dead subscription is not a current plan", () => {
    view(
      { tab: "plans" },
      { plan: "starter", subscription_status: "canceled", has_subscription: true, has_access: false },
    )
    expect(screen.queryByRole("button", { name: "Current plan" })).toBeNull()
    expect(screen.getByRole("button", { name: "Pay now" })).toBeTruthy()
  })

  it("a LIVE subscriber sees Upgrade on the other plan, and cannot rebuy their own", () => {
    view(
      { tab: "plans" },
      { plan: "starter", subscription_status: "active", has_subscription: true, has_access: true },
    )
    expect(
      (screen.getByRole("button", { name: "Current plan" }) as HTMLButtonElement).disabled,
    ).toBe(true)
    expect(screen.getByRole("button", { name: "Upgrade" })).toBeTruthy()
    // No Pay now: they have paid. The card they are on is the disabled one.
    expect(screen.queryByRole("button", { name: "Pay now" })).toBeNull()
  })

  it("a trialling subscriber counts as live", () => {
    view(
      { tab: "plans" },
      { plan: "starter", subscription_status: "trialing", has_subscription: true, has_access: true },
    )
    expect(screen.getByRole("button", { name: "Current plan" })).toBeTruthy()
  })

  it("the banner's call to action reads Pay now too", () => {
    view({}, unsubscribed)
    expect(screen.getByTestId("billing-subscribe-now").textContent).toBe("Pay now")
  })
})

describe("the invoiced tiers are left alone", () => {
  it("Team and Enterprise still say Talk to us — no Pay now, no Upgrade", () => {
    // They carry no self-serve price and the backend rejects a checkout naming
    // either, so any buy-shaped label there would be a 400 waiting to happen.
    view({ tab: "plans" }, { plan: "starter", has_subscription: false, has_access: false })
    const contacts = document.querySelectorAll(".bill-plan-contact")
    expect(contacts.length).toBeGreaterThan(0)
    for (const card of contacts) {
      expect(card.textContent).toMatch(/talk to sales|contact us/i)
      expect(card.textContent).not.toMatch(/pay now|upgrade|downgrade/i)
    }
  })
})


describe("Subscriptions — the money record", () => {
  // What a customer opens billing to see is what they were charged, when, and
  // for what. Nothing in the app could answer that: the credit ledger deals
  // only in credits and the company row holds one current period.
  const invoice = (over: Record<string, unknown> = {}) => ({
    id: 1,
    stripe_invoice_id: "in_1",
    plan: "starter",
    amount_paid_cents: 5900,
    currency: "usd",
    status: "paid",
    period_start: "2026-08-29T00:00:00Z",
    period_end: "2026-09-29T00:00:00Z",
    paid_at: "2026-08-29T00:00:00Z",
    invoice_number: "SPR-0001",
    hosted_invoice_url: "https://invoice.stripe.test/i/abc",
    invoice_pdf_url: "https://invoice.stripe.test/i/abc.pdf",
    ...over,
  })

  it("shows the amount in MONEY, not credits", () => {
    view({ tab: "history" }, { invoices: [invoice()] })
    expect(screen.getByText("$59.00")).toBeTruthy()
  })

  it("names the plan the payment was for", () => {
    view({ tab: "history" }, { invoices: [invoice()] })
    expect(screen.getByTestId("invoice-row").textContent).toContain("Starter")
  })

  it("shows the period the invoice COVERS, not just the day it was paid", () => {
    view({ tab: "history" }, { invoices: [invoice()] })
    const row = screen.getByTestId("invoice-row")
    expect(row.textContent).toMatch(/Aug 29, 2026.*Sep 29, 2026/)
  })

  it("carries the invoice number and a link to Stripe's PDF", () => {
    view({ tab: "history" }, { invoices: [invoice()] })
    expect(screen.getByText("SPR-0001")).toBeTruthy()
    expect(screen.getByText("PDF").getAttribute("href")).toBe(
      "https://invoice.stripe.test/i/abc.pdf",
    )
  })

  it("lists one row per month", () => {
    view(
      { tab: "history" },
      {
        invoices: [
          invoice({ id: 2, stripe_invoice_id: "in_2", invoice_number: "SPR-0002" }),
          invoice(),
        ],
      },
    )
    expect(screen.getAllByTestId("invoice-row")).toHaveLength(2)
  })

  it("survives an invoice with no PDF without breaking the row", () => {
    view({ tab: "history" }, { invoices: [invoice({ invoice_pdf_url: null })] })
    expect(screen.getByTestId("invoice-row")).toBeTruthy()
    expect(screen.queryByText("PDF")).toBeNull()
  })

  it("formats a non-USD currency rather than assuming dollars", () => {
    view({ tab: "history" }, { invoices: [invoice({ currency: "gbp", amount_paid_cents: 4900 })] })
    expect(screen.getByTestId("invoice-row").textContent).toMatch(/£49\.00|GBP/)
  })

  it("says so plainly when nothing has been charged yet", () => {
    view({ tab: "history" }, { invoices: [] })
    expect(screen.getByText(/no payments yet/i)).toBeTruthy()
  })

  it("survives a payload with no invoices field at all", () => {
    view({ tab: "history" }, { invoices: undefined })
    expect(screen.getByText(/no payments yet/i)).toBeTruthy()
  })

  it("still shows the credit ledger underneath — a finer question", () => {
    view({ tab: "history" }, { invoices: [invoice()] })
    expect(screen.getByText("Subscriptions")).toBeTruthy()
    expect(screen.getByText("Credit history")).toBeTruthy()
  })

  it("does NOT render plan history, which is served but not for customers", () => {
    view(
      { tab: "history" },
      {
        invoices: [invoice()],
        subscription_history: [
          {
            id: 1, plan: "product_builder", status: "active",
            previous_plan: "starter", previous_status: "active",
            source: "change_plan", created_at: "2026-08-29T16:36:28Z",
          },
        ],
      },
    )
    expect(screen.queryByText("Plan history")).toBeNull()
    expect(screen.queryByText("Starter → Product Builder")).toBeNull()
  })
})
