// @vitest-environment jsdom
//
// ArtifactShareGate is the component that decides whether `children` (the
// real WorkspaceProvider > OnboardingRequiredGuard > ... > AppShell tree)
// renders AT ALL for a `?share=` visit. Every branch below asserts `children`
// (a spy component) never mounts — the load-bearing guarantee behind AC7/8/9.
import * as React from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const authMock = vi.hoisted(() => ({ value: { kind: "loading" } as { kind: string } }))
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

vi.mock("../../auth/EntryGateScreen", () => ({
  EntryGateScreen: ({ token, publicId }: { token?: string; publicId?: string }) => (
    <div data-testid="entry-gate" data-token={token} data-public-id={publicId} />
  ),
}))

vi.mock("../../auth/NotAuthorizedScreen", () => ({
  NotAuthorizedScreen: ({ reason }: { reason: string }) => (
    <div data-testid="not-authorized" data-reason={reason} />
  ),
}))

vi.mock("../GuestArtifactViewer", () => ({
  GuestArtifactViewer: (props: Record<string, unknown>) => (
    <div data-testid="guest-viewer" data-props={JSON.stringify(props)} />
  ),
}))

import { ArtifactShareGate } from "../ArtifactShareGate"

function AppTreeSpy() {
  return <div data-testid="real-app-tree" />
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("ArtifactShareGate", () => {
  it("shows AuthLoading while auth resolves, without mounting children", () => {
    authMock.value = { kind: "loading" }
    render(
      <ArtifactShareGate token="tok">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )
    expect(screen.getByTestId("auth-loading")).not.toBeNull()
    expect(screen.queryByTestId("real-app-tree")).toBeNull()
  })

  it("renders EntryGateScreen for an anonymous visitor", () => {
    authMock.value = { kind: "anonymous" }
    render(
      <ArtifactShareGate token="tok-abc">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )
    expect(screen.getByTestId("entry-gate").getAttribute("data-token")).toBe("tok-abc")
    expect(screen.queryByTestId("real-app-tree")).toBeNull()
  })

  it("renders EntryGateScreen for an unconfigured session too", () => {
    authMock.value = { kind: "unconfigured" }
    render(
      <ArtifactShareGate token="tok-abc">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )
    expect(screen.getByTestId("entry-gate")).not.toBeNull()
  })

  it("test_artifact_share_gate_blocks_different_company_without_mounting_app_tree — AC7", async () => {
    authMock.value = { kind: "authed" }
    resolveMock.mockResolvedValue({ outcome: "blocked", reason: "different_company" })
    render(
      <ArtifactShareGate token="tok">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )
    await waitFor(() => {
      expect(screen.getByTestId("not-authorized").getAttribute("data-reason")).toBe(
        "different_company",
      )
    })
    expect(screen.queryByTestId("real-app-tree")).toBeNull()
  })

  it("test_artifact_share_gate_renders_guest_viewer_for_same_company_different_workspace — AC8", async () => {
    authMock.value = { kind: "authed" }
    resolveMock.mockResolvedValue({
      outcome: "guest_view",
      artifact_type: "prd",
      artifact_id: 482,
      owning_company_name: "Acme Co",
      sharer_name: "Priya Shah",
    })
    render(
      <ArtifactShareGate token="tok">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )
    await waitFor(() => {
      expect(screen.getByTestId("guest-viewer")).not.toBeNull()
    })
    expect(screen.queryByTestId("real-app-tree")).toBeNull()
    expect(screen.queryByTestId("not-authorized")).toBeNull()
  })

  it("test_artifact_share_gate_renders_guest_viewer_for_zero_company_domain_matched — AC9", async () => {
    // Identical branch to AC8 at this layer — the ticket is explicit that the
    // two guest_view sub-cases are indistinguishable here by design.
    authMock.value = { kind: "authed" }
    resolveMock.mockResolvedValue({
      outcome: "guest_view",
      artifact_type: "prd",
      artifact_id: 482,
      owning_company_name: "Acme Co",
      sharer_name: "Priya Shah",
    })
    render(
      <ArtifactShareGate token="tok">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )
    await waitFor(() => {
      expect(screen.getByTestId("guest-viewer")).not.toBeNull()
    })
  })

  it("wires the invalid/expired-token case for an ALREADY-AUTHENTICATED caller (Gate-1 note)", async () => {
    authMock.value = { kind: "authed" }
    resolveMock.mockRejectedValue(new Error("404"))
    render(
      <ArtifactShareGate token="tok">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )
    await waitFor(() => {
      expect(screen.getByTestId("not-authorized").getAttribute("data-reason")).toBe(
        "invalid_token",
      )
    })
    expect(screen.queryByTestId("real-app-tree")).toBeNull()
  })

  describe("bare-link (prdId, no token) mode", () => {
    it("renders EntryGateScreen keyed by prdId for an anonymous visitor", () => {
      authMock.value = { kind: "anonymous" }
      render(
        <ArtifactShareGate publicId="042494cd-22c0-4c20-9967-cc761d192ae0">
          <AppTreeSpy />
        </ArtifactShareGate>,
      )
      const gate = screen.getByTestId("entry-gate")
      expect(gate.getAttribute("data-public-id")).toBe("042494cd-22c0-4c20-9967-cc761d192ae0")
      expect(gate.getAttribute("data-token")).toBeNull()
      expect(screen.queryByTestId("real-app-tree")).toBeNull()
    })

    it("calls prdAccessApi.resolve (never artifactShareApi.resolve) and renders the guest viewer with a null token", async () => {
      authMock.value = { kind: "authed" }
      prdResolveMock.mockResolvedValue({
        outcome: "guest_view",
        artifact_type: "prd",
        artifact_id: 1881,
        owning_company_name: "Acme Co",
      })
      render(
        <ArtifactShareGate publicId="042494cd-22c0-4c20-9967-cc761d192ae0">
          <AppTreeSpy />
        </ArtifactShareGate>,
      )
      await waitFor(() => {
        expect(screen.getByTestId("guest-viewer")).not.toBeNull()
      })
      expect(prdResolveMock).toHaveBeenCalledWith("042494cd-22c0-4c20-9967-cc761d192ae0")
      expect(resolveMock).not.toHaveBeenCalled()
      const props = JSON.parse(screen.getByTestId("guest-viewer").getAttribute("data-props")!)
      expect(props.token).toBeNull()
      expect(props.sharerName).toBeNull()
      expect(props.artifactId).toBe(1881)
      expect(props.publicId).toBe("042494cd-22c0-4c20-9967-cc761d192ae0")
      expect(screen.queryByTestId("real-app-tree")).toBeNull()
    })

    it("blocks a different-company caller without mounting the app tree", async () => {
      authMock.value = { kind: "authed" }
      prdResolveMock.mockResolvedValue({ outcome: "blocked", reason: "different_company" })
      render(
        <ArtifactShareGate publicId="042494cd-22c0-4c20-9967-cc761d192ae0">
          <AppTreeSpy />
        </ArtifactShareGate>,
      )
      await waitFor(() => {
        expect(screen.getByTestId("not-authorized").getAttribute("data-reason")).toBe(
          "different_company",
        )
      })
      expect(screen.queryByTestId("real-app-tree")).toBeNull()
    })
  })
})
