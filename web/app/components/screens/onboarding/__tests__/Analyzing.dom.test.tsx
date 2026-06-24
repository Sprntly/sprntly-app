// @vitest-environment jsdom
//
// Container-level mount test for the "Gathering information about your business"
// interstitial. This is the SENSITIVE part of the onboarding flow: it must kick
// off the website analysis, advance to the connectors step on a ready result, and —
// critically — STILL advance on error / ok:false / timeout / no result so the
// user is never trapped on the loader.
//
// The analysis is now fire-and-forget + blur/remount-safe: the screen calls
// runWebsiteAnalysis (POST → poll the status endpoint), persisting the job_id so
// a remount re-attaches via resumeWebsiteAnalysis instead of re-POSTing. We mock
// the runWebsiteAnalysis module (its own unit test covers the POST/poll/jobResume
// internals) and assert the screen's orchestration: stash-on-ready, forward
// always, resume-on-pending.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const runWebsiteAnalysisMock = vi.fn()
const resumeWebsiteAnalysisMock = vi.fn()
const getPendingAnalysisMock = vi.fn()

vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/runWebsiteAnalysis", () => ({
  runWebsiteAnalysis: (...a: unknown[]) => runWebsiteAnalysisMock(...a),
  resumeWebsiteAnalysis: (...a: unknown[]) => resumeWebsiteAnalysisMock(...a),
  getPendingAnalysis: (...a: unknown[]) => getPendingAnalysisMock(...a),
}))

import { Analyzing } from "../Analyzing"
import { makeWorkspace, makeAnalysis, makeOnboardingCtx } from "./fixtures"

function withWebsite(over: Record<string, unknown> = {}) {
  return makeOnboardingCtx({
    workspace: makeWorkspace({
      product: {
        id: "p-1",
        company_id: "ws-1",
        name: "Acme App",
        website: "https://acme.com",
        description: null,
        is_primary: true,
      },
    }),
    ...over,
  })
}

beforeEach(() => {
  // Default: no pending job persisted → fresh POST path.
  getPendingAnalysisMock.mockReturnValue(null)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.useRealTimers()
})

describe("Analyzing (interstitial)", () => {
  it("renders the gathering-information loader with a spinner", () => {
    runWebsiteAnalysisMock.mockReturnValue(new Promise(() => {})) // never resolves
    onboardingMock.mockReturnValue(withWebsite())
    const { container } = render(React.createElement(Analyzing))
    expect(
      screen.getByText("Gathering information about your business"),
    ).not.toBeNull()
    expect(container.querySelector(".onb-spinner")).not.toBeNull()
  })

  it("kicks off runWebsiteAnalysis once with the website + workspace scope", async () => {
    runWebsiteAnalysisMock.mockResolvedValue({ result: makeAnalysis() })
    onboardingMock.mockReturnValue(withWebsite({ setWebsiteAnalysis: vi.fn() }))
    await act(async () => {
      render(React.createElement(Analyzing))
    })
    expect(runWebsiteAnalysisMock).toHaveBeenCalledTimes(1)
    // (url, company=workspaceId, workspaceId, isCancelled)
    expect(runWebsiteAnalysisMock.mock.calls[0][0]).toBe("https://acme.com")
    expect(runWebsiteAnalysisMock.mock.calls[0][1]).toBe("ws-1")
    expect(runWebsiteAnalysisMock.mock.calls[0][2]).toBe("ws-1")
  })

  it("on a ready result: stashes the analysis and advances to the connectors step", async () => {
    const analysis = makeAnalysis()
    runWebsiteAnalysisMock.mockResolvedValue({ result: analysis })
    const setWebsiteAnalysis = vi.fn()
    onboardingMock.mockReturnValue(withWebsite({ setWebsiteAnalysis }))
    await act(async () => {
      render(React.createElement(Analyzing))
    })
    expect(setWebsiteAnalysis).toHaveBeenCalledWith(analysis)
    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/connectors")
  })

  it("on result:null (error / timeout): forwards WITHOUT stashing (manual fallback)", async () => {
    runWebsiteAnalysisMock.mockResolvedValue({ result: null })
    const setWebsiteAnalysis = vi.fn()
    onboardingMock.mockReturnValue(withWebsite({ setWebsiteAnalysis }))
    await act(async () => {
      render(React.createElement(Analyzing))
    })
    expect(setWebsiteAnalysis).not.toHaveBeenCalled()
    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/connectors")
  })

  it("re-attaches to a persisted pending job on mount (resume, no re-POST)", async () => {
    getPendingAnalysisMock.mockReturnValue({ id: "321" })
    resumeWebsiteAnalysisMock.mockResolvedValue({ result: makeAnalysis() })
    const setWebsiteAnalysis = vi.fn()
    onboardingMock.mockReturnValue(withWebsite({ setWebsiteAnalysis }))
    await act(async () => {
      render(React.createElement(Analyzing))
    })
    // Resume by id — never a fresh POST.
    expect(resumeWebsiteAnalysisMock).toHaveBeenCalledTimes(1)
    expect(resumeWebsiteAnalysisMock.mock.calls[0][0]).toBe(321)
    expect(runWebsiteAnalysisMock).not.toHaveBeenCalled()
    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/connectors")
  })

  it("advances exactly ONCE on a ready result", async () => {
    runWebsiteAnalysisMock.mockResolvedValue({ result: makeAnalysis() })
    onboardingMock.mockReturnValue(withWebsite({ setWebsiteAnalysis: vi.fn() }))
    await act(async () => {
      render(React.createElement(Analyzing))
    })
    const toMetrics = routerMock.replace.mock.calls.filter(
      (c) => c[0] === "/onboarding/connectors",
    )
    expect(toMetrics).toHaveLength(1)
  })

  it("with NO website: skips analysis and advances straight to connectors", async () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ product: null }),
      }),
    )
    await act(async () => {
      render(React.createElement(Analyzing))
    })
    expect(runWebsiteAnalysisMock).not.toHaveBeenCalled()
    expect(resumeWebsiteAnalysisMock).not.toHaveBeenCalled()
    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/connectors")
  })

  it("with NO workspace: redirects back to step 1 from an effect (never during render)", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(Analyzing))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/business-info")
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
