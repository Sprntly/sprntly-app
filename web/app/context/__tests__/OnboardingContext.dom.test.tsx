// @vitest-environment jsdom
//
// Hook-level tests for OnboardingContext's refresh-identity behavior — the fix
// for onboarding "restarting" when a backgrounded tab refocuses.
//
// Root cause: AuthProvider rebuilds a NEW auth state object on every Supabase
// auth event (incl. TOKEN_REFRESHED / SIGNED_IN emitted when a backgrounded
// tab refocuses). The old context keyed `refresh` on the whole `auth` object,
// so a new-but-equivalent auth object re-fired refresh → setLoading(true) →
// step screens dropped back to the "Loading…" shell and re-ran their kickoff
// effects, discarding in-memory progress.
//
// These tests prove:
//   (a) a same-user auth change (token refresh) does NOT re-run the fetch and
//       does NOT flip `loading` back to true (no shell flash);
//   (b) a different-user / sign-out DOES reset and re-fetch.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const fetchProfileMock = vi.fn()
const fetchWorkspaceMock = vi.fn()
const runWebsiteAnalysisMock = vi.fn()
const resumeWebsiteAnalysisMock = vi.fn()
const getPendingAnalysisMock = vi.fn()

vi.mock("../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../lib/onboarding/store", () => ({
  fetchUserProfile: (...a: unknown[]) => fetchProfileMock(...a),
  fetchWorkspaceForUser: (...a: unknown[]) => fetchWorkspaceMock(...a),
}))
vi.mock("../../lib/onboarding/runWebsiteAnalysis", () => ({
  runWebsiteAnalysis: (...a: unknown[]) => runWebsiteAnalysisMock(...a),
  resumeWebsiteAnalysis: (...a: unknown[]) => resumeWebsiteAnalysisMock(...a),
  getPendingAnalysis: (...a: unknown[]) => getPendingAnalysisMock(...a),
}))

import { OnboardingProvider, useOnboarding } from "../OnboardingContext"

// A probe component that surfaces the context for assertions and records each
// `loading` value it ever renders with (to catch a transient flip to true).
const loadingHistory: boolean[] = []
function Probe() {
  const { loading, refreshing, workspace } = useOnboarding()
  loadingHistory.push(loading)
  return (
    <div>
      <span data-testid="loading">{String(loading)}</span>
      <span data-testid="refreshing">{String(refreshing)}</span>
      <span data-testid="workspace">{workspace?.slug ?? "none"}</span>
    </div>
  )
}

const authed = (id: string) => ({
  kind: "authed" as const,
  user: { id },
  session: { access_token: "t" },
})

beforeEach(() => {
  loadingHistory.length = 0
  fetchProfileMock.mockResolvedValue({ id: "prof" })
  fetchWorkspaceMock.mockImplementation((uid: string) =>
    Promise.resolve({ slug: `ws-${uid}` }),
  )
  // No persisted analysis job by default → the auto-resume effect is a no-op.
  getPendingAnalysisMock.mockReturnValue(null)
})

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

async function renderProvider() {
  let utils!: ReturnType<typeof render>
  await act(async () => {
    utils = render(
      React.createElement(OnboardingProvider, null, React.createElement(Probe)),
    )
  })
  return utils
}

