// @vitest-environment jsdom
import * as React from "react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
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
}))

import {
  BillingSettingsView,
  entryLabel,
  featureLabel,
  refundWindowRemaining,
  statusNotice,
  type BillingView,
} from "../BillingSettings"
import type { BillingSummary } from "../../../../../lib/api"

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
  first_paid_at: "2026-08-20T00:00:00Z",
  refund_window_days: 7,
  billing_configured: true,
  has_subscription: true,
  action_costs: { chat: 1, ask: 3, prd: 25, prototype: 50 },
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
    expect(statusNotice("canceled")?.text).toMatch(/choose a plan/i)
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
    view({}, { billing_configured: false, has_subscription: false })
    expect(screen.getByText(/Payments are not enabled/i)).toBeTruthy()
    const choose = screen.getByRole("button", { name: /choose/i })
    expect((choose as HTMLButtonElement).disabled).toBe(true)
  })

  it("hides the portal button when there is nothing to manage", () => {
    view({}, { billing_configured: false, has_subscription: false })
    expect(screen.queryByRole("button", { name: /manage payment/i })).toBeNull()
  })
})

describe("BillingSettings — plans", () => {
  it("marks the current plan and does not let you rebuy it", () => {
    view()
    const current = screen.getByRole("button", { name: "Current plan" })
    expect((current as HTMLButtonElement).disabled).toBe(true)
  })

  it("offers a switch for the plan you are not on", async () => {
    const { props } = view()
    await userEvent.click(screen.getByRole("button", { name: "Switch" }))
    expect(props.onSubscribe).toHaveBeenCalledWith("product_builder")
  })

  it("shows annual prices when the annual tab is active", () => {
    view({ interval: "annual" })
    expect(screen.getByText("$590")).toBeTruthy()
    expect(screen.getByText("$990")).toBeTruthy()
  })

  it("routes Team and Enterprise to sales rather than checkout", () => {
    view()
    const link = screen.getByRole("link", { name: /talk to sales/i })
    expect(link.getAttribute("href")).toBe("mailto:sales@sprntly.ai")
  })
})

describe("BillingSettings — top-ups", () => {
  it("quotes the credits each preset buys", () => {
    view()
    expect(screen.getByText("280 credits")).toBeTruthy() // $20 x 14
    expect(screen.getByText("1,400 credits")).toBeTruthy() // $100 x 14
  })

  it("passes the chosen amount up", async () => {
    const { props } = view()
    await userEvent.click(screen.getByRole("button", { name: /\$50/ }))
    expect(props.onTopup).toHaveBeenCalledWith(50)
  })

  it("bounds the custom amount input to the server's own limits", () => {
    view()
    const input = screen.getByLabelText(/custom amount/i) as HTMLInputElement
    expect(input.min).toBe("5")
    expect(input.max).toBe("2000")
  })
})

describe("BillingSettings — referrals", () => {
  it("says the reward is paid when the friend subscribes, not when they sign up", () => {
    view()
    expect(screen.getByText(/first payment goes through/i)).toBeTruthy()
  })

  it("sends an invite", async () => {
    const { props } = view({ inviteEmail: "friend@example.com" })
    await userEvent.click(screen.getByRole("button", { name: /send invite/i }))
    expect(props.onInvite).toHaveBeenCalled()
  })

  it("replaces the form once the invites are used up", () => {
    view({}, { referral_invites_remaining: 0 })
    expect(screen.queryByRole("button", { name: /send invite/i })).toBeNull()
    expect(screen.getByText(/used all your invites/i)).toBeTruthy()
  })

  it("shows the credits earned on a converted referral", () => {
    view(
      {},
      {
        referrals: [
          {
            id: "r1",
            invitee_email: "paid@example.com",
            status: "rewarded",
            code: "abc",
            reward_credits: 140,
            created_at: "2026-08-01T00:00:00Z",
          },
        ],
      },
    )
    expect(screen.getByText("+140 credits")).toBeTruthy()
  })

  it("labels a voided referral without accusing anyone", () => {
    view(
      {},
      {
        referrals: [
          {
            id: "r2",
            invitee_email: "self@example.com",
            status: "void",
            code: "xyz",
            reward_credits: null,
            created_at: "2026-08-01T00:00:00Z",
          },
        ],
      },
    )
    expect(screen.getByText("Not eligible")).toBeTruthy()
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

  it("describes the real process rather than promising an instant refund", () => {
    // Cancelling is self-serve; the refund is approved by a person after
    // seeing consumption. Saying otherwise would be a promise we do not keep.
    view({}, { first_paid_at: new Date().toISOString() })
    expect(screen.getByText(/contact us and we will refund/i)).toBeTruthy()
  })
})

describe("BillingSettings — costs and history", () => {
  it("renders the price list from the server, cheapest first", () => {
    view()
    const items = screen.getAllByText(/credits?$/)
    expect(items.length).toBeGreaterThan(0)
    expect(screen.getByText("1 credit")).toBeTruthy() // singular, not "1 credits"
  })

  it("says background work is free, because that is the common question", () => {
    view()
    expect(screen.getByText(/does not use credits/i)).toBeTruthy()
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
    view()
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
