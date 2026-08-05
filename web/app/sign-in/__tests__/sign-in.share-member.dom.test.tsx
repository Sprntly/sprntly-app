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
vi.mock("../../components/auth/SignInView", () => ({
  SignInView: () => <div data-testid="sign-in-view" />,
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
