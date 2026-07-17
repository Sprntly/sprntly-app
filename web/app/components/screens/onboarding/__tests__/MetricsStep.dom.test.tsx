// @vitest-environment jsdom
//
// Container mount test for onboarding step 03 — "Your metrics" (v6 screenshot
// spec 2026-07-17: pick UP TO 5 metrics, at least 1, plus the prioritization
// framework moved here from the old team step). Covers: the candidate chips
// seed from the business-type/industry defaults (fixture workspace: SaaS /
// B2B SaaS) with the first 3 pre-selected; a saved workspace kpi_tree takes
// seeding priority (up to 5 pre-selected); picking a 6th metric flashes the
// limit warning; Continue requires ≥1 metric AND a framework; a valid Continue
// PUTs the picks to the from-selection endpoint, persists the framework via
// updateWorkspace (onboarding_step 4) and routes to /onboarding/connectors;
// "Skip to end ⇥" persists then jumps to the review step.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const advanceStepMock = vi.fn()
const updateWorkspaceMock = vi.fn()
const kpiSelectionMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  advanceOnboardingStep: (...a: unknown[]) => advanceStepMock(...a),
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
}))
vi.mock("../../../../lib/onboarding/useFormDraft", () => ({
  saveDraft: vi.fn(),
  loadDraft: () => null,
  clearDraft: vi.fn(),
}))
vi.mock("../../../../lib/onboarding/kpiTreeApi", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("../../../../lib/onboarding/kpiTreeApi")>()
  return {
    ...actual,
    kpiTreeApi: {
      put: vi.fn(),
      putFromSelection: (...a: unknown[]) => kpiSelectionMock(...a),
    },
  }
})

import { MetricsStep } from "../MetricsStep"
import { ONBOARDING_STEP_COUNT } from "../../../../lib/onboarding/types"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

// The fixture workspace is business_type "SaaS" / industry "B2B SaaS" with an
// empty kpi_tree and no website analysis, so the pool is the SaaS curated
// defaults merged with the B2B-SaaS industry fallback (deduped).
const DEFAULT_POOL = [
  "Incremental revenue",
  "Number of new subscribers",
  "Conversion rate",
  "Weekly active teams",
  "Activation rate",
]

function mount(workspace = makeWorkspace({ onboarding_step: 3 })) {
  onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace }))
  return render(React.createElement(MetricsStep))
}

function chipNames(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll("#suggestedMetrics .metric")).map(
    (c) => c.getAttribute("data-metric") ?? "",
  )
}

function selectedChips(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll("#suggestedMetrics .metric.sel")).map(
    (c) => c.getAttribute("data-metric") ?? "",
  )
}

function chipByName(container: HTMLElement, name: string): HTMLButtonElement {
  return container.querySelector(
    `#suggestedMetrics .metric[data-metric="${name}"]`,
  ) as HTMLButtonElement
}

function frameworkSelect(): HTMLSelectElement {
  return document.querySelector(
    'select[aria-label="Prioritization framework"]',
  ) as HTMLSelectElement
}

function continueBtn(): HTMLButtonElement {
  return Array.from(document.querySelectorAll("button")).find((b) =>
    /^continue$/i.test((b.textContent ?? "").trim()),
  ) as HTMLButtonElement
}

function skipToEndBtn(): HTMLButtonElement {
  return Array.from(document.querySelectorAll("button")).find((b) =>
    /Skip to end/.test(b.textContent ?? ""),
  ) as HTMLButtonElement
}

