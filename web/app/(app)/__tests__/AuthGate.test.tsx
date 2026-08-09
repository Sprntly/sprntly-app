// @vitest-environment jsdom
//
// AuthGate: presence of `?share=` hands ALL routing decisions to
// ArtifactShareGate (its own effect's redirect-to-/sign-in never fires);
// absence renders unchanged from before this ticket — same redirect, same
// AuthLoading fallback, same pass-through once authed. The Suspense boundary
// AuthGate now wraps its render in (required for useSearchParams — Next 15's
// CSR-bailout rule) is exercised here too: these assertions only pass if it
// doesn't swallow or delay the existing redirect/pass-through behavior.
import * as React from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const routerMock = { push: vi.fn(), replace: vi.fn() }
const searchParamsMock = vi.hoisted(() => ({ share: null as string | null }))
vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useSearchParams: () => ({
    get: (key: string) => (searchParamsMock as Record<string, string | null>)[key] ?? null,
  }),
}))

const authMock = vi.hoisted(() => ({ value: { kind: "anonymous" } as { kind: string } }))
vi.mock("../../lib/auth", () => ({
  useAuth: () => authMock.value,
}))

vi.mock("../../components/shared/ArtifactShareGate", () => ({
  ArtifactShareGate: ({ token }: { token: string }) => (
    <div data-testid="artifact-share-gate" data-token={token} />
  ),
}))

import { AuthGate } from "../AuthGate"

function AppTreeSpy() {
  return <div data-testid="real-app-tree" />
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  searchParamsMock.share = null
})

describe("AuthGate", () => {
  it("test_auth_gate_delegates_to_artifact_share_gate_when_share_param_present", async () => {
    searchParamsMock.share = "tok-1"
    authMock.value = { kind: "anonymous" }
    render(
      <AuthGate>
        <AppTreeSpy />
      </AuthGate>,
    )

    await waitFor(() => {
      expect(screen.getByTestId("artifact-share-gate").getAttribute("data-token")).toBe("tok-1")
    })
    // The bypassed redirect effect: presence of ?share= must skip the
    // anonymous→/sign-in redirect entirely, even though auth.kind is
    // "anonymous" here — ArtifactShareGate owns routing for this visit.
    expect(routerMock.replace).not.toHaveBeenCalled()
    expect(screen.queryByTestId("real-app-tree")).toBeNull()
  })

  it("delegates for an unconfigured session too, without redirecting", async () => {
    searchParamsMock.share = "tok-1"
    authMock.value = { kind: "unconfigured" }
    render(
      <AuthGate>
        <AppTreeSpy />
      </AuthGate>,
    )
    await waitFor(() => {
      expect(screen.getByTestId("artifact-share-gate")).not.toBeNull()
    })
    expect(routerMock.replace).not.toHaveBeenCalled()
  })

  it("test_auth_gate_unchanged_redirect_when_no_share_param", async () => {
    authMock.value = { kind: "anonymous" }
    render(
      <AuthGate>
        <AppTreeSpy />
      </AuthGate>,
    )

    await waitFor(() => {
      expect(routerMock.replace).toHaveBeenCalledWith("/sign-in")
    })
    expect(screen.queryByTestId("artifact-share-gate")).toBeNull()
    expect(screen.queryByTestId("real-app-tree")).toBeNull()
  })

  it("unconfigured with no share param still redirects to /sign-in", async () => {
    authMock.value = { kind: "unconfigured" }
    render(
      <AuthGate>
        <AppTreeSpy />
      </AuthGate>,
    )
    await waitFor(() => {
      expect(routerMock.replace).toHaveBeenCalledWith("/sign-in")
    })
  })

  it("regression: an authed visit with no share param renders children (Suspense wrapper doesn't block pass-through)", async () => {
    authMock.value = { kind: "authed" }
    render(
      <AuthGate>
        <AppTreeSpy />
      </AuthGate>,
    )
    await waitFor(() => {
      expect(screen.getByTestId("real-app-tree")).not.toBeNull()
    })
    expect(routerMock.replace).not.toHaveBeenCalled()
  })

  it("shows the AuthLoading shell (not a blank Suspense fallback flash) while auth.kind is not yet authed and no share param", () => {
    authMock.value = { kind: "loading" }
    render(
      <AuthGate>
        <AppTreeSpy />
      </AuthGate>,
    )
    expect(screen.getByText("Loading…")).not.toBeNull()
    expect(screen.queryByTestId("real-app-tree")).toBeNull()
  })
})
