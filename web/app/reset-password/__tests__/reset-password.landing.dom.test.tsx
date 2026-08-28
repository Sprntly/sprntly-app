// @vitest-environment jsdom
//
// Finishing a password reset must not ask for anything else.
//
// The recovery code already minted a session and `updateUser` returns one, so
// the person IS signed in the moment their new password is saved. Anything
// between that and the app — a confirmation screen with a button, a timer, a
// sign-in form — is a door being held shut in front of someone who already has
// the key.
import * as React from "react"
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const routerMock = { replace: vi.fn(), push: vi.fn() }
const getSessionMock = vi.fn()
const updateUserMock = vi.fn()
const postLoginPathMock = vi.fn()

vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useSearchParams: () => new URLSearchParams(window.location.search),
}))
vi.mock("../../lib/supabase/client", () => ({
  isSupabaseConfigured: () => true,
  postLoginPath: () => postLoginPathMock(),
  getSupabase: () => ({
    auth: {
      getSession: () => getSessionMock(),
      updateUser: (...a: unknown[]) => updateUserMock(...a),
    },
  }),
}))
vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    verifyPasswordResetOtp: vi.fn(),
    resetPassword: vi.fn(),
  }),
}))

import ResetPasswordPage from "../page"

const SESSION = { user: { id: "u-1", email: "someone@co.com" } }

beforeEach(() => {
  vi.clearAllMocks()
  window.history.replaceState({}, "", "/reset-password")
  // A recovery session already in hand — the state the password form is
  // reached in, whether the code was just verified or the link carried one.
  getSessionMock.mockResolvedValue({ data: { session: SESSION } })
  updateUserMock.mockResolvedValue({ error: null })
  postLoginPathMock.mockResolvedValue("/")
})

afterEach(cleanup)

async function submitNewPassword(password = "Sufficiently-long-1") {
  render(<ResetPasswordPage />)
  const inputs = await waitFor(() => {
    const found = document.querySelectorAll('input[type="password"]')
    expect(found.length).toBeGreaterThanOrEqual(2)
    return found
  })
  fireEvent.change(inputs[0], { target: { value: password } })
  fireEvent.change(inputs[1], { target: { value: password } })
  fireEvent.submit(inputs[0].closest("form")!)
}

describe("finishing a password reset", () => {
  it("puts them in the app without another click", async () => {
    await submitNewPassword()

    await waitFor(() => expect(updateUserMock).toHaveBeenCalled())
    await waitFor(() => expect(routerMock.replace).toHaveBeenCalledWith("/"))
  })

  it("never sends them to sign in", async () => {
    // The whole report: "we should just log them in, no need to click sign in
    // before bringing them to Sprntly."
    await submitNewPassword()

    await waitFor(() => expect(routerMock.replace).toHaveBeenCalled())
    for (const [target] of routerMock.replace.mock.calls) {
      expect(target).not.toMatch(/sign-in/)
    }
  })

  it("routes through postLoginPath, not a hardcoded /", async () => {
    // Someone resetting a password can still be mid-onboarding or carrying an
    // unaccepted invite. "/" is only right for the finished case, and this
    // page has no business deciding which case it is.
    postLoginPathMock.mockResolvedValue("/onboarding/your-name")

    await submitNewPassword()

    await waitFor(() =>
      expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/your-name"),
    )
  })

  it("says what happened while the redirect resolves", async () => {
    // Not a blank screen: `postLoginPath` reads the user and their workspace,
    // so there is a moment to fill. It reports, it does not ask.
    await submitNewPassword()
    await waitFor(() => expect(routerMock.replace).toHaveBeenCalled())

    const text = (document.body.textContent || "").replace(/\s+/g, " ")
    expect(text).toMatch(/taking you to sprntly/i)
    expect(text).not.toMatch(/sign in/i)
  })

  it("leaves a way out if the redirect never lands", async () => {
    // The link stays as the escape from a stuck redirect — never the way in.
    await submitNewPassword()
    await waitFor(() => expect(routerMock.replace).toHaveBeenCalled())

    const link = document.querySelector('a[href="/"]')
    expect(link, "no way off the screen if the redirect fails").not.toBeNull()
    expect(link?.className).not.toMatch(/btn-brand/)
  })
})
