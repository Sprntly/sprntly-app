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
import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const fetchProfileMock = vi.fn()
const fetchWorkspaceMock = vi.fn()

vi.mock("../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../lib/onboarding/store", () => ({
  fetchUserProfile: (...a: unknown[]) => fetchProfileMock(...a),
  fetchWorkspaceForUser: (...a: unknown[]) => fetchWorkspaceMock(...a),
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
