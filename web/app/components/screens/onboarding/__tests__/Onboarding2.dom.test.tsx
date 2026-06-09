// @vitest-environment jsdom
//
// Container-level mount test for onboarding step 02 — "What are you optimizing
// for right now?" (the strategic-context step, moved here in the restructure).
// Asserts the optimizing-for fields render and that NO success-metric inputs
// appear on this page. Mounts the real container under jsdom with mocked
// auth/onboarding/router so a render-time throw is caught.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  markSkippedFields: vi.fn(),
  saveStrategicContext: vi.fn(),
}))

import { Onboarding2 } from "../Onboarding2"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Onboarding2 (container) — optimizing-for step", () => {
  it("renders the optimizing-for fields for a loaded workspace", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({ workspace: makeWorkspace({ onboarding_step: 2 }) }),
    )

    render(React.createElement(Onboarding2))
    expect(screen.getByText("What are you optimizing for right now?")).not.toBeNull()
    expect(screen.getByText(/Current OKRs/)).not.toBeNull()
    expect(screen.getByText(/Biggest risk/)).not.toBeNull()
  })

  it("shows NO success-metric / North Star inputs on this page", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({ workspace: makeWorkspace({ onboarding_step: 2 }) }),
    )

    const { container } = render(React.createElement(Onboarding2))
    expect(container.textContent).not.toContain("North Star")
    expect(container.textContent).not.toContain("Supporting metrics")
    expect(container.textContent).not.toContain("success metrics")
  })

  it("shows the loading shell while the workspace is loading", () => {
    authMock.mockReturnValue({ kind: "loading" })
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({ loading: true, workspace: null }),
    )
    render(React.createElement(Onboarding2))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(Onboarding2))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/1")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
