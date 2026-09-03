// @vitest-environment jsdom
//
// OnboardingRequiredGuard DOM tests.
//
// The guard sits in the protected `(app)` route group and ensures every entry
// into the app — not just the sign-in form + auth callback that run
// postLoginPath() — enforces finished onboarding. Workspace-LESS users are
// delegated to postLoginPath() (which owns invite auto-accept and the profile
// gates); a cached-but-unfinished workspace is refreshed once and then routed
// to its resume step locally, skipping postLoginPath's duplicate waterfall.
// The app shell never paints for a user who isn't fully onboarded, so a
// workspace-less user can't get stranded on an empty company-less app.
//
// Branches pinned here:
//   - completed user            → render the app
//   - company exists, unfinished→ refresh, then local redirect to resume step
//   - refresh flips completed   → render the app (no bounce back to onboarding)
//   - no company (no invite)    → postLoginPath → redirect to onboarding entry
//   - invite auto-accepted ("/")→ refresh workspace, then render
//   - on an /onboarding route   → render children (defer to onboarding layout)
//   - still loading             → hold on the shell, no routing
import * as React from "react"
import { cleanup, render, waitFor } from "@testing-library/react"
import { BILLING_ENABLED } from "../../lib/billingAccess"
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
  workspace: {
    onboarding_completed_at: string | null
    onboarding_step: number
    // The payment gate reads these off the company row.
    plan?: string | null
    subscription_status?: string | null
  } | null
} = { loading: true, workspace: null }

let lockMode = "off"
vi.mock("../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ ...ws, refresh, subscriptionLockMode: lockMode }),
}))

import { OnboardingRequiredGuard } from "../OnboardingRequiredGuard"

