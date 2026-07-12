// @vitest-environment jsdom
//
// Container mount test for the onboarding step 04 — "Your business context"
// (design scene onbctx). PRODUCT DECISION: this step is the design's TWO
// narrative textareas only — NOT the full structured 8-layer editor, and NOT
// the company-shape fields (industry / business type / tech stack), which moved
// to Settings → Business Context.
//
// The two narratives map onto the #450 Business Context model
// (businessContextApi.get/update, GET/PUT /v1/company/business-context):
//   - "What the company does"  → product_value.what_it_does (+ identity.one_liner)
//   - "What it cares about"     → goals_strategy.stated_goal (+ current_priorities)
//
// Covers: renders ONLY the 2 textareas (no structured doc, no company-shape),
// loads the AI-drafted narratives, edits + PUTs them on Next, the 404 empty
// state, and skip.
import * as React from "react"
import { act, cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const advanceStepMock = vi.fn()
const updateWorkspaceMock = vi.fn()
const bcGetMock = vi.fn()
const bcUpdateMock = vi.fn()
const bcRefreshMock = vi.fn()

vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  advanceOnboardingStep: (...a: unknown[]) => advanceStepMock(...a),
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
}))
vi.mock("../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../lib/api")>(
    "../../../../lib/api",
  )
  return {
    ...actual,
    businessContextApi: {
      get: (...a: unknown[]) => bcGetMock(...a),
      update: (...a: unknown[]) => bcUpdateMock(...a),
      refresh: (...a: unknown[]) => bcRefreshMock(...a),
    },
  }
})

import { BusinessContext } from "../BusinessContext"
import { makeOnboardingCtx, makeWorkspace } from "./fixtures"
import type { BcLeaf, BusinessContextDoc } from "../../../../lib/api"

function leaf<T>(value: T): BcLeaf<T> {
  return { value, src: "inferred", conf: "med", as_of: null, evidence: null }
}

