// @vitest-environment jsdom
//
// Container-level mount test for the REDESIGNED onboarding metrics page — the
// pick-exactly-3 success-metrics page. Asserts: a flat candidate pool is seeded
// from the website analysis (with up to 3 pre-selected), selecting/deselecting
// toggles the green `.sel` state, the page enforces EXACTLY 3 picks to advance
// (it no longer blocks on a separate North Star / supporting-metric split),
// add-your-own works, the industry/business-type dropdowns are pre-filled yet
// editable, and Save persists industry/business-type to the company AND the 3
// picks to the KPI tree. Plus the redirect-in-effect safety.
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
const kpiSelectionMock = vi.fn()

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
    kpiTreeApi: {
      put: vi.fn(),
      putFromSelection: (...a: unknown[]) => kpiSelectionMock(...a),
    },
  }
})

import { Metrics } from "../Metrics"
import { makeWorkspace, makeAnalysis, makeOnboardingCtx } from "./fixtures"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// An analysis that returns FIVE suggestions, so there's a real pool to pick 3
// from (the default fixture returns only 2).
function makeFiveAnalysis() {
  return makeAnalysis({
    suggested_metrics: [
      { metric: "Reconciled volume", description: "Total $ reconciled / week." },
      { metric: "Active connected accounts", description: "Accounts with a live sync." },
      { metric: "Incremental revenue", description: "New revenue / week." },
      { metric: "Conversion rate", description: "Signup → paid." },
      { metric: "Weekly active teams", description: "Teams active this week." },
    ],
  })
}

function selectedCards(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll(".metric-card.sel")).map(
    (c) => c.getAttribute("data-metric") ?? "",
  )
}

function continueBtn(): HTMLButtonElement {
  return Array.from(document.querySelectorAll("button")).find((b) =>
    /continue/i.test(b.textContent ?? ""),
  ) as HTMLButtonElement
}

function cardByName(container: HTMLElement, name: string): HTMLButtonElement {
  return container.querySelector(
    `.metric-card[data-metric="${name}"]`,
  ) as HTMLButtonElement
}

