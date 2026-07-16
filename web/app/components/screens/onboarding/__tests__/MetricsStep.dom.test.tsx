// @vitest-environment jsdom
//
// Container mount test for onboarding step 03 — "Your metrics" (the pick-
// exactly-3 chip picker split out of the old combined business-info step).
// Covers: the candidate chips seed from the business-type/industry defaults
// (fixture workspace: SaaS / B2B SaaS) with the first 3 pre-selected; a saved
// workspace kpi_tree takes seeding priority; COMPANY accounts must have
// exactly 3 selected to Continue (fewer → error, no KPI PUT); a valid Continue
// PUTs the picks to the from-selection endpoint, advances to step 4 and routes
// to /onboarding/api-key; PERSONAL accounts get the "skip for now" link which
// records the skip and advances without any KPI PUT.
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
const markSkippedMock = vi.fn()
const kpiSelectionMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  advanceOnboardingStep: (...a: unknown[]) => advanceStepMock(...a),
  markSkippedFields: (...a: unknown[]) => markSkippedMock(...a),
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
import { makeWorkspace, makeOnboardingCtx, makeProfile } from "./fixtures"

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

function mount(
  accountType: "company" | "personal" = "company",
  workspace = makeWorkspace({ onboarding_step: 3 }),
) {
  onboardingMock.mockReturnValue(
    makeOnboardingCtx({
      workspace,
      profile: makeProfile({ account_type: accountType }),
    }),
  )
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

function continueBtn(): HTMLButtonElement {
  return Array.from(document.querySelectorAll("button")).find((b) =>
    /^continue$/i.test((b.textContent ?? "").trim()),
  ) as HTMLButtonElement
}

beforeEach(() => {
  authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("MetricsStep (onboarding step 03 — pick exactly 3 metrics)", () => {
  it("seeds candidate chips from the business-type/industry defaults with the first 3 pre-selected", () => {
    const { container } = mount("company")
    const h = container.querySelector(".onb-h") as HTMLElement
    expect(h.textContent).toBe("Your metrics.")
    expect(chipNames(container)).toEqual(DEFAULT_POOL)
    expect(selectedChips(container)).toEqual(DEFAULT_POOL.slice(0, 3))
    // Selected chips carry the pressed/selected a11y state.
    const first = chipByName(container, DEFAULT_POOL[0])
    expect(first.getAttribute("aria-pressed")).toBe("true")
  })

  it("seeds the chips from a saved workspace kpi_tree instead, when one exists", () => {
    const { container } = mount(
      "company",
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
          ],
        },
      }),
    )
    expect(chipNames(container)).toEqual(["Custom A", "Custom B", "Custom C", "Custom D"])
    expect(selectedChips(container)).toEqual(["Custom A", "Custom B", "Custom C"])
  })

  it("COMPANY: Continue with fewer than 3 selected shows the pick-exactly-3 error and PUTs nothing", async () => {
    const { container } = mount("company")
    // Deselect one of the 3 pre-selected chips → down to 2.
    fireEvent.click(chipByName(container, DEFAULT_POOL[0]))
    expect(selectedChips(container).length).toBe(2)

    await act(async () => {
      continueBtn().click()
    })

    expect(screen.getByText("Pick exactly 3 metrics to continue.")).not.toBeNull()
    expect(kpiSelectionMock).not.toHaveBeenCalled()
    expect(advanceStepMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
  })

  it("COMPANY: Continue with 3 selected PUTs the picks, advances to 4 and routes to api-key", async () => {
    kpiSelectionMock.mockResolvedValue({ ok: true, version: 1, north_star: "x" })
    advanceStepMock.mockResolvedValue(makeWorkspace({ onboarding_step: 4 }))
    const { container } = mount("company")
    expect(selectedChips(container).length).toBe(3)

    await act(async () => {
      continueBtn().click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/api-key")
    })
    expect(kpiSelectionMock).toHaveBeenCalledTimes(1)
    const payload = kpiSelectionMock.mock.calls[0][0] as {
      metrics: { metric: string; description: string }[]
    }
    expect(payload.metrics.map((m) => m.metric)).toEqual(DEFAULT_POOL.slice(0, 3))
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 4)
    expect(markSkippedMock).not.toHaveBeenCalled()
  })

  it("PERSONAL: 'skip for now' records the skip, advances to 4 and routes to api-key without a KPI PUT", async () => {
    markSkippedMock.mockResolvedValue(undefined)
    advanceStepMock.mockResolvedValue(makeWorkspace({ onboarding_step: 4 }))
    mount("personal")

    const skip = screen.getByText("skip for now") as HTMLButtonElement
    await act(async () => {
      skip.click()
    })

    await waitFor(() => {
      expect(routerMock.push).toHaveBeenCalledWith("/onboarding/api-key")
    })
    expect(markSkippedMock).toHaveBeenCalledWith("u-1", ["metrics"])
    expect(advanceStepMock).toHaveBeenCalledWith("ws-1", 4)
    expect(kpiSelectionMock).not.toHaveBeenCalled()
  })

  it("COMPANY accounts get no skip link", () => {
    mount("company")
    expect(screen.queryByText("skip for now")).toBeNull()
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
