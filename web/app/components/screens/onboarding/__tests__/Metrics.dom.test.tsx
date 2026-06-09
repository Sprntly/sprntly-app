// @vitest-environment jsdom
//
// Container-level mount test for the onboarding metrics page — the consolidated
// success-metrics page. Asserts: analysis-suggested metrics are PRE-SEEDED as
// tree-target cards (no selectable suggestion chips), add-your-own works, the
// industry/business-type dropdowns are pre-filled from analysis yet editable,
// and Save persists the confirmed industry/business-type to the company AND the
// metrics to the KPI tree. Plus the redirect-in-effect safety.
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

import { Metrics } from "../Metrics"
import { makeWorkspace, makeAnalysis, makeOnboardingCtx } from "./fixtures"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Metrics (container) — consolidated metrics", () => {
  it("pre-seeds analysis-suggested metrics as tree-target cards, with NO suggestion chips", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 2 }),
        websiteAnalysis: makeAnalysis(),
      }),
    )
    const { container } = render(React.createElement(Metrics))
    expect(screen.getByText(/Set your success/i)).not.toBeNull()
    // seeded as tree-target cards (edit + delete), inside the metric-tree
    expect(container.querySelector(".metric-tree")).not.toBeNull()
    expect(
      container.querySelector('.mt-target[data-metric="Reconciled volume"]'),
    ).not.toBeNull()
    expect(container.querySelectorAll(".mt-target").length).toBe(2)
    // the selectable suggestion-chip surface is gone entirely
    expect(container.querySelector(".mt-suggested")).toBeNull()
    expect(container.querySelector("#suggestedMetrics")).toBeNull()
    expect(container.querySelector("[aria-pressed]")).toBeNull()
  })

  it("pre-seeds ALL suggested metrics into the supporting list on load (rendered as tree targets)", () => {
    const analysis = makeAnalysis()
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 2 }),
        websiteAnalysis: analysis,
      }),
    )
    const { container } = render(React.createElement(Metrics))
    const n = analysis.suggested_metrics.length
    // every suggestion is now a tree target...
    const targets = container.querySelectorAll(".mt-target")
    expect(targets.length).toBe(n)
    // count reflects N
    const count = container.querySelector(".metric-count") as HTMLElement
    expect(count.textContent).toContain(`${n} supporting metric`)
    // targets live in the tree, not a separate bottom block
    expect(container.querySelector(".metric-desc-block")).toBeNull()
    expect(container.querySelector(".metric-tree .mt-targets-cards")).not.toBeNull()
  })

  it("seeding does NOT clobber a user deletion on re-render", () => {
    const ctx = makeOnboardingCtx({
      workspace: makeWorkspace({ onboarding_step: 2 }),
      websiteAnalysis: makeAnalysis(),
    })
    onboardingMock.mockReturnValue(ctx)
    const { container, rerender } = render(React.createElement(Metrics))
    const n = (ctx.websiteAnalysis as ReturnType<typeof makeAnalysis>).suggested_metrics.length

    // delete the first target
    const del = container.querySelector(".mt-target .mt-target-del") as HTMLButtonElement
    fireEvent.click(del)
    expect(container.querySelectorAll(".mt-target").length).toBe(n - 1)

    // a re-render (same analysis) must not re-seed the deleted metric back in
    rerender(React.createElement(Metrics))
    expect(container.querySelectorAll(".mt-target").length).toBe(n - 1)
  })

  it("delete removes a metric and decrements the count; it can be re-added via 'write your own'", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 2 }),
        websiteAnalysis: makeAnalysis(),
      }),
    )
    const { container } = render(React.createElement(Metrics))
    const count = () => (container.querySelector(".metric-count") as HTMLElement).textContent ?? ""
    expect(count()).toContain("2 supporting metric")

    // delete "Reconciled volume"
    const del = container.querySelector(
      '.mt-target[data-metric="Reconciled volume"] .mt-target-del',
    ) as HTMLButtonElement
    fireEvent.click(del)
    expect(count()).toContain("1 supporting metric")
    expect(
      container.querySelector('.mt-target[data-metric="Reconciled volume"]'),
    ).toBeNull()

    // re-add it by hand via the "write your own" input (no suggestion chips)
    const customInput = container.querySelector(
      '[aria-label="Custom metric name"]',
    ) as HTMLInputElement
    fireEvent.change(customInput, { target: { value: "Reconciled volume" } })
    const addBtn = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.includes("Add"),
    ) as HTMLButtonElement
    fireEvent.click(addBtn)
    expect(count()).toContain("2 supporting metric")
    expect(
      container.querySelector('.mt-target[data-metric="Reconciled volume"]'),
    ).not.toBeNull()
  })

  it("custom add then delete works, and deleting to empty shows the targets empty state", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        // no suggestions → start empty
        workspace: makeWorkspace({ onboarding_step: 2 }),
        websiteAnalysis: makeAnalysis({ suggested_metrics: [] }),
      }),
    )
    const { container } = render(React.createElement(Metrics))
    expect(container.querySelector(".mt-targets-empty")).not.toBeNull()

    // add a custom metric → it appears as a tree target
    const nameInput = document.querySelector(
      'input[aria-label="Custom metric name"]',
    ) as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: "Gross margin" } })
    const addBtn = Array.from(document.querySelectorAll("button")).find(
      (b) => (b.textContent ?? "").trim() === "Add",
    ) as HTMLButtonElement
    fireEvent.click(addBtn)
    expect(
      container.querySelector('.mt-target[data-metric="Gross margin"]'),
    ).not.toBeNull()

    // delete it → back to the empty state
    const del = container.querySelector(
      '.mt-target[data-metric="Gross margin"] .mt-target-del',
    ) as HTMLButtonElement
    fireEvent.click(del)
    expect(container.querySelector(".mt-target")).toBeNull()
    expect(container.querySelector(".mt-targets-empty")).not.toBeNull()
  })

  it("editing a target's description updates it", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 2 }),
        websiteAnalysis: makeAnalysis(),
      }),
    )
    const { container } = render(React.createElement(Metrics))
    const ta = container.querySelector(
      'textarea[aria-label="Description for Reconciled volume"]',
    ) as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: "Edited description." } })
    expect(
      (
        container.querySelector(
          'textarea[aria-label="Description for Reconciled volume"]',
        ) as HTMLTextAreaElement
      ).value,
    ).toBe("Edited description.")
  })

  it("pre-fills industry/business-type dropdowns from analysis yet keeps them editable", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        // workspace carries no industry yet → comes from analysis
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
    // user can override
    fireEvent.change(industrySel, { target: { value: "Healthtech" } })
    expect(
      (document.querySelector('select[aria-label="Industry"]') as HTMLSelectElement).value,
    ).toBe("Healthtech")
  })

  it("adds a custom metric via Add your own (appended as a new tree target)", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        // no suggestions → start from an empty supporting list
        workspace: makeWorkspace({ onboarding_step: 2 }),
        websiteAnalysis: makeAnalysis({ suggested_metrics: [] }),
      }),
    )
    const { container } = render(React.createElement(Metrics))
    const nameInput = document.querySelector(
      'input[aria-label="Custom metric name"]',
    ) as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: "Gross margin" } })
    const addBtn = Array.from(document.querySelectorAll("button")).find(
      (b) => (b.textContent ?? "").trim() === "Add",
    ) as HTMLButtonElement
    fireEvent.click(addBtn)
    // count text is split across a <strong> node: "1" + " supporting metric selected"
    const count = document.querySelector(".metric-count") as HTMLElement
    expect(count.textContent).toContain("1 supporting metric selected")
    // appears immediately as a tree target
    expect(
      container.querySelector('.mt-target[data-metric="Gross margin"]'),
    ).not.toBeNull()
  })

  it("persists confirmed industry/business-type to the company AND metrics to the KPI tree on save, then advances to step 3", async () => {
    updateWorkspaceMock.mockResolvedValue(makeWorkspace({ onboarding_step: 3 }))
    advanceStepMock.mockResolvedValue(makeWorkspace({ onboarding_step: 3 }))
    kpiPutMock.mockResolvedValue({ ok: true, version: 2 })
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 2, industry: null, business_type: null }),
        websiteAnalysis: makeAnalysis({ industry: "Fintech", business_type: "Marketplace" }),
      }),
    )
    const { container } = render(React.createElement(Metrics))

    // North Star is required to save.
    const ns = document.querySelector(
      'input[placeholder="The one metric that best captures product value"]',
    ) as HTMLInputElement
    fireEvent.change(ns, { target: { value: "Net revenue retention" } })

    // The 2 suggested metrics are pre-seeded; delete one so we persist exactly
    // the post-edit set (the remaining "Active connected accounts").
    const del = container.querySelector(
      '.mt-target[data-metric="Reconciled volume"] .mt-target-del',
    ) as HTMLButtonElement
    fireEvent.click(del)

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
    // persists EXACTLY the post-edit supporting set (the deleted one is gone).
    const payload = kpiPutMock.mock.calls[0][0] as {
      primary_metrics: { metric: string }[]
      secondary_signals: { metric: string }[]
    }
    const persistedNames = [...payload.primary_metrics, ...payload.secondary_signals].map(
      (m) => m.metric,
    )
    expect(persistedNames).toEqual(["Active connected accounts"])
    // New flow: metrics page advances to the optimizing-for step (route 3).
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 3)
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/connectors")
  })

  it("works on the graceful-degrade path (analysis ok:false → manual entry)", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 2, industry: null, business_type: null }),
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
    render(React.createElement(Metrics))
    // no suggestions → manual fallback prompt, dropdowns still present + editable
    expect(screen.getByText(/No supporting metrics yet/)).not.toBeNull()
    const industrySel = document.querySelector(
      'select[aria-label="Industry"]',
    ) as HTMLSelectElement
    expect(industrySel).not.toBeNull()
    expect(industrySel.disabled).toBe(false)
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
