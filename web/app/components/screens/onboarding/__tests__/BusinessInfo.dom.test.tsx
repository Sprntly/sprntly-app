// @vitest-environment jsdom
//
// Container-level mount test for the onboarding step 01 — "Tell us about your
// product" (design scene onb1). The 5-step redesign COMBINES product + metrics
// onto this one screen:
//   - renders the new .onb-card design WITH the pick-3 metric picker inline,
//   - NO LONGER fires the website analysis in the background, and
//   - on Continue persists the workspace + KPI-tree picks then navigates to the
//     BLOCKING /onboarding/analyzing interstitial (which runs the analysis).
// Mounts the real container under jsdom with mocked auth/onboarding/router/store.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const createWorkspaceMock = vi.fn()
const updateWorkspaceMock = vi.fn()
const upsertPrimaryProductMock = vi.fn()
const analyzeWebsiteMock = vi.fn()
const kpiTreePutMock = vi.fn()

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  createWorkspace: (...a: unknown[]) => createWorkspaceMock(...a),
  updateWorkspace: (...a: unknown[]) => updateWorkspaceMock(...a),
  upsertPrimaryProduct: (...a: unknown[]) => upsertPrimaryProductMock(...a),
  markSkippedFields: vi.fn(),
}))
vi.mock("../../../../lib/api", () => ({
  onboardingApi: { analyzeWebsite: (...a: unknown[]) => analyzeWebsiteMock(...a) },
}))
vi.mock("../../../../lib/onboarding/kpiTreeApi", async () => {
  const actual = await vi.importActual<
    typeof import("../../../../lib/onboarding/kpiTreeApi")
  >("../../../../lib/onboarding/kpiTreeApi")
  return {
    ...actual,
    kpiTreeApi: {
      get: vi.fn(),
      put: vi.fn(),
      putFromSelection: (...a: unknown[]) => kpiTreePutMock(...a),
    },
  }
})