describe("Metrics (container) — pick 3 to 5", () => {
  it("seeds a flat candidate pool from the analysis with 3 pre-selected (the minimum)", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 2 }),
        websiteAnalysis: makeFiveAnalysis(),
      }),
    )
    const { container } = render(React.createElement(Metrics))
    expect(screen.getByText(/Set your success/i)).not.toBeNull()
    // The 5 analysis suggestions all become toggleable cards (the pool may be
    // rounded out with curated/industry fallbacks, so it's at least 5).
    const cardNames = Array.from(container.querySelectorAll(".metric-card")).map(
      (c) => c.getAttribute("data-metric"),
    )
    expect(container.querySelectorAll(".metric-card").length).toBeGreaterThanOrEqual(5)
    for (const m of [
      "Reconciled volume",
      "Active connected accounts",
      "Incremental revenue",
      "Conversion rate",
      "Weekly active teams",
    ]) {
      expect(cardNames).toContain(m)
    }
    // 3 are pre-selected (the minimum), shown in the green .sel state
    expect(selectedCards(container).length).toBe(3)
    // the count reflects 3 selected and a ready state
    const count = container.querySelector(".metric-count") as HTMLElement
    expect(count.textContent).toContain("3")
    expect(count.textContent).toContain("selected")
    // there is NO separate North Star input and NO supporting-metric tree
    expect(
      document.querySelector(
        'input[placeholder="The one metric that best captures product value"]',
      ),
    ).toBeNull()
    expect(container.querySelector(".mt-source")).toBeNull()
  })

  it("allows up to 5 picks; a 6th is refused with a max-5 warning; deselecting frees a slot", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 2 }),
        websiteAnalysis: makeFiveAnalysis(),
      }),
    )
    const { container } = render(React.createElement(Metrics))
    expect(selectedCards(container).length).toBe(3)

    const allNames = () =>
      Array.from(container.querySelectorAll(".metric-card")).map(
        (c) => c.getAttribute("data-metric") as string,
      )
    const unpicked = () =>
      allNames().filter((n) => !selectedCards(container).includes(n))
    // Need ≥3 spare candidates to exercise the 4th, 5th, and (refused) 6th.
    expect(unpicked().length).toBeGreaterThanOrEqual(3)

    // 4th and 5th picks are allowed (max is 5).
    fireEvent.click(cardByName(container, unpicked()[0]))
    expect(selectedCards(container).length).toBe(4)
    fireEvent.click(cardByName(container, unpicked()[0]))
    expect(selectedCards(container).length).toBe(5)

    // A 6th is refused: count stays 5 and the "up to 5" warning surfaces.
    const sixth = unpicked()[0]
    fireEvent.click(cardByName(container, sixth))
    expect(selectedCards(container)).not.toContain(sixth)
    expect(selectedCards(container).length).toBe(5)
    expect(screen.getByText(/up to 5 metrics/i)).not.toBeNull()

    // Deselect one → a slot frees up and the previously-refused card can be picked.
    fireEvent.click(cardByName(container, selectedCards(container)[0]))
    expect(selectedCards(container).length).toBe(4)
    fireEvent.click(cardByName(container, sixth))
    expect(selectedCards(container)).toContain(sixth)
    expect(selectedCards(container).length).toBe(5)
  })

  it("BLOCKS Continue when fewer than 3 are picked, with a pick-at-least-3 error (no supporting-metric blocker)", async () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 2 }),
        websiteAnalysis: makeFiveAnalysis(),
      }),
    )
    const { container } = render(React.createElement(Metrics))

    // drop to 2 picks
    const picked = selectedCards(container)[0]
    fireEvent.click(cardByName(container, picked))
    expect(selectedCards(container).length).toBe(2)

    await act(async () => {
      continueBtn().click()
    })

    // nothing persisted, no advance, no navigation
    expect(updateWorkspaceMock).not.toHaveBeenCalled()
    expect(kpiSelectionMock).not.toHaveBeenCalled()
    expect(advanceStepMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
    // a pick-at-least-3 error shows (the old "Set a North Star" blocker is gone)
    expect(screen.getByText(/Pick at least 3 metrics to continue\./)).not.toBeNull()
    // No North Star INPUT exists anymore (the metric is inferred server-side).
    expect(
      document.querySelector(
        'input[placeholder="The one metric that best captures product value"]',
      ),
    ).toBeNull()
  })

  it("advances on 3 picks, sending the picks to the from-selection endpoint (server infers the North Star)", async () => {
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 3 }))
    advanceStepMock.mockResolvedValue(makeWorkspace({ onboarding_step: 3 }))
    kpiSelectionMock.mockResolvedValue({ ok: true, version: 2, north_star: "x" })
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 2, industry: null, business_type: null }),
        websiteAnalysis: makeFiveAnalysis(),
      }),
    )
    const { container } = render(React.createElement(Metrics))
    const picks = selectedCards(container)
    expect(picks.length).toBe(3)

    await act(async () => {
      continueBtn().click()
    })

    // industry/business-type confirmed to the company (from analysis)
    expect(updateWorkspaceMock).toHaveBeenCalledWith("ws-1", {
      industry: "Fintech",
      business_type: "Marketplace",
    })
    // the picks → /kpi-tree/from-selection as a flat metric list; the server
    // infers the North Star (no north_star/placeholder sent from the client)
    expect(kpiSelectionMock).toHaveBeenCalledTimes(1)
    const payload = kpiSelectionMock.mock.calls[0][0] as {
      metrics: { metric: string; description: string }[]
    }
    const persisted = payload.metrics.map((m) => m.metric)
    // all three picks are sent (none dropped), and no north_star field is present
    expect(persisted.sort()).toEqual([...picks].sort())
    expect(persisted.length).toBe(3)
    expect("north_star" in payload).toBe(false)

    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 2)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
  })

  it("seeds the SaaS curated defaults when the analysis returned no suggestions", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 2, business_type: "SaaS", industry: "B2B SaaS" }),
        websiteAnalysis: makeAnalysis({ business_type: "SaaS", industry: "B2B SaaS", suggested_metrics: [] }),
      }),
    )
    const { container } = render(React.createElement(Metrics))
    const names = Array.from(container.querySelectorAll(".metric-card")).map(
      (c) => c.getAttribute("data-metric"),
    )
    // The SaaS curated defaults lead the pool (rename: Incremental revenue).
    expect(names.slice(0, 3)).toEqual([
      "Incremental revenue",
      "Number of new subscribers",
      "Conversion rate",
    ])
    expect(names).toContain("Incremental revenue")
    expect(names).not.toContain("Net revenue retention")
    // 3 pre-selected
    expect(container.querySelectorAll(".metric-card.sel").length).toBe(3)
  })

  it("lets the user add a custom candidate, which lands selected if there's room", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 2 }),
        websiteAnalysis: makeFiveAnalysis(),
      }),
    )
    const { container } = render(React.createElement(Metrics))
    // drop to 2 picks so the custom add has room to auto-select
    fireEvent.click(cardByName(container, selectedCards(container)[0]))
    expect(selectedCards(container).length).toBe(2)

    const nameInput = document.querySelector(
      'input[aria-label="Custom metric name"]',
    ) as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: "Gross margin" } })
    const addBtn = Array.from(document.querySelectorAll("button")).find(
      (b) => (b.textContent ?? "").trim().includes("Add"),
    ) as HTMLButtonElement
    fireEvent.click(addBtn)

    // appears as a card AND is selected (back to 3)
    expect(cardByName(container, "Gross margin")).not.toBeNull()
    expect(selectedCards(container)).toContain("Gross margin")
    expect(selectedCards(container).length).toBe(3)
  })

  it("hydrates the candidate pool from a saved KPI tree (first 3 pre-selected)", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({
          onboarding_step: 2,
          kpi_tree: {
            north_star: "Weekly active users",
            north_star_description: "",
            metrics: [
              { name: "Weekly active users", description: "WAU." },
              { name: "Day-30 retention", description: "" },
              { name: "Conversion rate", description: "" },
              { name: "Incremental revenue", description: "" },
            ],
          },
        }),
        websiteAnalysis: null,
      }),
    )
    const { container } = render(React.createElement(Metrics))
    expect(container.querySelectorAll(".metric-card").length).toBe(4)
    expect(selectedCards(container).length).toBe(3)
  })

  it("pre-fills industry/business-type dropdowns from analysis yet keeps them editable", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 2, industry: null, business_type: null }),
        websiteAnalysis: makeAnalysis({ industry: "Fintech", business_type: "Marketplace" }),
      }),
    )
    render(React.createElement(Metrics))
    const industrySel = document.querySelector(
      'select[aria-label="Industry"]',
    ) as HTMLSelectElement
    const bizSel = document.querySelector(
      'select[aria-label="Business type"]',
    ) as HTMLSelectElement
    expect(industrySel.value).toBe("Fintech")
    expect(bizSel.value).toBe("Marketplace")
    expect(industrySel.disabled).toBe(false)
    fireEvent.change(industrySel, { target: { value: "Healthtech" } })
    expect(
      (document.querySelector('select[aria-label="Industry"]') as HTMLSelectElement).value,
    ).toBe("Healthtech")
  })

  it("shows the loading shell while the workspace is loading", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(Metrics))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(Metrics))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/business-info")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
