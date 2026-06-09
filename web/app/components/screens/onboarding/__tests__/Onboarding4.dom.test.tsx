// @vitest-environment jsdom
//
// Container-level mount test for onboarding step 04 — the consolidated success-
// metrics page. Asserts: suggested metrics render selectable, add-your-own
// works, the industry/business-type dropdowns are pre-filled from analysis yet
// editable, and Save persists the confirmed industry/business-type to the
// company AND the metrics to the KPI tree. Plus the redirect-in-effect safety.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const updateWorkspaceMock = vi.fn()
const advanceStepMock = vi.fn()
const kpiPutMock = vi.fn()

vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
  advanceOnboardingStep: (...a: unknown[]) => advanceStepMock(...a),
}))
vi.mock("../../../../lib/onboarding/kpiTreeApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../lib/onboarding/kpiTreeApi")>()
  return {
    ...actual,
    kpiTreeApi: { put: (...a: unknown[]) => kpiPutMock(...a) },
  }
})

import { Onboarding4 } from "../Onboarding4"
import { makeWorkspace, makeAnalysis, makeOnboardingCtx } from "./fixtures"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Onboarding4 (container) — consolidated metrics", () => {
  it("renders suggested metrics as selectable options", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 4 }),
        websiteAnalysis: makeAnalysis(),
      }),
    )
    render(React.createElement(Onboarding4))
    expect(screen.getByText("Set your success metrics")).not.toBeNull()
    expect(screen.getByText("Reconciled volume")).not.toBeNull()
    const cards = document.querySelectorAll(".ob-suggested-card")
    expect(cards.length).toBe(2)
  })

  it("pre-fills industry/business-type dropdowns from analysis yet keeps them editable", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        // workspace carries no industry yet → comes from analysis
        workspace: makeWorkspace({ onboarding_step: 4, industry: null, business_type: null }),
        websiteAnalysis: makeAnalysis({ industry: "Fintech", business_type: "Marketplace" }),
      }),
    )
    render(React.createElement(Onboarding4))
    const industrySel = document.querySelector(
      'select[aria-label="Industry"]',
    ) as HTMLSelectElement
    const bizSel = document.querySelector(
      'select[aria-label="Business type"]',
    ) as HTMLSelectElement
    expect(industrySel.value).toBe("Fintech")
    expect(bizSel.value).toBe("Marketplace")
    expect(industrySel.disabled).toBe(false)
    // user can override
    fireEvent.change(industrySel, { target: { value: "Healthtech" } })
    expect(
      (document.querySelector('select[aria-label="Industry"]') as HTMLSelectElement).value,
    ).toBe("Healthtech")
  })

  it("adds a custom metric via Add your own", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 4 }),
        websiteAnalysis: makeAnalysis(),
      }),
    )
    render(React.createElement(Onboarding4))
    const nameInput = document.querySelector(
      'input[aria-label="Custom metric name"]',
    ) as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: "Gross margin" } })
    const addBtn = Array.from(document.querySelectorAll("button")).find(
      (b) => b.textContent === "Add",
    ) as HTMLButtonElement
    fireEvent.click(addBtn)
    expect(screen.getByText("1 supporting metric selected")).not.toBeNull()
    expect(screen.getByText("Gross margin")).not.toBeNull()
  })

  it("persists confirmed industry/business-type to the company AND metrics to the KPI tree on save", async () => {
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 5 }))
    advanceStepMock.mockResolvedValue(makeWorkspace({ onboarding_step: 5 }))
    kpiPutMock.mockResolvedValue({ ok: true, version: 2 })
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 4, industry: null, business_type: null }),
        websiteAnalysis: makeAnalysis({ industry: "Fintech", business_type: "Marketplace" }),
      }),
    )
    render(React.createElement(Onboarding4))

    // North Star is required to save.
    const ns = document.querySelector(
      'input[placeholder="The one metric that best captures product value"]',
    ) as HTMLInputElement
    fireEvent.change(ns, { target: { value: "Reconciled volume" } })

    // select a suggested metric
    const card = document.querySelector(".ob-suggested-card") as HTMLButtonElement
    fireEvent.click(card)

    const continueBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /continue/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      continueBtn.click()
    })

    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      industry: "Fintech",
      business_type: "Marketplace",
    })
    expect(kpiPutMock).toHaveBeenCalledTimes(1)
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 5)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/5")
  })

  it("works on the graceful-degrade path (analysis ok:false → manual entry)", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 4, industry: null, business_type: null }),
        websiteAnalysis: makeAnalysis({
          ok: false,
          reason: "blocked_url",
          industry: null,
          business_type: null,
          business_context: "",
          suggested_metrics: [],
        }),
      }),
    )
    render(React.createElement(Onboarding4))
    // no suggestions → manual fallback prompt, dropdowns still present + editable
    expect(screen.getByText(/No suggestions yet/)).not.toBeNull()
    const industrySel = document.querySelector(
      'select[aria-label="Industry"]',
    ) as HTMLSelectElement
    expect(industrySel).not.toBeNull()
    expect(industrySel.disabled).toBe(false)
  })

  it("shows the loading shell while the workspace is loading", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(Onboarding4))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(Onboarding4))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/1")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
