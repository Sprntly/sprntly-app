// @vitest-environment jsdom
//
// /sign-up?share={token}: mounts ShareContextStrip + a domain-naming hint on
// step 1, and threads pendingShareToken through to signUpWithPassword (AC4/5).
// /sign-up with no share param renders byte-identically to before this ticket.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const routerMock = { push: vi.fn(), replace: vi.fn() }
const searchParamsMock = vi.hoisted(() => ({ share: null as string | null, email: null as string | null }))
vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useSearchParams: () => ({
    get: (key: string) => (searchParamsMock as Record<string, string | null>)[key] ?? null,
  }),
}))

const signUpWithPasswordMock = vi.fn()
vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    kind: "anonymous",
    signUpWithPassword: signUpWithPasswordMock,
    postLoginPath: vi.fn().mockResolvedValue("/"),
  }),
}))

vi.mock("../../lib/api", () => ({
  signupApi: { emailExists: vi.fn().mockResolvedValue({ exists: false }) },
}))

const getMetadataMock = vi.fn()
vi.mock("../../lib/artifactShareApi", () => ({
  artifactShareApi: { getMetadata: (...a: unknown[]) => getMetadataMock(...a) },
}))

import SignUpPage from "../page"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  searchParamsMock.share = null
  searchParamsMock.email = null
})

async function fillStep1() {
  fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "sarah@acme.com" } })
  fireEvent.change(screen.getByLabelText(/^password$/i), {
    target: { value: "Abcdef1!ghij" },
  })
  fireEvent.change(screen.getByLabelText(/confirm password/i), {
    target: { value: "Abcdef1!ghij" },
  })
  fireEvent.click(screen.getByRole("button", { name: /create account/i }))
  await waitFor(() => expect(screen.getByText(/2 of 2/i)).not.toBeNull())
}

describe("/sign-up — share context", () => {
  it("test_sign_up_step1_view_mounts_share_context_strip_when_present — AC4", async () => {
    searchParamsMock.share = "tok-1"
    getMetadataMock.mockResolvedValue({
      artifact_type: "prd",
      title: "Q3 Retention PRD",
      sharer_name: "Priya Shah",
      owning_company_name: "Acme Co",
      required_email_domain: "acme.com",
    })
    render(<SignUpPage />)

    await waitFor(() => {
      expect(document.querySelector('[data-testid="share-context-strip"]')).not.toBeNull()
    })
    expect(document.querySelector('[data-testid="sign-up-domain-hint"]')).not.toBeNull()
  })

  it("test_sign_up_step1_view_unchanged_without_share_context — AC4 regression", () => {
    render(<SignUpPage />)
    expect(document.querySelector('[data-testid="share-context-strip"]')).toBeNull()
    expect(document.querySelector('[data-testid="sign-up-domain-hint"]')).toBeNull()
  })

  it("test_sign_up_page_passes_pending_share_token_to_signup_call — AC5", async () => {
    searchParamsMock.share = "tok-42"
    getMetadataMock.mockResolvedValue({
      artifact_type: "prd",
      title: "Q3 Retention PRD",
      sharer_name: "Priya Shah",
      owning_company_name: "Acme Co",
      required_email_domain: null,
    })
    signUpWithPasswordMock.mockResolvedValue("confirm_email")
    render(<SignUpPage />)

    await fillStep1()

    fireEvent.change(screen.getByLabelText(/first name/i), { target: { value: "Sarah" } })
    fireEvent.change(screen.getByLabelText(/last name/i), { target: { value: "Chen" } })
    fireEvent.click(screen.getByRole("button", { name: /continue/i }))

    await waitFor(() => {
      expect(signUpWithPasswordMock).toHaveBeenCalled()
    })
    const call = signUpWithPasswordMock.mock.calls[0][0]
    expect(call.pendingShareToken).toBe("tok-42")
    // The verify-email redirect must also carry the token through (AC6 wiring).
    await waitFor(() => {
      expect(routerMock.replace).toHaveBeenCalledWith(
        expect.stringContaining("share=tok-42"),
      )
    })
  })

  it("omits pendingShareToken entirely when no share param is present", async () => {
    signUpWithPasswordMock.mockResolvedValue("confirm_email")
    render(<SignUpPage />)

    await fillStep1()
    fireEvent.change(screen.getByLabelText(/first name/i), { target: { value: "Sarah" } })
    fireEvent.change(screen.getByLabelText(/last name/i), { target: { value: "Chen" } })
    fireEvent.click(screen.getByRole("button", { name: /continue/i }))

    await waitFor(() => {
      expect(signUpWithPasswordMock).toHaveBeenCalled()
    })
    const call = signUpWithPasswordMock.mock.calls[0][0]
    expect(call.pendingShareToken).toBeUndefined()
  })
})