afterEach(() => {
  cleanup()
  replace.mockReset()
  postLoginPath.mockReset()
  refresh.mockClear()
  pathname = "/"
  lockMode = "off"
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

  it("redirects a company-but-unfinished user to the resume step after a refresh (no postLoginPath)", async () => {
    ws = {
      loading: false,
      workspace: {
        onboarding_completed_at: null,
        onboarding_step: 3,
        // Past the payment gate — see the gate's own tests below.
        plan: "starter",
        subscription_status: "active",
      },
    }
    const { queryByText } = renderGuard()
    // Step 3 → the third slug ("review"), mapped locally via slugForStep.
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/onboarding/review"),
    )
    // The cache was re-checked first, and the postLoginPath waterfall (getUser
    // → workspace fetch → invite accept) never ran for a known-workspace user.
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(postLoginPath).not.toHaveBeenCalled()
    // App content must never paint for a non-completed user.
    expect(queryByText("APP_CONTENT")).toBeNull()
  })

  it("renders the app (no bounce) when the refresh reveals onboarding just completed", async () => {
    // The just-completed edge case: the cached workspace is momentarily stale
    // right after finishing onboarding in-session; the guard's refresh picks up
    // the persisted completion and clears the user into the app.
    ws = {
      loading: false,
      workspace: { onboarding_completed_at: null, onboarding_step: 9 },
    }
    refresh.mockImplementationOnce(async () => {
      ws = {
        loading: false,
        workspace: {
          onboarding_completed_at: "2026-07-17T00:00:00Z",
          onboarding_step: 9,
        },
      }
    })
    const { getByText } = renderGuard()
    await waitFor(() => expect(getByText("APP_CONTENT")).toBeTruthy())
    expect(replace).not.toHaveBeenCalled()
    expect(postLoginPath).not.toHaveBeenCalled()
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

  it("holds on the wordless mark, on white (not a black screen)", () => {
    // The shell is the same spinning mark the server splash paints, with no
    // caption: a reload runs ONE animation to the app instead of a logo that
    // vanishes and leaves the word "Loading…" standing behind it.
    ws = { loading: true, workspace: null }
    const { container } = renderGuard()
    const mark = container.querySelector(".spr-iris")
    expect(mark).not.toBeNull()
    expect(container.textContent).not.toContain("Loading")
    const shell = container.firstElementChild as HTMLElement
    expect(shell.style.background).toBe("rgb(255, 255, 255)")
  })

  it("falls back to the onboarding entry if postLoginPath throws", async () => {
    ws = { loading: false, workspace: null }
    postLoginPath.mockRejectedValue(new Error("network"))
    renderGuard()
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/onboarding/your-name"),
    )
  })

  // DORMANT WHILE PAYMENTS ARE HIDDEN — this is the gate itself, and it is
  // open. Kept verbatim so flipping `BILLING_ENABLED` restores it.
  it.skipIf(!BILLING_ENABLED)("sends an UNPAID company's unfinished onboarding to the payment gate", async () => {
    // THE HOLE THIS CLOSES: postLoginPath gates a fresh sign-in, but this guard
    // is the other door — a reload, a bookmark, a deep link, anyone already
    // signed in. Gating only the sign-in path let all of them resume at their
    // numbered step and never see the gate at all.
    ws = {
      loading: false,
      workspace: {
        onboarding_completed_at: null,
        onboarding_step: 3,
        plan: "starter",
        subscription_status: null,
      },
    }
    renderGuard()
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/onboarding/plan"))
    // NOT the resume step — the gate comes first.
    expect(replace).not.toHaveBeenCalledWith("/onboarding/review")
  })

  it("does not gate a plan that was never sold through Stripe", async () => {
    // LEGACY and ENTERPRISE carry a null subscription_status by design.
    ws = {
      loading: false,
      workspace: {
        onboarding_completed_at: null,
        onboarding_step: 3,
        plan: "legacy",
        subscription_status: null,
      },
    }
    renderGuard()
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/onboarding/review"),
    )
  })

  // DORMANT WHILE PAYMENTS ARE HIDDEN — this is the gate itself, and it is
  // open. Kept verbatim so flipping `BILLING_ENABLED` restores it.
  it.skipIf(!BILLING_ENABLED)("hard lock: a lapsed company is routed to Billing from the app", async () => {
    // `enforce.bill` already refuses their work, so without this the app
    // renders completely normally and every action fails generically — a
    // working-looking product where nothing works.
    lockMode = "hard"
    ws = {
      loading: false,
      workspace: {
        onboarding_completed_at: "2026-06-01T00:00:00Z",
        onboarding_step: 10,
        plan: "starter",
        subscription_status: "canceled",
      },
    }
    const { queryByText } = renderGuard()
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/settings?section=billing"),
    )
    // …and the app is not painted on the way out.
    expect(queryByText("APP_CONTENT")).toBeNull()
  })

  it.runIf(!BILLING_ENABLED)("payments hidden: an unpaid company resumes its step, and a lapsed one is not locked out", async () => {
    // Both halves of this guard's billing behaviour, in one place because they
    // have one cause. Unfinished onboarding resumes where it left off rather
    // than at a plan picker; and a company still marked `canceled` in the
    // database from before the switch is left alone even under a "hard" lock,
    // because routing them to a billing screen they cannot act on is a trap.
    ws = {
      loading: false,
      workspace: {
        onboarding_completed_at: null,
        onboarding_step: 3,
        plan: "starter",
        subscription_status: null,
      },
    }
    renderGuard()
    await waitFor(() => expect(replace).toHaveBeenCalled())
    expect(replace).not.toHaveBeenCalledWith("/onboarding/plan")

    cleanup()
    replace.mockReset()
    lockMode = "hard"
    ws = {
      loading: false,
      workspace: {
        onboarding_completed_at: "2026-06-01T00:00:00Z",
        onboarding_step: 10,
        plan: "starter",
        subscription_status: "canceled",
      },
    }
    const { getByText } = renderGuard()
    expect(getByText("APP_CONTENT")).toBeTruthy()
    expect(replace).not.toHaveBeenCalledWith("/settings?section=billing")
  })

  it("hard lock: Billing itself stays reachable — locking that away is a trap", () => {
    lockMode = "hard"
    pathname = "/settings"
    ws = {
      loading: false,
      workspace: {
        onboarding_completed_at: "2026-06-01T00:00:00Z",
        onboarding_step: 10,
        subscription_status: "canceled",
      },
    }
    const { getByText } = renderGuard()
    expect(getByText("APP_CONTENT")).toBeTruthy()
    expect(replace).not.toHaveBeenCalled()
  })

  it("read_only never redirects — reading what they paid for is not hostage to a card", () => {
    lockMode = "read_only"
    ws = {
      loading: false,
      workspace: {
        onboarding_completed_at: "2026-06-01T00:00:00Z",
        onboarding_step: 10,
        subscription_status: "canceled",
      },
    }
    const { getByText } = renderGuard()
    expect(getByText("APP_CONTENT")).toBeTruthy()
    expect(replace).not.toHaveBeenCalledWith("/settings?section=billing")
  })

  it("past_due is NOT locked — Stripe is still working the card", () => {
    lockMode = "hard"
    ws = {
      loading: false,
      workspace: {
        onboarding_completed_at: "2026-06-01T00:00:00Z",
        onboarding_step: 10,
        subscription_status: "past_due",
      },
    }
    const { getByText } = renderGuard()
    expect(getByText("APP_CONTENT")).toBeTruthy()
    expect(replace).not.toHaveBeenCalled()
  })

  it("an unset mode locks nobody — a failed fetch must not wall off a working app", () => {
    lockMode = "off"
    ws = {
      loading: false,
      workspace: {
        onboarding_completed_at: "2026-06-01T00:00:00Z",
        onboarding_step: 10,
        subscription_status: "canceled",
      },
    }
    const { getByText } = renderGuard()
    expect(getByText("APP_CONTENT")).toBeTruthy()
    expect(replace).not.toHaveBeenCalled()
  })
})
