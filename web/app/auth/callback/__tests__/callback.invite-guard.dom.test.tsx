// @vitest-environment jsdom
//
// Guard test for /auth/callback: opening an invite magic link in a browser
// already signed in as a DIFFERENT user must NOT hijack the existing session.
// The callback stashes the (already minted) invitee session, restores the prior
// session (setSession with its tokens), and routes to /invite-conflict?kept=1
// instead of entering as the invitee. A fresh invitee (no prior session) and an
// invite for the SAME user still flow on to /set-password.
import * as React from "react"
import { cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const routerMock = { replace: vi.fn(), push: vi.fn() }
const getSessionMock = vi.fn()
const setSessionMock = vi.fn()
const exchangeCodeMock = vi.fn()
const onAuthStateChangeMock = vi.fn((..._a: unknown[]) => ({
  data: { subscription: { unsubscribe: vi.fn() } },
}))
const priorSnapshotMock = vi.fn()
const postLoginPathMock = vi.fn()
const setPendingInviteSessionMock = vi.fn()

vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../lib/supabase/client", () => ({
  isSupabaseConfigured: () => true,
  getPriorSessionSnapshot: () => priorSnapshotMock(),
  postLoginPath: () => postLoginPathMock(),
  setPendingInviteSession: (...a: unknown[]) => setPendingInviteSessionMock(...a),
  getSupabase: () => ({
    auth: {
      getSession: (...a: unknown[]) => getSessionMock(...a),
      setSession: (...a: unknown[]) => setSessionMock(...a),
      exchangeCodeForSession: (...a: unknown[]) => exchangeCodeMock(...a),
      onAuthStateChange: (...a: unknown[]) => onAuthStateChangeMock(...a),
    },
  }),
}))

import AuthCallbackPage from "../page"

function setInviteUrl() {
  // type=invite in the query drives isInviteFlow(); no ?code so exchange is skipped.
  window.history.replaceState({}, "", "/auth/callback?type=invite")
}

beforeEach(() => {
  setInviteUrl()
  postLoginPathMock.mockResolvedValue("/onboarding/your-name")
  setSessionMock.mockResolvedValue({ data: {}, error: null })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("/auth/callback — invite session guard", () => {
  it("stashes the invitee, restores the prior session, and blocks when signed in as a DIFFERENT user", async () => {
    priorSnapshotMock.mockReturnValue({
      userId: "user-A",
      email: "a@example.com",
      accessToken: "acc-A",
      refreshToken: "ref-A",
    })
    getSessionMock.mockResolvedValue({
      data: {
        session: {
          user: { id: "user-B", email: "b@example.com" },
          access_token: "acc-B",
          refresh_token: "ref-B",
        },
      },
    })

    render(React.createElement(AuthCallbackPage))

    await waitFor(() => {
      expect(setSessionMock).toHaveBeenCalledWith({
        access_token: "acc-A",
        refresh_token: "ref-A",
      })
    })
    // The already-minted invitee session is held for /invite-conflict to offer.
    expect(setPendingInviteSessionMock).toHaveBeenCalledWith({
      email: "b@example.com",
      accessToken: "acc-B",
      refreshToken: "ref-B",
    })
    expect(routerMock.replace).toHaveBeenCalledWith("/invite-conflict?kept=1")
  })

  it("lets a fresh invitee (no prior session) through to /set-password", async () => {
    priorSnapshotMock.mockReturnValue(null)
    getSessionMock.mockResolvedValue({
      data: { session: { user: { id: "user-B", email: "b@example.com" } } },
    })

    render(React.createElement(AuthCallbackPage))

    await waitFor(() => {
      expect(routerMock.replace).toHaveBeenCalledWith("/set-password")
    })
    expect(setSessionMock).not.toHaveBeenCalled()
  })

  it("does not block when the invite is for the SAME already-signed-in user", async () => {
    priorSnapshotMock.mockReturnValue({
      userId: "user-A",
      email: "a@example.com",
      accessToken: "acc-A",
      refreshToken: "ref-A",
    })
    getSessionMock.mockResolvedValue({
      data: { session: { user: { id: "user-A", email: "a@example.com" } } },
    })

    render(React.createElement(AuthCallbackPage))

    await waitFor(() => {
      expect(routerMock.replace).toHaveBeenCalledWith("/set-password")
    })
    expect(setSessionMock).not.toHaveBeenCalled()
  })
})