import { BusinessInfo } from "../BusinessInfo"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("BusinessInfo (container) — Product + metrics page", () => {
  it("renders the new .onb-card design and the product heading", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const { container } = render(React.createElement(BusinessInfo))
    expect(container.querySelector(".onb-card")).not.toBeNull()
    expect(container.querySelector(".onb-shell")).not.toBeNull()
    expect(screen.getByText(/tell us about your/i)).not.toBeNull()
  })

  it("renders the exact onb1 design copy (subtitle, field labels, note)", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const { container } = render(React.createElement(BusinessInfo))
    const text = container.textContent ?? ""
    // Design subtitle, verbatim.
    expect(text).toContain(
      "A name and your success metrics anchor the whole workspace. You'll add the full description in Settings.",
    )
    // Design field labels.
    expect(text).toContain("Company name")
    expect(text).toContain("Product name")
    expect(text).toContain("Product website")
    expect(text).toContain("Your metrics")
    // Design metric note, verbatim (split across <strong>).
    expect(text).toContain("These are how Sprntly")
    expect(text).toContain("prioritizes which issues and ideas to surface")
    expect(text).toContain(
      "every brief is ranked by impact on the metrics you pick.",
    )
  })

  it("renders the flat onb1 metric-chips picker inline (combined onto this screen)", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const { container } = render(React.createElement(BusinessInfo))
    // The onb1 design renders a flat chip row + custom-add row + a note callout.
    expect(container.querySelector(".metric-chips")).not.toBeNull()
    expect(container.querySelector(".metric")).not.toBeNull()
    expect(container.querySelector(".metric-other-row")).not.toBeNull()
    expect(container.querySelector(".metric-note")).not.toBeNull()
    expect(container.querySelector("#customMetricInput")).not.toBeNull()
    // Inference-seed website input is still present.
    expect(container.querySelector('input[type="url"]')).not.toBeNull()
  })

  it("keeps the 3–5 pick function: toggling chips updates the selection", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    // Seed a saved KPI tree so the chip pool is deterministic in the test.
    const ws = makeWorkspace({
      kpi_tree: {
        north_star: "",
        north_star_description: "",
        metrics: [
          { name: "Weekly active users", description: "" },
          { name: "Net revenue retention", description: "" },
          { name: "Paid conversion rate", description: "" },
          { name: "Activation rate", description: "" },
        ],
      } as never,
    })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: ws }))

    const { container } = render(React.createElement(BusinessInfo))
    const chips = Array.from(
      container.querySelectorAll<HTMLButtonElement>(".metric-chips .metric"),
    )
    expect(chips.length).toBeGreaterThanOrEqual(4)
    // First 3 are pre-selected (MIN_METRIC_PICKS).
    const selectedBefore = chips.filter(
      (c) => c.getAttribute("aria-pressed") === "true",
    )
    expect(selectedBefore.length).toBe(3)
    // Toggling a 4th selects it (3–5 range allows this).
    const fourth = chips.find((c) => c.getAttribute("aria-pressed") === "false")!
    act(() => {
      fourth.click()
    })
    expect(fourth.getAttribute("aria-pressed")).toBe("true")
  })

  it("no longer renders the Stage or Team size steps", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const { container } = render(React.createElement(BusinessInfo))
    expect(container.textContent).not.toContain("Team size")
    // no headcount number input remains
    expect(container.querySelector('input[type="number"]')).toBeNull()
    // tech-stack chips still render (untouched)
    expect(container.querySelector(".onb-chip")).not.toBeNull()
  })

  it("does NOT send stage/team_size in the create payload (dropped cleanly)", async () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    createWorkspaceMock.mockResolvedValue(makeWorkspace())
    kpiTreePutMock.mockResolvedValue({ ok: true, version: 1 })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    render(React.createElement(BusinessInfo))
    const inputs = document.querySelectorAll("input.inp")
    fireEvent.change(inputs[0], { target: { value: "Acme" } })
    fireEvent.change(inputs[1], { target: { value: "Acme App" } })

    const continueBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /continue/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      continueBtn.click()
    })

    expect(createWorkspaceMock).toHaveBeenCalledTimes(1)
    const payload = createWorkspaceMock.mock.calls[0][0] as Record<string, unknown>
    expect("stage" in payload).toBe(false)
    expect("teamSize" in payload).toBe(false)
  })

  it("Continue persists the workspace + KPI picks then navigates to the analyzing interstitial (no background analysis)", async () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    createWorkspaceMock.mockResolvedValue(makeWorkspace())
    kpiTreePutMock.mockResolvedValue({ ok: true, version: 1 })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    render(React.createElement(BusinessInfo))

    const inputs = document.querySelectorAll("input.inp")
    // company name, product name (website is the url input)
    fireEvent.change(inputs[0], { target: { value: "Acme" } })
    fireEvent.change(inputs[1], { target: { value: "Acme App" } })
    fireEvent.change(
      document.querySelector('input[type="url"]') as HTMLInputElement,
      { target: { value: "https://acme.com" } },
    )

    const continueBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /continue/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      continueBtn.click()
    })

    expect(createWorkspaceMock).toHaveBeenCalledTimes(1)
    // The 3 seeded metric picks are persisted to the KPI tree on this screen.
    expect(kpiTreePutMock).toHaveBeenCalledTimes(1)
    // Analysis is NOT fired from this page anymore — the interstitial owns it.
    expect(analyzeWebsiteMock).not.toHaveBeenCalled()
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/analyzing")
  })

  it("blocks Continue (and does not navigate) when required fields are empty", async () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    render(React.createElement(BusinessInfo))
    const continueBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /continue/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      continueBtn.click()
    })
    expect(createWorkspaceMock).not.toHaveBeenCalled()
    expect(routerMock.push).not.toHaveBeenCalled()
  })

  it("shows the loading shell while the workspace is loading", () => {
    authMock.mockReturnValue({ kind: "loading" })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(BusinessInfo))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })
})
