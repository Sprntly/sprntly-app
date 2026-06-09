// @vitest-environment jsdom
//
// Container-level mount test for onboarding step 01 — "Tell me about your
// company and product." After the restructure this page NO LONGER asks for
// industry / business type (Claude infers them) and fires the background
// website analysis on submit. Mounts the real container under jsdom with
// mocked auth/onboarding/router/store/api.
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

import { Onboarding1 } from "../Onboarding1"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Onboarding1 (container) — workspace & product", () => {
  it("does NOT render industry or business-type inputs (Claude infers them)", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const { container } = render(React.createElement(Onboarding1))
    expect(screen.getByText("Tell me about your company and product")).not.toBeNull()
    expect(container.textContent).not.toContain("Industry")
    expect(container.textContent).not.toContain("Business type")
    // The website input — the seed for the inference — is still present.
    expect(container.querySelector('input[type="url"]')).not.toBeNull()
  })

  it("fires the background website analysis on submit (and still navigates)", async () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    const setWebsiteAnalysis = vi.fn()
    createWorkspaceMock.mockResolvedValue(makeWorkspace())
    // analyze resolves async; submit must not await it.
    analyzeWebsiteMock.mockResolvedValue({ ok: true })
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({ workspace: null, setWebsiteAnalysis }),
    )

    render(React.createElement(Onboarding1))

    const inputs = document.querySelectorAll("input.input")
    // company name, product name, website (order matches the field layout)
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
    expect(analyzeWebsiteMock).toHaveBeenCalledWith("https://acme.com")
    expect(routerMock.push).toHaveBeenCalledWith("/onboarding/2")
  })

  it("does NOT fire website analysis when no website was entered", async () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    createWorkspaceMock.mockResolvedValue(makeWorkspace())
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    render(React.createElement(Onboarding1))
    const inputs = document.querySelectorAll("input.input")
    fireEvent.change(inputs[0], { target: { value: "Acme" } })
    fireEvent.change(inputs[1], { target: { value: "Acme App" } })

    const continueBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      /continue/i.test(b.textContent ?? ""),
    ) as HTMLButtonElement
    await act(async () => {
      continueBtn.click()
    })

    expect(createWorkspaceMock).toHaveBeenCalledTimes(1)
    expect(analyzeWebsiteMock).not.toHaveBeenCalled()
  })

  it("shows the loading shell while the workspace is loading", () => {
    authMock.mockReturnValue({ kind: "loading" })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(Onboarding1))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })
})
