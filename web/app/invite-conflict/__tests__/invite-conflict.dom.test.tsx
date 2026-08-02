// @vitest-environment jsdom
//
// /invite-conflict kept-session mode (?kept=1): after an invite link was opened
// while already signed in as someone else, the page offers a real choice.
//  - Held invitee session present → "Stay" (keep current account → "/") and
//    "Switch" (adopt invitee via setSession → /set-password) buttons.
//  - Held session gone (page reloaded) → the one-time-link explanation, no
//    switch button.
// The default (no ?kept) one-user-one-company copy is unchanged.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const routerMock = { replace: vi.fn(), push: vi.fn() }
const getSessionMock = vi.fn()
const setSessionMock = vi.fn()
const getPendingMock = vi.fn()
const clearPendingMock = vi.fn()
const clearStorageMock = vi.fn()

vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../lib/auth", () => ({
  clearSessionScopedStorage: () => clearStorageMock(),
}))
vi.mock("../../lib/supabase/client", () => ({
  isSupabaseConfigured: () => true,
  getPendingInviteSession: () => getPendingMock(),
  clearPendingInviteSession: () => clearPendingMock(),
  getSupabase: () => ({
    auth: {
      getSession: (...a: unknown[]) => getSessionMock(...a),
      setSession: (...a: unknown[]) => setSessionMock(...a),
    },
  }),
}))

import InviteConflictPage from "../page"

function mount(search: string) {
  window.history.replaceState({}, "", `/invite-conflict${search}`)
  return render(React.createElement(InviteConflictPage))
}

beforeEach(() => {
  getSessionMock.mockResolvedValue({
    data: { session: { user: { email: "a@example.com" } } },
  })
  setSessionMock.mockResolvedValue({ data: {}, error: null })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("/invite-conflict — kept-session choice", () => {
  it("offers Stay and Switch when the invitee session is held", async () => {
    getPendingMock.mockReturnValue({
      email: "b@example.com",
      accessToken: "acc-B",
      refreshToken: "ref-B",
    })
    mount("?kept=1")

    await waitFor(() => {
      expect(screen.getByText(/This invite was sent to/)).toBeTruthy()
    })
    // Each choice is a button whose accessible name is caption + email.
    expect(screen.getByRole("button", { name: /stay signed in/i })).toBeTruthy()
    expect(
      screen.getByRole("button", { name: /switch account b@example\.com/i }),
    ).toBeTruthy()
  })

  it("Stay keeps the current account and goes home", async () => {
    getPendingMock.mockReturnValue({
      email: "b@example.com",
      accessToken: "acc-B",
      refreshToken: "ref-B",
    })
    mount("?kept=1")
    await waitFor(() => screen.getByRole("button", { name: /stay signed in/i }))

    fireEvent.click(screen.getByRole("button", { name: /stay signed in/i }))

    expect(clearPendingMock).toHaveBeenCalled()
    expect(routerMock.replace).toHaveBeenCalledWith("/")
    expect(setSessionMock).not.toHaveBeenCalled()
  })

  it("Switch adopts the invitee session and routes to /set-password", async () => {
    getPendingMock.mockReturnValue({
      email: "b@example.com",
      accessToken: "acc-B",
      refreshToken: "ref-B",
    })
    mount("?kept=1")
    await waitFor(() =>
      screen.getByRole("button", { name: /switch account b@example\.com/i }),
    )

    fireEvent.click(
      screen.getByRole("button", { name: /switch account b@example\.com/i }),
    )

    await waitFor(() => {
      expect(setSessionMock).toHaveBeenCalledWith({
        access_token: "acc-B",
        refresh_token: "ref-B",
      })
    })
    // Current account's session-scoped state is wiped before the swap.
    expect(clearStorageMock).toHaveBeenCalled()
    expect(routerMock.replace).toHaveBeenCalledWith("/set-password")
  })

  it("explains the one-time link (no Switch) when the held session is gone", async () => {
    getPendingMock.mockReturnValue(null)
    mount("?kept=1")

    await waitFor(() => {
      expect(screen.getByText(/can only be used once/)).toBeTruthy()
    })
    expect(screen.queryByRole("button", { name: /switch/i })).toBeNull()
  })

  it("still shows the one-user-one-company copy without ?kept", async () => {
    getPendingMock.mockReturnValue(null)
    mount("")

    await waitFor(() => {
      expect(screen.getByText(/belongs to a different company/)).toBeTruthy()
    })
    expect(screen.queryByRole("button", { name: /stay signed in/i })).toBeNull()
  })
})
