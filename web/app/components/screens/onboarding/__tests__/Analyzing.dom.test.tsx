// @vitest-environment jsdom
//
// Container-level mount test for the blocking "Gathering information about your
// business" interstitial. This is the SENSITIVE part of the onboarding flow:
// it must call analyze-website exactly once, advance to the metrics page on
// success, and — critically — STILL advance on failure / ok:false / timeout so
// the user is never trapped on the loader. All navigation is driven from
// effects (never during render).
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }
const analyzeWebsiteMock = vi.fn()

vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/api", () => ({
  onboardingApi: { analyzeWebsite: (...a: unknown[]) => analyzeWebsiteMock(...a) },
}))

import { OnboardingAnalyzing } from "../OnboardingAnalyzing"
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

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.useRealTimers()
})

describe("OnboardingAnalyzing (interstitial)", () => {
  it("renders the gathering-information loader with a spinner", () => {
    analyzeWebsiteMock.mockReturnValue(new Promise(() => {})) // never resolves
    onboardingMock.mockReturnValue(withWebsite())
    const { container } = render(React.createElement(OnboardingAnalyzing))
    expect(
      screen.getByText("Gathering information about your business"),
    ).not.toBeNull()
    expect(container.querySelector(".onb-spinner")).not.toBeNull()
  })

  it("calls analyzeWebsite once with the product website", async () => {
    analyzeWebsiteMock.mockResolvedValue(makeAnalysis())
    const setWebsiteAnalysis = vi.fn()
    onboardingMock.mockReturnValue(withWebsite({ setWebsiteAnalysis }))
    await act(async () => {
      render(React.createElement(OnboardingAnalyzing))
    })
    expect(analyzeWebsiteMock).toHaveBeenCalledTimes(1)
    expect(analyzeWebsiteMock).toHaveBeenCalledWith("https://acme.com")
  })

  it("on success: stashes the analysis and advances to the metrics page (route 2)", async () => {
    const analysis = makeAnalysis()
    analyzeWebsiteMock.mockResolvedValue(analysis)
    const setWebsiteAnalysis = vi.fn()
    onboardingMock.mockReturnValue(withWebsite({ setWebsiteAnalysis }))
    await act(async () => {
      render(React.createElement(OnboardingAnalyzing))
    })
    expect(setWebsiteAnalysis).toHaveBeenCalledWith(analysis)
    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/2")
  })

  it("on transport FAILURE: still advances to the metrics page (manual fallback)", async () => {
    analyzeWebsiteMock.mockRejectedValue(new Error("network down"))
    onboardingMock.mockReturnValue(withWebsite())
    await act(async () => {
      render(React.createElement(OnboardingAnalyzing))
    })
    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/2")
  })

  it("on ok:false (degraded): still advances to the metrics page", async () => {
    const degraded = makeAnalysis({
      ok: false,
      reason: "blocked_url",
      industry: null,
      business_type: null,
      suggested_metrics: [],
    })
    analyzeWebsiteMock.mockResolvedValue(degraded)
    const setWebsiteAnalysis = vi.fn()
    onboardingMock.mockReturnValue(withWebsite({ setWebsiteAnalysis }))
    await act(async () => {
      render(React.createElement(OnboardingAnalyzing))
    })
    expect(setWebsiteAnalysis).toHaveBeenCalledWith(degraded)
    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/2")
  })

  it("advances exactly ONCE even if both the promise and the timeout could fire", async () => {
    analyzeWebsiteMock.mockResolvedValue(makeAnalysis())
    onboardingMock.mockReturnValue(withWebsite())
    await act(async () => {
      render(React.createElement(OnboardingAnalyzing))
    })
    const toMetrics = routerMock.replace.mock.calls.filter(
      (c) => c[0] === "/onboarding/2",
    )
    expect(toMetrics).toHaveLength(1)
  })

  it("has a TIMEOUT guard: advances even when analyze-website never resolves", async () => {
    vi.useFakeTimers()
    analyzeWebsiteMock.mockReturnValue(new Promise(() => {})) // hangs forever
    onboardingMock.mockReturnValue(withWebsite())
    render(React.createElement(OnboardingAnalyzing))
    expect(routerMock.replace).not.toHaveBeenCalledWith("/onboarding/2")
    await act(async () => {
      vi.advanceTimersByTime(12_000)
    })
    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/2")
  })

  it("with NO website: skips analysis and advances straight to metrics", async () => {
    onboardingMock.mockReturnValue(
      makeOnboardingCtx({
        workspace: makeWorkspace({ product: null }),
      }),
    )
    await act(async () => {
      render(React.createElement(OnboardingAnalyzing))
    })
    expect(analyzeWebsiteMock).not.toHaveBeenCalled()
    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/2")
  })

  it("with NO workspace: redirects back to step 1 from an effect (never during render)", () => {
    onboardingMock.mockReturnValue(makeOnboardingCtx({ workspace: null }))

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(OnboardingAnalyzing))
    spy.mockRestore()

    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/1")
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
