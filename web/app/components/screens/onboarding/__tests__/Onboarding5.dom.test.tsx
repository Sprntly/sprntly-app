// @vitest-environment jsdom
//
// Container-level mount test for onboarding step 05 — "Connect your tools."
// (Connectors moved here from step 6 in the restructure.) Mounts the real
// container under jsdom with mocked auth/onboarding/router/api so a render-time
// throw is caught.
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
  advanceOnboardingStep: vi.fn(),
  markSkippedFields: vi.fn(),
}))
vi.mock("../../../../lib/api", () => ({
  connectorsApi: { list: vi.fn().mockResolvedValue({ connections: [] }) },
}))

import { Onboarding5 } from "../Onboarding5"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Onboarding5 (container) — connectors", () => {
  it("renders the connectors step for a loaded workspace", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({ workspace: makeWorkspace({ onboarding_step: 5 }) }),
    )
    render(React.createElement(Onboarding5))
    expect(screen.getByText("Connect your tools")).not.toBeNull()
  })

  it("shows the loading shell while the workspace is loading", () => {
    authMock.mockReturnValue({ kind: "loading" })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(Onboarding5))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(Onboarding5))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/1")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