/** Minimal but complete BusinessContextDoc with the narrative leaves set. */
function makeDoc(over: Partial<BusinessContextDoc> = {}): BusinessContextDoc {
  return {
    identity: {
      legal_name: leaf("Acme Inc."),
      also_known_as: leaf<string[]>([]),
      website: leaf("https://acme.com"),
      one_liner: leaf("Acme reconciles payments."),
      industry: leaf("Fintech"),
      sub_vertical: leaf("Payments"),
      company_size: leaf("50-200"),
      stage: leaf("Growth"),
      hq_geography: leaf("US"),
      markets_served: leaf<string[]>(["US"]),
    },
    business_model: {
      model_type: leaf("SaaS"),
      revenue_model: leaf("Subscription"),
      pricing_model: leaf("Per seat"),
      who_pays: leaf("Finance teams"),
      who_uses: leaf("Accountants"),
      monetization_unit: leaf("Seat"),
      unit_economics_shape: leaf("High margin"),
      good_outcome: leaf("Reconciled volume grows"),
    },
    users_segments: { segments: [], primary_segment: leaf("SMB finance") },
    product_value: {
      what_it_does: leaf("Reconciles payments across providers."),
      core_value_moments: leaf("First reconciliation"),
      activation_definition: leaf("First synced account"),
      key_features: leaf<string[]>(["Sync", "Reports"]),
      platforms: leaf<string[]>(["Web"]),
    },
    market_competition: {
      category: leaf("Fintech ops"),
      main_alternatives: leaf<string[]>(["Spreadsheets"]),
      positioning_angle: leaf("Automated"),
    },
    goals_strategy: {
      stated_goal: leaf("Grow reconciled volume"),
      north_star: leaf("Reconciled volume"),
      current_priorities: leaf("Onboarding"),
      known_constraints: leaf("Small team"),
    },
    vocabulary: { terms: [] },
    meta: {
      created: leaf("2026-01-01"),
      last_refreshed: leaf("2026-01-02"),
      refresh_trigger: leaf("onboarding"),
      overall_confidence: leaf("med"),
      sources: [],
    },
    version: 3,
    ...over,
  }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("BusinessContext (onboarding step 04 — onbctx)", () => {
  it("renders ONLY the two narrative textareas — no structured doc, no company-shape", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    bcGetMock.mockResolvedValue(makeDoc())

    await act(async () => {
      render(React.createElement(BusinessContext))
    })

    expect(document.querySelector(".onb-h")?.textContent).toMatch(/business context/i)

    // Exactly the two narrative textareas are present.
    expect(document.querySelector('[data-field="what-it-does"]')).not.toBeNull()
    expect(document.querySelector('[data-field="what-it-cares"]')).not.toBeNull()
    expect(document.body.textContent).toContain("What the company does")
    expect(document.body.textContent).toContain("What does the company care about?")

    // NO structured 8-layer editor leaves and NO company-shape fields.
    expect(
      document.querySelector('[data-field="product_value.what_it_does"]'),
    ).toBeNull()
    expect(document.querySelector(".bc-layer")).toBeNull()
    expect(document.querySelector("[data-bc-company-shape]")).toBeNull()
    expect(document.querySelector('[data-field="industry"]')).toBeNull()
    expect(document.querySelector(".onb-chip")).toBeNull()
  })

  it("loads the AI-drafted narratives from the #450 doc", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    bcGetMock.mockResolvedValue(makeDoc())

    await act(async () => {
      render(React.createElement(BusinessContext))
    })

    expect(bcGetMock).toHaveBeenCalledTimes(1)
    const whatDoes = document.querySelector(
      '[data-field="what-it-does"]',
    ) as HTMLTextAreaElement
    const whatCares = document.querySelector(
      '[data-field="what-it-cares"]',
    ) as HTMLTextAreaElement
    expect(whatDoes.value).toContain("Reconciles payments across providers.")
    expect(whatCares.value).toContain("Grow reconciled volume")
  })

  it("edits both narratives and PUTs them onto their leaves on Next, then advances", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    bcGetMock.mockResolvedValue(makeDoc())
    bcUpdateMock.mockResolvedValue({ ok: true, version: 4 })
    advanceStepMock.mockResolvedValue(undefined)

    await act(async () => {
      render(React.createElement(BusinessContext))
    })

    const whatDoes = document.querySelector(
      '[data-field="what-it-does"]',
    ) as HTMLTextAreaElement
    const whatCares = document.querySelector(
      '[data-field="what-it-cares"]',
    ) as HTMLTextAreaElement
    fireEvent.change(whatDoes, { target: { value: "We do new things." } })
    fireEvent.change(whatCares, { target: { value: "We care about growth." } })

    const nextBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /next/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      nextBtn.click()
    })

    expect(bcUpdateMock).toHaveBeenCalledTimes(1)
    const sent = bcUpdateMock.mock.calls[0][0] as BusinessContextDoc
    // "What the company does" → product_value.what_it_does (+ identity.one_liner)
    expect(sent.product_value.what_it_does.value).toBe("We do new things.")
    expect(sent.identity.one_liner.value).toBe("We do new things.")
    // "What it cares about" → goals_strategy.stated_goal (+ current_priorities)
    expect(sent.goals_strategy.stated_goal.value).toBe("We care about growth.")
    expect(sent.goals_strategy.current_priorities.value).toBe("We care about growth.")

    // Company-shape is NOT persisted from this step anymore.
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 6)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/strategy")
  })

  it("shows the empty 'not generated yet' state on a 404 (null doc) and stays skippable", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    bcGetMock.mockResolvedValue(null) // GET returned 404
    advanceStepMock.mockResolvedValue(undefined)

    await act(async () => {
      render(React.createElement(BusinessContext))
    })

    expect(document.querySelector('[data-bc-state="empty"]')).not.toBeNull()
    // No doc → Next must NOT PUT, but must still advance.
    const nextBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /next/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      nextBtn.click()
    })
    expect(bcUpdateMock).not.toHaveBeenCalled()
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 6)
  })

  it("Skip for now advances without PUTting any edits", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    bcGetMock.mockResolvedValue(makeDoc())
    advanceStepMock.mockResolvedValue(undefined)

    await act(async () => {
      render(React.createElement(BusinessContext))
    })

    const skip = Array.from(document.querySelectorAll("button")).find((b) =>
      /skip for now/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      skip.click()
    })
    expect(bcUpdateMock).not.toHaveBeenCalled()
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 6)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/strategy")
  })
})

// Reference makeWorkspace so the import stays used even though company-shape
// persistence moved out of this step.
void makeWorkspace