beforeEach(() => {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("MetricsStep (onboarding step 03 — up to 5 metrics + framework)", () => {
  it("seeds candidate chips from the business-type/industry defaults with the first 3 pre-selected", () => {
    const { container } = mount()
    const h = container.querySelector(".onb-h") as HTMLElement
    expect(h.textContent).toBe("Your metrics.")
    expect(chipNames(container)).toEqual(DEFAULT_POOL)
    expect(selectedChips(container)).toEqual(DEFAULT_POOL.slice(0, 3))
    // Selected chips carry the pressed/selected a11y state.
    const first = chipByName(container, DEFAULT_POOL[0])
    expect(first.getAttribute("aria-pressed")).toBe("true")
    // The framework select renders alongside, empty by default.
    expect(frameworkSelect()).not.toBeNull()
    expect(frameworkSelect().value).toBe("")
  })

  it("seeds the chips from a saved workspace kpi_tree instead, pre-selecting up to 5", () => {
    const { container } = mount(
      makeWorkspace({
        onboarding_step: 3,
        kpi_tree: {
          north_star: "Custom A",
          north_star_description: "",
          metrics: [
            { name: "Custom A", description: "a" },
            { name: "Custom B", description: "" },
            { name: "Custom C", description: "" },
            { name: "Custom D", description: "" },
            { name: "Custom E", description: "" },
            { name: "Custom F", description: "" },
          ],
        },
      }),
    )
    expect(chipNames(container)).toEqual([
      "Custom A",
      "Custom B",
      "Custom C",
      "Custom D",
      "Custom E",
      "Custom F",
    ])
    // Up to METRIC_PICKS (5) are pre-selected — never all 6.
    expect(selectedChips(container)).toEqual([
      "Custom A",
      "Custom B",
      "Custom C",
      "Custom D",
      "Custom E",
    ])
  })

  it("selecting a 6th metric flashes the up-to-5 limit warning and keeps the selection at 5", () => {
    const { container } = mount(
      makeWorkspace({
        onboarding_step: 3,
        kpi_tree: {
          north_star: "Custom A",
          north_star_description: "",
          metrics: ["A", "B", "C", "D", "E", "F"].map((n) => ({
            name: `Custom ${n}`,
            description: "",
          })),
        },
      }),
    )
    expect(selectedChips(container).length).toBe(5)
    fireEvent.click(chipByName(container, "Custom F"))
    expect(selectedChips(container).length).toBe(5)
    expect(selectedChips(container)).not.toContain("Custom F")
    expect(
      screen.getByText("You can pick up to 5 metrics — deselect one to swap."),
    ).not.toBeNull()
  })

  it("Continue with ZERO metrics selected shows the at-least-one error and persists nothing", async () => {
    const { container } = mount()
    // Deselect all 3 pre-selected chips.
    for (const name of DEFAULT_POOL.slice(0, 3)) {
      fireEvent.click(chipByName(container, name))
    }
    expect(selectedChips(container).length).toBe(0)
    fireEvent.change(frameworkSelect(), { target: { value: "rice" } })

    await act(async () => {
      continueBtn().click()
    })

    expect(screen.getByText("Pick at least one metric to continue.")).not.toBeNull()
    expect(kpiSelectionMock).not.toHaveBeenCalled()
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
  })

  it("Continue without a framework shows the framework error and persists nothing", async () => {
    const { container } = mount()
    expect(selectedChips(container).length).toBe(3)

    await act(async () => {
      continueBtn().click()
    })

    expect(screen.getByText("Pick how your team prioritizes.")).not.toBeNull()
    expect(kpiSelectionMock).not.toHaveBeenCalled()
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
  })

  it("a valid Continue PUTs the picks, saves the framework (step 4) and routes to connectors", async () => {
    kpiSelectionMock.mockResolvedValue({ ok: true, version: 1, north_star: "x" })
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 4 }))
    const { container } = mount()
    expect(selectedChips(container).length).toBe(3)
    fireEvent.change(frameworkSelect(), { target: { value: "rice" } })

    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
    })
    expect(kpiSelectionMock).toHaveBeenCalledTimes(1)
    const payload = kpiSelectionMock.mock.calls[0][0] as {
      metrics: { metric: string; description: string }[]
    }
    expect(payload.metrics.map((m) => m.metric)).toEqual(DEFAULT_POOL.slice(0, 3))
    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      prioritization_framework: "rice",
      onboarding_step: 4,
    })
    expect(advanceStepMock).not.toHaveBeenCalled()
  })

  it("'Skip to end ⇥' persists the picks + framework, jumps to step 9 and routes to review", async () => {
    kpiSelectionMock.mockResolvedValue({ ok: true, version: 1, north_star: "x" })
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 4 }))
    advanceStepMock.mockResolvedValue(
      makeWorkspace({ onboarding_step: ONBOARDING_STEP_COUNT }),
    )
    mount()
    fireEvent.change(frameworkSelect(), { target: { value: "wsjf" } })

    await act(async () => {
      skipToEndBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/review")
    })
    expect(kpiSelectionMock).toHaveBeenCalledTimes(1)
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", ONBOARDING_STEP_COUNT)
  })

  it("shows the loading shell while the workspace is loading", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(MetricsStep))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(MetricsStep))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/company")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
