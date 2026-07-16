// @vitest-environment jsdom
//
// Hook-level tests for WorkspaceContext's refresh-identity behavior — the
// "make server-side AI flows blur-safe" fix (mirrors the merged
// OnboardingContext.dom.test.tsx).
//
// Root cause: AuthProvider rebuilds a NEW auth state object on every Supabase
// auth event (incl. TOKEN_REFRESHED / SIGNED_IN emitted when a backgrounded
// tab refocuses). The old context keyed `refresh` on the whole `auth` object,
// so a new-but-equivalent auth object re-fired refresh → setLoading(true),
// flashing the whole app to a loading shell and churning
// CompanyContext/activeCompany on every refocus.
//
// These tests prove:
//   (a) a same-user auth change (token refresh) does NOT re-fetch and does NOT
//       flip `loading` back to true (it would use `refreshing` instead);
//   (b) a different-user / sign-out DOES reset and re-fetch.
import * as React from "react"
import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.fn()
const fetchProfileMock = vi.fn()
const fetchWorkspaceMock = vi.fn()
const listWorkspacesMock = vi.fn()

vi.mock("../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../lib/onboarding/store", () => ({
  fetchUserProfile: (...a: unknown[]) => fetchProfileMock(...a),
  fetchWorkspaceForUser: (...a: unknown[]) => fetchWorkspaceMock(...a),
}))
// The workspaces list ride-along (multi-workspace 2026-07) — stubbed so no
// real fetch fires; the identity-stability behavior under test is unchanged.
vi.mock("../../lib/api", () => ({
  setActiveWorkspaceId: vi.fn(),
  workspacesApi: { list: (...a: unknown[]) => listWorkspacesMock(...a) },
}))
// Supabase is "configured" in every test — the unauthed branch is exercised
// purely via auth.kind, matching production where the env is always present.
vi.mock("../../lib/supabase/client", () => ({ isSupabaseConfigured: () => true }))

import { WorkspaceProvider, useWorkspace } from "../WorkspaceContext"

const loadingHistory: boolean[] = []
function Probe() {
  const { loading, refreshing, workspace } = useWorkspace()
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
  listWorkspacesMock.mockResolvedValue({ workspaces: [] })
})

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

async function renderProvider() {
  let utils!: ReturnType<typeof render>
  await act(async () => {
    utils = render(
      React.createElement(WorkspaceProvider, null, React.createElement(Probe)),
    )
  })
  return utils
}

describe("WorkspaceContext — refresh identity stability", () => {
  it("does NOT re-fetch or flip loading on a same-user auth change (token refresh)", async () => {
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
        React.createElement(WorkspaceProvider, null, React.createElement(Probe)),
      )
    })

    // No re-fetch fired, and loading never flipped back to true → no app-wide
    // loading flash, no CompanyContext churn.
    expect(fetchWorkspaceMock).toHaveBeenCalledTimes(1)
    expect(loadingHistory.every((l) => l === false)).toBe(true)
    expect(screen.getByTestId("loading").textContent).toBe("false")
    expect(screen.getByTestId("refreshing").textContent).toBe("false")
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
        React.createElement(WorkspaceProvider, null, React.createElement(Probe)),
      )
    })
    expect(fetchWorkspaceMock).toHaveBeenCalledTimes(2)
    expect(fetchWorkspaceMock).toHaveBeenLastCalledWith("u-2")
    expect(screen.getByTestId("workspace").textContent).toBe("ws-u-2")

    // Sign-out → workspace data is cleared and no extra fetch fires.
    authMock.mockReturnValue({ kind: "anonymous" as const })
    await act(async () => {
      utils.rerender(
        React.createElement(WorkspaceProvider, null, React.createElement(Probe)),
      )
    })
    expect(fetchWorkspaceMock).toHaveBeenCalledTimes(2)
    expect(screen.getByTestId("workspace").textContent).toBe("none")
    expect(screen.getByTestId("loading").textContent).toBe("false")
  })
})
