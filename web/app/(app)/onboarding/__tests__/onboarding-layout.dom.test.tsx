// @vitest-environment jsdom
//
// Mount tests for the onboarding layout guards. Covers the completed-user
// guard (already-onboarded users are bounced to "/" and never see the
// onboarding pages), the mid-onboarding case (children render, no redirect),
// the still-loading case (loading shell, no redirect either way), and that the
// existing email-verify guard still fires for unverified users.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { makeWorkspace } from "../../../components/screens/onboarding/__tests__/fixtures"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const routerMock = { push: vi.fn(), replace: vi.fn() }
const onboardingMock = vi.fn()
const authMock = vi.fn()

vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))

// Keep OnboardingProvider as a transparent pass-through so the layout mounts;
// drive useOnboarding from the test.
vi.mock("../../../context/OnboardingContext", () => ({
  OnboardingProvider: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  useOnboarding: () => onboardingMock(),
}))

vi.mock("../../../lib/auth", () => ({
  useAuth: () => authMock(),
}))

import OnboardingLayout from "../layout"

const verifiedAuth = {
  kind: "authed" as const,
  user: { email: "a@b.com" },
  isEmailVerified: () => true,
}

function Child() {
  return <div data-testid="ob-child">onboarding page</div>
}

beforeEach(() => {
  routerMock.push.mockReset()
  routerMock.replace.mockReset()
  authMock.mockReset()
  onboardingMock.mockReset()
  authMock.mockReturnValue(verifiedAuth)
})

afterEach(() => {
  cleanup()
})

function renderLayout() {
  return render(
    <OnboardingLayout>
      <Child />
    </OnboardingLayout>,
  )
}

describe("OnboardingLayout completion guard", () => {
  it("redirects a user who already completed onboarding to / and hides children", () => {
    onboardingMock.mockReturnValue({
      loading: false,
      profile: null,
      workspace: makeWorkspace({
        onboarding_completed_at: "2026-01-01T00:00:00Z",
      }),
      refresh: () => Promise.resolve(),
      setWorkspace: () => {},
      websiteAnalysis: null,
      setWebsiteAnalysis: () => {},
    })

    renderLayout()

    expect(routerMock.replace).toHaveBeenCalledWith("/")
    expect(screen.queryByTestId("ob-child")).toBeNull()
  })

  it("renders the onboarding pages for a mid-onboarding user with no redirect to /", () => {
    onboardingMock.mockReturnValue({
      loading: false,
      profile: null,
      workspace: makeWorkspace({ onboarding_completed_at: null }),
      refresh: () => Promise.resolve(),
      setWorkspace: () => {},
      websiteAnalysis: null,
      setWebsiteAnalysis: () => {},
    })

    renderLayout()

    expect(screen.getByTestId("ob-child")).toBeTruthy()
    expect(routerMock.replace).not.toHaveBeenCalledWith("/")
  })

  it("shows the loading shell and fires no redirect while the workspace is loading", () => {
    onboardingMock.mockReturnValue({
      loading: true,
      profile: null,
      // completed-looking workspace must NOT trigger a redirect while loading
      workspace: makeWorkspace({
        onboarding_completed_at: "2026-01-01T00:00:00Z",
      }),
      refresh: () => Promise.resolve(),
      setWorkspace: () => {},
      websiteAnalysis: null,
      setWebsiteAnalysis: () => {},
    })

    renderLayout()

    expect(routerMock.replace).not.toHaveBeenCalled()
    expect(screen.queryByTestId("ob-child")).toBeNull()
    expect(screen.getByText("Loading…")).toBeTruthy()
  })

  it("does not redirect during render (no update-during-render console error)", () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {})
    onboardingMock.mockReturnValue({
      loading: false,
      profile: null,
      workspace: makeWorkspace({
        onboarding_completed_at: "2026-01-01T00:00:00Z",
      }),
      refresh: () => Promise.resolve(),
      setWorkspace: () => {},
      websiteAnalysis: null,
      setWebsiteAnalysis: () => {},
    })

    renderLayout()

    const updateDuringRender = errSpy.mock.calls.some((args) =>
      String(args[0] ?? "").includes("Cannot update a component"),
    )
    expect(updateDuringRender).toBe(false)
    expect(routerMock.replace).toHaveBeenCalledWith("/")
    errSpy.mockRestore()
  })
})

describe("OnboardingLayout email-verify guard", () => {
  it("redirects an unverified-email user to /verify-email and hides children", () => {
    authMock.mockReturnValue({
      kind: "authed" as const,
      user: { email: "unverified@b.com" },
      isEmailVerified: () => false,
    })
    onboardingMock.mockReturnValue({
      loading: false,
      profile: null,
      workspace: makeWorkspace({ onboarding_completed_at: null }),
      refresh: () => Promise.resolve(),
      setWorkspace: () => {},
      websiteAnalysis: null,
      setWebsiteAnalysis: () => {},
    })

    renderLayout()

    expect(routerMock.replace).toHaveBeenCalledWith(
      "/verify-email?email=unverified%40b.com",
    )
    expect(screen.queryByTestId("ob-child")).toBeNull()
    expect(routerMock.replace).not.toHaveBeenCalledWith("/")
  })
})
