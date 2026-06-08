// @vitest-environment jsdom
//
// Container-level mount test for onboarding step 04 — "Share your business
// context." The sibling Onboarding4.test.tsx renders only the pure
// ContextUploadView via renderToStaticMarkup, so it never exercises the
// stateful Onboarding4 container and misses container-level crashes (the
// production "Application error: a client-side exception has occurred" on
// /onboarding/4). This file mounts the real default container under jsdom
// with mocked auth/onboarding/router so a render-time throw is caught.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// Hooks + API the container depends on are mocked so the mount is
// deterministic and offline. Paths resolve relative to this test file.
const authMock = vi.fn()
const onboardingMock = vi.fn()
const routerMock = { push: vi.fn(), replace: vi.fn() }

vi.mock("../../../../lib/auth", () => ({ useAuth: () => authMock() }))
vi.mock("../../../../context/OnboardingContext", () => ({
  useOnboarding: () => onboardingMock(),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../../lib/onboarding/store", () => ({
  advanceOnboardingStep: vi.fn(),
  markSkippedFields: vi.fn(),
}))
vi.mock("../../../../lib/api", () => ({
  companiesApi: { uploadFiles: vi.fn() },
}))

import { Onboarding4 } from "../Onboarding4"
import type { WorkspaceCompany } from "../../../../lib/onboarding/types"

// A loaded workspace matching the real Workspace type, as the context yields
// it mid-onboarding (step 4): product not yet captured (null), industry set.
function makeWorkspace(over: Partial<WorkspaceCompany> = {}): WorkspaceCompany {
  return {
    id: "ws-1",
    slug: "acme",
    display_name: "Acme",
    product_description: null,
    product: null,
    industry: "B2B SaaS",
    stage: "Seed",
    business_type: "SaaS",
    team_size: null,
    engineering_capacity: null,
    pm_engineer_ratio: null,
    competitors: [],
    tech_stack: [],
    okrs: null,
    recent_decisions: null,
    dead_ends: [],
    biggest_risk: null,
    kpi_tree: { north_star: "", metrics: [] },
    feature_flags: {
      weekly_brief: true,
      on_demand_analysis: true,
      auto_prd_generation: true,
      engineer_agent: false,
      research_agent: false,
      on_call_agent: false,
      claude_code_handoff: false,
    },
    notification_settings: {},
    onboarding_step: 4,
    onboarding_completed_at: null,
    ...over,
  }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Onboarding4 (container) — mounts without crashing", () => {
  it("renders the context-upload step for a loaded workspace", () => {
    authMock.mockReturnValue({
      kind: "authed",
      user: { id: "u-1" },
      session: {},
    })
    onboardingMock.mockReturnValue({
      loading: false,
      profile: null,
      workspace: makeWorkspace(),
      refresh: vi.fn(),
      setWorkspace: vi.fn(),
    })

    // Regression: this mount threw in production. It must render the step.
    render(React.createElement(Onboarding4))
    expect(screen.getByText("Share your business context")).not.toBeNull()
    expect(screen.getByText(/Documents/)).not.toBeNull()
  })

  it("renders for a workspace whose product/industry are still null (mid-onboarding)", () => {
    // Step 4 runs before product/industry are captured, so the loaded
    // workspace legitimately has product: null / industry: null. The mount
    // must not throw on these.
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue({
      loading: false,
      profile: null,
      workspace: makeWorkspace({ product: null, industry: null }),
      refresh: vi.fn(),
      setWorkspace: vi.fn(),
    })
    render(React.createElement(Onboarding4))
    expect(screen.getByText("Share your business context")).not.toBeNull()
  })

  it("shows the loading shell while the workspace is loading", () => {
    authMock.mockReturnValue({ kind: "loading" })
    onboardingMock.mockReturnValue({
      loading: true,
      profile: null,
      workspace: null,
      refresh: vi.fn(),
      setWorkspace: vi.fn(),
    })
    render(React.createElement(Onboarding4))
    expect(screen.getByText("Loading…")).not.toBeNull()
  })

  it("redirects to step 1 from an EFFECT (never during render) when there is no workspace", () => {
    // Regression for the client-side exception: the prior code called
    // router.replace() during render when loading had finished with no
    // workspace. Navigating as a render side-effect throws in React 19
    // (update-while-rendering) and surfaces as the production error
    // boundary. The fix moves the redirect into an effect and renders the
    // loading shell meanwhile — so this mount stays crash-free.
    authMock.mockReturnValue({ kind: "authed", user: { id: "u-1" }, session: {} })
    onboardingMock.mockReturnValue({
      loading: false,
      profile: null,
      workspace: null,
      refresh: vi.fn(),
      setWorkspace: vi.fn(),
    })

    const errors: unknown[] = []
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args) => errors.push(args[0]))
    render(React.createElement(Onboarding4))
    spy.mockRestore()

    // Redirect fired (from the effect, after commit), shell shown, and React
    // logged no "update a component while rendering" / act warnings.
    expect(routerMock.replace).toHaveBeenCalledWith("/onboarding/1")
    expect(screen.getByText("Loading…")).not.toBeNull()
    const sideEffectInRender = errors
      .map(String)
      .filter((m) => /while rendering a different component|Cannot update a component/.test(m))
    expect(sideEffectInRender).toEqual([])
  })
})