describe("OnboardingContext — refresh identity stability", () => {
  it("does NOT re-fetch or flip loading on a same-user auth change (token refresh)", async () => {
    // Initial authed render → first load resolves.
    authMock.mockReturnValue(authed("u-1"))
    const utils = await renderProvider()

    expect(fetchWorkspaceMock).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId("loading").textContent).toBe("false")
    expect(screen.getByTestId("workspace").textContent).toBe("ws-u-1")

    loadingHistory.length = 0

    // Simulate a Supabase TOKEN_REFRESHED: a NEW auth object, SAME user id.
    authMock.mockReturnValue(authed("u-1"))
    await act(async () => {
      utils.rerender(
        React.createElement(OnboardingProvider, null, React.createElement(Probe)),
      )
    })

    // No re-fetch fired, and loading never flipped back to true → no "Loading…"
    // shell flash, no kickoff re-run.
    expect(fetchWorkspaceMock).toHaveBeenCalledTimes(1)
    expect(loadingHistory.every((l) => l === false)).toBe(true)
    expect(screen.getByTestId("loading").textContent).toBe("false")
  })

  it("re-fetches for a different user and resets on sign-out", async () => {
    authMock.mockReturnValue(authed("u-1"))
    const utils = await renderProvider()
    expect(fetchWorkspaceMock).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId("workspace").textContent).toBe("ws-u-1")

    // Different user logs in (same browser) → MUST re-fetch their data.
    authMock.mockReturnValue(authed("u-2"))
    await act(async () => {
      utils.rerender(
        React.createElement(OnboardingProvider, null, React.createElement(Probe)),
      )
    })
    expect(fetchWorkspaceMock).toHaveBeenCalledTimes(2)
    expect(fetchWorkspaceMock).toHaveBeenLastCalledWith("u-2")
    expect(screen.getByTestId("workspace").textContent).toBe("ws-u-2")

    // Sign-out → onboarding data is cleared and no extra fetch fires.
    authMock.mockReturnValue({ kind: "anonymous" as const })
    await act(async () => {
      utils.rerender(
        React.createElement(OnboardingProvider, null, React.createElement(Probe)),
      )
    })
    expect(fetchWorkspaceMock).toHaveBeenCalledTimes(2)
    expect(screen.getByTestId("workspace").textContent).toBe("none")
    expect(screen.getByTestId("loading").textContent).toBe("false")
  })
})

// A probe that surfaces the website-analysis result and lets a test trigger the
// background kickoff via a button (so we exercise the real context wiring).
function AnalysisProbe() {
  const { websiteAnalysis, startWebsiteAnalysis } = useOnboarding()
  return (
    <div>
      <span data-testid="analysis">{websiteAnalysis?.industry ?? "none"}</span>
      <button
        data-testid="start"
        onClick={() => startWebsiteAnalysis("https://acme.com", "ws-1")}
      >
        start
      </button>
    </div>
  )
}

const makeAnalysis = () => ({
  ok: true,
  reason: null,
  url: "https://acme.com",
  industry: "Fintech",
  sub_vertical: null,
  business_type: "Marketplace",
  stage: "Growth",
  business_context: "ctx",
  suggested_metrics: [],
  provenance: "website" as const,
  business_context_version: 1,
})

async function renderWith(node: React.ReactElement) {
  let utils!: ReturnType<typeof render>
  await act(async () => {
    utils = render(React.createElement(OnboardingProvider, null, node))
  })
  return utils
}

describe("OnboardingContext — background website analysis", () => {
  it("startWebsiteAnalysis fires the analysis once and stashes the result", async () => {
    authMock.mockReturnValue(authed("u-1"))
    runWebsiteAnalysisMock.mockResolvedValue({ result: makeAnalysis() })

    await renderWith(React.createElement(AnalysisProbe))
    expect(screen.getByTestId("analysis").textContent).toBe("none")

    // Two clicks → the run fires exactly once (fire-once guard).
    await act(async () => {
      fireEvent.click(screen.getByTestId("start"))
      fireEvent.click(screen.getByTestId("start"))
    })

    expect(runWebsiteAnalysisMock).toHaveBeenCalledTimes(1)
    // (website, company, workspaceId) — company === workspaceId scope.
    expect(runWebsiteAnalysisMock).toHaveBeenCalledWith(
      "https://acme.com",
      "ws-1",
      "ws-1",
    )
    // The result lands on context for the later step to read.
    expect(screen.getByTestId("analysis").textContent).toBe("Fintech")
    // No interstitial to re-attach to on this happy path.
    expect(resumeWebsiteAnalysisMock).not.toHaveBeenCalled()
  })

  it("re-attaches to a persisted job on load (resume after refresh)", async () => {
    authMock.mockReturnValue(authed("u-1"))
    // The loaded workspace carries the id the auto-resume effect keys on.
    fetchWorkspaceMock.mockResolvedValue({ id: "ws-1", slug: "acme" })
    getPendingAnalysisMock.mockReturnValue({ id: "77" })
    resumeWebsiteAnalysisMock.mockResolvedValue({ result: makeAnalysis() })

    await renderWith(React.createElement(AnalysisProbe))

    // On load, the persisted job is resumed (NOT re-POSTed) and stashed.
    expect(resumeWebsiteAnalysisMock).toHaveBeenCalledTimes(1)
    expect(resumeWebsiteAnalysisMock).toHaveBeenCalledWith(77, "ws-1", "ws-1")
    expect(runWebsiteAnalysisMock).not.toHaveBeenCalled()
    expect(screen.getByTestId("analysis").textContent).toBe("Fintech")
  })
})
