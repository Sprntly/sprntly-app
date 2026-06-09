// @vitest-environment jsdom
//
// Container-level mount test for onboarding step 06 — "Introducing your AI
// coworkers." (Coworkers moved here from step 7 in the restructure.) Mounts
// the real container under jsdom with mocked onboarding/router and the
// coworkers network client so a render-time throw is caught.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }

vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  advanceOnboardingStep: vi.fn(),
}))
// Keep the pure helpers (COWORKERS, emptyCoworkerNames, canLaunchWorkspace)
// real; only stub the network client so the mount is offline.
vi.mock("../../../../lib/onboarding/coworkersApi", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../../../lib/onboarding/coworkersApi")
  >()
  return {
    ...actual,
    coworkersApi: { get: vi.fn().mockResolvedValue({}), put: vi.fn() },
  }
})

import { Coworkers } from "../Coworkers"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Coworkers (container) — coworkers", () => {
  it("renders the coworkers step for a loaded workspace", () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({ workspace: makeWorkspace({ onboarding_step: 6 }) }),
    )
    render(React.createElement(Coworkers))
    expect(
      screen.getByText("Introducing your AI coworkers. Give them a name."),
    ).not.toBeNull()
  })

  it("shows the loading shell while the workspace is loading", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(Coworkers))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(Coworkers))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/business-info")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
