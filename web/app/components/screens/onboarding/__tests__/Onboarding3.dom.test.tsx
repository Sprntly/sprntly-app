// @vitest-environment jsdom
//
// Container-level mount test for onboarding step 03 — "Share your business
// context." The restructure moved the context-upload step here and added a
// paste box pre-filled from the website-analysis blurb. This file asserts the
// prefill behaviour (present when analysis returned, editable, empty when
// absent) and the redirect-in-effect safety. Mounts the real container under
// jsdom with mocked auth/onboarding/router/store/api.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
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
  companiesApi: { uploadFiles: vi.fn() },
}))

import { Onboarding3 } from "../Onboarding3"
import { makeWorkspace, makeAnalysis, makeOnboardingCtx } from "./fixtures"

function pasteBox(): HTMLTextAreaElement {
  // First textarea on the page is the paste-context box.
  return document.querySelector("textarea.ob-ctx-textarea") as HTMLTextAreaElement
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Onboarding3 (container) — business context with prefill", () => {
  it("renders the context-upload step for a loaded workspace", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({ workspace: makeWorkspace({ onboarding_step: 3 }) }),
    )
    render(React.createElement(Onboarding3))
    expect(screen.getByText("Share your business context")).not.toBeNull()
    expect(screen.getByText(/Documents/)).not.toBeNull()
  })

  it("pre-fills the paste box from the analysis business_context and shows the hint", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 3 }),
        websiteAnalysis: makeAnalysis({
          business_context: "Acme reconciles payments for SMBs.",
        }),
      }),
    )
    render(React.createElement(Onboarding3))
    expect(pasteBox().value).toBe("Acme reconciles payments for SMBs.")
    expect(screen.getByText(/Drafted from your website/)).not.toBeNull()
  })

  it("keeps the prefilled context editable (user can clear/replace it)", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 3 }),
        websiteAnalysis: makeAnalysis({ business_context: "Drafted blurb." }),
      }),
    )
    render(React.createElement(Onboarding3))
    const box = pasteBox()
    expect(box.value).toBe("Drafted blurb.")
    fireEvent.change(box, { target: { value: "My own context." } })
    expect(pasteBox().value).toBe("My own context.")
    // hint disappears once the user edits the box
    expect(screen.queryByText(/Drafted from your website/)).toBeNull()
  })

  it("leaves the paste box EMPTY when analysis hasn't returned (no block)", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 3 }),
        websiteAnalysis: null,
      }),
    )
    render(React.createElement(Onboarding3))
    expect(pasteBox().value).toBe("")
    expect(screen.queryByText(/Drafted from your website/)).toBeNull()
  })

  it("does NOT prefill from a graceful-degrade analysis with an empty blurb", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ onboarding_step: 3 }),
        websiteAnalysis: makeAnalysis({
          ok: false,
          reason: "unreachable_or_empty",
          business_context: "",
          suggested_metrics: [],
        }),
      }),
    )
    render(React.createElement(Onboarding3))
    expect(pasteBox().value).toBe("")
    expect(screen.queryByText(/Drafted from your website/)).toBeNull()
  })

  it("shows the loading shell while the workspace is loading", () => {
    authMock.mockReturnValue({ kind: "loading" })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ loading: true, workspace: null }))
    render(React.createElement(Onboarding3))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(Onboarding3))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/1")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
