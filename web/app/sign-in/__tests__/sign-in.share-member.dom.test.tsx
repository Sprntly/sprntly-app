// @vitest-environment jsdom
//
// The likeliest real-world shape of the bug: a colleague clicks a teammate's
// share link, signs in, and the sign-in redirect decides where they land.
//
// It used to send every same-company caller to `/?prd=X&share=TOKEN`, which
// AuthGate routes into ArtifactShareGate → the READ-ONLY guest viewer. A full
// member of the owning company must land in the real app instead, with the
// PRD and its tickets editable.
import * as React from "react"
import { cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.hoisted(() => ({
  value: {} as Record<string, unknown>,
}))
vi.mock("../../lib/auth", () => ({ useAuth: () => authMock.value }))

const replaceMock = vi.fn()
const searchMock = vi.hoisted(() => ({ params: new URLSearchParams() }))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
  useSearchParams: () => searchMock.params,
}))

const resolveMock = vi.fn()
vi.mock("../../lib/artifactShareApi", () => ({
  artifactShareApi: { resolve: (...a: unknown[]) => resolveMock(...a) },
}))

const presetMock = vi.fn()
vi.mock("../../context/WorkspaceContext", () => ({
  presetActiveWorkspace: (...a: unknown[]) => presetMock(...a),
}))

vi.mock("../../components/auth/AuthShell", () => ({
  AuthShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))
// Capture the real props so a test can drive the SUBMIT path, not just the
// already-authed effect path. The submit path is where the workspace-preset
// race lived, and a harness that only seeds `kind: "authed"` can never see it.
const viewProps = vi.hoisted(() => ({ current: null as Record<string, any> | null }))
vi.mock("../../components/auth/SignInView", () => ({
  SignInView: (props: Record<string, any>) => {
    viewProps.current = props
    return <div data-testid="sign-in-view" />
  },
}))

const getUserMock = vi.fn()
vi.mock("../../lib/supabase/client", () => ({
  getSupabase: () => ({ auth: { getUser: getUserMock } }),
}))

vi.mock("../../lib/auth-validation", () => ({
  authLockoutRemainingMs: () => 0,
  clearSignInAttempts: () => {},
  describeSignInError: (e: unknown) => String(e),
  recordFailedSignIn: () => {},
  validateWorkEmail: () => null,
}))

import SignInPage from "../page"

const MEMBER = {
  outcome: "member" as const,
  artifact_type: "prd" as const,
  artifact_id: 482,
  public_id: "042494cd-22c0-4c20-9967-cc761d192ae0",
  owner_workspace_id: "ws-notifications",
  owning_company_name: "Acme Co",
  sharer_name: "Priya Shah",
}

beforeEach(() => {
  searchMock.params = new URLSearchParams("share=tok-abc")
  authMock.value = {
    kind: "authed",
    user: { id: "user-42" },
    postLoginPath: vi.fn().mockResolvedValue("/"),
  }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("sign-in redirect for a shared artifact", () => {
  it("sends a company member to the editable app, dropping the share token", async () => {
    resolveMock.mockResolvedValue(MEMBER)

    render(<SignInPage />)

    await waitFor(() => expect(replaceMock).toHaveBeenCalled())
    const target = replaceMock.mock.calls.at(-1)![0] as string
    expect(target).toBe("/?prd=042494cd-22c0-4c20-9967-cc761d192ae0")
    // Keeping `share=` is precisely what handed a colleague the read-only
    // viewer — the regression this asserts against.
    expect(target).not.toContain("share=")
    expect(presetMock).toHaveBeenCalledWith("user-42", "ws-notifications")
  })

  it("still sends a guest_view caller through the share pipeline", async () => {
    resolveMock.mockResolvedValue({ ...MEMBER, outcome: "guest_view" })

    render(<SignInPage />)

    await waitFor(() => expect(replaceMock).toHaveBeenCalled())
    expect(replaceMock.mock.calls.at(-1)![0]).toBe(
      "/?prd=042494cd-22c0-4c20-9967-cc761d192ae0&share=tok-abc",
    )
    expect(presetMock).not.toHaveBeenCalled()
  })

  it("still sends a blocked caller to the not-authorized screen", async () => {
    resolveMock.mockResolvedValue({ outcome: "blocked", reason: "different_company" })

    render(<SignInPage />)

    await waitFor(() => expect(replaceMock).toHaveBeenCalled())
    expect(replaceMock.mock.calls.at(-1)![0]).toBe(
      "/not-authorized?share=tok-abc&reason=different_company",
    )
  })
})

describe("sign-in SUBMIT path — the workspace-preset race", () => {
  it("presets the workspace even though `auth` is still the anonymous closure", async () => {
    // Exactly the real sequence: the page renders while ANONYMOUS, the user
    // submits, signInWithPassword resolves, and withShareResolved runs — all
    // before any re-render has replaced `auth`. The old code tested
    // `auth.kind === "authed"` against that stale closure, so the preset was
    // skipped unless the auth effect happened to win the race.
    authMock.value = {
      kind: "anonymous",
      signInWithPassword: vi.fn().mockResolvedValue(undefined),
      postLoginPath: vi.fn().mockResolvedValue("/"),
    }
    getUserMock.mockResolvedValue({ data: { user: { id: "user-99" } } })
    resolveMock.mockResolvedValue(MEMBER)

    render(<SignInPage />)
    await waitFor(() => expect(viewProps.current).not.toBeNull())

    await viewProps.current!.onSubmit({ preventDefault() {} })

    await waitFor(() => expect(replaceMock).toHaveBeenCalled())
    expect(replaceMock.mock.calls.at(-1)![0]).toBe(
      "/?prd=042494cd-22c0-4c20-9967-cc761d192ae0",
    )
    // The assertion that fails on the pre-fix closure guard.
    expect(presetMock).toHaveBeenCalledWith("user-99", "ws-notifications")
  })

  it("still signs in and redirects when the user lookup fails", async () => {
    // The preset is an optimisation, never a gate — a getUser() failure must
    // cost the workspace hint and nothing else.
    authMock.value = {
      kind: "anonymous",
      signInWithPassword: vi.fn().mockResolvedValue(undefined),
      postLoginPath: vi.fn().mockResolvedValue("/"),
    }
    getUserMock.mockRejectedValue(new Error("network"))
    resolveMock.mockResolvedValue(MEMBER)

    render(<SignInPage />)
    await waitFor(() => expect(viewProps.current).not.toBeNull())
    await viewProps.current!.onSubmit({ preventDefault() {} })

    await waitFor(() => expect(replaceMock).toHaveBeenCalled())
    expect(replaceMock.mock.calls.at(-1)![0]).toBe(
      "/?prd=042494cd-22c0-4c20-9967-cc761d192ae0",
    )
    expect(presetMock).not.toHaveBeenCalled()
  })
})
