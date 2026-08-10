// @vitest-environment jsdom
//
// ArtifactShareGate's evidence-token branches: `member` redirects to the
// real app at `/?evidence=<artifact_id>` (never the PRD deep link), and
// `guest_view` mounts GuestArtifactViewer with `artifactType="evidence"`.
// Mirrors the harness from ArtifactShareGate.member.dom.test.tsx / .test.tsx.
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

const guestViewerPropsMock = vi.fn()
vi.mock("../GuestArtifactViewer", () => ({
  GuestArtifactViewer: (props: Record<string, unknown>) => {
    guestViewerPropsMock(props)
    return <div data-testid="guest-viewer" />
  },
}))

import { ArtifactShareGate } from "../ArtifactShareGate"

function AppTreeSpy() {
  return <div data-testid="real-app-tree" />
}

const assignMock = vi.fn()

beforeEach(() => {
  authMock.value = { kind: "authed", user: { id: "user-42" } }
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, assign: assignMock },
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const EVIDENCE_SHARE_BASE = {
  artifact_type: "evidence" as const,
  artifact_id: 501,
  public_id: null,
  owner_workspace_id: "ws-notifications",
  owning_company_name: "Acme Co",
  sharer_name: "Priya Shah",
}

describe("ArtifactShareGate — evidence token, member outcome", () => {
  it("test_gate_member_evidence_redirects_to_evidence_param — AC12", async () => {
    resolveMock.mockResolvedValue({ ...EVIDENCE_SHARE_BASE, outcome: "member" })

    render(
      <ArtifactShareGate token="tok-evidence">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )

    await waitFor(() => {
      expect(assignMock).toHaveBeenCalledWith("/?evidence=501")
    })
    expect(screen.queryByTestId("guest-viewer")).toBeNull()
    const target = assignMock.mock.calls[0][0] as string
    expect(target).not.toContain("share=")
    expect(target).not.toContain("prd=")
  })

  it("activates the artifact's own workspace before handing over", async () => {
    resolveMock.mockResolvedValue({ ...EVIDENCE_SHARE_BASE, outcome: "member" })

    render(
      <ArtifactShareGate token="tok-evidence">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )

    await waitFor(() => expect(assignMock).toHaveBeenCalled())
    expect(presetMock).toHaveBeenCalledWith("user-42", "ws-notifications")
  })
})

describe("ArtifactShareGate — evidence token, guest_view outcome", () => {
  it("test_gate_guest_evidence_renders_evidence_viewer_arm — AC12", async () => {
    resolveMock.mockResolvedValue({ ...EVIDENCE_SHARE_BASE, outcome: "guest_view" })

    render(
      <ArtifactShareGate token="tok-evidence">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )

    await waitFor(() => {
      expect(screen.getByTestId("guest-viewer")).not.toBeNull()
    })
    expect(assignMock).not.toHaveBeenCalled()
    const props = guestViewerPropsMock.mock.calls.at(-1)?.[0]
    expect(props.artifactType).toBe("evidence")
    expect(props.artifactId).toBe(501)
    expect(props.token).toBe("tok-evidence")
  })
})

describe("ArtifactShareGate — the prd branch stays unchanged", () => {
  it("still redirects a prd member to the /?prd= deep link, not /?evidence=", async () => {
    resolveMock.mockResolvedValue({
      outcome: "member",
      artifact_type: "prd",
      artifact_id: 482,
      public_id: "pub-482",
      owner_workspace_id: "ws-notifications",
      owning_company_name: "Acme Co",
      sharer_name: "Priya Shah",
    })

    render(
      <ArtifactShareGate token="tok-prd">
        <AppTreeSpy />
      </ArtifactShareGate>,
    )

    await waitFor(() => {
      expect(assignMock).toHaveBeenCalledWith("/?prd=pub-482")
    })
  })
})
