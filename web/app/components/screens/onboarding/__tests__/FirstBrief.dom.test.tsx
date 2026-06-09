// @vitest-environment jsdom
//
// Container-level mount test for onboarding step 07 — "Preparing your first
// Brief." (The first-Brief step moved here from step 8 in the restructure.)
// Mounts the real container under jsdom with mocked
// auth/onboarding/content/router and the brief-generation client so a
// render-time throw is caught.
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
vi.mock("../../../../context/ContentContext", () => ({
  useContent: () => ({ setContent: vi.fn() }),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  completeOnboarding: vi.fn(),
}))
vi.mock("../../../../lib/brief-adapter", () => ({
  briefToContentPatch: vi.fn(() => ({})),
}))
// The brief-generation client runs from the mount effect; stub it so the
// mount is offline and deterministic.
vi.mock("../../../../lib/workspace-brief", () => ({
  briefPreviewInsight: vi.fn(() => null),
  ensureDatasetForWorkspace: vi.fn().mockResolvedValue(undefined),
  fetchBriefWhenReady: vi.fn().mockResolvedValue(null),
  pollBriefStatus: vi.fn().mockResolvedValue({ status: "ready" }),
  seedWorkspaceContextFiles: vi.fn().mockResolvedValue(undefined),
  startBriefGeneration: vi.fn().mockResolvedValue(undefined),
}))

import { FirstBrief } from "../FirstBrief"
import { makeWorkspace, makeOnboardingCtx } from "./fixtures"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("FirstBrief (container) — first brief", () => {
  it("renders the first-Brief step for a loaded workspace", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({ workspace: makeWorkspace({ onboarding_step: 7 }) }),
    )
    render(React.createElement(FirstBrief))
    expect(screen.getByText("Preparing your first Brief")).not.toBeNull()
  })

  it("shows the loading shell while the workspace is loading", () => {
    authMock.mockReturnValue({ kind: "loading" })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(FirstBrief))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(FirstBrief))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/business-info")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
