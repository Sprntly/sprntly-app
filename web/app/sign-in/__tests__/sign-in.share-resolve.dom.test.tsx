// @vitest-environment jsdom
//
// A returning user (with an existing account, no pending_share_token metadata
// at all) who signs in from a `?share={token}` link must still resolve that
// token — postLoginPath()'s own pending-share resolution only fires for a
// token set at SIGN-UP time, so a sign-in never sees it otherwise.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const routerMock = { push: vi.fn(), replace: vi.fn() }
const searchParamsMock = vi.hoisted(() => ({ share: null as string | null }))
vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useSearchParams: () => ({
    get: (key: string) => (searchParamsMock as Record<string, string | null>)[key] ?? null,
  }),
}))

const signInWithPasswordMock = vi.fn()
const postLoginPathMock = vi.fn()
vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    kind: "anonymous",
    signInWithPassword: signInWithPasswordMock,
    postLoginPath: postLoginPathMock,
    resetPassword: vi.fn(),
    signInWithGoogle: vi.fn(),
  }),
}))

const resolveMock = vi.fn()
vi.mock("../../lib/artifactShareApi", () => ({
  artifactShareApi: { resolve: (...a: unknown[]) => resolveMock(...a) },
}))

import SignInPage from "../page"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  searchParamsMock.share = null
})

beforeEach(() => {
  postLoginPathMock.mockResolvedValue("/")
})

async function signIn() {
  fireEvent.change(screen.getByLabelText(/work email/i), {
    target: { value: "sarah@acme.com" },
  })
  fireEvent.change(screen.getByLabelText(/^password$/i), {
    target: { value: "hunter2pass" },
  })
  fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }))
}

describe("/sign-in — resolves a ?share= token for a returning user", () => {
  it("routes to the guest-view URL on a resolved guest_view outcome", async () => {
    searchParamsMock.share = "tok-42"
    signInWithPasswordMock.mockResolvedValue(undefined)
    resolveMock.mockResolvedValue({
      outcome: "guest_view",
      artifact_type: "prd",
      artifact_id: 482,
      owning_company_name: "Acme Co",
      sharer_name: "Priya Shah",
    })
    render(<SignInPage />)

    await signIn()

    await waitFor(() => {
      expect(routerMock.replace).toHaveBeenCalledWith("/?prd=482&share=tok-42")
    })
    expect(resolveMock).toHaveBeenCalledWith("tok-42")
  })

  it("routes to /not-authorized with the reason on a blocked outcome", async () => {
    searchParamsMock.share = "tok-42"
    signInWithPasswordMock.mockResolvedValue(undefined)
    resolveMock.mockResolvedValue({ outcome: "blocked", reason: "different_company" })
    render(<SignInPage />)

    await signIn()

    await waitFor(() => {
      expect(routerMock.replace).toHaveBeenCalledWith(
        "/not-authorized?share=tok-42&reason=different_company",
      )
    })
  })

  it("falls back to postLoginPath()'s own result when resolve fails (fail-open, never stranded)", async () => {
    searchParamsMock.share = "bad-token"
    signInWithPasswordMock.mockResolvedValue(undefined)
    postLoginPathMock.mockResolvedValue("/onboarding/your-name")
    resolveMock.mockRejectedValue(new Error("404"))
    render(<SignInPage />)

    await signIn()

    await waitFor(() => {
      expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/your-name")
    })
  })

  it("regression: sign-in with no share param is unaffected — routes straight to postLoginPath()'s result", async () => {
    signInWithPasswordMock.mockResolvedValue(undefined)
    postLoginPathMock.mockResolvedValue("/")
    render(<SignInPage />)

    await signIn()

    await waitFor(() => {
      expect(routerMock.replace).toHaveBeenCalledWith("/")
    })
    expect(resolveMock).not.toHaveBeenCalled()
  })
})
