// @vitest-environment jsdom
//
// The `member` outcome: a visitor who is a real member of the owning company
// AND can act in the artifact's workspace must reach the REAL, editable app —
// not GuestArtifactViewer, which is read-only.
//
// Before this branch existed, every same-company caller resolved to
// `guest_view`, so following a teammate's share link left a colleague unable
// to edit the PRD or its tickets even though they could edit the very same
// PRD by opening it from the sidebar.
import * as React from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.hoisted(() => ({
  value: { kind: "loading" } as { kind: string; user?: { id: string } },
}))
vi.mock("../../../lib/auth", () => ({
  useAuth: () => authMock.value,
}))

vi.mock("../../../(app)/AuthGate", () => ({
  AuthLoading: () => <div data-testid="auth-loading" />,
}))

const resolveMock = vi.fn()
vi.mock("../../../lib/artifactShareApi", () => ({
  artifactShareApi: { resolve: (...a: unknown[]) => resolveMock(...a) },
}))

const prdResolveMock = vi.fn()
vi.mock("../../../lib/prdAccessApi", () => ({
  prdAccessApi: { resolve: (...a: unknown[]) => prdResolveMock(...a) },
}))

const presetMock = vi.fn()
vi.mock("../../../context/WorkspaceContext", () => ({
  presetActiveWorkspace: (...a: unknown[]) => presetMock(...a),
}))

vi.mock("../../auth/EntryGateScreen", () => ({
  EntryGateScreen: () => <div data-testid="entry-gate" />,
}))

vi.mock("../../auth/NotAuthorizedScreen", () => ({
  NotAuthorizedScreen: ({ reason }: { reason: string }) => (
    <div data-testid="not-authorized" data-reason={reason} />
  ),
}))

vi.mock("../GuestArtifactViewer", () => ({
  GuestArtifactViewer: () => <div data-testid="guest-viewer" />,
}))

import { ArtifactShareGate } from "../ArtifactShareGate"

function AppTreeSpy() {
  return <div data-testid="real-app-tree" />
}

const assignMock = vi.fn()

beforeEach(() => {
  authMock.value = { kind: "authed", user: { id: "user-42" } }
  // jsdom's window.location is not assignable wholesale; replacing just the
  // method keeps the rest of the object (origin, href reads) intact.
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, assign: assignMock },
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const MEMBER_SHARE = {
  outcome: "member" as const,
  artifact_type: "prd" as const,
  artifact_id: 482,
  public_id: "042494cd-22c0-4c20-9967-cc761d192ae0",
  owner_workspace_id: "ws-notifications",
  owning_company_name: "Acme Co",
  sharer_name: "Priya Shah",
}

describe("ArtifactShareGate — member outcome (share token)", () => {
  it("hands a member to the real app deep link instead of the read-only guest viewer", async () => {
    resolveMock.mockResolvedValue(MEMBER_SHARE)

    render(
      <ArtifactShareGate token="tok">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )

    await waitFor(() => {
      expect(assignMock).toHaveBeenCalledWith(
        "/?prd=042494cd-22c0-4c20-9967-cc761d192ae0",
      )
    })
    // The read-only shell must never flash, and the target URL must carry
    // neither the share token nor the access=guest marker — either one would
    // send AuthGate straight back through the guest pipeline.
    expect(screen.queryByTestId("guest-viewer")).toBeNull()
    const target = assignMock.mock.calls[0][0] as string
    expect(target).not.toContain("share=")
    expect(target).not.toContain("access=guest")
  })

  it("activates the artifact's own workspace before handing over", async () => {
    resolveMock.mockResolvedValue(MEMBER_SHARE)

    render(
      <ArtifactShareGate token="tok">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )

    await waitFor(() => expect(assignMock).toHaveBeenCalled())
    // Without this, a member of several workspaces lands in whichever one
    // they used last and require_owned_prd 404s the PRD they just opened.
    expect(presetMock).toHaveBeenCalledWith("user-42", "ws-notifications")
  })

  it("navigates exactly once even across re-renders", async () => {
    resolveMock.mockResolvedValue(MEMBER_SHARE)

    const { rerender } = render(
      <ArtifactShareGate token="tok">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )
    await waitFor(() => expect(assignMock).toHaveBeenCalled())
    rerender(
      <ArtifactShareGate token="tok">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )

    expect(assignMock).toHaveBeenCalledTimes(1)
  })

  it("falls back to the raw artifact_id only when there is no public_id", async () => {
    resolveMock.mockResolvedValue({ ...MEMBER_SHARE, public_id: null })

    render(
      <ArtifactShareGate token="tok">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )

    await waitFor(() => expect(assignMock).toHaveBeenCalledWith("/?prd=482"))
  })
})

describe("ArtifactShareGate — member outcome (bare link)", () => {
  it("hands a member to the real app deep link", async () => {
    prdResolveMock.mockResolvedValue({
      outcome: "member",
      artifact_type: "prd",
      artifact_id: 1881,
      owner_workspace_id: "ws-default",
      owning_company_name: "Acme Co",
    })

    render(
      <ArtifactShareGate publicId="042494cd-22c0-4c20-9967-cc761d192ae0">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )

    // The bare-link resolve payload carries NO public_id — but the component
    // is holding the opaque id in its own `publicId` prop, so that is what
    // the URL must use. This previously fell through to the raw sequential
    // artifact_id (`/?prd=1881`), undoing the "never expose the sequential
    // id" rule and producing a re-shareable link that 404s for a colleague.
    await waitFor(() =>
      expect(assignMock).toHaveBeenCalledWith(
        "/?prd=042494cd-22c0-4c20-9967-cc761d192ae0",
      ),
    )
    expect(assignMock.mock.calls[0][0]).not.toContain("1881")
    expect(screen.queryByTestId("guest-viewer")).toBeNull()
    expect(presetMock).toHaveBeenCalledWith("user-42", "ws-default")
  })

  it("falls back to the raw id only when neither opaque id is available", async () => {
    prdResolveMock.mockResolvedValue({
      outcome: "member",
      artifact_type: "prd",
      artifact_id: 1881,
      owner_workspace_id: "ws-default",
      owning_company_name: "Acme Co",
    })

    // Token mode with a null public_id and no publicId prop — the only path
    // left with nothing opaque to use.
    resolveMock.mockResolvedValue({ ...MEMBER_SHARE, public_id: null })
    render(
      <ArtifactShareGate token="tok">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )

    await waitFor(() => expect(assignMock).toHaveBeenCalledWith("/?prd=482"))
  })
})

describe("ArtifactShareGate — the branches that must NOT change", () => {
  it("still renders the read-only guest viewer for a guest_view outcome", async () => {
    resolveMock.mockResolvedValue({ ...MEMBER_SHARE, outcome: "guest_view" })

    render(
      <ArtifactShareGate token="tok">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )

    await waitFor(() => expect(screen.getByTestId("guest-viewer")).not.toBeNull())
    expect(assignMock).not.toHaveBeenCalled()
  })

  it("still blocks a different-company caller and never navigates", async () => {
    resolveMock.mockResolvedValue({ outcome: "blocked", reason: "different_company" })

    render(
      <ArtifactShareGate token="tok">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )

    await waitFor(() =>
      expect(screen.getByTestId("not-authorized").getAttribute("data-reason")).toBe(
        "different_company",
      ),
    )
    expect(assignMock).not.toHaveBeenCalled()
    expect(presetMock).not.toHaveBeenCalled()
  })
})
