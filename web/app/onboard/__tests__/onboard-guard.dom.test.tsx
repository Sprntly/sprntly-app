// @vitest-environment jsdom
//
// Mount tests for the /onboard completed-user guard (mirror of the
// /onboarding/* layout guard): a user who already finished onboarding is
// bounced to "/" and never sees the create-company form; mid-onboarding and
// no-workspace users still get the page; the form never flashes while the
// workspace check is in flight.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { makeWorkspace } from "../../components/screens/onboarding/__tests__/fixtures"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const routerMock = { push: vi.fn(), replace: vi.fn() }
const authMock = vi.fn()
const fetchWorkspaceMock = vi.fn()

vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))

vi.mock("../../lib/auth", () => ({
  useAuth: () => authMock(),
}))

vi.mock("../../lib/supabase/client", () => ({
  isSupabaseConfigured: () => true,
}))

vi.mock("../../lib/onboarding/store", () => ({
  fetchWorkspaceForUser: (userId: string) => fetchWorkspaceMock(userId),
}))

import OnboardPage from "../page"

const authedUser = {
  kind: "authed" as const,
  user: { id: "user-1", email: "a@b.com" },
  isEmailVerified: () => true,
}

beforeEach(() => {
  routerMock.push.mockReset()
  routerMock.replace.mockReset()
  authMock.mockReset()
  fetchWorkspaceMock.mockReset()
  authMock.mockReturnValue(authedUser)
})

afterEach(() => {
  cleanup()
})

describe("OnboardPage completed-user guard", () => {
  it("redirects a user who already completed onboarding to / and never shows the form", async () => {
    fetchWorkspaceMock.mockResolvedValue(
      makeWorkspace({ onboarding_completed_at: "2026-01-01T00:00:00Z" }),
    )

    render(<OnboardPage />)

    await waitFor(() => expect(routerMock.replace).toHaveBeenCalledWith("/"))
    expect(screen.queryByTestId("onboard-display-name")).toBeNull()
  })

  it("renders the form for a mid-onboarding user (workspace, not completed)", async () => {
    fetchWorkspaceMock.mockResolvedValue(
      makeWorkspace({ onboarding_completed_at: null }),
    )

    render(<OnboardPage />)

    expect(await screen.findByTestId("onboard-display-name")).toBeTruthy()
    expect(routerMock.replace).not.toHaveBeenCalledWith("/")
  })

  it("renders the form for a user with no workspace yet", async () => {
    fetchWorkspaceMock.mockResolvedValue(null)

    render(<OnboardPage />)

    expect(await screen.findByTestId("onboard-display-name")).toBeTruthy()
    expect(routerMock.replace).not.toHaveBeenCalledWith("/")
  })

  it("renders nothing while the workspace check is still in flight (no form flash)", () => {
    // A promise that never resolves during the test keeps the check pending.
    fetchWorkspaceMock.mockReturnValue(new Promise(() => {}))

    render(<OnboardPage />)

    expect(screen.queryByTestId("onboard-display-name")).toBeNull()
    expect(routerMock.replace).not.toHaveBeenCalledWith("/")
  })

  it("fails open if the workspace lookup errors (mid-onboarding user not locked out)", async () => {
    fetchWorkspaceMock.mockRejectedValue(new Error("network down"))

    render(<OnboardPage />)

    expect(await screen.findByTestId("onboard-display-name")).toBeTruthy()
    expect(routerMock.replace).not.toHaveBeenCalledWith("/")
  })
})
