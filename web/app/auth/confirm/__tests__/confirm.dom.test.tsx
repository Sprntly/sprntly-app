// @vitest-environment jsdom
//
// /auth/confirm — scanner-proof auth-link landing. The emailed invite URL
// points here with ?token_hash&type; merely LOADING the page must consume
// nothing (mail scanners prefetch links with a GET). The token is only spent
// by verifyOtp when the user clicks the accept button. After verifyOtp the
// page mirrors /auth/callback's routing: invite-conflict guard, then
// /set-password for invites.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const routerMock = { replace: vi.fn(), push: vi.fn() }
const verifyOtpMock = vi.fn()
const setSessionMock = vi.fn()
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
      verifyOtp: (...a: unknown[]) => verifyOtpMock(...a),
      setSession: (...a: unknown[]) => setSessionMock(...a),
    },
  }),
}))

import AuthConfirmPage from "../page"

const SESSION = {
  user: { id: "invitee-1", email: "new@co.com" },
  access_token: "at-invitee",
  refresh_token: "rt-invitee",
}

function setUrl(qs: string) {
  window.history.replaceState({}, "", `/auth/confirm${qs}`)
}

beforeEach(() => {
  setUrl("?token_hash=hash123&type=invite")
  priorSnapshotMock.mockReturnValue(null)
  postLoginPathMock.mockResolvedValue("/onboarding/your-name")
  setSessionMock.mockResolvedValue({ data: {}, error: null })
  verifyOtpMock.mockResolvedValue({ data: { session: SESSION }, error: null })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("/auth/confirm — scanner-proof invite landing", () => {
  it("does NOT consume the token on load (scanner GET is harmless)", async () => {
    render(<AuthConfirmPage />)
    expect(await screen.findByRole("button", { name: /accept invitation/i })).toBeTruthy()
    expect(verifyOtpMock).not.toHaveBeenCalled()
    expect(routerMock.replace).not.toHaveBeenCalled()
  })

  it("verifies the token_hash on click and routes an invite to /set-password", async () => {
    render(<AuthConfirmPage />)
    fireEvent.click(await screen.findByRole("button", { name: /accept invitation/i }))
    await waitFor(() =>
      expect(verifyOtpMock).toHaveBeenCalledWith({
        type: "invite",
        token_hash: "hash123",
      }),
    )
    await waitFor(() => expect(routerMock.replace).toHaveBeenCalledWith("/set-password"))
  })

  it("guards an invite opened while signed in as a DIFFERENT user", async () => {
    priorSnapshotMock.mockReturnValue({
      userId: "someone-else",
      accessToken: "at-prior",
      refreshToken: "rt-prior",
    })
    render(<AuthConfirmPage />)
    fireEvent.click(await screen.findByRole("button", { name: /accept invitation/i }))
    await waitFor(() =>
      expect(routerMock.replace).toHaveBeenCalledWith("/invite-conflict?kept=1"),
    )
    expect(setPendingInviteSessionMock).toHaveBeenCalledWith({
      email: "new@co.com",
      accessToken: "at-invitee",
      refreshToken: "rt-invitee",
    })
    expect(setSessionMock).toHaveBeenCalledWith({
      access_token: "at-prior",
      refresh_token: "rt-prior",
    })
  })

  it("shows the expired state (with a sign-in link) when verifyOtp fails", async () => {
    verifyOtpMock.mockResolvedValue({
      data: { session: null },
      error: { message: "Token has expired or is invalid" },
    })
    render(<AuthConfirmPage />)
    fireEvent.click(await screen.findByRole("button", { name: /accept invitation/i }))
    expect(await screen.findByText(/expired or was already used/i)).toBeTruthy()
    expect(routerMock.replace).not.toHaveBeenCalled()
  })

  it("redirects to /sign-in when opened without a token_hash", async () => {
    setUrl("")
    render(<AuthConfirmPage />)
    await waitFor(() => expect(routerMock.replace).toHaveBeenCalledWith("/sign-in"))
    expect(verifyOtpMock).not.toHaveBeenCalled()
  })

  it("routes recovery links to /reset-password after verification", async () => {
    setUrl("?token_hash=hash456&type=recovery")
    render(<AuthConfirmPage />)
    fireEvent.click(await screen.findByRole("button", { name: /continue/i }))
    await waitFor(() =>
      expect(verifyOtpMock).toHaveBeenCalledWith({
        type: "recovery",
        token_hash: "hash456",
      }),
    )
    await waitFor(() => expect(routerMock.replace).toHaveBeenCalledWith("/reset-password"))
  })
})
