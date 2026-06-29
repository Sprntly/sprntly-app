// @vitest-environment jsdom
//
// OnboardingRequiredGuard DOM tests.
//
// The guard sits in the protected `(app)` route group and ensures every entry
// into the app — not just the sign-in form + auth callback that run
// postLoginPath() — enforces finished onboarding. It delegates the routing
// decision to postLoginPath() (single source of truth) and never paints the app
// shell for a user who isn't fully onboarded, so a workspace-less user can't get
// stranded on an empty company-less app.
//
// Branches pinned here:
//   - completed user            → render the app
//   - company exists, unfinished→ postLoginPath → redirect to resume step
//   - no company (no invite)    → postLoginPath → redirect to onboarding entry
//   - invite auto-accepted ("/")→ refresh workspace, then render
//   - on an /onboarding route   → render children (defer to onboarding layout)
//   - still loading             → hold on the shell, no routing
import * as React from "react"
import { cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const replace = vi.fn()
let pathname = "/"
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => pathname,
}))

const postLoginPath = vi.fn<() => Promise<string>>()
vi.mock("../../lib/supabase/client", () => ({
  postLoginPath: () => postLoginPath(),
}))

const refresh = vi.fn(async () => {})
let ws: {
  loading: boolean
  workspace: { onboarding_completed_at: string | null; onboarding_step: number } | null
} = { loading: true, workspace: null }

vi.mock("../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ ...ws, refresh }),
}))

import { OnboardingRequiredGuard } from "../OnboardingRequiredGuard"

afterEach(() => {
  cleanup()
  replace.mockReset()
  postLoginPath.mockReset()
  refresh.mockClear()
  pathname = "/"
  ws = { loading: true, workspace: null }
})

function renderGuard() {
  return render(
    React.createElement(OnboardingRequiredGuard, null, "APP_CONTENT"),
  )
}

describe("OnboardingRequiredGuard", () => {
  it("renders the app for a fully-onboarded user without resolving a route", () => {
    ws = {
      loading: false,
      workspace: {
        onboarding_completed_at: "2026-06-29T00:00:00Z",
        onboarding_step: 5,
      },
    }
    const { getByText } = renderGuard()
    expect(getByText("APP_CONTENT")).toBeTruthy()
    expect(postLoginPath).not.toHaveBeenCalled()
    expect(replace).not.toHaveBeenCalled()
  })

  it("redirects a company-but-unfinished user to the resume step from postLoginPath", async () => {
    ws = {
      loading: false,
      workspace: { onboarding_completed_at: null, onboarding_step: 3 },
    }
    postLoginPath.mockResolvedValue("/onboarding/connectors")
    const { queryByText } = renderGuard()
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/onboarding/connectors"),
    )
    // App content must never paint for a non-completed user.
    expect(queryByText("APP_CONTENT")).toBeNull()
  })

  it("routes a workspace-less, non-invited user to the onboarding entry (not an empty shell)", async () => {
    ws = { loading: false, workspace: null }
    postLoginPath.mockResolvedValue("/onboarding/your-name")
    const { queryByText } = renderGuard()
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/onboarding/your-name"),
    )
    expect(queryByText("APP_CONTENT")).toBeNull()
  })

  it("refreshes the workspace (no redirect) when postLoginPath clears the user into the app", async () => {
    // e.g. a pending invite was auto-accepted, or onboarding just completed and
    // the cached workspace is stale — postLoginPath returns "/".
    ws = { loading: false, workspace: null }
    postLoginPath.mockResolvedValue("/")
    renderGuard()
    await waitFor(() => expect(refresh).toHaveBeenCalled())
    expect(replace).not.toHaveBeenCalled()
  })

  it("defers to the onboarding layout on /onboarding routes (no routing, renders children)", () => {
    pathname = "/onboarding/metrics"
    // Even with an unfinished workspace, the guard must not act here — that
    // would fight step navigation (incl. going back a step).
    ws = {
      loading: false,
      workspace: { onboarding_completed_at: null, onboarding_step: 2 },
    }
    const { getByText } = renderGuard()
    expect(getByText("APP_CONTENT")).toBeTruthy()
    expect(postLoginPath).not.toHaveBeenCalled()
    expect(replace).not.toHaveBeenCalled()
  })

  it("holds on the shell while the workspace is still loading", () => {
    ws = { loading: true, workspace: null }
    const { queryByText } = renderGuard()
    expect(queryByText("APP_CONTENT")).toBeNull()
    expect(postLoginPath).not.toHaveBeenCalled()
    expect(replace).not.toHaveBeenCalled()
  })

  it("falls back to the onboarding entry if postLoginPath throws", async () => {
    ws = { loading: false, workspace: null }
    postLoginPath.mockRejectedValue(new Error("network"))
    renderGuard()
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/onboarding/your-name"),
    )
  })
})
