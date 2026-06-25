// @vitest-environment jsdom
//
// Container mount test for the onboarding step 03 — "Your business context"
// (design scene onbctx). It REUSES the #450 Business Context surface: it loads
// the doc via businessContextApi.get (GET /v1/company/business-context), lets
// the PM edit leaves inline, and on Continue PUTs edits + advances to strategy.
//
// Covers: loads + renders the auto-drafted doc, edits a field and persists it on
// Continue (PUT), the 404 "not generated yet" empty state, and skip.
//
// Matchers: native DOM only.
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
// The step pulls buildLayers from the Settings pane; that module imports the
// real lib/api, which is mocked above (so businessContextApi is our spy).

import { BusinessContext } from "../BusinessContext"
import { makeOnboardingCtx, makeWorkspace } from "./fixtures"
import type { BcLeaf, BusinessContextDoc } from "../../../../lib/api"

function leaf<T>(value: T): BcLeaf<T> {
  return { value, src: "inferred", conf: "med", as_of: null, evidence: null }
}

/** Minimal but complete BusinessContextDoc with a couple of editable values. */
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

describe("BusinessContext (onboarding step 03)", () => {
  it("loads the auto-drafted doc and renders its editable fields", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    bcGetMock.mockResolvedValue(makeDoc())

    await act(async () => {
      render(React.createElement(BusinessContext))
    })

    // The step heading + the doc's drafted value are on screen.
    expect(document.querySelector(".onb-h")?.textContent).toMatch(/business context/i)
    const whatItDoes = document.querySelector(
      '[data-field="product_value.what_it_does"] textarea',
    ) as HTMLTextAreaElement
    expect(whatItDoes).not.toBeNull()
    expect(whatItDoes.value).toContain("Reconciles payments")
  })

  it("edits a field and PUTs the edit on Continue, then advances to strategy", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    bcGetMock.mockResolvedValue(makeDoc())
    bcUpdateMock.mockResolvedValue({ ok: true, version: 4 })
    updateWorkspaceMock.mockResolvedValue(makeWorkspace())
    advanceStepMock.mockResolvedValue(undefined)

    await act(async () => {
      render(React.createElement(BusinessContext))
    })

    const whatItDoes = document.querySelector(
      '[data-field="product_value.what_it_does"] textarea',
    ) as HTMLTextAreaElement
    fireEvent.change(whatItDoes, { target: { value: "Edited description." } })

    const continueBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /continue/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      continueBtn.click()
    })

    expect(bcUpdateMock).toHaveBeenCalledTimes(1)
    const sent = bcUpdateMock.mock.calls[0][0] as BusinessContextDoc
    expect(sent.product_value.what_it_does.value).toBe("Edited description.")
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 4)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/strategy")
  })

  // ── relocated company-shape fields (moved off onb1) ─────────────────────────
  it("renders the relocated industry / business-type / tech-stack controls", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    bcGetMock.mockResolvedValue(makeDoc())

    await act(async () => {
      render(React.createElement(BusinessContext))
    })

    expect(document.querySelector("[data-bc-company-shape]")).not.toBeNull()
    expect(document.querySelector('[data-field="industry"] select')).not.toBeNull()
    expect(document.querySelector('[data-field="businessType"] select')).not.toBeNull()
    expect(document.querySelector(".onb-chip")).not.toBeNull()
    expect(document.body.textContent).toContain("Tech stack")
  })

  it("persists the relocated company-shape fields on Continue (industry / business type / tech stack)", async () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({
          industry: "Fintech",
          business_type: "Marketplace",
          tech_stack: [],
        }),
      }),
    )
    bcGetMock.mockResolvedValue(makeDoc())
    bcUpdateMock.mockResolvedValue({ ok: true, version: 4 })
    updateWorkspaceMock.mockResolvedValue(makeWorkspace())
    advanceStepMock.mockResolvedValue(undefined)

    await act(async () => {
      render(React.createElement(BusinessContext))
    })

    // Toggle a tech-stack chip on.
    const chip = document.querySelector(".onb-chip") as HTMLButtonElement
    const chipLabel = chip.textContent ?? ""
    fireEvent.click(chip)

    const continueBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /continue/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      continueBtn.click()
    })

    expect(updateWorkspaceMock).toHaveBeenCalledTimes(1)
    const [id, patch] = updateWorkspaceMock.mock.calls[0] as [
      string,
      Record<string, unknown>,
    ]
    expect(id).toBe("ws-1")
    expect(patch.industry).toBe("Fintech")
    expect(patch.business_type).toBe("Marketplace")
    expect(patch.tech_stack).toEqual([chipLabel])
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 4)
  })

  it("shows the empty 'not generated yet' state on a 404 (null doc), still renders company-shape, and stays skippable", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    bcGetMock.mockResolvedValue(null) // GET returned 404
    updateWorkspaceMock.mockResolvedValue(makeWorkspace())
    advanceStepMock.mockResolvedValue(undefined)

    await act(async () => {
      render(React.createElement(BusinessContext))
    })

    expect(document.querySelector('[data-bc-state="empty"]')).not.toBeNull()
    // Company-shape fields still render even when the doc isn't drafted yet.
    expect(document.querySelector("[data-bc-company-shape]")).not.toBeNull()
    // No doc → Continue must NOT PUT the doc, but must still advance + persist
    // the company-shape fields.
    const continueBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /continue/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      continueBtn.click()
    })
    expect(bcUpdateMock).not.toHaveBeenCalled()
    expect(updateWorkspaceMock).toHaveBeenCalledTimes(1)
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 4)
  })

  it("Skip for now advances without PUTting doc edits (still persists company-shape)", async () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx())
    bcGetMock.mockResolvedValue(makeDoc())
    updateWorkspaceMock.mockResolvedValue(makeWorkspace())
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
    expect(updateWorkspaceMock).toHaveBeenCalledTimes(1)
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 4)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/strategy")
  })
})
