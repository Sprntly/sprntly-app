// @vitest-environment jsdom
//
// Container-level mount test for the onboarding Company page (step 01, v4
// .onb-* design). After the page-05 restructure this page:
//   - renders the new .onb-card design (no metric fields live here),
//   - NO LONGER fires the website analysis in the background, and
//   - on Continue persists the workspace then navigates to the BLOCKING
//     /onboarding/analyzing interstitial (which runs the analysis).
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

import { BusinessInfo } from "../BusinessInfo"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("BusinessInfo (container) — Company page", () => {
  it("renders the new .onb-card design and the company heading", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const { container } = render(React.createElement(BusinessInfo))
    expect(container.querySelector(".onb-card")).not.toBeNull()
    expect(container.querySelector(".onb-shell")).not.toBeNull()
    expect(screen.getByText(/get to know your/i)).not.toBeNull()
  })

  it("renders NO metric fields on the company page", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const { container } = render(React.createElement(BusinessInfo))
    // The metric tree and North-Star/supporting-metric UI live on the metrics
    // step, NOT here.
    expect(container.querySelector(".metric-tree")).toBeNull()
    expect(container.querySelector(".metric-other")).toBeNull()
    expect(container.textContent).not.toContain("North Star")
    expect(container.textContent).not.toContain("Supporting metrics")
    // Inference-seed website input is still present.
    expect(container.querySelector('input[type="url"]')).not.toBeNull()
  })

  it("Continue persists the workspace then navigates to the analyzing interstitial (no background analysis)", async () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    createWorkspaceMock.mockResolvedValue(makeWorkspace())
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
